import json
import types

from jevagent.escalation import VERDICT_SCHEMA, EscalationManager, OpusEscalator, Verdict

T = 1_795_800_000_000


class Fixed:
    def __init__(self, v):
        self.v, self.calls = v, 0

    def review(self, ctx):
        self.calls += 1
        return {"verdict": self.v, "rationale": "test"}


def test_restricted_while_pending_and_after_non_resume():
    m = EscalationManager(Fixed("stay_flat"))
    assert m.request("S", {}, T)
    assert m.restricted("S", T)
    m.wait_idle()
    assert m.poll("S", T + 1)["verdict"] == "stay_flat"
    assert m.restricted("S", T + 60_000)
    assert not m.restricted("S", T + 241 * 60_000)


def test_resume_lifts_restriction():
    m = EscalationManager(Fixed("resume"))
    m.request("S", {}, T)
    m.wait_idle()
    m.poll("S", T + 1)
    assert not m.restricted("S", T + 2)


def test_rate_limited_escalations_fail_safe():
    r = Fixed("resume")
    m = EscalationManager(r, max_per_hour=2)
    for k in range(5):
        m.request("S", {}, T + k)
        m.wait_idle()
        m.poll("S", T + k)
    assert r.calls == 2
    assert m.restricted("S", T + 10)  # over budget -> restricted, not unrestricted


def test_reviewer_exception_is_stay_flat():
    class Boom:
        def review(self, ctx):
            raise RuntimeError("down")
    m = EscalationManager(Boom())
    m.request("S", {}, T)
    m.wait_idle()
    assert m.poll("S", T)["verdict"] == "stay_flat"


def _fake_client(text=None, stop_reason="end_turn"):
    calls = {}

    def create(**kw):
        calls.update(kw)
        return types.SimpleNamespace(stop_reason=stop_reason, model=kw["model"],
                                     content=[types.SimpleNamespace(type="text", text=text or "")])
    c = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    return c, calls


def test_opus_request_shape_and_parse():
    good = json.dumps({"verdict": "reduce", "thesis_still_valid": False, "regime_assessment": "crisis", "rationale": "dd"})
    c, calls = _fake_client(good)
    out = OpusEscalator(_client=c).review({"symbol": "S"})
    assert out["verdict"] == "reduce"
    assert calls["model"] == "claude-opus-5-5"
    assert calls["output_config"]["format"]["schema"] == VERDICT_SCHEMA
    assert calls["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in calls["betas"]
    assert "thinking" not in calls  # Opus 5.5: adaptive by default; disabling is a 400


def test_opus_refusal_or_garbage_is_stay_flat():
    c, _ = _fake_client("", stop_reason="refusal")
    assert OpusEscalator(_client=c).review({})["verdict"] == Verdict.STAY_FLAT.value
    c, _ = _fake_client("not json")
    assert OpusEscalator(_client=c).review({})["verdict"] == Verdict.STAY_FLAT.value
    c, _ = _fake_client(json.dumps({"verdict": "double_down"}))
    assert OpusEscalator(_client=c).review({})["verdict"] == Verdict.STAY_FLAT.value
