# Phase 5 — Review (approval gate G5 prerequisite)

## Findings during the build (all fixed)
| # | Finding | Fix |
|---|---|---|
| R1 | End-to-end paper test passed with **zero fills**: the scripted stand-in is rarely confident, so the trade path was never exercised. | Added a confident test double and `test_trade_path_entries_stops_exits` asserting entries, exits and per-order caps. |
| R2 | Crisis handling depended only on Jev's `regime` answer, i.e. a hard behaviour was delegated to the model. | Added deterministic `risk.market_stress` (24h move, 1h vol, spread); the symbol becomes reduce-only in the risk layer itself. |
| R3 | Journal hash chain broke across the UTC-midnight file boundary. | Chain continues across files; `journal.read_all` verifies it; tamper test added. |
| R4 | Drawdown test assumed a −10% day would not hit the daily-loss limit. It does (−3% limit); behaviour was right, test was wrong. | Test rewritten across two UTC days. |
| R5 | A live smoke test via the CLI needed the operator's limit approval; writing it myself was refused. | Correct: approval is the operator's gate. Smoke test runs through a paper-only harness that never touches `gates/`. |

| R6 | **Live smoke test:** polled trades were re-ingested every 5 s (n5m = 1800, trades_z = 134.7). | `StateEngine.on_trade` is idempotent (dedup by trade id / full tuple); test added. |
| R7 | **Live smoke test:** OKX swap sizes are contracts, not coins (ctVal: HYPE 0.1, SKY 100, PUMP 1000). Depth and paper fills would have been wrong by up to 1000x. | `feeds/okx.contract_value()` converts at the feed boundary; test added. Live order routing must convert back. |
| R8 | **Live smoke test:** cold engine (no 1h history) had null vol, so the stop fell to its 30 bps floor (over-sizing risk); trades_z inflated with <1h baseline; top-5-level depth meaningless with 0.16 bps ticks. | `warm` gate check; trades_z only after 60 min; depth measured within +-10 bps (null if the book is too shallow). |

| R9 | **Live smoke test:** depth within ±10 bps is $1.1k on SKY and ~$5k on JUP/AERO; a 10%-of-equity order could consume the whole book. | Hard `max_depth_participation` (25% of ±10 bps depth) in `RiskEngine.check`; unknown depth means no new risk; test added. |

## Open risks (not fixed)
- Live Jev and live Opus calls are unit-tested against fakes only; first real calls happen at G4.
- No funding-cost accounting in `PaperBroker`.
- No correlation-aware exposure cap (gross cap only).
- `ScriptedJev` is a wiring stand-in with no edge; never use it beyond paper.
- REST polling, not websockets: book snapshots every ~5 s; fine for 1m–5m candles, not for HFT.
