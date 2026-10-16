# LACD — Lightweight Agentic Collaborative Development

## 1. LACD in one sentence

> LACD (Lightweight Agentic Collaborative Development) is an open-source framework that enables AI agents like Muse to develop software directly on GitHub without a local development environment, combining automated testing, GitHub-native safeguards, independent AI reviews, and human approval for efficient, verifiable, and traceable software delivery.

**What "without a local development environment" means:** you may choose a
GitHub cloud-based development path without installing a local IDE. It does
**not** mean the toolchain disappears — CI runners, Python, Git, and an
execution environment are still required (see §5 Prerequisites).

> **Status: v1.0.2 is the latest released version.**
> Install: `pip install "git+https://github.com/SkyOneBranch/LACD.git@v1.0.2"`.
> v1.0.3 is in development and **not installable yet** — do not use `@v1.0.3`.
> Tags, GitHub Releases, and branch protection remain human actions
> (see [docs/operations/human-approval.md](docs/operations/human-approval.md)).

<img width="1774" height="887" alt="LACD Workflow Infographic Poster" src="https://github.com/user-attachments/assets/bfce422c-7890-4050-92fc-f9467f708668" />

## 2. LACD in 30 seconds

**What LACD does**

- **Verifiable adoption**: `lacd init` declares intent in `lacd.yaml`;
  `lacd status --json` reports a machine-readable adoption state
  (`NOT_INSTALLED` / `DECLARED` / `VALIDATED` / `ENFORCED` / `DRIFTED` /
  `BLOCKED` / `UNVERIFIED`); `lacd verify` checks it. Agents read state
  from the CLI, not from vibes.
- **GitHub-native safeguards**: read-only verification of branch protection
  and rulesets (each policy item is checked separately and reported
  `UNVERIFIED` instead of guessed when unreadable); upgrade PRs are prepared
  with evidence-bound bodies and are **never auto-merged**.
- **Human decision by default**: every PR is merged by a human. A solo
  repository with 0 required approvals is supported but reported honestly
  as `human_merge_required` — never disguised as an approving review.
- **Safe versioned upgrades**: `lacd upgrade` discovers versions, previews
  every change with `--dry-run`, migrates with a 3-way merge, refuses on
  conflict (no silent overwrites), keeps a backup, and can roll back.
  Template bundles are SHA-256 verified before use.
- **Risk-graded changes**: every file change classifies as **T0** (docs),
  **T1** (code/tests), or **T2** (workflows/secrets/governance); unknown
  paths are T2 (fail-closed).

**What LACD does not do**

- It does not write code for you, judge code quality, or decide
  correctness — it is governance scaffolding, **not an AI model**, and it
  ships **no built-in AI coding or review service**. You bring your own
  agent (Muse or others); LACD governs how its work is verified.
- It does not merge PRs, change branch protection, manage secrets, or
  publish releases — those stay with a human, always.
- It does not phone home: no central server, no SaaS, no telemetry.
- It makes **no promise** about AI search or recommendation: whether any
  AI assistant recommends LACD depends on that system's retrieval and
  model behavior, which we do not control (see [ADR-0011](docs/decisions/ADR-0011-discoverability.md)).

## 3. Roles when developing on GitHub with AI agents

**AI-written code is not final acceptance. Automated tests, independent
review, and human decisions together form delivery governance.** No check
can catch every problem — governance reduces risk, it does not eliminate it.

| Role | Capability | Responsibility | Boundary |
|---|---|---|---|
| **Human** — product manager and final acceptor | Defines requirements and acceptance criteria; approves, merges, and releases | Final decisions on scope, risk, merge, and release; bears the consequences | A human decision is a decision — it is **not** the same as having a GitHub `APPROVED` review record on every repository |
| **Muse or another AI agent** — implementer | Writes code, runs tests, prepares PRs on a branch | Implements to the requirement; provides evidence bound to the commit SHA | Never merges; never approves its own work; never changes branch protection |
| **GitHub Actions** — automated testing | Runs tests and checks on every push and PR | Objective, repeatable evidence (CI logs) | Cannot judge correctness beyond what tests assert; CI passing is not proof of enforcement |
| **Independent AI reviewer** — isolated quality reviewer | Reviews the PR's final HEAD in a session isolated from development | Independent judgment outside the implementer's context, with its own evidence | **Self-review by the same agent in the same session never counts as independent review**; "third-party" here means *isolated from development*, not certified by any external company or commercial audit |
| **GitHub + Human** — safeguards and delivery decision | Branch protection, required checks, human merge | Gate enforcement before code lands | Safeguards only work if actually configured — verify, don't assume; see §8 |

## 4. Who it's for — and when not to use it

**Who it's for**

- Individual developers **without a local IDE** who want to build with AI
  agents directly on GitHub, with auditable guardrails instead of
  prompt-only instructions.
- Teams running AI coding agents on GitHub that want verifiable adoption
  state and controlled upgrades.
- Solo maintainers who want upgrade safety for shared workflow templates
  across projects.
- Anyone who wants "human-in-the-loop" as an enforced default, not a
  suggestion in a doc file.

**When not to use LACD**

- You want a hosted dashboard or SaaS — LACD has none by design.
- You want automatic merging — LACD refuses this by design.
- You don't use GitHub: remote-enforcement checks and PR preparation are
  GitHub-specific (the core CLI — init/status/verify/upgrade — works on
  any git repository).

## 5. Prerequisites

- **Python 3.11+** (CI tests 3.11 and 3.12). Runtime dependencies: only
  `pyyaml` and `jsonschema`.
- **Git** and a **GitHub account** (for the GitHub cloud path and for
  remote-enforcement checks).
- LACD v1.0 is **not** published to PyPI. Install from a verifiable source:

```bash
# Latest released version (verified 2026-10-16):
pip install "git+https://github.com/SkyOneBranch/LACD.git@v1.0.2"

# Or pin to an exact commit (strongest reproducibility):
pip install "git+https://github.com/SkyOneBranch/LACD.git@<40-hex-commit>"

# Local development:
git clone https://github.com/SkyOneBranch/LACD.git && cd LACD && pip install .
```

Verify the install:

```bash
lacd --version   # → 1.0.2 (for the released install above)
```

> The next development target is **v1.0.3** (repository-migration
> compatibility, version/template reliability, and this README rewrite).
> It is **not released** — `@v1.0.3` cannot be installed.

For projects pinning **different** LACD versions on one machine, use an
isolated venv per version (see [docs/operations/install.md](docs/operations/install.md)).

## 6. Quick start

```bash
cd your-project
lacd init --dry-run   # preview what would be written; writes nothing
lacd init             # lacd.yaml, .lacd/lock.json, managed templates, AGENTS.md section
lacd status           # adoption state
lacd verify --local --strict   # local checks must pass; exits non-zero otherwise
```

`init` is idempotent (running it twice reports `already-initialized`),
never runs `git add`/`commit`/`push`, and never silently overwrites your
files. Details: [docs/operations/adopt.md](docs/operations/adopt.md).

Real transcript (commands run 2026-10-16 against the development build,
v1.0.3). Released v1.0.2 accepts the same commands and follows the same
workflow; its output shows version `1.0.2` and the previous default upstream
`SkyCjq/LACD` instead of the values below:

```text
$ lacd init --dry-run
DRY-RUN init for /tmp/lacd-readme-demo: no files will be written
  create         lacd.yaml  (default lacd.yaml (schema_version 1))
  create         .lacd/lock.json  (source git-tag SkyOneBranch/LACD@v1.0.3)
  create         .lacd/project-rules.yaml  (empty project-rules template)
  create         .github/workflows/lacd-verify.yml  (managed CI workflow template)
  create         AGENTS.md  (new file with managed section)

$ lacd init
initialized LACD 1.0.3 in /tmp/lacd-readme-demo
  wrote lacd.yaml, .lacd/lock.json, .github/workflows/lacd-verify.yml
  AGENTS.md managed section: created
Note: lacd never runs git add/commit/push; review and commit yourself.

$ lacd status
LACD status for /tmp/lacd-readme-demo
  state: VALIDATED
  cli: 1.0.3 | branch: unknown | head: unknown | mode: lacd
  local_validation: pass
  remote_enforcement: unverified (REMOTE_NOT_CHECKED)
  drift: clean
  blocking: none
  version_consistency: consistent - running CLI 1.0.3 matches locked lacd_cli_version (cli=1.0.3 package=1.0.3 locked_cli=1.0.3 template=1.0.3)

$ lacd verify --local --strict
LACD verify for /tmp/lacd-readme-demo
  mode: local-only (remote enforcement not queried)
  state: VALIDATED
  local_validation: pass
  remote_enforcement: unverified (LOCAL_ONLY)  [remote check skipped (--local): no network requests were made; remote enforcement is NOT verified, only local dimensions]
  drift: clean
  blocking: none
  version_consistency: consistent - running CLI 1.0.3 matches locked lacd_cli_version (cli=1.0.3 package=1.0.3 locked_cli=1.0.3 template=1.0.3)
STRICT: all local dimensions satisfied (remote not queried -- --local mode)
```

## 7. A real example: requirement → tests → independent review → human merge

LACD's user-facing delivery flow (the internal Q1/Q2 labels are LACD's own
process terms, **not** industry standards):

`Requirement → Acceptance Criteria → AI Agent Development → CI & Testing → Independent AI Review → Human Decision → Merge`

1. **Requirement → Acceptance Criteria.** The human writes what should be
   built and how to tell it's done. Vague requirements are the most common
   source of wasted agent work — write the acceptance criteria first.
2. **AI Agent Development.** The agent (Muse or another) implements on a
   branch: reads `AGENTS.md`, runs `lacd status --json`, classifies the
   change (T0/T1/T2), implements, and runs `lacd verify` before submitting.
   Evidence binds to the commit SHA.
3. **CI & Testing.** The agent pushes the branch; GitHub Actions runs the
   test suite. CI passing is objective evidence — but it is not proof that
   branch protection is enforced (see §8).
4. **Independent AI Review.** An independent reviewer — a separate AI
   session isolated from development, with its own evidence — reviews the
   PR's final HEAD. **The same agent checking its own work in the same
   session is self-review, never independent review.**
5. **Human Decision.** The human reviews the requirement fit, the CI
   evidence, and the independent review, then approves and merges. LACD
   prepares PRs; it **never** auto-merges.
6. **Merge.** The merge is the delivery decision. Evidence is archived
   against the merge SHA.

For complex projects or parallel multi-agent development, LACD's own
team additionally uses pre-development spec review and frozen requirements
internally — recommended as a governance practice, not a CLI feature, and
never presented as an industry standard.

## 8. Security capabilities and limitations

**Capabilities**

- **Fail-closed**: unverified remote state is reported `UNVERIFIED`, never
  `ENFORCED`. Each enforcement-policy item is verified independently; any
  unreadable item yields `UNVERIFIED` plus a missing list.
- **No silent overwrites**: migration conflicts abort instead of merging;
  project-owned files are never touched by upgrades.
- **Verified downloads**: template bundles are SHA-256 verified before
  use; downloaded content is never executed as scripts.
- **Least privilege**: tokens come from the environment only and never
  appear in logs, URLs, or reports; CI templates pin third-party Actions
  to full commit SHAs.
- **Evidence binding**: test/review evidence records a `head_sha`;
  `lacd verify --evidence` rejects stale bindings.
- **Trust boundary for policy**: remote-enforcement policy is read from
  the protected baseline (`origin/main`), never from unmerged PR files —
  a PR cannot lower its own bar.

**Limitations (read before relying on LACD)**

- Remote-enforcement checks need a GitHub token permitted to read branch
  protection/rulesets; without it the result is `UNVERIFIED` (honest, not
  an error). **CI passing does not equal GitHub-enforced.**
- Whether required checks and approval rules are actually enforced
  depends on your repository's real configuration — verify it in
  repository settings; LACD reports what it can prove.
- No signature infrastructure yet: release integrity rests on tag
  immutability plus the bundle SHA-256 published in release notes.
- Not published to PyPI; install from a git tag or commit.
- One CLI version per environment; multi-version coexistence via venvs.
- `llms.txt` is experimental and not a cross-platform standard.

## 9. Init, version lock, and upgrade usage

**Initialize** — see §6. The version lock (`.lacd/lock.json`) records the
exact CLI version, the trusted source (`SkyOneBranch/LACD` for new
projects), template versions, and SHA-256 hashes of every managed file.

**Check state**

```bash
lacd status --json   # machine-readable: state + four dimensions + versions
lacd verify          # local checks + remote enforcement when provable
```

**Upgrade safely**

```bash
lacd upgrade --check                    # discover versions: Patch/Minor/Major
lacd upgrade --dry-run --to 1.0.3       # preview every change; worktree untouched
lacd upgrade --to 1.0.3                 # migrate: backup -> 3-way merge -> lock update
lacd upgrade --to 1.0.3 --prepare-pr    # build branch + PR (never auto-merges)
```

Upgrade rules: conflicts abort without overwriting your changes; a backup
is kept under `.lacd/backup/` and `rollback` can restore it; re-running an
upgrade is idempotent. Version lock, compatibility policy, and the
no-central-registry rule are specified in
[docs/specification/UPGRADE_CONTRACT.md](docs/specification/UPGRADE_CONTRACT.md).

## 10. FAQ

**Does LACD work with my AI coding assistant?**
LACD is vendor-neutral and ships no built-in AI. Any assistant that can run
shell commands can follow [docs/agents/](docs/agents/). We do not claim any
product auto-detects or endorses LACD, and multi-agent here means roles are
separable and tools replaceable — not that every AI platform has identical
capabilities.

**Will AI search engines recommend LACD?**
We cannot guarantee that — it depends on each system's retrieval and model
behavior. See [ADR-0011](docs/decisions/ADR-0011-discoverability.md) and our
[retrieval baseline](docs/discoverability/AI_DISCOVERABILITY_BASELINE.md),
which records "not yet indexed" honestly where true.

**Who merges the PRs?**
A human, always. LACD prepares PRs; it never merges them.

**Is the independent review done by LACD itself?**
No. LACD provides no built-in review service. "Independent" means the
reviewer is isolated from the development process with its own evidence —
a separate reviewer and session, never the same agent reviewing its own
work in the same session.

**Can LACD manage non-GitHub forges?**
The core CLI (init/status/verify/upgrade) works on any git repository.
Remote-enforcement verification and PR preparation are GitHub-specific.

**Is there a hosted version?**
No. LACD is local-first by design: no server, no account, no telemetry.

**Does LACD guarantee my code is correct or secure?**
No. Automated tests, independent review, and human decisions together form
delivery governance — they reduce risk but cannot catch every problem, and
LACD makes no guarantee otherwise.

## 11. Advanced technical and governance docs

- [docs/specification/PRODUCT.md](docs/specification/PRODUCT.md) — goals, non-goals, independence rules, terms
- [docs/specification/PROTOCOL.md](docs/specification/PROTOCOL.md) — modes, agents, roles, state machine
- [docs/specification/UPGRADE_CONTRACT.md](docs/specification/UPGRADE_CONTRACT.md) — versioning, upgrade, rollback
- [docs/architecture/MODULE_CONTRACT.md](docs/architecture/MODULE_CONTRACT.md) — frozen module interfaces
- [docs/agents/](docs/agents/) — optional agent onboarding
- [docs/decisions/](docs/decisions/) — architecture decision records
- [docs/operations/](docs/operations/) — runbooks (install / adopt / upgrade / troubleshooting / human-approval / release)
- [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md)

**Repository layout**

```text
src/lacd/            # the CLI package
  cli/               # argparse commands: init/doctor/status/verify/upgrade
  core/              # state machine, status JSON, semver
  config/            # lacd.yaml schema + .lacd/lock.json
  governance/        # T0/T1/T2 risk classification
  agents/            # agent protocol checklist
  github/            # remote-enforcement check, upgrade-PR preparation
  upgrade/           # version source, compatibility, 3-way migration, rollback
  templates/         # managed templates (hashed in manifest.json)
schemas/             # lacd-config / lock / status JSON schemas (versioned)
tests/               # unit / integration / acceptance (AT-01..AT-25) / security
examples/generic-project/
docs/
  specification/     # PRODUCT / PROTOCOL / UPGRADE_CONTRACT (frozen)
  architecture/      # module interface contract
  agents/            # vendor-neutral agent onboarding (optional)
  discoverability/   # retrieval baseline (honest, re-runnable)
  decisions/         # ADRs
  operations/        # install / adopt / upgrade / troubleshooting / human-approval / release
llms.txt             # experimental agent entry point (not a standard)
```

**Independence guarantee**

LACD never contains business-project names, data models, credentials, or
workflows. Its generic capabilities are proven in two independent synthetic
repositories (see `tests/acceptance/`). Adopters keep only a minimal managed
entry point, a lock file, and auditable project extensions — they upgrade by
choosing when to merge a controlled PR, never by copying code.

[README.zh-CN.md](README.zh-CN.md) — 中文版.

## License

MIT — see [LICENSE](LICENSE).
