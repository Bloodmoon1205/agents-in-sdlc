# Phase 6 — Release (gates G4, G5)

Crypto has no "open", so "before the next open" means the daily **00:00 UTC cutover**: the loop reloads
`schemas/jev/ACTIVE.json` at cutover; the nightly review runs at 00:05 UTC on the closed day.

## G0 — Limits approval (operator)
```bash
jevagent status            # shows the limits fingerprint
jevagent approve-limits    # prints every limit, requires typing APPROVE
```

## G4 — Paper with real models (≥ 14 days)
```bash
export TYPESAFE_API_KEY=...   # console.typesafe.ai
export ANTHROPIC_API_KEY=...  # or `ant auth login`
jevagent compile-schemas
jevagent paper --jev typesafe --symbols HYPE-USDT-SWAP,LIT-USDT-SWAP,SKY-USDT-SWAP,JUP-USDT-SWAP,UNI-USDT-SWAP,AERO-USDT-SWAP,PUMP-USDT-SWAP --minutes 20160
# cron (UTC): 5 0 * * *  cd /path/crypto-agent && jevagent review --brain
```
Exit criteria: Jev p95 latency journaled; ≥ 200 path-labelled samples per traded symbol; direction Brier
skill > 0 vs climatology on every symbol you intend to trade; paper net P&L after fees ≥ 0; zero risk-layer
invariant violations; kill-switch drill done (`touch runtime/KILL`, verify flatten, `jevagent reset-kill`).

## G5 — Live (small)
Not buildable from this release: write `src/jevagent/broker_okx.py` against OKX **demo trading**
(`x-simulated-trading: 1`), with order-ack reconciliation, then repeat G4 on demo, then live with
equity ≤ an amount you can lose entirely. The risk layer, journal and kill switch are unchanged by this step.

## Using AgenKit
AgenKit (agenkit.xyz) is a paid Claude Code / Codex plugin (license key required). It was **not**
installed in this build. If you activate it, run `/agenkit` in this directory and point it at
`docs/phases/01-spec.md`; its six stages map onto these six documents, and its approval pauses map onto G1–G5.
