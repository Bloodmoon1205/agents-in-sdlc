"""Escalation to the brain (Claude Opus 5.5) for a deep re-read.

Triggered by policy when Jev's direction confidence < 0.60, the regime is
`crisis`, or a thesis invalidation rule fires. Calls are asynchronous: while a
review is pending the symbol is reduce-only, so the 24/7 loop never blocks on
slow reasoning. The verdict is a closed enum and can only make the system MORE
conservative; Opus never sets sizes, thresholds or limits.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    RESUME = "resume"        # lift the restriction; the normal Jev gate still applies
    STAY_FLAT = "stay_flat"  # no new entries for the cooldown
    REDUCE = "reduce"        # cut position by half, then stay flat for the cooldown
    FLATTEN = "flatten"      # close position, then stay flat for the cooldown


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
        "thesis_still_valid": {"type": "boolean"},
        "regime_assessment": {"type": "string", "enum": ["trending", "mean_reverting", "high_vol", "crisis"]},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "thesis_still_valid", "regime_assessment", "rationale"],
    "additionalProperties": False,
}

SYSTEM = """You are the slow, careful reviewer inside an automated crypto trading system.
A fast model (Jev) flagged uncertainty or stress on one symbol. Re-read the evidence and choose ONE verdict:
- resume: evidence is consistent with the written thesis and normal conditions; let the normal gate decide.
- stay_flat: unclear or contradictory evidence; take no new risk for a while.
- reduce: risk is rising against the position; halve it.
- flatten: the thesis is invalidated or conditions are disorderly; close the position.
Check every invalidation condition explicitly. Prefer the more conservative verdict when evidence is mixed.
You cannot change position sizes, limits or thresholds; those are enforced in code regardless of your answer.
Keep the rationale under 120 words and cite the specific fields you relied on."""


@dataclass
class OpusEscalator:
    model: str = "claude-opus-5-5"
    effort: str = "high"
    timeout_s: float = 180.0
    _client: object = None

    def _c(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(timeout=self.timeout_s, max_retries=2)
        return self._client

    def review(self, context: dict) -> dict:
        import anthropic
        try:
            resp = self._c().beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # server-side refusal fallback
                system=SYSTEM,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                messages=[{"role": "user", "content": "Escalation context (JSON):\n" + json.dumps(context, separators=(",", ":"))}],
            )
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            return {"verdict": Verdict.STAY_FLAT.value, "error": type(e).__name__, "rationale": "brain unavailable; fail safe"}
        if resp.stop_reason == "refusal":
            return {"verdict": Verdict.STAY_FLAT.value, "error": "refusal", "rationale": "brain declined; fail safe"}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            out = json.loads(text)
            Verdict(out["verdict"])
        except (ValueError, KeyError, TypeError):
            return {"verdict": Verdict.STAY_FLAT.value, "error": "unparseable", "rationale": "fail safe"}
        out["model"] = resp.model
        return out


@dataclass
class EscalationManager:
    reviewer: object                      # anything with .review(context) -> dict
    cooldown_min: int = 240
    max_per_hour: int = 4                 # cost control: heavy reasoning only when it matters
    _pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=2))
    _pending: dict = field(default_factory=dict)      # sym -> Future
    _restricted_until: dict = field(default_factory=dict)
    _recent: dict = field(default_factory=dict)       # sym -> [ts_ms]
    verdicts: list = field(default_factory=list)

    def request(self, sym: str, context: dict, now_ms: int) -> bool:
        if sym in self._pending:
            return False
        rec = [t for t in self._recent.get(sym, []) if now_ms - t < 3_600_000]
        if len(rec) >= self.max_per_hour:
            self._restricted_until[sym] = max(self._restricted_until.get(sym, 0), now_ms + self.cooldown_min * 60_000)
            return False
        self._recent[sym] = rec + [now_ms]
        self._pending[sym] = self._pool.submit(self.reviewer.review, context)
        return True

    def poll(self, sym: str, now_ms: int) -> dict | None:
        """Returns a finished verdict once, and updates restrictions."""
        fut: Future | None = self._pending.get(sym)
        if fut is None or not fut.done():
            return None
        del self._pending[sym]
        try:
            v = fut.result()
        except Exception as e:  # noqa: BLE001
            v = {"verdict": Verdict.STAY_FLAT.value, "error": repr(e)[:200]}
        if v["verdict"] == Verdict.RESUME.value:
            self._restricted_until.pop(sym, None)
        else:
            self._restricted_until[sym] = now_ms + self.cooldown_min * 60_000
        v["at_ms"] = now_ms
        self.verdicts.append((sym, v))
        return v

    def restricted(self, sym: str, now_ms: int) -> bool:
        return sym in self._pending or self._restricted_until.get(sym, 0) > now_ms

    def wait_idle(self, timeout_s: float = 10.0) -> None:
        end = time.time() + timeout_s
        while any(not f.done() for f in self._pending.values()) and time.time() < end:
            time.sleep(0.01)
