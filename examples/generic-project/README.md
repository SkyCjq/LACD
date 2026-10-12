# generic-project — LACD adoption example (synthetic)

A minimal, business-free example showing what a LACD-adopted project looks
like. Nothing here is real business logic.

## Adopt it

```bash
cd examples/generic-project
lacd init --dry-run
lacd init
lacd status --json
```

## What LACD manages vs what you own

| LACD managed (do not hand-edit) | You own |
|---|---|
| `lacd.yaml` (declared intent) | `src/` — your code |
| `.lacd/lock.json` (version lock) | `.lacd/project-rules.yaml` — your rules |
| `.github/workflows/lacd-verify.yml` | your other workflows |
| `AGENTS.md` marked section | rest of `AGENTS.md` |

## Upgrade demo

```bash
lacd upgrade --check
lacd upgrade --dry-run --to <next>
```
