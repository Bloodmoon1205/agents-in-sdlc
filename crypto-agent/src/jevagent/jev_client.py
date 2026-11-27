"""Jev adapter. One request per candle per symbol: all five questions (plus the
shadow schema's questions, if any) are evaluated together in a single call.

TypeSafeJev  - the real model via the official `typesafe-sdk` (reads TYPESAFE_API_KEY).
ScriptedJev  - deterministic stand-in for tests and paper wiring. It is a crude
               heuristic, NOT a strategy, and must never be used live.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Protocol

from .schema import DIRECTION, REGIME, RISK_STATE, JevSchema

SHADOW_PREFIX = "shadow__"


class JevUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ChoiceAns:
    choice: str
    probs: dict
    confidence: float


@dataclass(frozen=True)
class ScoreAns:
    score: float
    probs: dict
    confidence: float


@dataclass(frozen=True)
class JevAnswers:
    regime: ChoiceAns
    direction: ChoiceAns
    toxic_flow: float
    setup_quality: ScoreAns
    risk_state: ChoiceAns
    schema_version: str
    latency_ms: float = 0.0
    input_tokens: int | None = None
    model: str = ""
    shadow: "JevAnswers | None" = None

    def to_dict(self) -> dict:
        d = {
            "regime": vars(self.regime), "direction": vars(self.direction), "toxic_flow": self.toxic_flow,
            "setup_quality": vars(self.setup_quality), "risk_state": vars(self.risk_state),
            "schema_version": self.schema_version, "latency_ms": round(self.latency_ms, 1),
            "input_tokens": self.input_tokens, "model": self.model,
        }
        if self.shadow is not None:
            d["shadow"] = self.shadow.to_dict()
        return json.loads(json.dumps(d, default=str))


class JevClient(Protocol):
    def decide(self, state: str, schema: JevSchema, shadow: JevSchema | None = None) -> JevAnswers: ...


def _parse(resp, prefix: str, version: str) -> JevAnswers:
    def ch(name):
        a = resp.choices[prefix + name]
        return ChoiceAns(a.choice, {str(k): float(v) for k, v in a.probabilities.items()}, float(a.confidence))
    s = resp.scores[prefix + "setup_quality"]
    return JevAnswers(
        regime=ch("regime"), direction=ch("direction"),
        toxic_flow=float(resp.nouls[prefix + "toxic_flow"].noul),
        setup_quality=ScoreAns(float(s.score), {int(k): float(v) for k, v in s.probabilities.items()}, float(s.confidence)),
        risk_state=ch("risk_state"), schema_version=version,
    )


class TypeSafeJev:
    def __init__(self, model: str = "jev-latest", timeout_s: float = 2.0):
        from typesafe_sdk import RetryPolicy, TypeSafeClient
        # Live loop: no SDK retries. A late answer is a wrong answer; the policy holds instead.
        self._client = TypeSafeClient(model=model, timeout=timeout_s, retry=RetryPolicy(max_retries=0))
        self.model = model

    def decide(self, state: str, schema: JevSchema, shadow: JevSchema | None = None) -> JevAnswers:
        qs = schema.compile()
        if shadow is not None:
            qs.update(shadow.compile(prefix=SHADOW_PREFIX))
        t0 = time.perf_counter()
        try:
            resp = self._client.system_one(state=state, questions=qs)
        except Exception as e:  # any SDK/API/transport error -> hold, never guess
            raise JevUnavailable(repr(e)[:300]) from e
        lat = (time.perf_counter() - t0) * 1e3
        main = _parse(resp, "", schema.version)
        sh = _parse(resp, SHADOW_PREFIX, shadow.version) if shadow is not None else None
        usage = getattr(resp, "usage", None)
        return JevAnswers(**{**main.__dict__, "latency_ms": lat, "model": getattr(resp, "model", self.model),
                             "input_tokens": getattr(usage, "input_tokens", None), "shadow": sh})


def _softmax(xs):
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    s = sum(e)
    return [v / s for v in e]


def _conf(ps):
    h = -sum(p * math.log(p) for p in ps if p > 0)
    return 1 - h / math.log(len(ps))


@dataclass
class ScriptedJev:
    """Deterministic heuristic stand-in. Reads the same snapshot text Jev would."""
    temperature: float = 1.0
    calls: list = field(default_factory=list)

    def _one(self, snap: dict, version: str) -> JevAnswers:
        g = lambda sec, k, d=0.0: (snap.get(sec) or {}).get(k) if (snap.get(sec) or {}).get(k) is not None else d
        r1h, r4h, r24 = g("ret_bps", "1h"), g("ret_bps", "4h"), g("ret_bps", "24h")
        rv1, ratio = g("vol", "rv1h_ann", 0.5), g("vol", "ratio", 1.0)
        imb, buy = g("px", "imb5"), g("flow", "buy_share_5m", 0.5)
        dd = g("pos", "dd")
        reg = _softmax([abs(r4h) / 150, 1.0 - abs(r1h) / 100, (ratio - 1) * 2, (-r24 / 800) + (rv1 - 1.0) * 2])
        sig = (r1h / 80 + r4h / 200 + imb * 1.5 + (buy - 0.5) * 3) / self.temperature
        d = _softmax([sig, -sig, 0.8])
        tox = 1 / (1 + math.exp(-(abs(buy - 0.5) * 8 + abs(g("flow", "trades_z")) / 2 - 3)))
        sq = _softmax([1.0, 0.5 + abs(sig) * 0.3, abs(sig) * 0.6, abs(sig) * 0.9 - 0.8])
        rs = _softmax([2.0 - dd * 40, dd * 30, dd * 50 - 2])
        mk = lambda keys, ps: ChoiceAns(keys[max(range(len(ps)), key=ps.__getitem__)], dict(zip(keys, ps)), _conf(ps))
        return JevAnswers(mk(REGIME, reg), mk(DIRECTION, d), tox,
                          ScoreAns(sum(i * p for i, p in enumerate(sq)), dict(enumerate(sq)), _conf(sq)),
                          mk(RISK_STATE, rs), version, latency_ms=0.0, model="scripted")

    def decide(self, state: str, schema: JevSchema, shadow: JevSchema | None = None) -> JevAnswers:
        snap = json.loads(state)
        self.calls.append(state)
        main = self._one(snap, schema.version)
        if shadow is not None:
            return JevAnswers(**{**main.__dict__, "shadow": self._one(snap, shadow.version)})
        return main
