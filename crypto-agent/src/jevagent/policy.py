"""Policy: turns Jev's typed answers into an intent. Code owns every threshold.

Entry fires only when ALL hold (GateConfig):
  setup_quality (expected score) >= 2, direction in {long, short},
  direction confidence > 0.80, risk_state == safe, toxic_flow < 0.5,
  no invalidation rule blocks that side, regime != crisis.
Size: fractional Kelly on the CALIBRATED probability, capped at quarter Kelly,
expressed as the fraction of equity lost if the stop is hit. The risk layer
then clips it further; it can never grow.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .calibration import Calibrator
from .candidates import Candidate
from .config import GateConfig
from .jev_client import JevAnswers

MIN_STOP_FRAC = 0.003  # 30 bps floor so a quiet tape can't produce huge size


@dataclass
class Intent:
    action: str                 # enter_long | enter_short | exit | reduce | hold
    symbol: str
    risk_frac: float = 0.0      # equity fraction at risk to the stop
    stop_frac: float = 0.0      # stop distance as fraction of price
    reduce_frac: float = 0.0    # for "reduce": fraction of position to cut
    escalate: bool = False
    escalate_reason: str = ""
    trace: dict = field(default_factory=dict)


def kelly(p: float, b: float) -> float:
    """Kelly fraction for a bet that wins b per 1 risked with probability p."""
    return p - (1 - p) / b


def stop_fraction(snap: dict, gate: GateConfig) -> float:
    vol = snap.get("vol") or {}
    rv = max(v for v in (vol.get("rv1h_ann"), vol.get("rv24h_ann"), 0.0) if v is not None)
    move = rv * math.sqrt(gate.horizon_minutes / 525_600)
    return max(MIN_STOP_FRAC, gate.stop_vol_mult * move)


def decide(ans: JevAnswers | None, snap: dict, position_qty: float, cand: Candidate,
           gate: GateConfig, calib: Calibrator, stress: list | None = None) -> Intent:
    sym = cand.inst_id
    if stress:  # code-detected crisis outranks the model: reduce, never enter, ask the brain
        act = "reduce" if position_qty else "hold"
        return Intent(act, sym, reduce_frac=0.5, escalate=True, escalate_reason="code_stress:" + ",".join(stress),
                      trace={"stress": stress})
    if ans is None:
        return Intent("hold", sym, trace={"why": "jev_unavailable"})
    fund = snap.get("fund") or {}
    blocked = {r.blocks for r in cand.invalidation if r.fires(fund)}
    tr = {"regime": ans.regime.choice, "dir": ans.direction.choice, "dir_conf": round(ans.direction.confidence, 3),
          "setup": round(ans.setup_quality.score, 3), "risk_state": ans.risk_state.choice,
          "toxic": round(ans.toxic_flow, 3), "invalidated": sorted(blocked)}

    esc, why = False, []
    if ans.direction.confidence < gate.escalate_below_confidence:
        esc, why = True, why + ["low_confidence"]
    if ans.regime.choice == "crisis":
        esc, why = True, why + ["crisis"]
    if blocked:
        esc, why = True, why + ["invalidation"]
    base = dict(escalate=esc, escalate_reason=",".join(why), trace=tr)

    # Existing position management (exits are always allowed by the risk layer).
    if position_qty:
        side = "long" if position_qty > 0 else "short"
        if ans.regime.choice == "crisis":
            return Intent("reduce", sym, reduce_frac=0.5, **base)
        if ans.risk_state.choice == "reduce":
            return Intent("reduce", sym, reduce_frac=0.5, **base)
        flip = ans.direction.choice in ("long", "short") and ans.direction.choice != side \
            and ans.direction.confidence > gate.min_direction_confidence
        if flip or side in blocked or "both" in blocked:
            return Intent("exit", sym, **base)
        return Intent("hold", sym, **base)

    # Entry gate.
    d = ans.direction.choice
    vol, rets = snap.get("vol") or {}, snap.get("ret_bps") or {}
    checks = {
        "warm": vol.get("rv1h_ann") is not None and rets.get("1h") is not None,
        "setup>=min": ans.setup_quality.score >= gate.min_setup_quality,
        "directional": d in ("long", "short"),
        "conf>min": ans.direction.confidence > gate.min_direction_confidence,
        "risk_safe": ans.risk_state.choice == "safe",
        "not_toxic": ans.toxic_flow < gate.max_toxic_flow_p,
        "not_crisis": ans.regime.choice != "crisis",
        "not_invalidated": d not in blocked and "both" not in blocked,
    }
    tr["gate"] = checks
    if not all(checks.values()):
        return Intent("hold", sym, **base)

    p_raw = ans.direction.probs.get(d, 0.0)
    p = calib.map(p_raw)
    f = kelly(p, gate.reward_risk)
    frac = min(gate.kelly_fraction, gate.kelly_cap)
    tr.update({"p_raw": round(p_raw, 4), "p_cal": round(p, 4), "kelly": round(f, 4)})
    if f <= 0:
        return Intent("hold", sym, **base)
    return Intent("enter_long" if d == "long" else "enter_short", sym,
                  risk_frac=frac * f, stop_frac=stop_fraction(snap, gate), **base)
