# Phase 1 — Spec (approval gate G1)

## Goal
A 24/7 autonomous crypto agent that studies the market, finds asymmetric setups among the research
finalists, and executes them, with every limit, size and side effect owned by deterministic code.

## Roles
| Layer | Who | Does | Never does |
|---|---|---|---|
| Brain | Claude Opus 5.5 (`claude-opus-5-5`) | research, strategy, code, escalation re-reads, nightly review and schema rewrites | touch limits, size orders, place orders |
| Reflex | Jev (`jev-latest`, TypeSafe `system_one`) | one typed judgment per candle per symbol: regime, direction, toxic_flow, setup_quality, risk_state | see anything except the snapshot; output free text |
| Code | `src/jevagent/*` | arithmetic, thresholds, sizing, risk, kill switch, orders, journal | delegate a hard limit to a model |
| Operator | you | approves gates G1–G5, limit fingerprint, schema promotions, kill-switch reset | — |

## Functional requirements
- F1 State engine turns the live order book into one numeric snapshot <= 400 tokens per symbol per candle, causal timestamps (`state_engine.py`).
- F2 One Jev call per candle per symbol evaluates all five questions (+ shadow questions) (`jev_client.py`).
- F3 Fire only if setup_quality >= 2, direction confidence > 0.80, risk_state == safe (plus stricter-only: toxic_flow < 0.5, not crisis, not invalidated) (`policy.py`).
- F4 Size = fractional Kelly on the calibrated probability, capped at 1/4 Kelly (`policy.py`, `calibration.py`).
- F5 Hard risk layer: 15% max drawdown, max position, max daily loss, armed kill switch checked before every order (`risk.py`).
- F6 Escalate to Opus 5.5 when confidence < 0.60, regime == crisis, code-detected stress, or invalidation (`escalation.py`).
- F7 Nightly review: every execution and miss, Brier scores, calibration refit, schema rewrite, gated promotion (`review/nightly.py`).

## Non-goals (this release)
- Live order routing. Paper only until G5 (see 06-release.md).
- Market making, cross-exchange arbitrage, leverage above 0.6x gross.

## Acceptance criteria
- All tests in `tests/` pass (`pytest -q`).
- No order reaches the broker without `RiskEngine.check()` in the same call path (test_loop, test_risk).
- Snapshot causality and budget proven by tests (test_state_engine).
- Limit fingerprint mismatch blocks startup (test_risk::test_limits_fingerprint_gate).

## Open spec items
- Holder-concentration pull (on-chain top holders) for each finalist.
- Verified unlock schedules from issuers (LIT cliff date conflicts between sources).
- Correlation-aware exposure cap.
