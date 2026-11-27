import json
import types

import pytest
from conftest import MIN, T0, random_walk

from jevagent.candidates import BY_INST
from jevagent.config import RiskLimits
from jevagent.journal import read_all
from jevagent.review.nightly import (Registry, brier_multi, label, propose_revision, reliability, run_nightly,
                                     shadow_promotion_evidence)
from jevagent.schema import DIRECTION, build_schema
from test_loop import ConfidentJev, make_agent, run


def test_brier_basics():
    assert brier_multi([{"long": 1.0}], ["long"], DIRECTION) == 0.0
    assert brier_multi([{"long": 1.0}], ["short"], DIRECTION) == 2.0
    r = reliability([0.9] * 10, [True] * 9 + [False])
    assert r["ece"] == pytest.approx(0.0, abs=1e-9)


def test_labels_are_causal(tmp_path):
    a = make_agent(tmp_path, ConfidentJev())
    run(a, random_walk(2000, seed=4, vol_ann=0.8))
    recs = read_all(tmp_path / "journal")
    last = max(r["ts_ms"] for r in recs)
    rows = label(recs, now_ms=last)
    hmax = 720 * MIN
    assert rows and all(r.t + hmax <= last for r in rows)
    # labels for early decisions don't change when more future data is appended
    early = {r.t: r.dir_label for r in label(recs, now_ms=last)}
    later = {r.t: r.dir_label for r in label(recs, now_ms=last + 10 ** 9)}
    assert all(later[t] == v for t, v in early.items())


def test_nightly_end_to_end_with_shadow_and_gate(tmp_path):
    a = make_agent(tmp_path, ConfidentJev())
    run(a, random_walk(2500, seed=8, vol_ann=0.8))
    reg = Registry(tmp_path / "schemas")
    c = BY_INST["HYPE-USDT-SWAP"]
    reg.stage_shadow(build_schema(c, "v1"), "init")
    reg.write({c.inst_id: {"active": "v1", "shadow": None}})
    recs = read_all(tmp_path / "journal")
    now = max(r["ts_ms"] for r in recs)

    revised = {}

    def create(**kw):
        cur = json.loads(kw["messages"][0]["content"])["current_schema"]["questions"]
        qs = {k: {"instructions": v["instructions"] + " (revised)", **({"criteria": v["criteria"]} if v["criteria"] else {})}
              for k, v in cur.items()}
        revised["called"] = True
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(
            type="text", text=json.dumps({"changes_summary": "tighten wording", "questions": qs}))])
    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))

    rep = run_nightly(tmp_path / "journal", tmp_path / "runtime", reg, now, brain_client=client)
    sym = rep["symbols"][c.inst_id]
    assert sym["active"]["n"] > 0 and "direction" in sym["active"]
    assert (tmp_path / "runtime" / "calibration" / f"{c.inst_id}.json").exists()
    assert sym["active"]["direction"]["skill"] < 0  # fake-confident momentum on noise must score as anti-skill
    assert revised, "weak calibration should trigger a revision proposal"
    assert reg.read()[c.inst_id]["shadow"] == "v2"
    assert reg.load(c.inst_id, "shadow").questions["direction"].instructions.endswith("(revised)")
    assert RiskLimits().max_drawdown == 0.15


def test_promotion_requires_evidence_and_operator(tmp_path):
    reg = Registry(tmp_path)
    c = BY_INST["SKY-USDT-SWAP"]
    reg.stage_shadow(build_schema(c, "v2"), "x")
    ap = tmp_path / "approval.json"
    ap.write_text(json.dumps({"symbol": c.inst_id, "version": "v2", "approved_by": ""}))
    with pytest.raises(PermissionError):
        reg.promote(c.inst_id, "v2", ap)
    ap.write_text(json.dumps({"symbol": c.inst_id, "version": "v2", "approved_by": "operator"}))
    reg.promote(c.inst_id, "v2", ap)
    assert reg.read()[c.inst_id]["active"] == "v2"
    weak = shadow_promotion_evidence({"direction": {"skill": 0.05}, "direction_reliability": {"ece": 0.05}},
                                     {"n": 50, "direction": {"skill": 0.30}, "direction_reliability": {"ece": 0.01}})
    assert not weak["eligible"]  # too few samples, however good it looks


def test_revision_cannot_change_keys():
    c = BY_INST["HYPE-USDT-SWAP"]
    cur = build_schema(c)

    def create(**kw):
        qs = {k: {"instructions": "x", **({"criteria": v.criteria} if v.criteria else {})} for k, v in cur.questions.items()}
        qs["setup_quality"]["criteria"] = ["a", "b"]  # wrong number of levels
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(
            type="text", text=json.dumps({"changes_summary": "", "questions": qs}))])
    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    with pytest.raises(Exception):
        propose_revision({}, cur, "v2", client)
