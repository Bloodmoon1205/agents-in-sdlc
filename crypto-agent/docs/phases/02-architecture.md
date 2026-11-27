# Phase 2 — Architecture (approval gate G2)

```
                      ┌──────────────── nightly (00:05 UTC) ───────────────┐
                      │ review/nightly.py  (Opus 5.5 = brain)              │
 journal/*.jsonl ───▶ │ label (causal) → Brier/ECE → misses → calibration  │──▶ schemas/jev/<SYM>/vN.json (shadow)
 (hash-chained)       │ → propose_revision (text only) → shadow evidence   │──▶ runtime/gates/pending/*.json
                      └────────────────────────────────────────────────────┘          │ operator approves
                                                                                       ▼
 OKX books/trades ─▶ StateEngine ─▶ snapshot (≤400 tok) ─▶ Jev (1 call: 5 Q + shadow Q) ─▶ policy (gate + ¼-Kelly)
                         │                                                                │
                         └─▶ market_stress (code) ──────────────▶ RiskEngine.check() ◀───┘   ◀── KillSwitch (every order)
                                                                        │
                                       EscalationManager ─▶ Opus 5.5    ▼
                                       (async; reduce-only while pending) PaperBroker ─▶ Journal
```

## Invariants
1. **Code owns every number.** `config.py` is frozen; its SHA-256 must match `gates/approved_limits.sha256`.
2. **Monotone safety.** Jev's `risk_state`, Opus verdicts and code stress can only block or shrink; `RiskEngine.check` never increases quantity (randomized test).
3. **Causality.** Snapshot at t uses events with exchange ts ≤ t; nightly labels only decisions whose window closed before review time.
4. **Fail safe.** Jev error → hold. Opus error/refusal/garbage → stay_flat. Stale data → no new risk. Non-finite equity → kill.
5. **One experiment at a time.** At most one shadow schema per symbol; promotion requires ≥300 labelled samples, better direction skill, no worse ECE, and an operator approval file.

## Cost model for model calls
- Jev: 1 call per symbol per candle (1m default). Cheap and fast by vendor description; latency is measured and journaled.
- Opus 5.5: only on escalation (≤4 per symbol-hour, then the symbol stays restricted) and once nightly per weak schema. `effort: high`, structured output, server-side refusal fallback (`fallbacks: "default"`).
