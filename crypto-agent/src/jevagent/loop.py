"""The live reflex loop: one decision per closed candle per symbol.

Order of operations each candle (never reordered):
  1. mark equity -> risk.on_mark (drawdown / daily-loss / kill switch)
  2. code-owned exits (stop, take-profit, time stop)
  3. state engine snapshot (causal, <400 tokens)  -> the ONLY thing Jev sees
  4. one Jev call (5 questions + shadow schema questions)
  5. policy gate + fractional Kelly
  6. escalation to Opus if needed (async; symbol reduce-only while pending)
  7. risk.check() on every order, immediately before the broker
  8. journal everything
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .broker import PaperBroker
from .calibration import Calibrator
from .candidates import Candidate
from .config import GateConfig
from .escalation import EscalationManager, Verdict
from .jev_client import JevClient, JevUnavailable
from .journal import Journal
from .policy import Intent, decide
from .risk import OrderRequest, RiskContext, RiskEngine, market_stress
from .schema import JevSchema
from .state_engine import Fundamentals, Portfolio, StateEngine


@dataclass
class SymbolRuntime:
    cand: Candidate
    engine: StateEngine
    schema: JevSchema
    shadow: JevSchema | None = None
    calib: Calibrator = field(default_factory=Calibrator)


@dataclass
class Agent:
    symbols: dict                   # inst_id -> SymbolRuntime
    jev: JevClient
    risk: RiskEngine
    broker: PaperBroker
    escalations: EscalationManager
    journal: Journal
    gate: GateConfig = field(default_factory=GateConfig)
    fundamentals_as_of_ms: int = 0
    context_fn: object = None       # callable(t_ms) -> small dict of cross-asset context (causal)

    def _mids(self, t_ms: int) -> dict:
        out = {}
        for sym, rt in self.symbols.items():
            b = rt.engine._book_at(t_ms)
            if b is not None:
                out[sym] = (b.bids[0][0] + b.asks[0][0]) / 2
        return out

    _stressed: set = field(default_factory=set)
    _depth: dict = field(default_factory=dict)

    def _ctx(self, t_ms: int, mids: dict, age_s: float) -> RiskContext:
        ro = {s for s in self.symbols if self.escalations.restricted(s, t_ms)} | self._stressed
        return RiskContext(
            now_ms=t_ms, equity=self.broker.equity(mids),
            positions={s: p.qty for s, p in self.broker.positions.items()}, mids=mids, snapshot_age_s=age_s,
            reduce_only_symbols=frozenset(ro), depth_usd=dict(self._depth))

    def _send(self, o: OrderRequest, t_ms: int, mids: dict, age_s: float):
        rt = self.symbols[o.symbol]
        dec = self.risk.check(o, self._ctx(t_ms, mids, age_s))   # 7. kill switch + limits, every order
        self.journal.write("order", t_ms, order=o.__dict__, risk={"approved": dec.approved, "qty": dec.qty, "reasons": list(dec.reasons)})
        if not dec.approved:
            return None
        fill = self.broker.submit(o, dec.qty, rt.engine._book_at(t_ms), t_ms)
        if fill:
            self.journal.write("fill", t_ms, fill=fill.__dict__)
        return fill

    def flatten_all(self, t_ms: int, why: str) -> None:
        mids = self._mids(t_ms)
        for sym, p in list(self.broker.positions.items()):
            if p.qty:
                side = "sell" if p.qty > 0 else "buy"
                px = mids[sym] * (0.997 if p.qty > 0 else 1.003)
                self._send(OrderRequest(sym, side, abs(p.qty), px, reduce_only=True, emergency=True, tag=f"flatten:{why}"), t_ms, mids, 0.0)

    def on_candle(self, t_ms: int) -> dict:
        mids = self._mids(t_ms)
        equity = self.broker.equity(mids)
        actions = self.risk.on_mark(equity, t_ms)                         # 1
        self.journal.write("mark", t_ms, equity=equity, hwm=self.risk.hwm, actions=actions, mids=mids)
        if "flatten" in actions:
            self.flatten_all(t_ms, self.risk.kill.reason() or "daily_loss")
        out = {}
        for sym, rt in self.symbols.items():
            out[sym] = self._step(sym, rt, t_ms)
        return out

    def _fund(self, rt: SymbolRuntime, t_ms: int) -> Fundamentals:
        f = dict(rt.cand.fundamentals)
        if rt.cand.next_unlock:
            today = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).date()
            f["unlock_days"] = (rt.cand.next_unlock - today).days
        return Fundamentals(self.fundamentals_as_of_ms, f)

    def _step(self, sym: str, rt: SymbolRuntime, t_ms: int) -> Intent | None:
        mids = self._mids(t_ms)
        if sym not in mids:
            return None
        for o in self.broker.exit_orders(sym, mids[sym], t_ms, self.gate.max_hold_minutes * 60_000):  # 2
            self._send(o, t_ms, mids, 0.0)
        v = self.escalations.poll(sym, t_ms)
        if v:
            self.journal.write("verdict", t_ms, symbol=sym, verdict=v)
            p = self.broker.positions.get(sym)
            if p and p.qty and v["verdict"] in (Verdict.REDUCE.value, Verdict.FLATTEN.value):
                frac = 0.5 if v["verdict"] == Verdict.REDUCE.value else 1.0
                side = "sell" if p.qty > 0 else "buy"
                self._send(OrderRequest(sym, side, abs(p.qty) * frac, mids[sym] * (0.997 if p.qty > 0 else 1.003),
                                        reduce_only=True, tag=f"verdict:{v['verdict']}"), t_ms, mids, 0.0)
        pos = self.broker.positions.get(sym)
        equity = self.broker.equity(mids)
        port = Portfolio(t_ms, pos.qty if pos else 0.0, pos.avg_px if pos else 0.0, equity,
                         max(self.risk.hwm, equity), self.risk.day_start_equity or equity)
        ctx = self.context_fn(t_ms) if self.context_fn else None
        snap, text = rt.engine.snapshot(t_ms, port, self._fund(rt, t_ms), ctx)  # 3
        age = snap.get("age_s") or 0.0
        d10 = (snap.get("px") or {}).get("depth10bp_usd_k")
        self._depth[sym] = d10 * 1e3 if d10 is not None else None
        stress = market_stress(snap, self.risk.limits)
        if stress:
            self._stressed.add(sym)
        else:
            self._stressed.discard(sym)
        try:
            ans = self.jev.decide(text, rt.schema, rt.shadow)                      # 4
        except JevUnavailable as e:
            ans = None
            self.journal.write("jev_error", t_ms, symbol=sym, error=str(e))
        intent = decide(ans, snap, pos.qty if pos else 0.0, rt.cand, self.gate, rt.calib, stress)  # 5
        if intent.escalate:                                                        # 6
            started = self.escalations.request(sym, {
                "symbol": sym, "reason": intent.escalate_reason, "snapshot": snap,
                "jev": ans.to_dict() if ans else None, "thesis": rt.cand.thesis, "bear_case": rt.cand.bear_case,
                "invalidation": [r.__dict__ for r in rt.cand.invalidation],
                "position": pos.__dict__ if pos else None}, t_ms)
            self.journal.write("escalation", t_ms, symbol=sym, reason=intent.escalate_reason, started=started)
        mid = mids[sym]
        if intent.action in ("enter_long", "enter_short"):                         # 7
            long = intent.action == "enter_long"
            stop = mid * (1 - intent.stop_frac) if long else mid * (1 + intent.stop_frac)
            qty = intent.risk_frac * equity / (intent.stop_frac * mid)
            fill = self._send(OrderRequest(sym, "buy" if long else "sell", qty, mid * (1.002 if long else 0.998),
                                           stop_px=stop, tag=intent.action), t_ms, mids, age)
            if fill:
                p = self.broker.pos(sym)
                p.stop_px = stop
                p.tp_px = mid * (1 + intent.stop_frac * self.gate.reward_risk) if long else mid * (1 - intent.stop_frac * self.gate.reward_risk)
                p.opened_ms = t_ms
        elif intent.action in ("exit", "reduce") and pos and pos.qty:
            frac = 1.0 if intent.action == "exit" else intent.reduce_frac
            side = "sell" if pos.qty > 0 else "buy"
            self._send(OrderRequest(sym, side, abs(pos.qty) * frac, mid * (0.997 if pos.qty > 0 else 1.003),
                                    reduce_only=True, tag=intent.action), t_ms, mids, age)
        self.journal.write("decision", t_ms, symbol=sym, snapshot=snap, snapshot_text=text, stress=stress,
                           jev=ans.to_dict() if ans else None, intent=intent.__dict__, mid=mid)  # 8
        return intent
