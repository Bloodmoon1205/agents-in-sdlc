"""Paper broker: fills marketable-limit (IOC) orders by walking the recorded
book, charges taker fees, and manages code-owned stops, targets and time stops.

There is deliberately no live broker in this release. Live order routing is a
Phase-6 gate item (docs/phases/06-release.md) and must be built and tested
against the exchange's demo environment before it exists in this codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .risk import OrderRequest
from .state_engine import BookEvent


@dataclass
class Position:
    qty: float = 0.0
    avg_px: float = 0.0
    stop_px: float | None = None
    tp_px: float | None = None
    opened_ms: int = 0


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    qty: float
    px: float
    fee: float
    ts_ms: int
    tag: str
    realized: float


@dataclass
class PaperBroker:
    cash: float
    taker_fee_bps: float = 5.0
    positions: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)

    def pos(self, sym: str) -> Position:
        return self.positions.setdefault(sym, Position())

    def equity(self, mids: dict) -> float:
        return self.cash + sum(p.qty * mids.get(s, p.avg_px) for s, p in self.positions.items())

    def submit(self, o: OrderRequest, qty: float, book: BookEvent, ts_ms: int) -> Fill | None:
        levels = book.asks if o.side == "buy" else book.bids
        left, cost = qty, 0.0
        for px, sz in levels:
            if (o.side == "buy" and px > o.limit_px) or (o.side == "sell" and px < o.limit_px):
                break
            take = min(left, sz)
            cost += take * px
            left -= take
            if left <= 1e-12:
                break
        filled = qty - max(left, 0.0)
        if filled <= 1e-12:
            return None
        vwap = cost / filled
        fee = cost * self.taker_fee_bps / 1e4
        p = self.pos(o.symbol)
        sgn = 1 if o.side == "buy" else -1
        realized = 0.0
        if p.qty == 0 or p.qty * sgn > 0:           # open / add
            new = p.qty + sgn * filled
            p.avg_px = (p.avg_px * abs(p.qty) + vwap * filled) / abs(new)
            p.qty = new
            if p.opened_ms == 0:
                p.opened_ms = ts_ms
        else:                                       # reduce / close (never flips: risk clips reduces)
            closed = min(filled, abs(p.qty))
            realized = (vwap - p.avg_px) * closed * (1 if p.qty > 0 else -1)
            p.qty += sgn * closed
            if abs(p.qty) < 1e-12:
                self.positions[o.symbol] = Position()
        self.cash -= sgn * filled * vwap + fee
        f = Fill(o.symbol, o.side, filled, vwap, fee, ts_ms, o.tag, realized)
        self.fills.append(f)
        return f

    def exit_orders(self, sym: str, mid: float, now_ms: int, max_hold_ms: int) -> list[OrderRequest]:
        """Code-owned exits: stop, take-profit, time stop. Reduce-only."""
        p = self.positions.get(sym)
        if not p or p.qty == 0:
            return []
        long = p.qty > 0
        hit_stop = p.stop_px is not None and ((long and mid <= p.stop_px) or (not long and mid >= p.stop_px))
        hit_tp = p.tp_px is not None and ((long and mid >= p.tp_px) or (not long and mid <= p.tp_px))
        timed = p.opened_ms and now_ms - p.opened_ms >= max_hold_ms
        if not (hit_stop or hit_tp or timed):
            return []
        tag = "stop" if hit_stop else ("tp" if hit_tp else "time_stop")
        side = "sell" if long else "buy"
        px = mid * (0.997 if long else 1.003)  # marketable within the 50 bps band
        return [OrderRequest(sym, side, abs(p.qty), px, reduce_only=True, tag=tag)]
