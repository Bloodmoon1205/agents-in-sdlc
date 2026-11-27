import pytest

from jevagent.calibration import Calibrator
from jevagent.candidates import BY_INST
from jevagent.config import GateConfig
from jevagent.jev_client import ChoiceAns, JevAnswers, ScoreAns
from jevagent.policy import decide, kelly

HYPE = BY_INST["HYPE-USDT-SWAP"]
G = GateConfig()
SNAP = {"vol": {"rv1h_ann": 0.6, "rv24h_ann": 0.5}, "ret_bps": {"1h": 25.0}, "fund": dict(HYPE.fundamentals)}


def ans(direction="long", p=0.92, conf=0.85, setup=2.4, regime="trending", risk="safe", tox=0.1):
    rest = (1 - p) / 2
    probs = {"long": rest, "short": rest, "neutral": rest}
    probs[direction] = p
    return JevAnswers(
        ChoiceAns(regime, {regime: 0.9}, 0.8), ChoiceAns(direction, probs, conf), tox,
        ScoreAns(setup, {0: 0.0, 1: 0.1, 2: 0.4, 3: 0.5}, 0.6), ChoiceAns(risk, {risk: 0.9}, 0.8), "v1")


def test_kelly_formula():
    assert kelly(0.5, 2.0) == pytest.approx(0.25)
    assert kelly(1 / 3, 2.0) == pytest.approx(0.0)


def test_fires_only_when_every_gate_passes():
    assert decide(ans(), SNAP, 0, HYPE, G, Calibrator()).action == "enter_long"
    for bad in (dict(setup=1.99), dict(conf=0.80), dict(direction="neutral"), dict(risk="near_limit"),
                dict(tox=0.6), dict(regime="crisis")):
        assert decide(ans(**bad), SNAP, 0, HYPE, G, Calibrator()).action == "hold", bad


def test_size_is_fractional_kelly_capped_at_quarter():
    i = decide(ans(p=0.99), SNAP, 0, HYPE, G, Calibrator())
    p = i.trace["p_cal"]
    assert i.risk_frac == pytest.approx(0.25 * kelly(p, 2.0))
    loose = GateConfig(kelly_fraction=1.0)  # even if someone configures full Kelly...
    assert decide(ans(p=0.99), SNAP, 0, HYPE, loose, Calibrator()).risk_frac == pytest.approx(0.25 * kelly(p, 2.0))


def test_uncalibrated_probability_is_shrunk():
    i = decide(ans(p=0.95), SNAP, 0, HYPE, G, Calibrator())
    assert i.trace["p_cal"] < 0.80  # shrink 0.5 -> 0.725


def test_escalation_triggers():
    assert decide(ans(conf=0.55), SNAP, 0, HYPE, G, Calibrator()).escalate
    i = decide(ans(regime="crisis"), SNAP, 0, HYPE, G, Calibrator())
    assert i.escalate and "crisis" in i.escalate_reason
    assert not decide(ans(), SNAP, 0, HYPE, G, Calibrator()).escalate


def test_invalidation_blocks_thesis_side_and_escalates():
    snap = {**SNAP, "fund": {**HYPE.fundamentals, "fee_mom": -0.5}}
    i = decide(ans(), snap, 0, HYPE, G, Calibrator())
    assert i.action == "hold" and i.escalate and "invalidation" in i.escalate_reason
    assert decide(ans(), snap, 10.0, HYPE, G, Calibrator()).action == "exit"


def test_position_management():
    assert decide(ans(regime="crisis"), SNAP, 5.0, HYPE, G, Calibrator()).action == "reduce"
    assert decide(ans(direction="short", conf=0.9), SNAP, 5.0, HYPE, G, Calibrator()).action == "exit"
    assert decide(ans(), SNAP, 5.0, HYPE, G, Calibrator()).action == "hold"
    assert decide(None, SNAP, 5.0, HYPE, G, Calibrator()).action == "hold"


def test_code_stress_overrides_a_confident_model():
    i = decide(ans(p=0.99, conf=0.95, setup=3.0), SNAP, 0, HYPE, G, Calibrator(), stress=["rv1h"])
    assert i.action == "hold" and i.escalate and "code_stress" in i.escalate_reason
    assert decide(ans(), SNAP, 4.0, HYPE, G, Calibrator(), stress=["ret24h"]).action == "reduce"


def test_cold_engine_never_enters():
    cold = {"vol": {"rv1h_ann": None, "rv24h_ann": None}, "ret_bps": {"1h": None}, "fund": dict(HYPE.fundamentals)}
    i = decide(ans(p=0.99, conf=0.95, setup=3.0), cold, 0, HYPE, G, Calibrator())
    assert i.action == "hold" and i.trace["gate"]["warm"] is False
