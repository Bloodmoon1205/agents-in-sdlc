import random

import pytest

from jevagent.config import GateConfig, RiskLimits, fingerprint, verify_approved
from jevagent.risk import KillSwitch, OrderRequest, RiskContext, RiskEngine

T = 1_795_800_000_000


def ctx(pos=0.0, eq=10_000.0, mid=100.0, age=1.0, ro=frozenset(), depth=1e9):
    return RiskContext(T, eq, {"S": pos}, {"S": mid}, age, ro, {"S": depth})


@pytest.fixture
def risk(tmp_path):
    r = RiskEngine(RiskLimits(), KillSwitch(tmp_path))
    r.on_mark(10_000.0, T)
    return r


def test_entry_requires_stop(risk):
    assert not risk.check(OrderRequest("S", "buy", 1, 100.1), ctx()).approved
    assert not risk.check(OrderRequest("S", "buy", 1, 100.1, stop_px=101.0), ctx()).approved  # wrong side
    assert risk.check(OrderRequest("S", "buy", 1, 100.1, stop_px=99.0), ctx()).approved


def test_never_increases_size_randomized(risk):
    rng = random.Random(0)
    for _ in range(3000):
        side = rng.choice(["buy", "sell"])
        mid = rng.uniform(0.01, 1000)
        px = mid * rng.uniform(0.99, 1.01)
        stop = px * (rng.uniform(0.9, 0.999) if side == "buy" else rng.uniform(1.001, 1.1))
        o = OrderRequest("S", side, rng.uniform(1e-6, 1e6), px, stop_px=stop, reduce_only=rng.random() < 0.2)
        d = risk.check(o, ctx(pos=rng.uniform(-100, 100), mid=mid, eq=rng.uniform(100, 1e6)))
        assert d.qty <= o.qty + 1e-9
        if d.approved:
            assert d.qty > 0


def test_position_and_risk_per_trade_caps(risk):
    d = risk.check(OrderRequest("S", "buy", 1e6, 100.0, stop_px=99.0), ctx())
    assert d.approved
    notional = d.qty * 100
    assert notional <= 0.10 * 10_000 + 1e-6            # max_order_frac binds first
    assert d.qty * 1.0 <= 0.01 * 10_000 + 1e-6          # loss at stop <= 1% equity


def test_kill_switch_blocks_everything_but_emergency_reduce(risk, tmp_path):
    (tmp_path / "KILL").write_text("operator")
    assert not risk.check(OrderRequest("S", "buy", 1, 100, stop_px=99), ctx()).approved
    assert not risk.check(OrderRequest("S", "sell", 1, 100, reduce_only=True), ctx(pos=5)).approved
    d = risk.check(OrderRequest("S", "sell", 9, 100, reduce_only=True, emergency=True), ctx(pos=5))
    assert d.approved and d.qty == 5


def test_drawdown_trips_kill_switch(risk):
    day2 = T + 86_400_000
    assert risk.on_mark(9_000.0, day2) == []          # -10% from HWM, new UTC day: no limit hit
    assert not risk.kill.tripped()
    assert "flatten" in risk.on_mark(8_499.0, day2 + 1)  # -15.01% from HWM
    assert risk.kill.tripped()
    with pytest.raises(PermissionError):
        risk.kill.operator_reset("yes")
    risk.kill.operator_reset("I have reviewed the incident")
    assert not risk.kill.tripped()


def test_daily_loss_halts_new_risk_until_next_utc_day(risk):
    assert "flatten" in risk.on_mark(9_690.0, T + 1000)
    assert not risk.check(OrderRequest("S", "buy", 1, 100, stop_px=99), ctx(eq=9_690)).approved
    assert risk.check(OrderRequest("S", "sell", 1, 100, reduce_only=True), ctx(pos=2, eq=9_690)).approved
    next_day = T + 86_400_000
    risk.on_mark(9_690.0, next_day)
    assert risk.check(OrderRequest("S", "buy", 1, 100, stop_px=99), RiskContext(next_day, 9_690, {"S": 0}, {"S": 100}, 1.0, frozenset(), {"S": 1e9})).approved


def test_stale_data_band_and_reduce_only_symbol(risk):
    assert not risk.check(OrderRequest("S", "buy", 1, 100, stop_px=99), ctx(age=500)).approved
    assert not risk.check(OrderRequest("S", "buy", 1, 101.0, stop_px=99), ctx()).approved  # 100 bps > 50 band
    assert not risk.check(OrderRequest("S", "buy", 1, 100, stop_px=99), ctx(ro=frozenset({"S"}))).approved


def test_reduce_never_flips(risk):
    d = risk.check(OrderRequest("S", "sell", 10, 100, reduce_only=True), ctx(pos=3))
    assert d.approved and d.qty == 3
    assert not risk.check(OrderRequest("S", "buy", 1, 100, reduce_only=True), ctx(pos=3)).approved


def test_limits_fingerprint_gate(tmp_path):
    f = tmp_path / "approved.sha256"
    with pytest.raises(PermissionError):
        verify_approved(RiskLimits(), GateConfig(), f)
    f.write_text(fingerprint(RiskLimits(), GateConfig()))
    verify_approved(RiskLimits(), GateConfig(), f)
    with pytest.raises(PermissionError):  # any loosening requires re-approval
        verify_approved(RiskLimits(max_drawdown=0.5), GateConfig(), f)
    with pytest.raises(Exception):
        RiskLimits().max_drawdown = 0.9  # frozen


def test_tampered_journal_is_detected(tmp_path):
    from jevagent.journal import Journal, read_all
    j = Journal(tmp_path)
    for k in range(5):
        j.write("x", T + k, v=k)
    f = next(tmp_path.glob("*.jsonl"))
    lines = f.read_text().splitlines()
    lines[2] = lines[2].replace('"v":2', '"v":99')
    f.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        read_all(tmp_path)


def test_code_side_stress_detector():
    from jevagent.risk import market_stress
    L = RiskLimits()
    calm = {"ret_bps": {"24h": -300}, "vol": {"rv1h_ann": 0.6}, "px": {"spread_bps": 2.0}}
    assert market_stress(calm, L) == []
    assert market_stress({**calm, "ret_bps": {"24h": -1600}}, L) == ["ret24h"]
    assert market_stress({**calm, "vol": {"rv1h_ann": 2.0}}, L) == ["rv1h"]
    assert market_stress({**calm, "px": {"spread_bps": 40}}, L) == ["spread"]


def test_liquidity_participation_cap(risk):
    d = risk.check(OrderRequest("S", "buy", 1e6, 100.0, stop_px=99.0), ctx(depth=1_100.0))  # SKY-like book
    assert d.approved and d.qty * 100 <= 0.25 * 1_100 + 1e-6 and "clipped:max_depth_participation" in d.reasons
    assert not risk.check(OrderRequest("S", "buy", 1, 100.0, stop_px=99.0), ctx(depth=None)).approved
    assert risk.check(OrderRequest("S", "sell", 1, 100.0, reduce_only=True), ctx(pos=1, depth=None)).approved  # exits always allowed
