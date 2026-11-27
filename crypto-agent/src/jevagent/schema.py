"""Typed Jev decision schema (one per finalist) and its compiler.

Only TEXT lives in a schema: question instructions and option descriptions.
Option keys, the number of score levels, and every threshold that acts on the
answers are fixed in code (REQUIRED below + config.GateConfig). That is what
lets the nightly loop rewrite schemas without being able to loosen any limit.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGIME = ("trending", "mean_reverting", "high_vol", "crisis")
DIRECTION = ("long", "short", "neutral")
RISK_STATE = ("safe", "near_limit", "reduce")
SETUP_LEVELS = 4  # 0..3
MAX_TEXT = 900

REQUIRED = {
    "regime": ("choice", REGIME),
    "direction": ("choice", DIRECTION),
    "toxic_flow": ("noul", None),
    "setup_quality": ("score", SETUP_LEVELS),
    "risk_state": ("choice", RISK_STATE),
}


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class QuestionSpec:
    kind: str
    instructions: str
    criteria: dict | list | None = None


@dataclass(frozen=True)
class JevSchema:
    version: str
    symbol: str
    questions: dict = field(default_factory=dict)  # name -> QuestionSpec

    # -- validation ------------------------------------------------------
    def validate(self) -> "JevSchema":
        if set(self.questions) != set(REQUIRED):
            raise SchemaError(f"{self.symbol}: questions must be exactly {sorted(REQUIRED)}")
        for name, (kind, shape) in REQUIRED.items():
            q = self.questions[name]
            if q.kind != kind:
                raise SchemaError(f"{name}: kind {q.kind} != {kind}")
            if not q.instructions or len(q.instructions) > MAX_TEXT:
                raise SchemaError(f"{name}: instructions missing or > {MAX_TEXT} chars")
            if kind == "choice":
                if not isinstance(q.criteria, dict) or tuple(q.criteria) != shape:
                    raise SchemaError(f"{name}: options must be exactly {shape} in that order")
                if any(not isinstance(v, str) or not v or len(v) > MAX_TEXT for v in q.criteria.values()):
                    raise SchemaError(f"{name}: every option needs a description <= {MAX_TEXT} chars")
            if kind == "score":
                if not isinstance(q.criteria, list) or len(q.criteria) != shape:
                    raise SchemaError(f"{name}: score needs exactly {shape} levels")
                if any(not isinstance(v, str) or not v or len(v) > MAX_TEXT for v in q.criteria):
                    raise SchemaError(f"{name}: every level needs a description <= {MAX_TEXT} chars")
            if kind == "noul" and q.criteria is not None:
                raise SchemaError(f"{name}: noul takes no criteria here")
        return self

    # -- (de)serialization ----------------------------------------------
    def to_json(self) -> str:
        return json.dumps({"version": self.version, "symbol": self.symbol,
                           "questions": {k: asdict(v) for k, v in self.questions.items()}}, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "JevSchema":
        d = json.loads(text)
        qs = {k: QuestionSpec(**v) for k, v in d["questions"].items()}
        return cls(version=d["version"], symbol=d["symbol"], questions=qs).validate()

    @classmethod
    def load(cls, path: Path) -> "JevSchema":
        return cls.from_json(Path(path).read_text())

    # -- compile to the TypeSafe SDK -------------------------------------
    def compile(self, prefix: str = "") -> dict:
        from typesafe_sdk import Choice, Noul, Score  # lazy: only needed for real Jev calls
        out = {}
        for name, q in self.questions.items():
            if q.kind == "choice":
                out[prefix + name] = Choice(instructions=q.instructions, criteria=dict(q.criteria))
            elif q.kind == "score":
                out[prefix + name] = Score(instructions=q.instructions, criteria=list(q.criteria))
            else:
                out[prefix + name] = Noul(instructions=q.instructions)
        return out


def build_schema(c: "Candidate", version: str = "v1") -> JevSchema:  # noqa: F821 - see candidates.py
    sym = c.base
    ctx = (f"State is a compact numeric snapshot of the {c.inst_id} perpetual: order book (px), "
           f"returns in bps (ret_bps), realized vol (vol), trade flow (flow), our position (pos), "
           f"fundamentals (fund) and market context (mkt). Thesis: {c.thesis} Bear case: {c.bear_case}")
    qs = {
        "regime": QuestionSpec("choice", f"Which market regime best describes {sym} right now? {ctx}", {
            "trending": "Persistent directional move: returns over 1h and 4h share a sign and vol is normal for this asset.",
            "mean_reverting": "Range-bound: short-term moves fade, returns flip sign, vol at or below its 24h level.",
            "high_vol": "Volatility well above its 24h level (vol.ratio high) without disorderly market structure.",
            "crisis": "Disorderly: large 24h drawdown, very high vol, wide spreads or thin depth, one-sided flow, market-wide stress.",
        }),
        "direction": QuestionSpec("choice",
            f"Which direction for {sym} has the better expected result over the next ~4 hours after ~15 bps of costs? {ctx}", {
            "long": c.long_desc,
            "short": c.short_desc,
            "neutral": "No edge after costs, signals conflict, or the move has already happened.",
        }),
        "toxic_flow": QuestionSpec("noul",
            f"Recent {sym} order flow looks informed or one-sided enough that a position opened now would likely be "
            f"adversely selected (sweeps through levels, sudden spread widening, trade bursts against the resting book)."),
        "setup_quality": QuestionSpec("score", f"How good is the trade setup for {sym} right now? {ctx}", [
            "No setup: conflicting signals, thin or unstable book, or the move already happened.",
            "Weak: one supporting signal, others neutral or mixed.",
            "Good: price action, order flow and fundamentals agree on direction; liquidity is adequate.",
            "Excellent: strong agreement across flow, regime and fundamentals, tight spread, no conflicting risk.",
        ]),
        "risk_state": QuestionSpec("choice", f"Given our position, drawdown and daily P&L in pos, what is the risk state for {sym}?", {
            "safe": "Comfortable room: small inventory, shallow drawdown, daily P&L not near a loss limit.",
            "near_limit": "Drawdown, daily loss or inventory is approaching the levels where we would have to cut.",
            "reduce": "Exposure should be cut now: losses accelerating or inventory too large for current conditions.",
        }),
    }
    return JevSchema(version=version, symbol=c.inst_id, questions=qs).validate()
