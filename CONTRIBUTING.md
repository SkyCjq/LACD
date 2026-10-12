# Contributing to LACD

## Ground rules

- **Independence first.** No business-project names, data models,
  credentials, or workflows in this repository — ever. See
  `docs/specification/PRODUCT.md` §4.
- **Evidence over claims.** A feature is done when it is implemented,
  tested, and demonstrated — not when it is described.
- **Tests are load-bearing.** New behavior needs tests; tests must assert
  real behavior (no weakened assertions to "go green"). Negative/security
  tests are as important as happy paths.
- **Contracts are frozen deliberately.** `docs/specification/*` and
  `docs/architecture/MODULE_CONTRACT.md` change only via ADR + maintainer
  approval.

## Development setup

```bash
git clone https://github.com/SkyCjq/LACD.git && cd LACD
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest -q            # full suite: unit + integration + acceptance
```

Python 3.11+ required. Runtime dependencies are intentionally minimal
(`pyyaml`, `jsonschema`); think twice before adding one (ADR required).

## Pull requests

- One logical change per PR; include tests and docs updates.
- All changes go through PRs — never push to `main` directly.
- CI must be green (`pytest --cov=lacd --cov-fail-under=80`).
- A maintainer merges. Contributors never merge their own PRs.
- Review terms follow `docs/specification/PROTOCOL.md` §5: **Q1** (pre-dev),
  **Q2** (independent review of the final stable HEAD before merge),
  **RRC** (post-merge release qualification), **PRV** (post-release verification).
  A Q2 `PASS` is a recommendation, never a merge authorization.

## Release process (maintainers)

See [docs/operations/release.md](docs/operations/release.md).
