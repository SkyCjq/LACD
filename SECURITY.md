# Security Policy

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | :white_check_mark: |

LACD follows SemVer. Security fixes land as patch releases; adopters
upgrade through the normal controlled-PR flow (`lacd upgrade --to`).

## Design principles (enforced in code, not just documented)

- **Fail-closed.** Unverified remote state is reported `UNVERIFIED`, never
  `ENFORCED`. Migration conflicts abort instead of overwriting. Tampered
  template bundles (SHA-256 mismatch) are rejected before use.
- **No silent weakening.** Project-owned content is never touched by
  upgrades; shared files merge only inside LACD-managed marked sections.
- **Evidence binds to commits.** Test/review evidence records a `head_sha`;
  `lacd verify --evidence` rejects stale bindings.
- **Least privilege.** GitHub Actions templates pin third-party actions to
  full commit SHAs; untrusted PR code never runs with secrets.
- **No secret exfiltration.** `lacd status --json` and all reports never
  contain secrets. Tokens are read from `GITHUB_TOKEN`/environment and
  passed to git via `http.extraHeader`, never embedded in URLs or logs.

## Reporting a vulnerability

Open a GitHub issue titled `[SECURITY]` in this repository, or contact the
maintainer privately. Please include: affected version, reproduction steps,
and impact assessment. Do not open a public PR with exploit details before
a fix is available.

## Scope notes

- LACD never asks for, stores, or transmits your repository's secrets.
- The `verify` remote check only *reads* branch protection/rulesets; it
  never creates or modifies remote configuration.
