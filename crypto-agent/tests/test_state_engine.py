import json

import pytest
from conftest import MIN, T0, book, random_walk

from jevagent.state_engine import (TOKEN_BUDGET, CausalityError, Fundamentals, Portfolio, StateEngine,
                                   TradeEvent, approx_tokens)


def _port(t, eq=10_000.0):
    return Portfolio(t, 0.0, 0.0, eq, eq, eq)


def test_snapshot_under_token_budget_with_all_sections(feed):
    e = StateEngine("HYPE-USDT-SWAP")
    t = feed(e, random_walk(1500))
    snap, text = e.snapshot(t, _port(t), Fundamentals(t - 1, {"p_hr": 15.5, "fee_mom": 0.311, "float": 0.25, "unlock_days": 9}),
                            {"btc_24h_bps": -410.0, "funding_8h_bps": 0.25})
    assert approx_tokens(text) <= TOKEN_BUDGET
    assert {"px", "ret_bps", "vol", "flow", "pos", "fund", "mkt"} <= set(snap)
    assert json.loads(text) == snap


def test_future_events_never_leak_into_snapshot(feed):
    mids = random_walk(300)
    a, b = StateEngine("X"), StateEngine("X")
    t = feed(a, mids)
    feed(b, mids)
    # b additionally receives events stamped AFTER t (late-arriving future data, a spike).
    for k in range(1, 30):
        b.on_book(book(t + k * MIN, mids[-1] * 1.5))
        b.on_trade(TradeEvent(t + k * MIN, mids[-1] * 1.5, 999.0, "buy"))
    assert a.snapshot(t)[1] == b.snapshot(t)[1]


def test_fundamentals_from_the_future_are_excluded(feed):
    e = StateEngine("X")
    t = feed(e, random_walk(100))
    snap, _ = e.snapshot(t, fundamentals=Fundamentals(t + 1, {"p_hr": 1.0}))
    assert "fund" not in snap


def test_portfolio_from_the_future_raises(feed):
    e = StateEngine("X")
    t = feed(e, random_walk(100))
    with pytest.raises(CausalityError):
        e.snapshot(t, _port(t + 1))


def test_time_cannot_go_backwards(feed):
    e = StateEngine("X")
    t = feed(e, random_walk(100))
    e.snapshot(t)
    with pytest.raises(CausalityError):
        e.snapshot(t - MIN)


def test_deterministic_text(feed):
    mids = random_walk(400, seed=11)
    texts = []
    for _ in range(2):
        e = StateEngine("X")
        t = feed(e, mids)
        texts.append(e.snapshot(t, _port(t))[1])
    assert texts[0] == texts[1]


def test_arithmetic_is_in_code():
    e = StateEngine("X")
    e.on_book(book(T0, 100.0, spread_bps=4.0, skew=0.2))
    snap, _ = e.snapshot(T0)
    assert snap["px"]["mid"] == pytest.approx(100.0)
    assert snap["px"]["spread_bps"] == pytest.approx(4.0, abs=0.01)
    assert snap["px"]["imb1"] == pytest.approx(0.2, abs=1e-3)
    assert snap["px"]["micro_bps"] > 0  # more bid size -> microprice above mid


def test_drawdown_and_inventory_in_snapshot():
    e = StateEngine("X")
    e.on_book(book(T0, 50.0))
    snap, _ = e.snapshot(T0, Portfolio(T0, 20.0, 48.0, 9_000.0, 10_000.0, 9_500.0))
    assert snap["pos"]["inv"] == pytest.approx(20 * 50 / 9000, abs=1e-3)
    assert snap["pos"]["dd"] == pytest.approx(0.1)
    assert snap["pos"]["day_pnl"] == pytest.approx(9000 / 9500 - 1, abs=1e-4)


def test_polled_duplicate_trades_are_ignored():
    e = StateEngine("X")
    e.on_book(book(T0, 10.0))
    tr = [TradeEvent(T0 - k * 1000, 10.0, 2.0, "buy", trade_id=str(k)) for k in range(50)]
    for _ in range(10):          # the same 50 trades polled ten times
        for t in tr:
            e.on_trade(t)
    snap, _ = e.snapshot(T0)
    assert snap["flow"]["n5m"] == 50


def test_trade_z_needs_an_hour_of_baseline_and_depth_band():
    e = StateEngine("X")
    e.on_book(book(T0, 10.0, depth=30))
    for k in range(100):
        e.on_trade(TradeEvent(T0 - k * 1000, 10.0, 1.0, "buy", str(k)))
    snap, _ = e.snapshot(T0)
    assert snap["flow"]["trades_z"] is None            # only ~2 minutes of trade history
    assert snap["px"]["depth10bp_usd_k"] is not None   # 30 levels at 1 bp spacing reach +-10 bps
    e2 = StateEngine("Y")
    e2.on_book(book(T0, 10.0, depth=3))
    assert e2.snapshot(T0)[0]["px"]["depth10bp_usd_k"] is None  # book too shallow to measure: say so
