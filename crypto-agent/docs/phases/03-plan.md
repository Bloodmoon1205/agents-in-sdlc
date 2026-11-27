# Phase 3 — Plan (approval gate G3)

Test-first order. Each step: write the failing test, make it pass, refactor.

| # | File(s) | Test(s) | Status |
|---|---|---|---|
| 1 | `src/jevagent/config.py` | `tests/test_risk.py::test_limits_fingerprint_gate` | done |
| 2 | `src/jevagent/state_engine.py` | `tests/test_state_engine.py` (budget, causality, determinism, arithmetic) | done |
| 3 | `src/jevagent/candidates.py`, `src/jevagent/schema.py` | `tests/test_schema.py` (every finalist compiles to real `typesafe_sdk` objects; keys immutable) | done |
| 4 | `src/jevagent/jev_client.py` | covered by `tests/test_loop.py` (shadow fan-out, outage) | done; live call untested (no key) |
| 5 | `src/jevagent/calibration.py`, `src/jevagent/policy.py` | `tests/test_policy.py` (gate, ¼-Kelly cap, shrink, escalation, invalidation, code stress) | done |
| 6 | `src/jevagent/risk.py` | `tests/test_risk.py` (stops, caps, kill, drawdown, daily loss, band, stress, never-increase) | done |
| 7 | `src/jevagent/broker.py` | `tests/test_broker.py` (book walk, fees, PnL, stop/tp/time-stop) | done |
| 8 | `src/jevagent/escalation.py` | `tests/test_escalation.py` (request shape, refusal/garbage → stay_flat, rate limit) | done; live call untested (no key) |
| 9 | `src/jevagent/journal.py` | `tests/test_risk.py::test_tampered_journal_is_detected` | done |
| 10 | `src/jevagent/loop.py` | `tests/test_loop.py` (end-to-end, kill file, outage, crash → reduce-only, trade path) | done |
| 11 | `src/jevagent/review/nightly.py` | `tests/test_nightly.py` (causal labels, Brier, revision, promotion gate) | done |
| 12 | `src/jevagent/feeds/okx.py`, `src/jevagent/cli.py` | live smoke on public OKX books (see 04-build.md) | done |
| 13 | `research/*.py` | reproducible snapshot in `data/snapshots/2026-11-27/` | done |
| 14 | live broker (`src/jevagent/broker_okx.py`) | must be built against OKX demo trading first | **not started — G5 item** |
