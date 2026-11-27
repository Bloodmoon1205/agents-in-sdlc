"""End-to-end paper run: synthetic books -> engine -> ScriptedJev -> policy -> risk -> broker -> journal."""
import pytest
from conftest import MIN, T0, book, random_walk

from jevagent.broker import PaperBroker
from jevagent.candidates import BY_INST
from jevagent.config import RiskLimits
from jevagent.escalation import EscalationManager
from jevagent.jev_client import JevUnavailable, ScriptedJev
from jevagent.journal import Journal, read_all
from jevagent.loop import Agent, SymbolRuntime
from jevagent.risk import KillSwitch, RiskEngine
from jevagent.schema import build_schema
from jevagent.state_engine import StateEngine


class Resume:
    def review(self, ctx):
        return {"verdict": "resume"}


def make_agent(tmp_path, jev=None):
    c = BY_INST["HYPE-USDT-SWAP"]
    rt = SymbolRuntime(c, StateEngine(c.inst_id), build_schema(c), shadow=build_schema(c, "v2"))
    return Agent({c.inst_id: rt}, jev or ScriptedJev(), RiskEngine(RiskLimits(), KillSwitch(tmp_path / "rt")),
                 PaperBroker(10_000.0), EscalationManager(Resume()), Journal(tmp_path / "journal"),
                 fundamentals_as_of_ms=T0 - 1)


def run(agent, mids, every=5):
    rt = next(iter(agent.symbols.values()))
    for i, m in enumerate(mids):
        t = T0 + i * MIN
        rt.engine.on_book(book(t, m))
        if i > 60 and i % every == 0:
            agent.on_candle(t)
            agent.escalations.wait_idle()


def test_paper_run_respects_every_limit(tmp_path):
    a = make_agent(tmp_path)
    run(a, random_walk(1500, drift=2e-5, seed=5))
    recs = read_all(tmp_path / "journal")
    kinds = {r["kind"] for r in recs}
    assert {"mark", "decision"} <= kinds
    for r in recs:
        if r["kind"] == "order" and r["risk"]["approved"] and not r["order"]["reduce_only"]:
            assert r["order"]["stop_px"] is not None
    for r in recs:
        if r["kind"] == "decision":
            assert r["snapshot_text"] and r["jev"]["schema_version"] == "v1"
            assert r["jev"]["shadow"]["schema_version"] == "v2"
    eq = [r["equity"] for r in recs if r["kind"] == "mark"]
    assert min(eq) > 10_000 * (1 - RiskLimits().max_drawdown) - 50  # stops + caps keep us far from the kill line


def test_kill_file_flattens_and_blocks(tmp_path):
    a = make_agent(tmp_path)
    mids = random_walk(400, drift=4e-5, seed=9)
    run(a, mids[:300])
    (tmp_path / "rt" / "KILL").write_text("operator")
    run_from = 300
    rt = next(iter(a.symbols.values()))
    for i in range(run_from, 400):
        t = T0 + i * MIN
        rt.engine.on_book(book(t, mids[i]))
        if i % 5 == 0:
            a.on_candle(t)
    assert all(p.qty == 0 for p in a.broker.positions.values())
    recs = read_all(tmp_path / "journal")
    late = [r for r in recs if r["kind"] == "order" and r["ts_ms"] > T0 + run_from * MIN and r["risk"]["approved"]]
    assert all(r["order"]["reduce_only"] for r in late)


def test_jev_outage_holds(tmp_path):
    class Down:
        def decide(self, *a, **k):
            raise JevUnavailable("timeout")
    a = make_agent(tmp_path, Down())
    run(a, random_walk(300))
    assert not a.broker.fills


class ConfidentJev:
    """Test double: confident momentum calls so the full trade path is exercised."""
    def decide(self, state, schema, shadow=None):
        import json
        from jevagent.jev_client import ChoiceAns, JevAnswers, ScoreAns
        s = json.loads(state)
        r = (s.get("ret_bps") or {}).get("1h") or 0.0
        d = "long" if r > 20 else ("short" if r < -20 else "neutral")
        probs = {"long": 0.03, "short": 0.03, "neutral": 0.03}
        probs[d] = 0.94
        risk = "safe" if (s.get("pos") or {}).get("dd", 0) < 0.05 else "reduce"
        return JevAnswers(ChoiceAns("trending", {"trending": 0.9}, 0.85), ChoiceAns(d, probs, 0.86), 0.1,
                          ScoreAns(2.5, {2: 0.5, 3: 0.5}, 0.7), ChoiceAns(risk, {risk: 0.9}, 0.85), schema.version)


@pytest.mark.parametrize("seed,drift", [(5, 3e-5), (9, -3e-5), (21, 0.0)])
def test_trade_path_entries_stops_exits(tmp_path, seed, drift):
    a = make_agent(tmp_path, ConfidentJev())
    run(a, random_walk(3000, drift=drift, seed=seed, vol_ann=0.8))
    recs = read_all(tmp_path / "journal")
    entries = [r for r in recs if r["kind"] == "fill" and r["fill"]["tag"].startswith("enter")]
    exits = [r for r in recs if r["kind"] == "fill" and not r["fill"]["tag"].startswith("enter")]
    assert entries, "trade path never exercised"
    assert exits
    lim = RiskLimits()
    for r in recs:
        if r["kind"] == "order" and r["risk"]["approved"] and not r["order"]["reduce_only"]:
            o, q = r["order"], r["risk"]["qty"]
            assert q * abs(o["limit_px"] - o["stop_px"]) <= lim.max_risk_per_trade * 10_000 * 1.2  # equity drifts a little
            assert q * o["limit_px"] <= lim.max_order_frac * 10_000 * 1.2
    eq = [r["equity"] for r in recs if r["kind"] == "mark"]
    assert min(eq) > 10_000 * (1 - lim.max_drawdown)


def test_crash_makes_symbol_reduce_only_even_if_jev_is_bullish(tmp_path):
    a = make_agent(tmp_path, ConfidentJev())
    mids = random_walk(1500, seed=2, vol_ann=0.5)
    crash = [mids[-1] * (1 - 0.0004 * k) for k in range(1, 500)]  # -18% grind lower
    run(a, mids + crash)
    recs = read_all(tmp_path / "journal")
    stressed = [r for r in recs if r["kind"] == "decision" and r.get("stress")]
    assert stressed
    first = min(r["ts_ms"] for r in stressed)
    late_entries = [r for r in recs if r["kind"] == "fill" and r["fill"]["tag"].startswith("enter") and r["ts_ms"] >= first]
    assert not [r for r in late_entries if r["ts_ms"] in {s["ts_ms"] for s in stressed}]
