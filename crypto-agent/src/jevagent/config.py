"""Hard limits and gate thresholds.

Everything in this file is CODE-OWNED. No model output (Jev, Opus, the nightly
review) is ever allowed to write these values. The limits are frozen
dataclasses, and their SHA-256 fingerprint must match the operator-approved
fingerprint in gates/approved_limits.sha256 or the agent refuses to start.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RiskLimits:
    max_drawdown: float = 0.15          # from equity high-water mark -> kill switch trips
    max_daily_loss: float = 0.03        # from UTC-day starting equity -> flatten + halt for the day
    max_position_frac: float = 0.20     # |notional| per symbol / equity
    max_gross_frac: float = 0.60        # sum |notional| / equity (=> no leverage above 0.6x)
    max_risk_per_trade: float = 0.01    # equity lost if the stop is hit
    max_order_frac: float = 0.10        # single order notional / equity
    price_band_bps: float = 50.0        # limit price must be within this of mid
    max_snapshot_age_s: float = 90.0    # stale data -> no new risk
    max_orders_per_hour: int = 30       # per symbol
    min_order_notional: float = 5.0     # exchange minimum proxy, quote currency
    max_depth_participation: float = 0.25  # order notional <= 25% of book depth within +-10 bps; unknown depth -> no new risk
    # Code-side stress detector: any one of these forces reduce-only for the symbol,
    # whatever Jev says about the regime (crisis detection is never delegated to a model).
    stress_ret24h_bps: float = -1500.0  # 24h move worse than -15%
    stress_rv1h_ann: float = 1.50       # 1h realized vol above 150% annualized
    stress_spread_bps: float = 25.0     # top-of-book spread wider than 25 bps


@dataclass(frozen=True)
class GateConfig:
    min_setup_quality: float = 2.0          # Jev Score expected value, scale 0..3
    min_direction_confidence: float = 0.80  # strict: must be ABOVE this
    max_toxic_flow_p: float = 0.50          # stricter-only addition: no entries into toxic flow
    kelly_fraction: float = 0.25            # fractional Kelly multiplier
    kelly_cap: float = 0.25                 # hard cap: never above quarter Kelly
    escalate_below_confidence: float = 0.60 # -> Opus 5.5 deep re-read
    reward_risk: float = 2.0                # b in Kelly: take-profit distance / stop distance
    stop_vol_mult: float = 1.5              # stop = mult * expected move over the holding horizon
    horizon_minutes: int = 240              # holding horizon used for stops and labels
    max_hold_minutes: int = 720             # time stop
    cost_hurdle_bps: float = 15.0           # round-trip fee+slippage estimate; labels below this are "neutral"


def fingerprint(*objs) -> str:
    blob = json.dumps([asdict(o) for o in objs], sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def verify_approved(limits: RiskLimits, gate: GateConfig, approved_file: Path) -> None:
    """Refuse to run if the code-owned limits differ from what the operator approved."""
    want = approved_file.read_text().strip().split()[0] if approved_file.exists() else None
    have = fingerprint(limits, gate)
    if want != have:
        raise PermissionError(
            f"risk/gate config fingerprint {have[:12]} is not operator-approved "
            f"(approved: {want[:12] if want else 'none'}). Run `jevagent approve-limits` after review."
        )
