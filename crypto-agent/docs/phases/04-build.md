# Phase 4 — Build (test-first)

`pytest -q`: 64 tests pass. Modules and their tests are listed in 03-plan.md.

## Live smoke tests: public OKX books, paper, scripted stand-in (2026-11-27 22:29–22:41 UTC)
Harness: same construction as the unit tests, journal in a temp dir, no approval files touched.
Three runs; runs 1 and 2 found bugs R6–R8 (see 05-review.md). Run 3, after the fixes, all seven finalists:

| Symbol | spread (bps) | depth within ±10 bps (USD) | trades in 5 min |
|---|---|---|---|
| HYPE-USDT-SWAP | 0.16 | 181,500 | 392 |
| PUMP-USDT-SWAP | 3.88 | 103,300 | 134 |
| UNI-USDT-SWAP | 2.08 | 79,300 | 118 |
| LIT-USDT-SWAP | 0.38 | 16,900 | 173 |
| JUP-USDT-SWAP | 4.05 | 5,500 | 60 |
| AERO-USDT-SWAP | 2.57 | 5,300 | 33 |
| SKY-USDT-SWAP | 5.69 | **1,100** | 284 |

- Snapshot size on real books: max 293, mean 287 approximate tokens (budget 400).
- Feed latency (book + trades REST): p50 ≈ 485 ms per symbol from this host.
- Every decision was `hold`: the engine was cold (<1 h of history → `warm` gate) and the scripted stand-in is not confident. That is the designed behaviour.
- Consequence (R9): a hard `max_depth_participation = 25%` limit was added. On SKY that caps any order at ≈ $275 notional, so SKY's thesis is not tradeable on the OKX perp at meaningful size today.
