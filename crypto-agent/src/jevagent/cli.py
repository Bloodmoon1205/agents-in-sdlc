"""Operator CLI.

  jevagent compile-schemas                 write schemas/jev/<SYM>/v1.json + ACTIVE.json
  jevagent approve-limits                  record the reviewed risk/gate fingerprint (operator gate)
  jevagent paper --minutes 30 [--jev scripted|typesafe] [--symbols HYPE-USDT-SWAP,...]
  jevagent review [--brain]                nightly review on runtime/journal
  jevagent promote SYM VERSION APPROVAL.json
  jevagent kill "reason"   |  jevagent reset-kill "I have reviewed the incident"
  jevagent status
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import GateConfig, RiskLimits, fingerprint, verify_approved

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SCHEMAS = ROOT / "schemas" / "jev"
APPROVED = ROOT / "gates" / "approved_limits.sha256"


def cmd_compile(_a):
    from .candidates import CANDIDATES
    from .review.nightly import Registry
    from .schema import build_schema
    reg = Registry(SCHEMAS)
    active = reg.read()
    for c in CANDIDATES:
        s = build_schema(c, "v1")
        p = reg.path(c.inst_id, "v1")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(s.to_json() + "\n")
        active.setdefault(c.inst_id, {"active": "v1", "shadow": None})
    reg.write(active)
    print(f"compiled {len(CANDIDATES)} schemas into {SCHEMAS}")


def cmd_approve_limits(_a):
    fp = fingerprint(RiskLimits(), GateConfig())
    print(json.dumps({"risk_limits": RiskLimits().__dict__, "gate": GateConfig().__dict__}, indent=2))
    if input("Type APPROVE to record these limits as reviewed: ").strip() != "APPROVE":
        sys.exit("not approved")
    APPROVED.parent.mkdir(parents=True, exist_ok=True)
    APPROVED.write_text(fp + "\n")
    print("approved", fp)


def build_agent(symbols, jev_kind, equity):
    from .broker import PaperBroker
    from .calibration import Calibrator
    from .candidates import BY_INST, SNAPSHOT_AS_OF
    from .escalation import EscalationManager, OpusEscalator
    from .jev_client import ScriptedJev, TypeSafeJev
    from .journal import Journal
    from .loop import Agent, SymbolRuntime
    from .review.nightly import Registry
    from .risk import KillSwitch, RiskEngine
    from .state_engine import StateEngine
    from datetime import datetime
    verify_approved(RiskLimits(), GateConfig(), APPROVED)
    reg = Registry(SCHEMAS)
    rts = {}
    for s in symbols:
        c = BY_INST[s]
        rts[s] = SymbolRuntime(c, StateEngine(s), reg.load(s, "active"), reg.load(s, "shadow"),
                               Calibrator.load(RUNTIME / "calibration" / f"{s}.json"))
    jev = TypeSafeJev() if jev_kind == "typesafe" else ScriptedJev()
    reviewer = OpusEscalator() if jev_kind == "typesafe" else type("NoBrain", (), {"review": lambda self, c: {"verdict": "stay_flat", "rationale": "paper: no brain configured"}})()
    as_of = int(datetime.fromisoformat(SNAPSHOT_AS_OF.replace("Z", "+00:00")).timestamp() * 1000)
    return Agent(rts, jev, RiskEngine(RiskLimits(), KillSwitch(RUNTIME)), PaperBroker(equity),
                 EscalationManager(reviewer), Journal(RUNTIME / "journal"), fundamentals_as_of_ms=as_of)


def cmd_paper(a):
    from .feeds import okx
    syms = a.symbols.split(",")
    agent = build_agent(syms, a.jev, a.equity)
    end = time.time() + a.minutes * 60
    next_candle = (int(time.time() * 1000) // 60_000 + 1) * 60_000
    # warm-up: seed one book per minute from recent closed candles is not causal-safe for depth,
    # so the engine starts cold and fills its history live.
    while time.time() < end:
        for s in syms:
            try:
                agent.symbols[s].engine.on_book(okx.book(s))
                for tr in okx.trades(s, 50):
                    agent.symbols[s].engine.on_trade(tr)
            except Exception as e:  # noqa: BLE001 - feed hiccup: record, keep running, risk handles staleness
                agent.journal.write("feed_error", int(time.time() * 1000), symbol=s, error=repr(e)[:200])
        now = int(time.time() * 1000)
        if now >= next_candle:
            out = agent.on_candle(next_candle)
            print(time.strftime("%H:%M:%S"), {k: (v.action if v else None) for k, v in out.items()},
                  "equity", round(agent.broker.equity(agent._mids(next_candle)), 2), flush=True)
            next_candle += 60_000
        time.sleep(a.poll_s)


def cmd_review(a):
    from .review.nightly import Registry, run_nightly
    client = None
    if a.brain:
        import anthropic
        client = anthropic.Anthropic()
    rep = run_nightly(RUNTIME / "journal", RUNTIME, Registry(SCHEMAS), int(time.time() * 1000), brain_client=client)
    print(json.dumps({k: rep[k] for k in ("labeled", "executions", "calibration")}, indent=2, default=str))


def cmd_promote(a):
    from .review.nightly import Registry
    Registry(SCHEMAS).promote(a.symbol, a.version, Path(a.approval))
    print("promoted; the loop picks it up at the next 00:00 UTC cutover or restart")


def cmd_kill(a):
    from .risk import KillSwitch
    KillSwitch(RUNTIME).trip(a.reason)
    print("KILLED:", a.reason)


def cmd_reset(a):
    from .risk import KillSwitch
    KillSwitch(RUNTIME).operator_reset(a.confirm)
    print("kill switch reset")


def cmd_status(_a):
    from .risk import KillSwitch
    from .review.nightly import Registry
    k = KillSwitch(RUNTIME)
    print(json.dumps({"killed": k.tripped(), "reason": k.reason(), "schemas": Registry(SCHEMAS).read(),
                      "limits_fingerprint": fingerprint(RiskLimits(), GateConfig())[:12],
                      "limits_approved": APPROVED.exists() and APPROVED.read_text().split()[0] == fingerprint(RiskLimits(), GateConfig())},
                     indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="jevagent")
    sp = p.add_subparsers(required=True)
    sp.add_parser("compile-schemas").set_defaults(f=cmd_compile)
    sp.add_parser("approve-limits").set_defaults(f=cmd_approve_limits)
    q = sp.add_parser("paper")
    q.add_argument("--minutes", type=float, default=30)
    q.add_argument("--jev", choices=["scripted", "typesafe"], default="scripted")
    q.add_argument("--symbols", default="HYPE-USDT-SWAP")
    q.add_argument("--equity", type=float, default=10_000.0)
    q.add_argument("--poll-s", type=float, default=5.0)
    q.set_defaults(f=cmd_paper)
    r = sp.add_parser("review"); r.add_argument("--brain", action="store_true"); r.set_defaults(f=cmd_review)
    pr = sp.add_parser("promote"); pr.add_argument("symbol"); pr.add_argument("version"); pr.add_argument("approval"); pr.set_defaults(f=cmd_promote)
    k = sp.add_parser("kill"); k.add_argument("reason"); k.set_defaults(f=cmd_kill)
    rs = sp.add_parser("reset-kill"); rs.add_argument("confirm"); rs.set_defaults(f=cmd_reset)
    sp.add_parser("status").set_defaults(f=cmd_status)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
