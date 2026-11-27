"""OKX public market data (no keys). REST polling; exchange timestamps are kept
as the event time so the state engine's causality checks use exchange time."""
from __future__ import annotations

import json
import urllib.request

from ..state_engine import BookEvent, TradeEvent

BASE = "https://www.okx.com"


def _get(path: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "jevagent/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    if d.get("code") not in ("0", 0):
        raise RuntimeError(f"OKX error {d.get('code')}: {d.get('msg')}")
    return d


_CT_VAL: dict[str, float] = {}


def contract_value(inst_id: str) -> float:
    """Base-currency units per contract. OKX swap sizes are in CONTRACTS
    (e.g. HYPE 0.1, SKY 100, PUMP 1000 coins per contract)."""
    if inst_id not in _CT_VAL:
        x = _get(f"/api/v5/public/instruments?instType=SWAP&instId={inst_id}")["data"][0]
        if x.get("ctValCcy") and not inst_id.startswith(x["ctValCcy"] + "-"):
            raise ValueError(f"{inst_id}: unexpected ctValCcy {x['ctValCcy']}")
        _CT_VAL[inst_id] = float(x["ctVal"])
    return _CT_VAL[inst_id]


def book(inst_id: str, depth: int = 100) -> BookEvent:
    cv = contract_value(inst_id)
    b = _get(f"/api/v5/market/books?instId={inst_id}&sz={depth}")["data"][0]
    lv = lambda xs: tuple((float(p), float(s) * cv) for p, s, *_ in xs)
    return BookEvent(int(b["ts"]), lv(b["bids"]), lv(b["asks"]))


def trades(inst_id: str, limit: int = 100) -> list[TradeEvent]:
    cv = contract_value(inst_id)
    rows = _get(f"/api/v5/market/trades?instId={inst_id}&limit={limit}")["data"]
    return [TradeEvent(int(r["ts"]), float(r["px"]), float(r["sz"]) * cv, r["side"], r.get("tradeId")) for r in rows]


def candles_1m(inst_id: str, limit: int = 300) -> list[tuple[int, float, float, float, float]]:
    """Closed 1m candles, oldest first: (ts, open, high, low, close)."""
    rows = _get(f"/api/v5/market/history-candles?instId={inst_id}&bar=1m&limit={min(limit, 100)}")["data"]
    out = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows if r[8] == "1"]
    return sorted(out)


def funding(inst_id: str) -> float:
    return float(_get(f"/api/v5/public/funding-rate?instId={inst_id}")["data"][0]["fundingRate"])
