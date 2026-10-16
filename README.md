# LACD | Lightweight Agentic Collaborative Development

[简体中文](README.zh-CN.md) · [GitHub Releases](https://github.com/SkyOneBranch/LACD/releases)

**You define the task. An AI agent writes code. Tests and independent review help catch problems. You decide whether to merge.**

LACD is an open-source framework for governing AI-assisted software development. It helps you organize work on GitHub, verify changes, keep evidence, and adopt or upgrade project rules safely. **LACD is not an AI coding model and does not include Muse, ChatGPT, or any other AI service.**

You can work with cloud-based AI agents and GitHub without installing a local IDE. Python, Git, CI runners, and an actual execution environment are still necessary.

<img width="1774" height="887" alt="LACD Workflow Infographic Poster" src="https://github.com/user-attachments/assets/bfce422c-7890-4050-92fc-f9467f708668" />

## Be the product manager; let an AI agent help build it

Suppose you want a simple task-management app:

1. **You define the requirement:** for example, “add tasks and mark them done,” with clear acceptance criteria.
2. **An AI agent builds it:** Muse or another capable agent writes code and opens a GitHub Pull Request (PR), a proposed code change.
3. **Automated tests run:** GitHub Actions checks the change. Failures go back for fixes and another test run.
4. **Independent AI review follows:** a reviewer isolated from implementation checks the final PR commit and its evidence. Problems go back for repair.
5. **You accept or reject the result:** review the evidence and remaining risks, then decide whether to merge. Merging does not automatically mean production deployment.

**In short: agents build, tests and independent reviewers look for problems, and the human makes the final decision.**

For complex work, you can add an independent specification review (Q1) and a requirements freeze before implementation. These are recommended governance practices, **not CLI gates automatically enforced for every LACD adopter**.

## AI can write code quickly. How do you know it works?

AI-generated code can misunderstand requirements, miss test cases, or break existing behavior. LACD helps make the delivery process easier to inspect:

- **Traceable evidence:** see what changed, what was tested, and which Git commit was reviewed.
- **A repair loop:** failed tests and review findings are reasons to fix the work, not to merge it blindly.
- **Controlled adoption and upgrades:** `lacd init` sets up project rules, `lacd verify` checks their state, and `lacd upgrade` supports checking and previewing changes without silently overwriting conflicts.
- **Human control:** LACD does not approve PRs, merge them, or publish releases on your behalf.

These safeguards **reduce risk; they do not guarantee defect-free code**. A developer agent grading its own work in the same conversation is not an independent review.

## Choose your agents and manage your costs

You can use Muse or another capable coding agent for implementation, and an independent web-based AI tool (such as ChatGPT) for review when it can inspect the necessary code and evidence. Different roles can use different tools; LACD does not require one AI provider.

Some providers may offer free usage tiers for low-cost experimentation. **Availability, quotas, pricing, permissions, and GitHub access depend on each provider.** LACD is open source; it does not supply AI accounts, free model usage, or a guarantee that all agents have equivalent capabilities or review quality.

## Quick start

**Prerequisites:** Python 3.11+, Git, and an existing Git project. For the full GitHub PR workflow, you also need a GitHub repository and suitable permissions. An isolated Python virtual environment is recommended.

Install the **current stable release, v1.0.3** ([see Releases](https://github.com/SkyOneBranch/LACD/releases)). Run these commands in your project directory:

```bash
python -m pip install "git+https://github.com/SkyOneBranch/LACD.git@v1.0.3"
lacd --version
lacd init --dry-run
lacd init
lacd status --json
lacd verify --local --strict
```

`init --dry-run` previews the changes; `init` then writes configuration and managed templates. **Review the preview before applying it to your repository.**

`verify --local --strict` checks local conditions only. **It does not prove that GitHub branch protection is enforced.** See the [adoption guide](docs/operations/adopt.md) and [governance protocol](docs/specification/PROTOCOL.md) for remote verification.

If your project uses an older LACD version, its lock file may retain the original upstream repository name. This is historical source information, not something to rewrite manually. Follow the [upgrade guide](docs/operations/upgrade.md) when upgrading.

## Safety boundaries and further reading

- **LACD does not write your application code, auto-merge PRs, manage secrets, or publish releases.**
- **Green CI ≠ enforced GitHub Required Checks.** Remote state that cannot be verified must remain explicitly unverified.
- **Independent AI review ≠ implementer self-review.** It should be isolated from implementation and bound to the final PR commit. A human merge is not the same as a native GitHub `APPROVED` review record.
- **You own your project.** Review changes to managed files and resolve upgrade conflicts before accepting them; keep a rollback path.

Read more: [Adoption and setup](docs/operations/adopt.md) · [Safe upgrades](docs/operations/upgrade.md) · [Governance protocol](docs/specification/PROTOCOL.md) · [Upgrade contract](docs/specification/UPGRADE_CONTRACT.md) · [Agent integration](docs/agents/) · [Contributing](CONTRIBUTING.md)

**LACD is not about making AI infallible. It is about making mistakes easier to find, fix, and evaluate before you decide to ship.**
