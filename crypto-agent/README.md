# jevagent: autonomous crypto agent (brain + reflex + code-owned risk)

> **Paper trading only.** There is no live order router in this release (see `docs/phases/06-release.md`, gate G5).
> Nothing here is financial advice. The research is a dated snapshot, and every estimate is labelled.

| Layer | Component | Role |
|---|---|---|
| Brain | Claude Opus 5.5 (`claude-opus-5-5`) | research, escalation re-reads, nightly review, schema rewrites |
| Reflex | Jev (TypeSafe `jev-latest`) | one typed decision per candle per symbol, from a ≤400-token snapshot |
| Code | `src/jevagent/` | every number: state, gate, Kelly sizing, risk limits, kill switch, orders, journal |

## Read first
- Research and finalists, with sources and "where I might be wrong" (Arabic): `docs/research/2026-11-27-market-research.md`
- The six phases: `docs/phases/01-spec.md` … `06-release.md`
- Gates: `gates/README.md`

## Layout
```
research/                 primary-source fetch + regime + fundamentals screen (stdlib, no keys)
data/snapshots/2026-11-27 raw API responses + MANIFEST (URLs, fetch times) + REGIME/SCREEN outputs
schemas/jev/<SYM>/v1.json compiled Jev schemas; ACTIVE.json = active/shadow pointer
src/jevagent/
  config.py         frozen hard limits + fingerprint gate
  state_engine.py   order book -> causal compact snapshot (<400 tokens)
  candidates.py     7 finalists: thesis, bear case, machine-checkable invalidation
  schema.py         typed Jev schema + compiler to typesafe_sdk
  jev_client.py     TypeSafeJev (real) / ScriptedJev (wiring stand-in, no edge)
  policy.py         gate (setup>=2, conf>0.80, safe) + 1/4-capped Kelly
  calibration.py    raw p -> empirical win probability (shrunk until data exists)
  risk.py           RiskEngine + KillSwitch + code-side market_stress
  escalation.py     async Opus 5.5 re-read; verdicts can only make things safer
  broker.py         paper broker (book walk, fees, stops/targets/time stops)
  journal.py        hash-chained JSONL
  loop.py           the per-candle loop
  review/nightly.py Brier/ECE, misses, calibration refit, schema revision, promotion gate
  feeds/okx.py      OKX public books/trades (contracts -> coins)
  cli.py            operator commands
tests/              63 tests (pytest)
```

## Quick start
```bash
python -m venv .venv && .venv/bin/pip install -e ".[jev,brain,dev]"
.venv/bin/pytest -q
.venv/bin/jevagent compile-schemas
.venv/bin/jevagent approve-limits      # operator gate G0: review src/jevagent/config.py first
.venv/bin/jevagent paper --minutes 60  # scripted stand-in on live OKX books; no keys needed
```
With keys (`TYPESAFE_API_KEY`, `ANTHROPIC_API_KEY`), run `jevagent paper --jev typesafe …` and schedule
`jevagent review --brain` at 00:05 UTC daily.

## Not verified in this build
- No real Jev or Opus call was made (no keys in the build environment). Both adapters are tested against fakes, and request shapes are checked against the installed SDKs (`typesafe-sdk` 0.7.6, `anthropic` 2.14.0).
- AgenKit (agenkit.xyz) was not installed: it is a paid plugin that needs a license key. The six phases are reproduced as documents with operator gates instead.
