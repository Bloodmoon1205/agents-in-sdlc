"""Research finalists (2026-11-27 snapshot). Numbers come from
data/snapshots/2026-11-27/SCREEN.json; see docs/research/ for sources, dates,
bear cases and which inputs are estimates.

`invalidation` rules are machine-checked by policy.py against the fundamentals
block: when one fires, new entries in the thesis direction are blocked and the
symbol is escalated to Opus 5.5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Rule:
    field: str
    op: str   # "<" | ">"
    value: float
    blocks: str  # "long" | "short" | "both"

    def fires(self, fund: dict) -> bool:
        v = fund.get(self.field)
        if v is None:
            return False
        return v < self.value if self.op == "<" else v > self.value


@dataclass(frozen=True)
class Candidate:
    base: str
    inst_id: str
    bias: str                 # research bias: long | short | two_way | watch
    thesis: str
    bear_case: str
    long_desc: str
    short_desc: str
    fundamentals: dict        # as of SNAPSHOT_AS_OF (annualised 30d figures are estimates)
    next_unlock: date | None = None
    invalidation: tuple[Rule, ...] = field(default_factory=tuple)


SNAPSHOT_AS_OF = "2026-11-27T22:10:52Z"  # CoinGecko last_updated in cg_markets_1.json

CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "HYPE", "HYPE-USDT-SWAP", "long",
        "Fees grew 31% month over month while the market fell 26%; ~76% of fees fund HYPE buybacks; 15.5x price/holders-revenue.",
        "Only 25% of supply circulates (FDV/holders-revenue 62x); monthly team unlocks on the 6th; perp volume is cyclical and Lighter/Aster compete on fees.",
        "Rises: bid support and buying flow while fee momentum stays positive; dips into depth get absorbed.",
        "Falls: market-wide deleveraging or selling into unlock dates overwhelms buyback support.",
        {"p_hr": 15.5, "fdv_hr": 61.9, "fee_mom": 0.311, "hr_mom": 0.256, "float": 0.25},
        date(2026, 12, 6),
        (Rule("fee_mom", "<", -0.30, "long"), Rule("hr_mom", "<", -0.30, "long")),
    ),
    Candidate(
        "LIT", "LIT-USDT-SWAP", "two_way",
        "Lighter fees +29% and holders revenue +74% month over month at 11x price/holders-revenue.",
        "25% float; team and investor tokens (50% of supply) reportedly hit a one-year cliff around 2026-12-30 (sources conflict); FDV/holders-revenue 43x.",
        "Rises: overhang already priced, fees keep compounding, buyers absorb supply.",
        "Falls: supply anticipation into the insider cliff, buyers step back, sells hit the bid.",
        {"p_hr": 11.1, "fdv_hr": 43.4, "fee_mom": 0.294, "hr_mom": 0.744, "float": 0.255},
        date(2026, 12, 30),
        (Rule("fee_mom", "<", -0.30, "long"),),
    ),
    Candidate(
        "SKY", "SKY-USDT-SWAP", "long",
        "Sky earns a spread on USDS reserves, so higher policy rates (a hike is priced for 2026-12-09) support revenue; 9.6x price/holders-revenue with holders revenue +86% month over month and 99% float.",
        "Revenue depends on USDS supply, and stablecoin supply fell 5.8% in 30 days; buybacks were cut sharply in March 2026; governance can redirect revenue.",
        "Rises: rates-up regime favours yield-bearing protocol revenue; buyback flow on the bid.",
        "Falls: stablecoin outflows shrink USDS and revenue; broad risk-off selling.",
        {"p_hr": 9.6, "fdv_hr": 9.7, "fee_mom": -0.046, "hr_mom": 0.856, "float": 0.993},
        None,
        (Rule("hr_mom", "<", -0.40, "long"),),
    ),
    Candidate(
        "JUP", "JUP-USDT-SWAP", "two_way",
        "Holders revenue +162% month over month and fees +16% at 15.9x price/holders-revenue; DAO voted for net-zero emissions in February 2026.",
        "48% float with a large custody balance and no published schedule; Solana activity is falling (SOL fees -46% month over month); only 18% of fees reach holders.",
        "Rises: buybacks plus net-zero emissions tighten effective supply while volume holds.",
        "Falls: Solana activity keeps shrinking, or the DAO releases custody tokens.",
        {"p_hr": 15.9, "fdv_hr": 32.9, "fee_mom": 0.161, "hr_mom": 1.622, "float": 0.484},
        None,
        (Rule("fee_mom", "<", -0.30, "long"),),
    ),
    Candidate(
        "UNI", "UNI-USDT-SWAP", "two_way",
        "Fee switch live since December 2025 (TokenJar buy-and-burn), reportedly extended to v4 in July 2026; holders revenue +10% while fees fell 24%.",
        "Only ~12% of fees reach holders (29.5x price/holders-revenue); DEX volume is cyclical; LPs can migrate to fee-free venues.",
        "Rises: burn flow compounds as volume recovers; low leverage in the token.",
        "Falls: volume keeps falling and burns shrink with it.",
        {"p_hr": 29.5, "fdv_hr": 43.0, "fee_mom": -0.239, "hr_mom": 0.099, "float": 0.686},
        None,
        (Rule("hr_mom", "<", -0.30, "long"),),
    ),
    Candidate(
        "AERO", "AERO-USDT-SWAP", "watch",
        "Optically 3x price/holders-revenue after the Aerodrome+Velodrome merger into Aero (October 2026).",
        "Holders revenue includes bribes paid to direct emissions, so part of it is incentive-driven, not organic; 36% float; price -44% in 30 days; thin liquidity ($14M per day).",
        "Rises: bribe and fee income holds while selling exhausts.",
        "Falls: emissions dilution and falling bribes in risk-off; thin book amplifies moves.",
        {"p_hr": 3.0, "fdv_hr": 8.3, "fee_mom": -0.066, "hr_mom": 0.079, "float": 0.365},
        None,
        (Rule("hr_mom", "<", -0.30, "long"),),
    ),
    Candidate(
        "PUMP", "PUMP-USDT-SWAP", "short",
        "Fees -41% month over month: memecoin launch activity is collapsing in risk-off; 43% float.",
        "Short squeezes: at 4.3x price/holders-revenue the buyback (50% of revenue since April 2026) is a real bid, and a memecoin rebound would reverse fees fast.",
        "Rises: memecoin activity rebounds and buybacks squeeze shorts.",
        "Falls: fee decline continues, unlock supply meets a shrinking buyback.",
        {"p_hr": 4.3, "fdv_hr": 10.0, "fee_mom": -0.406, "hr_mom": -0.408, "float": 0.429},
        None,
        (Rule("fee_mom", ">", 0.20, "short"),),
    ),
)

BY_INST = {c.inst_id: c for c in CANDIDATES}
