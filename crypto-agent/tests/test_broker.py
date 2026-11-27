import pytest
from conftest import T0, book

from jevagent.broker import PaperBroker
from jevagent.risk import OrderRequest


def test_fill_walks_book_and_charges_fees():
    b = PaperBroker(10_000.0, taker_fee_bps=5.0)
    bk = book(T0, 100.0, spread_bps=2.0, size=1.0)
    f = b.submit(OrderRequest("S", "buy", 2.5, 100.025, stop_px=99), 2.5, bk, T0)  # asks at 100.01, 100.02, 100.03...
    assert f.qty == pytest.approx(2.0)  # only two levels at or under the limit
    assert f.px > 100.0 and f.fee == pytest.approx(f.px * f.qty * 5e-4)


def test_round_trip_pnl_accounting():
    b = PaperBroker(10_000.0, taker_fee_bps=0.0)
    b.submit(OrderRequest("S", "buy", 1, 101, stop_px=99), 1, book(T0, 100.0, spread_bps=0.0, size=5), T0)
    f = b.submit(OrderRequest("S", "sell", 1, 109, reduce_only=True), 1, book(T0 + 1, 110.0, spread_bps=0.0, size=5), T0 + 1)
    assert f.realized == pytest.approx(10.0)
    assert b.equity({"S": 110.0}) == pytest.approx(10_010.0)
    assert b.positions["S"].qty == 0


@pytest.mark.parametrize("mid,tag", [(94.0, "stop"), (111.0, "tp")])
def test_code_owned_stop_and_target(mid, tag):
    b = PaperBroker(10_000.0)
    b.submit(OrderRequest("S", "buy", 1, 101, stop_px=95), 1, book(T0, 100.0, size=5), T0)
    p = b.pos("S")
    p.stop_px, p.tp_px = 95.0, 110.0
    (o,) = b.exit_orders("S", mid, T0 + 1, 10 ** 9)
    assert o.tag == tag and o.reduce_only and o.side == "sell"
    assert b.exit_orders("S", 100.0, T0 + 1, 10 ** 9) == []
    (o,) = b.exit_orders("S", 100.0, T0 + 10 ** 9, 10 ** 9)
    assert o.tag == "time_stop"
