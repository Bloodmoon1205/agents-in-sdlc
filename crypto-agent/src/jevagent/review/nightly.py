"""Nightly review (the brain's slow loop).

1. Read the hash-chained journal.
2. Label every decision CAUSALLY: only decisions whose forward window has fully
   elapsed before `now_ms` get a label. Labels use code-owned rules.
3. Score Jev: multi-class Brier for direction and regime, binary Brier for
   toxic_flow, each against a climatology baseline (Brier skill score), plus
   reliability bins / ECE for direction confidence.
4. Review executions and misses (gate blocked, outcome would have paid).
5. Refit the win-probability calibrator from path labels (TP-before-stop).
6. Ask Opus 5.5 for a rewrite of the schema TEXT only, validate it, and stage
   it as the shadow schema. Shadow answers are collected in the same Jev call,
   so tomorrow's review compares them on identical states.
7. Promotion of a shadow schema to active is gated: automated evidence first,
   then an operator approval file. Risk limits are never touched; the config
   fingerprint is verified before and after.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..calibration import Calibrator
from ..config import GateConfig, RiskLimits, fingerprint
from ..policy import stop_fraction
from ..schema import DIRECTION, REGIME, JevSchema, QuestionSpec

FIVE_MIN = 5 * 60_000


@dataclass
class Labeled:
    sym: str
    t: int
    jev: dict
    intent: dict
    snapshot: dict
    snapshot_text: str
    fwd_bps: float
    dir_label: str
    regime_label: str
    toxic_label: int
    path_win: bool | None   # TP before stop for the side Jev chose (None if neutral)


def _mid_series(recs: list[dict]) -> dict:
    s = defaultdict(list)
    for r in recs:
        if r["kind"] == "decision":
            s[r["symbol"]].append((r["ts_ms"], r["mid"]))
        elif r["kind"] == "mark":
            for sym, m in (r.get("mids") or {}).items():
                s[sym].append((r["ts_ms"], m))
    return {k: sorted(set(v)) for k, v in s.items()}


def label(recs: list[dict], now_ms: int, gate: GateConfig = GateConfig()) -> list[Labeled]:
    H, Hmax = gate.horizon_minutes * 60_000, gate.max_hold_minutes * 60_000
    series = _mid_series(recs)
    out = []
    for r in recs:
        if r["kind"] != "decision" or not r.get("jev"):
            continue
        t, sym, m0 = r["ts_ms"], r["symbol"], r["mid"]
        if t + Hmax > now_ms:
            continue  # window not elapsed: no label (causality)
        path = [(ts, m) for ts, m in series[sym] if t < ts <= t + Hmax]
        at_h = [m for ts, m in path if ts >= t + H]
        if not at_h:
            continue
        fwd = math.log(at_h[0] / m0) * 1e4
        hurdle = gate.cost_hurdle_bps
        dlab = "long" if fwd > hurdle else ("short" if fwd < -hurdle else "neutral")
        vol = r["snapshot"].get("vol") or {}
        rv = max(v for v in (vol.get("rv24h_ann"), vol.get("rv1h_ann"), 0.3) if v is not None)
        exp_h = rv * math.sqrt(gate.horizon_minutes / 525_600) * 1e4
        win_h = [m for ts, m in path if ts <= t + H]
        mdd = min((math.log(m / m0) * 1e4 for m in win_h), default=0.0)
        if mdd < -max(800.0, 3 * exp_h):
            rlab = "crisis"
        elif abs(fwd) > 1.5 * exp_h:
            rlab = "trending"
        elif max((abs(math.log(m / m0) * 1e4) for m in win_h), default=0) > 2.0 * exp_h:
            rlab = "high_vol"
        else:
            rlab = "mean_reverting"
        five = [m for ts, m in path if ts >= t + FIVE_MIN]
        spread = (r["snapshot"].get("px") or {}).get("spread_bps") or 1.0
        tox = int(bool(five) and abs(math.log(five[0] / m0) * 1e4) > max(3 * spread, 0.5 * exp_h * math.sqrt(5 / gate.horizon_minutes) * 3))
        d = r["jev"]["direction"]["choice"]
        win = None
        if d in ("long", "short"):
            s = stop_fraction(r["snapshot"], gate)
            sg = 1 if d == "long" else -1
            win = False
            for _, m in path:
                ret = sg * (m / m0 - 1)
                if ret <= -s:
                    break
                if ret >= gate.reward_risk * s:
                    win = True
                    break
        out.append(Labeled(sym, t, r["jev"], r["intent"], r["snapshot"], r.get("snapshot_text", ""), fwd, dlab, rlab, tox, win))
    return out


def brier_multi(probs: list[dict], labels: list[str], classes) -> float:
    if not labels:
        return float("nan")
    return sum(sum((p.get(c, 0.0) - (1.0 if y == c else 0.0)) ** 2 for c in classes) for p, y in zip(probs, labels)) / len(labels)


def brier_skill(probs, labels, classes) -> dict:
    b = brier_multi(probs, labels, classes)
    freq = Counter(labels)
    clim = {c: freq[c] / len(labels) for c in classes} if labels else {}
    b0 = brier_multi([clim] * len(labels), labels, classes)
    return {"brier": b, "baseline": b0, "skill": (1 - b / b0) if b0 else float("nan"), "n": len(labels)}


def reliability(conf: list[float], correct: list[bool], bins: int = 10) -> dict:
    tbl, ece, n = [], 0.0, len(conf)
    for k in range(bins):
        lo, hi = k / bins, (k + 1) / bins
        idx = [i for i, c in enumerate(conf) if lo <= c < hi or (k == bins - 1 and c == 1.0)]
        if idx:
            acc = sum(correct[i] for i in idx) / len(idx)
            avg = sum(conf[i] for i in idx) / len(idx)
            ece += len(idx) / n * abs(acc - avg)
            tbl.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx), "conf": round(avg, 3), "acc": round(acc, 3)})
    return {"ece": ece if n else float("nan"), "table": tbl}


def score_answers(rows: list[Labeled], key: str | None = None) -> dict:
    get = (lambda r: r.jev.get("shadow")) if key == "shadow" else (lambda r: r.jev)
    rows = [r for r in rows if get(r)]
    if not rows:
        return {"n": 0}
    A = [get(r) for r in rows]
    tox_p = [a["toxic_flow"] for a in A]
    tox_y = [r.toxic_label for r in rows]
    tb = sum((p - y) ** 2 for p, y in zip(tox_p, tox_y)) / len(rows)
    base = sum(tox_y) / len(rows)
    tb0 = sum((base - y) ** 2 for y in tox_y) / len(rows)
    correct = [a["direction"]["choice"] == r.dir_label for a, r in zip(A, rows)]
    by_setup = defaultdict(list)
    for a, r in zip(A, rows):
        d = a["direction"]["choice"]
        if d in ("long", "short"):
            by_setup[min(3, int(a["setup_quality"]["score"]))].append((1 if d == "long" else -1) * r.fwd_bps - 15.0)
    return {
        "n": len(rows),
        "direction": brier_skill([a["direction"]["probs"] for a in A], [r.dir_label for r in rows], DIRECTION),
        "regime": brier_skill([a["regime"]["probs"] for a in A], [r.regime_label for r in rows], REGIME),
        "toxic_flow": {"brier": tb, "baseline": tb0, "skill": (1 - tb / tb0) if tb0 else float("nan")},
        "direction_reliability": reliability([a["direction"]["confidence"] for a in A], correct),
        "setup_quality_after_cost_bps": {k: {"n": len(v), "mean": sum(v) / len(v)} for k, v in sorted(by_setup.items())},
    }


def execution_review(recs: list[dict]) -> dict:
    fills = [r["fill"] for r in recs if r["kind"] == "fill"]
    realized = sum(f["realized"] for f in fills)
    fees = sum(f["fee"] for f in fills)
    closes = [f for f in fills if not f["tag"].startswith("enter")]
    rejected = Counter(x for r in recs if r["kind"] == "order" and not r["risk"]["approved"] for x in r["risk"]["reasons"])
    return {"fills": len(fills), "realized": realized, "fees": fees, "net": realized - fees,
            "closes": len(closes), "winning_closes": sum(f["realized"] > 0 for f in closes),
            "risk_rejections": dict(rejected),
            "escalations": sum(r["kind"] == "escalation" for r in recs),
            "verdicts": Counter(r["verdict"]["verdict"] for r in recs if r["kind"] == "verdict")}


def miss_review(rows: list[Labeled]) -> dict:
    """Gate blocked an entry, but Jev's direction was right after costs."""
    misses = Counter()
    examples = []
    for r in rows:
        g = (r.intent.get("trace") or {}).get("gate")
        d = r.jev["direction"]["choice"]
        if r.intent["action"] == "hold" and g and d in ("long", "short") and d == r.dir_label:
            for k, ok in g.items():
                if not ok:
                    misses[k] += 1
            if len(examples) < 5:
                examples.append({"t": r.t, "sym": r.sym, "failed": [k for k, ok in g.items() if not ok], "fwd_bps": round(r.fwd_bps, 1)})
    return {"by_failed_check": dict(misses), "examples": examples}


def refit_calibrators(rows: list[Labeled], root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    out = {}
    for sym in sorted({r.sym for r in rows}):
        c = Calibrator.load(root / f"{sym}.json")
        k = 0
        for r in rows:
            if r.sym == sym and r.path_win is not None:
                c.update(r.jev["direction"]["probs"][r.jev["direction"]["choice"]], r.path_win)
                k += 1
        c.save(root / f"{sym}.json")
        out[sym] = {"added": k, "total": c.n}
    return out


# ---- schema revision (brain) ------------------------------------------------

def revision_output_schema(s: JevSchema) -> dict:
    props = {}
    for name, q in s.questions.items():
        p = {"instructions": {"type": "string"}}
        req = ["instructions"]
        if q.kind == "choice":
            p["criteria"] = {"type": "object", "properties": {k: {"type": "string"} for k in q.criteria},
                             "required": list(q.criteria), "additionalProperties": False}
            req.append("criteria")
        elif q.kind == "score":
            p["criteria"] = {"type": "array", "items": {"type": "string"}}
            req.append("criteria")
        props[name] = {"type": "object", "properties": p, "required": req, "additionalProperties": False}
    return {"type": "object", "properties": {"changes_summary": {"type": "string"}, "questions": {
        "type": "object", "properties": props, "required": list(props), "additionalProperties": False}},
        "required": ["changes_summary", "questions"], "additionalProperties": False}


REVISE_SYSTEM = """You maintain the question wording for Jev, a fast typed-decision model used inside a trading system.
You receive yesterday's calibration report for one symbol and the current questions. Rewrite ONLY the wording of
instructions and option/level descriptions so Jev's answers become better calibrated and more discriminative.
Option keys and the number of score levels are fixed. Do not add numeric trading thresholds, sizes or limits:
those live in code and wording cannot change them. Change only what the evidence supports; keep everything else."""


def propose_revision(report: dict, current: JevSchema, next_version: str, client) -> tuple[JevSchema, str]:
    resp = client.beta.messages.create(
        model="claude-opus-5-5", max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=REVISE_SYSTEM,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": revision_output_schema(current)}},
        messages=[{"role": "user", "content": json.dumps({"report": report, "current_schema": json.loads(current.to_json())})}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("revision refused")
    d = json.loads(next(b.text for b in resp.content if b.type == "text"))
    qs = {}
    for name, q in current.questions.items():
        n = d["questions"][name]
        crit = q.criteria
        if q.kind == "choice":
            crit = {k: n["criteria"][k] for k in q.criteria}   # key order fixed by current schema
        elif q.kind == "score":
            crit = list(n["criteria"])
        qs[name] = QuestionSpec(q.kind, n["instructions"], crit)
    return JevSchema(next_version, current.symbol, qs).validate(), d["changes_summary"]


# ---- registry & promotion gate -----------------------------------------------

@dataclass
class Registry:
    root: Path  # schemas/jev

    @property
    def _file(self) -> Path:
        return self.root / "ACTIVE.json"

    def read(self) -> dict:
        return json.loads(self._file.read_text()) if self._file.exists() else {}

    def write(self, d: dict) -> None:
        self._file.write_text(json.dumps(d, indent=2, sort_keys=True))

    def path(self, sym: str, ver: str) -> Path:
        return self.root / sym / f"{ver}.json"

    def load(self, sym: str, which: str = "active") -> JevSchema | None:
        ver = self.read().get(sym, {}).get(which)
        return JevSchema.load(self.path(sym, ver)) if ver else None

    def stage_shadow(self, s: JevSchema, summary: str) -> None:
        p = self.path(s.symbol, s.version)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(s.to_json())
        (p.with_suffix(".changes.md")).write_text(summary)
        d = self.read()
        d.setdefault(s.symbol, {})["shadow"] = s.version
        self.write(d)

    def promote(self, sym: str, ver: str, approval: Path) -> None:
        a = json.loads(approval.read_text())
        if a.get("symbol") != sym or a.get("version") != ver or a.get("approved_by") in (None, ""):
            raise PermissionError("promotion requires an operator approval file for this symbol and version")
        d = self.read()
        d[sym] = {"active": ver, "shadow": None, "previous": d.get(sym, {}).get("active")}
        self.write(d)


def shadow_promotion_evidence(active: dict, shadow: dict, min_n: int = 300, margin: float = 0.01) -> dict:
    ok_n = shadow.get("n", 0) >= min_n
    a_sk, s_sk = active.get("direction", {}).get("skill", float("nan")), shadow.get("direction", {}).get("skill", float("nan"))
    a_ece = active.get("direction_reliability", {}).get("ece", float("nan"))
    s_ece = shadow.get("direction_reliability", {}).get("ece", float("nan"))
    better = s_sk > a_sk + margin and s_ece <= a_ece + margin
    return {"eligible": bool(ok_n and better), "n": shadow.get("n", 0), "min_n": min_n,
            "direction_skill": {"active": a_sk, "shadow": s_sk}, "ece": {"active": a_ece, "shadow": s_ece}}


def run_nightly(journal_root: Path, runtime: Path, registry: Registry, now_ms: int,
                brain_client=None, limits: RiskLimits = RiskLimits(), gate: GateConfig = GateConfig()) -> dict:
    from ..journal import read_all
    fp_before = fingerprint(limits, gate)
    recs = read_all(journal_root)
    rows = label(recs, now_ms, gate)
    report = {"now_ms": now_ms, "labeled": len(rows), "executions": execution_review(recs),
              "misses": miss_review(rows), "symbols": {}}
    for sym in sorted({r.sym for r in rows}):
        sr = [r for r in rows if r.sym == sym]
        rep = {"active": score_answers(sr), "shadow": score_answers(sr, "shadow")}
        if rep["shadow"].get("n"):
            rep["promotion"] = shadow_promotion_evidence(rep["active"], rep["shadow"])
            if rep["promotion"]["eligible"]:
                pend = runtime / "gates" / "pending"
                pend.mkdir(parents=True, exist_ok=True)
                ver = registry.read().get(sym, {}).get("shadow")
                (pend / f"promote-{sym}-{ver}.json").write_text(json.dumps({"symbol": sym, "version": ver,
                                                                             "evidence": rep["promotion"], "approved_by": ""}, indent=2))
        report["symbols"][sym] = rep
    report["calibration"] = refit_calibrators(rows, runtime / "calibration")
    if brain_client is not None:
        for sym, rep in report["symbols"].items():
            cur = registry.load(sym)
            if cur is None or registry.read().get(sym, {}).get("shadow"):
                continue  # one experiment at a time per symbol
            weak = rep["active"].get("direction", {}).get("skill", 0) < 0.02 or \
                rep["active"].get("direction_reliability", {}).get("ece", 0) > 0.08
            if weak and rep["active"].get("n", 0) >= 100:
                nxt = f"v{int(cur.version.lstrip('v')) + 1}"
                new, summary = propose_revision(rep, cur, nxt, brain_client)
                registry.stage_shadow(new, summary)
                rep["staged_shadow"] = nxt
    assert fingerprint(limits, gate) == fp_before, "nightly review must never alter risk limits"
    out = runtime / "reviews"
    out.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    day = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    (out / f"{day}.json").write_text(json.dumps(report, indent=2, default=str))
    return report
