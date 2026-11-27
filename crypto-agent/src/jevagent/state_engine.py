"""Deterministic state engine: live order book -> one compact numeric snapshot.

Rules this module enforces (and tests prove):
  * Causality: a snapshot at time t uses ONLY events with exchange ts <= t and
    fundamentals with as_of <= t. Events stamped after t are buffered, never used.
  * Determinism: same events in -> byte-identical snapshot text out.
  * Budget: rendered snapshot is <= TOKEN_BUDGET (approximate tokens). Optional
    sections are dropped in a fixed order before failing loudly.
  * All arithmetic (mid, spread, imbalance, realized vol, inventory, drawdown)
    happens here, in code. Jev only ever reads the result.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

TOKEN_BUDGET = 400
MIN_MS = 60_000


class CausalityError(RuntimeError):
    pass


@dataclass(frozen=True)
class BookEvent:
    ts_ms: int
    bids: tuple[tuple[float, float], ...]  # (price, size) best first
    asks: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class TradeEvent:
    ts_ms: int
    px: float
    sz: float          # base-currency units (feeds convert contracts -> coins)
    side: str          # "buy" (taker bought) | "sell"
    trade_id: str | None = None


@dataclass(frozen=True)
class Portfolio:
    as_of_ms: int
    position_qty: float
    avg_px: float
    equity: float
    hwm: float
    day_start_equity: float


@dataclass(frozen=True)
class Fundamentals:
    as_of_ms: int
    fields: dict  # small numeric dict from the research layer, e.g. p_hr, fee_mom


def approx_tokens(text: str) -> int:
    """Conservative token estimate for compact JSON: words, digit runs split in
    chunks of 3, and every punctuation char counted as one token."""
    n = 0
    for tok in re.findall(r"[A-Za-z_]+|\d+|[^\sA-Za-z_\d]", text):
        n += math.ceil(len(tok) / 3) if tok.isdigit() else (math.ceil(len(tok) / 6) if tok[0].isalpha() or tok[0] == "_" else 1)
    return n


def _r(x: float | None, nd: int):
    if x is None or not math.isfinite(x):
        return None
    v = round(x, nd)
    return 0.0 if v == 0 else v


@dataclass
class StateEngine:
    symbol: str
    history_minutes: int = 1440
    _books: list = field(default_factory=list)          # sorted by ts
    _book_ts: list = field(default_factory=list)
    _trades: deque = field(default_factory=lambda: deque(maxlen=50_000))
    _seen: set = field(default_factory=set)
    _seen_order: deque = field(default_factory=lambda: deque(maxlen=100_000))
    _last_snapshot_t: int = -1

    # ---- ingestion -------------------------------------------------------
    def on_book(self, ev: BookEvent) -> None:
        if not ev.bids or not ev.asks:
            return
        i = bisect.bisect_right(self._book_ts, ev.ts_ms)
        self._book_ts.insert(i, ev.ts_ms)
        self._books.insert(i, ev)
        cutoff = ev.ts_ms - (self.history_minutes + 5) * MIN_MS
        while self._book_ts and self._book_ts[0] < cutoff:
            self._book_ts.pop(0)
            self._books.pop(0)

    def on_trade(self, ev: TradeEvent) -> None:
        """Idempotent: polling feeds re-deliver trades, so dedup by id (or full tuple)."""
        key = ev.trade_id or (ev.ts_ms, ev.px, ev.sz, ev.side)
        if key in self._seen:
            return
        if len(self._seen_order) == self._seen_order.maxlen:
            self._seen.discard(self._seen_order[0])
        self._seen.add(key)
        self._seen_order.append(key)
        self._trades.append(ev)

    # ---- causal views ----------------------------------------------------
    def _book_at(self, t: int) -> BookEvent | None:
        i = bisect.bisect_right(self._book_ts, t)
        return self._books[i - 1] if i else None

    def _minute_mids(self, t: int, minutes: int) -> list[float]:
        """Last mid observed in each closed minute ending at t (causal)."""
        out = []
        for k in range(minutes, -1, -1):
            b = self._book_at(t - k * MIN_MS)
            if b is not None:
                out.append((b.bids[0][0] + b.asks[0][0]) / 2)
        return out

    @staticmethod
    def _rv_ann(mids: list[float]) -> float | None:
        lr = [math.log(b / a) for a, b in zip(mids[:-1], mids[1:]) if a > 0 and b > 0]
        if len(lr) < 10:
            return None
        m = sum(lr) / len(lr)
        var = sum((x - m) ** 2 for x in lr) / (len(lr) - 1)
        return math.sqrt(var * 525_600)  # minutes per year

    def _ret_bps(self, t: int, minutes: int, mid: float) -> float | None:
        b = self._book_at(t - minutes * MIN_MS)
        if b is None:
            return None
        m0 = (b.bids[0][0] + b.asks[0][0]) / 2
        return (math.log(mid / m0)) * 1e4

    # ---- snapshot --------------------------------------------------------
    def snapshot(self, t_ms: int, portfolio: Portfolio | None = None,
                 fundamentals: Fundamentals | None = None, context: dict | None = None) -> tuple[dict, str]:
        if t_ms < self._last_snapshot_t:
            raise CausalityError(f"time went backwards: {t_ms} < {self._last_snapshot_t}")
        book = self._book_at(t_ms)
        if book is None:
            raise CausalityError("no book event at or before t")
        if portfolio is not None and portfolio.as_of_ms > t_ms:
            raise CausalityError("portfolio state is from the future")
        self._last_snapshot_t = t_ms

        bb, ba = book.bids[0][0], book.asks[0][0]
        mid = (bb + ba) / 2
        b1, a1 = book.bids[0][1], book.asks[0][1]
        b5 = sum(s for _, s in book.bids[:5]); a5 = sum(s for _, s in book.asks[:5])
        micro = (ba * b1 + bb * a1) / (b1 + a1) if (b1 + a1) > 0 else mid
        # Depth within +-10 bps of mid: comparable across tick sizes (HYPE tick ~0.16 bps, SKY ~2 bps).
        band = mid * 1e-3
        depth10 = sum(p * s for p, s in book.bids if p >= mid - band) + sum(p * s for p, s in book.asks if p <= mid + band)
        reaches = book.bids[-1][0] <= mid - band and book.asks[-1][0] >= mid + band

        mids_1h = self._minute_mids(t_ms, 60)
        mids_24h = self._minute_mids(t_ms, min(self.history_minutes, 1440))
        rv1h, rv24h = self._rv_ann(mids_1h), self._rv_ann(mids_24h)

        recent = [tr for tr in self._trades if t_ms - 5 * MIN_MS < tr.ts_ms <= t_ms]
        buy = sum(tr.sz for tr in recent if tr.side == "buy"); sell = sum(tr.sz for tr in recent if tr.side == "sell")
        hour = [tr for tr in self._trades if t_ms - 60 * MIN_MS < tr.ts_ms <= t_ms]
        first = min((tr.ts_ms for tr in self._trades), default=t_ms)
        per5 = len(hour) / 12 if hour else 0.0
        warm = t_ms - first >= 60 * MIN_MS  # a z-score needs a full hour of baseline
        trade_z = ((len(recent) - per5) / math.sqrt(per5)) if (per5 > 0 and warm) else None

        snap: dict = {
            "sym": self.symbol,
            "t": datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "age_s": _r((t_ms - book.ts_ms) / 1000, 1),
            "px": {
                "mid": _r(mid, 6 if mid < 1 else 4), "spread_bps": _r((ba - bb) / mid * 1e4, 2),
                "micro_bps": _r((micro - mid) / mid * 1e4, 2),
                "imb1": _r((b1 - a1) / (b1 + a1), 3) if (b1 + a1) else None,
                "imb5": _r((b5 - a5) / (b5 + a5), 3) if (b5 + a5) else None,
                "depth10bp_usd_k": _r(depth10 / 1e3, 1) if reaches else None,
            },
            "ret_bps": {k: _r(self._ret_bps(t_ms, m, mid), 0) for k, m in (("5m", 5), ("1h", 60), ("4h", 240), ("24h", 1440))},
            "vol": {"rv1h_ann": _r(rv1h, 3), "rv24h_ann": _r(rv24h, 3),
                    "ratio": _r(rv1h / rv24h, 2) if rv1h and rv24h else None},
            "flow": {"buy_share_5m": _r(buy / (buy + sell), 3) if (buy + sell) else None,
                     "trades_z": _r(trade_z, 2), "n5m": len(recent)},
        }
        if portfolio is not None:
            notional = portfolio.position_qty * mid
            upnl = (mid - portfolio.avg_px) * portfolio.position_qty if portfolio.position_qty else 0.0
            snap["pos"] = {
                "inv": _r(notional / portfolio.equity, 3) if portfolio.equity else None,
                "upnl_bps": _r(upnl / portfolio.equity * 1e4, 0) if portfolio.equity else None,
                "dd": _r(1 - portfolio.equity / portfolio.hwm, 4) if portfolio.hwm else None,
                "day_pnl": _r(portfolio.equity / portfolio.day_start_equity - 1, 4) if portfolio.day_start_equity else None,
            }
        if fundamentals is not None and fundamentals.as_of_ms <= t_ms:
            snap["fund"] = {k: (_r(v, 3) if isinstance(v, (int, float)) else v) for k, v in sorted(fundamentals.fields.items())}
        if context:
            snap["mkt"] = {k: _r(v, 2) if isinstance(v, float) else v for k, v in sorted(context.items())}

        for drop in (None, "mkt", "flow", "fund"):  # fixed, deterministic degradation order
            if drop:
                snap.pop(drop, None)
            text = json.dumps(snap, separators=(",", ":"), sort_keys=False)
            if approx_tokens(text) <= TOKEN_BUDGET:
                return snap, text
        raise ValueError(f"snapshot exceeds {TOKEN_BUDGET}-token budget even after degradation")
