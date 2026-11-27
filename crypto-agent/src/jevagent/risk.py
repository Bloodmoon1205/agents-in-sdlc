"""Deterministic risk layer. The model cannot bypass it: every order, from any
source, goes through RiskEngine.check() immediately before submission, and the
kill switch is read on every call.

Invariants (tested):
  * check() never increases quantity; it approves, clips, or rejects.
  * every risk-increasing order must carry a stop.
  * while killed, only emergency reduce-only orders pass.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import RiskLimits


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str                  # buy | sell
    qty: float
    limit_px: float
    stop_px: float | None = None
    reduce_only: bool = False
    emergency: bool = False
    tag: str = ""


@dataclass(frozen=True)
class RiskContext:
    now_ms: int
    equity: float
    positions: dict            # symbol -> signed qty
    mids: dict                 # symbol -> mid
    snapshot_age_s: float
    reduce_only_symbols: frozenset = frozenset()
    depth_usd: dict | None = None  # symbol -> notional within +-10 bps of mid (state engine)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    qty: float
    reasons: tuple


class KillSwitch:
    """Armed by construction. Tripped by code (drawdown) or by the operator
    creating `<runtime>/KILL`. Reset only by the operator CLI."""

    def __init__(self, runtime_dir: Path):
        self.dir = Path(runtime_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.armed = True

    @property
    def _killed(self) -> Path:
        return self.dir / "KILLED"

    def tripped(self) -> bool:
        return self._killed.exists() or (self.dir / "KILL").exists()

    def trip(self, reason: str) -> None:
        if not self._killed.exists():
            self._killed.write_text(json.dumps({"reason": reason, "at": datetime.now(timezone.utc).isoformat()}))

    def reason(self) -> str:
        if self._killed.exists():
            return json.loads(self._killed.read_text())["reason"]
        return "operator KILL file" if (self.dir / "KILL").exists() else ""

    def operator_reset(self, confirm: str) -> None:
        if confirm != "I have reviewed the incident":
            raise PermissionError("reset requires the exact confirmation phrase")
        for f in ("KILLED", "KILL"):
            (self.dir / f).unlink(missing_ok=True)


def market_stress(snap: dict, limits: RiskLimits) -> list[str]:
    """Deterministic crisis detector on the state-engine snapshot."""
    why = []
    r24 = (snap.get("ret_bps") or {}).get("24h")
    rv = (snap.get("vol") or {}).get("rv1h_ann")
    sp = (snap.get("px") or {}).get("spread_bps")
    if r24 is not None and r24 <= limits.stress_ret24h_bps:
        why.append("ret24h")
    if rv is not None and rv >= limits.stress_rv1h_ann:
        why.append("rv1h")
    if sp is not None and sp >= limits.stress_spread_bps:
        why.append("spread")
    return why


def _finite(*xs) -> bool:
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


@dataclass
class RiskEngine:
    limits: RiskLimits
    kill: KillSwitch
    hwm: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0
    halted_day: str = ""
    _orders: dict = field(default_factory=lambda: defaultdict(deque))

    # ---- account-level marks: called every candle before any decision ----
    def on_mark(self, equity: float, now_ms: int) -> list[str]:
        """Returns actions the loop must take: [] | ["flatten"]."""
        actions: list[str] = []
        if not _finite(equity):
            self.kill.trip("non-finite equity")
            return ["flatten"]
        d = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if d != self.day:
            self.day, self.day_start_equity = d, equity
        self.hwm = max(self.hwm, equity)
        if self.hwm and 1 - equity / self.hwm >= self.limits.max_drawdown:
            self.kill.trip(f"max drawdown {1 - equity / self.hwm:.2%} >= {self.limits.max_drawdown:.0%}")
            actions.append("flatten")
        if self.day_start_equity and equity / self.day_start_equity - 1 <= -self.limits.max_daily_loss:
            if self.halted_day != d:
                self.halted_day = d
                actions.append("flatten")
        if self.kill.tripped() and "flatten" not in actions:
            actions.append("flatten")
        return actions

    @property
    def halted_today(self) -> bool:
        return self.halted_day == self.day and self.day != ""

    # ---- per-order check: called immediately before every submission ----
    def check(self, o: OrderRequest, ctx: RiskContext) -> RiskDecision:
        rs: list[str] = []
        if not _finite(o.qty, o.limit_px, ctx.equity) or o.qty <= 0 or o.limit_px <= 0 or ctx.equity <= 0:
            return RiskDecision(False, 0.0, ("invalid_numbers",))
        if o.side not in ("buy", "sell"):
            return RiskDecision(False, 0.0, ("invalid_side",))
        pos = ctx.positions.get(o.symbol, 0.0)
        mid = ctx.mids.get(o.symbol)
        if not mid or not _finite(mid):
            return RiskDecision(False, 0.0, ("no_mid",))
        sgn = 1 if o.side == "buy" else -1
        reduces = pos != 0 and sgn * pos < 0
        qty = o.qty

        # 1. Kill switch: armed and checked before every order.
        if not self.kill.armed:
            return RiskDecision(False, 0.0, ("kill_switch_disarmed",))
        if self.kill.tripped():
            if o.emergency and reduces:
                return RiskDecision(True, min(qty, abs(pos)), ("killed_emergency_reduce",))
            return RiskDecision(False, 0.0, ("killed",))

        if o.reduce_only or reduces:
            if not reduces:
                return RiskDecision(False, 0.0, ("reduce_only_would_increase",))
            qty = min(qty, abs(pos))  # never flip through zero on a reduce
        else:
            # 2. New risk: every gate below must pass.
            if ctx.snapshot_age_s > self.limits.max_snapshot_age_s:
                return RiskDecision(False, 0.0, ("stale_data",))
            if self.halted_today:
                return RiskDecision(False, 0.0, ("daily_loss_halt",))
            if o.symbol in ctx.reduce_only_symbols:
                return RiskDecision(False, 0.0, ("symbol_reduce_only",))
            if o.stop_px is None or not _finite(o.stop_px) or (o.stop_px - o.limit_px) * sgn >= 0:
                return RiskDecision(False, 0.0, ("missing_or_wrong_side_stop",))
            win = self._orders[o.symbol]
            while win and ctx.now_ms - win[0] > 3_600_000:
                win.popleft()
            if len(win) >= self.limits.max_orders_per_hour:
                return RiskDecision(False, 0.0, ("order_rate_limit",))
            eq = ctx.equity
            depth = (ctx.depth_usd or {}).get(o.symbol)
            if depth is None or not _finite(depth) or depth <= 0:
                return RiskDecision(False, 0.0, ("unknown_liquidity",))
            caps = {
                "max_depth_participation": self.limits.max_depth_participation * depth / mid,
                "max_order": self.limits.max_order_frac * eq / mid,
                "max_position": max(0.0, self.limits.max_position_frac * eq / mid - abs(pos)),
                "max_gross": max(0.0, (self.limits.max_gross_frac * eq
                                       - sum(abs(q) * ctx.mids.get(s, 0.0) for s, q in ctx.positions.items())) / mid),
                "max_risk_per_trade": self.limits.max_risk_per_trade * eq / abs(o.limit_px - o.stop_px),
            }
            for name, cap in caps.items():
                if qty > cap:
                    qty, rs = cap, rs + [f"clipped:{name}"]
        # 3. Fat-finger band (applies to everything, including reduces).
        if abs(o.limit_px - mid) / mid * 1e4 > self.limits.price_band_bps:
            return RiskDecision(False, 0.0, ("price_outside_band",))
        if qty * mid < self.limits.min_order_notional:
            return RiskDecision(False, 0.0, tuple(rs) + ("below_min_notional",))
        assert qty <= o.qty + 1e-12, "risk layer must never increase size"
        if not reduces:
            self._orders[o.symbol].append(ctx.now_ms)
        return RiskDecision(True, qty, tuple(rs) or ("ok",))
