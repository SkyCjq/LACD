# AGENTS.md — LACD (this repository)

> Agent entry hints for working in the LACD framework repository itself.
> This file is a hint, not a substitute for `lacd status --json`.

## Project in one line

LACD is an independent, generic, business-agnostic governance framework for
agentic software development. **No business-project content here, ever.**

## How to work here

1. Run `lacd status --json` in the repo root; obey the reported state.
2. Classify your change before editing:
   - **T0**: `docs/**`, `examples/**`, `README*`, `CHANGELOG*`, `LICENSE*`
   - **T1**: `src/**` (non-security), `tests/**`, `schemas/**`
   - **T2**: `.github/**`, `.lacd/**`, `SECURITY.md`, secrets-adjacent files,
     anything unknown → T2 (fail-closed)
3. Frozen contracts live in `docs/specification/` and
   `docs/architecture/MODULE_CONTRACT.md` — changing them needs an ADR and
   maintainer approval, not a drive-by edit.
4. Tests: `pytest -q` (full), `pytest tests/unit -q` (fast). New behavior
   needs real tests; never weaken assertions to go green. New security
   behavior needs a negative test.
5. Never commit secrets, tokens, or personal data. `lacd verify` before
   submitting; evidence binds to the commit SHA.

## Commands

- Install (dev): `pip install -e ".[test]"`
- Full tests: `pytest -q`
- Coverage gate: `pytest --cov=lacd --cov-fail-under=80 -q`
- CLI: `lacd init --dry-run`, `lacd status`, `lacd verify --strict`

## Boundaries

- Never push to `main`; all changes via PR; never merge your own PR.
- Never lower branch protection to make an implementation "easier".
- Docs' copy-paste commands must actually run (AT-25 smoke test guards this).
