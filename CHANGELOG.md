# Changelog

All notable changes to LACD are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/).

## [1.0.2] — (unreleased)

Patch (SemVer Patch, backward compatible). Implements
`docs/specification/V1_0_2_DELTA.md`:

### Fixed
- **Adopter template version**: `lacd-verify.yml` installs `@v1.0.2`
  (was `@v1.0.0` calling `--local`, which v1.0.0 lacks). Tag-based per
  ADR-0013; never pre-fabricates merge SHAs.
- **CLI remote path**: `_remote_dimension()` always constructs
  `EnforcementPolicy`; `policy=None` legacy banned from the main path by test.
  Empty `required_checks` + remote ENFORCED → downgraded to UNVERIFIED.

### Added
- Q1/Q2/RRC/PRV terminology and SOP (`PROTOCOL.md` §5, ADR-0012);
  `AGENTS.md`/`CONTRIBUTING.md` map to the same terms.
- RRC runbook (`docs/operations/rrc.md`): post-merge release-qualification
  checklist, `PASS/FAIL/HOLD`, evidence kept out of `main`.
- `test/fixtures/real-v101-release.json`: v1.0.1 is parseable;
  v1.0.2 correctly undiscoverable before release.

## [1.0.1] — 2026-10-12

Patch release: fixes real v1.0.0 defects, hardens the release/CI/governance
chain, and adds AI discoverability assets. Backward compatible (no CLI/Schema
breaking changes).

### Fixed
- **Wheel now ships templates** (P0): `pyproject.toml` `package-data` includes
  `lacd/templates/**`; resources located via `importlib.resources`
  (`lacd.cli.templates_dir()` / `lacd.config.schemas_dir()`). Clean
  `pip install` + `lacd init` no longer crashes with `FileNotFoundError`.
- **Release bundle discovery**: `parse_template_bundle()` supports three
  fail-closed formats (closed fenced JSON / `Template-Bundle-SHA256:` line /
  single-asset fallback); the real v1.0.0 release body (unclosed fence)
  is a regression fixture and correctly yields `None`.
- **ENFORCED no longer a false positive**: new versioned `EnforcementPolicy`;
  `check_remote_enforcement(..., policy=...)` verifies each item (classic
  protection and rulesets separately, ruleset must be active and ref-matched);
  any unverifiable key dimension → `UNVERIFIED`, unsatisfied → `NOT_ENFORCED`.
- `PRODUCT.md` no longer names any specific business project (independence).
- Tracked build artifacts (`.coverage`, `build/`) removed via `git rm --cached`.

### Added
- `lacd verify --local` (zero network, `remote_enforcement=unverified/LOCAL_ONLY`)
  and `--remote`; no flag keeps v1.0.0 behavior. Adopter CI template uses
  `--local` so low-privilege PR CI stays green.
- `status --json`: `dimensions.remote_enforcement.query` (`live|not_queried`).
- `lock.json` `source.commit` (full SHA) with derived `pin_strength`
  (`strong|tag-only`); never claims strong pin without a real SHA.
- `.github/CODEOWNERS`; PR/Issue templates; `llms.txt` (experimental);
  `docs/agents/` vendor-neutral quickstart; `docs/discoverability/` baseline.
- README (EN/ZH): positioning line, What-it-does/does-not-do, Who-it's-for,
  Quick Start, Security Model, Limitations, FAQ — every capability claim
  backed by a runnable feature.

### Changed
- README status: v1.0.0 released; install command documented.
- `template_version` → `1.0.1` (workflow template changed).

## [1.0.0] — 2026-10-12

First public version. Implements the complete v1.0 contract:

### Added
- CLI (`lacd`): `init` (dry-run, idempotent, conflict-visible, never
  commits/pushes), `doctor`, `status` (human + `--json`), `verify`
  (`--strict`, `--evidence`), `upgrade` (`--check`, `--dry-run`,
  `--to`, `--prepare-pr`, `--accept-major`, `--template-dir`, `--yes`).
- Adoption state machine: NOT_INSTALLED / DECLARED / VALIDATED / ENFORCED /
  DRIFTED / BLOCKED / UNVERIFIED, with primary state + four independent
  dimensions (local_validation, remote_enforcement, drift, blocking) and
  machine-readable reason codes. `status --json` is schema-validated.
- Versioned `lacd.yaml` (schema v1) and `.lacd/lock.json` (lock format v1):
  exact CLI version, trusted source, schema/policy/template versions,
  managed-file SHA-256 list, workflow pins. Floating refs (`latest`,
  `main`, `v1`) are rejected for security-sensitive fields.
- Upgrade subsystem (v1.0 hard requirement): trusted source discovery
  (GitHub Releases, or `file://` for offline/tests), SemVer compatibility
  policy (patch/minor/major, explicit major acceptance), 3-way migration
  (upstream baseline + project content + upstream target), marked-section
  merge for shared files (AGENTS.md), conflict refusal without silent
  overwrite, backup + rollback, idempotent re-runs, `--prepare-pr`
  (dedupe, evidence-bound, never auto-merges, patch fallback offline).
- Template bundles: `manifest.json` with SHA-256 per file; bundles are
  SHA-256-verified before use, never executed as scripts.
- T0/T1/T2 risk classification (path + semantics, max-wins, unknown → T2).
- GitHub integration: remote-enforcement verification (branch protection /
  rulesets; 403/no-credential/network → UNVERIFIED, never claimed ENFORCED),
  upgrade-PR preparation with risk-graded, SHA-bound PR bodies.
- Agent protocol: `AGENTS.md` managed section, `agent_checklist()` wired to
  live status computation, untrusted-input rules.
- JSON Schemas (v1): `schemas/lacd-config.schema.json`,
  `schemas/lock.schema.json`, `schemas/status.schema.json`.
- Docs: frozen PRODUCT / PROTOCOL / UPGRADE_CONTRACT specs, module
  interface contract, 7 ADRs, operations runbooks (install/adopt/upgrade/
  troubleshooting/human-approval), bilingual README, SECURITY, CONTRIBUTING.
- Tests: 299 passing — unit, integration, acceptance matrix AT-01..AT-25,
  security negatives (forged evidence, tampered bundle/lock, stale SHA,
  silent-overwrite attempts), two synthetic repos (Alpha/Beta) end-to-end.
  Core coverage 88% (gate: 80%).

### Security
- Fail-closed defaults: unverified remote state is never reported as
  ENFORCED; conflicts abort migration; tampered bundles are rejected;
  unknown change paths classify as T2.
- No secrets in outputs; tokens travel via environment/`http.extraHeader`,
  never in URLs or logs.

### Known limitations (v1.0)
- Not published to PyPI (no package-name ownership / release authorization
  yet); install from git tag/commit.
- No signature infrastructure; release integrity rests on tag immutability
  + bundle SHA-256 recorded in release metadata.
- `lacd verify` remote dimension needs a GitHub credential with permission
  to read branch protection/rulesets; otherwise UNVERIFIED (honest, not a
  failure).
- Multi-version coexistence uses per-version virtualenvs (no single
  dispatcher binary yet).

[1.0.0]: https://github.com/SkyCjq/LACD/releases/tag/v1.0.0
