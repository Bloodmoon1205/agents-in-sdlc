# Approval gates

| Gate | File / command | Who |
|---|---|---|
| G0 limits | `gates/approved_limits.sha256` via `jevagent approve-limits` | operator only |
| G1 spec | sign-off on `docs/phases/01-spec.md` | operator |
| G2 architecture | sign-off on `docs/phases/02-architecture.md` | operator |
| G3 plan | sign-off on `docs/phases/03-plan.md` | operator |
| G4 paper with real models | exit criteria in `docs/phases/06-release.md` | operator |
| G5 live | exit criteria in `docs/phases/06-release.md` | operator |
| schema promotion | `runtime/gates/pending/promote-<SYM>-<ver>.json` → set `approved_by` → `jevagent promote` | operator |
| kill-switch reset | `jevagent reset-kill "I have reviewed the incident"` | operator |

`approved_limits.sha256` is intentionally **absent** from this commit: the agent refuses to start until you
review `src/jevagent/config.py` and approve it.
