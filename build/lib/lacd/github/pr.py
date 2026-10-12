"""Upgrade PR preparation: create branch + commit, push, open PR.

Implements MODULE_CONTRACT §6.2.

Iron rules honored here:
  - Never merges a PR (creation only).
  - Never pushes to the base branch; the upgrade branch is the only
    ref ever pushed, and only when a token is available.
  - Without a token (or when push fails), writes a local patch file
    `.lacd/upgrade-<version>.patch` plus human steps instead of
    inventing remote facts.
  - Secrets are passed to git via `http.extraHeader` in the process
    environment, never embedded in a URL, and never logged.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from lacd.github.remote import API_BASE, _api_request, _NetworkError
from lacd.upgrade.migrate import apply_migration

_PUSH_TIMEOUT = 120
_GIT_TIMEOUT = 60


class PrError(Exception):
    """Preparation failed; working tree was restored to its prior branch."""


@dataclass
class PrReport:
    """Outcome of prepare_upgrade_pr."""

    created: bool
    reused: bool
    pr_url: str | None
    branch: str
    local_patch: str | None = None
    steps: list[str] = field(default_factory=list)


def _validate_repo(repo: str) -> str:
    repo = (repo or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("repo must look like 'owner/name'")
    return repo


def upgrade_branch_name(to_version: str) -> str:
    """Return the upgrade branch name: "lacd/upgrade-to-<version>"."""
    version = str(to_version).strip()
    if not version:
        raise ValueError("to_version must be non-empty")
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", version)
    return f"lacd/upgrade-to-{safe}"


def find_existing_upgrade_pr(repo: str, to_version: str,
                             token: str | None) -> dict | None:
    """Return the first open upgrade PR for this version, or None.

    Best-effort read: no token, network/auth failure, or an empty list
    all yield None. Returns {"number","html_url","title","head","state"}.
    """
    if not token:
        return None
    repo = _validate_repo(repo)
    owner = repo.split("/")[0]
    branch = upgrade_branch_name(to_version)
    head = urllib.parse.quote(f"{owner}:{branch}", safe="")
    url = f"{API_BASE}/repos/{repo}/pulls?state=open&head={head}"
    try:
        status, data = _api_request("GET", url, token)
    except _NetworkError:
        return None
    if status != 200 or not isinstance(data, list) or not data:
        return None
    pr = data[0] if isinstance(data[0], dict) else {}
    return {
        "number": pr.get("number"),
        "html_url": pr.get("html_url"),
        "title": pr.get("title"),
        "head": branch,
        "state": pr.get("state"),
    }


def _git(project_dir: str | Path, *args: str, env: dict | None = None) -> str:
    """Run git; return stdout. Raise PrError on failure (no secrets in text)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrError(f"git {' '.join(args)} failed to run: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        detail = detail[0][:300] if detail else "unknown error"
        raise PrError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _git_push(project_dir: str | Path, branch: str, token: str) -> None:
    """Push the upgrade branch using an Authorization header (no URL secret)."""
    env = dict(os.environ)
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
    env["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {token}"
    _git(project_dir, "push", "-u", "origin", branch, env=env)


def _plan_summary(plan: object) -> dict:
    """Extract human-safe facts from an opaque MigrationPlan (duck-typed)."""
    changes = getattr(plan, "changes", None) or []
    conflicts = getattr(plan, "conflicts", None) or []
    checkpoints = getattr(plan, "human_checkpoints", None) or []
    items = []
    tiers = set()
    for change in changes:
        path = getattr(change, "path", "?")
        action = getattr(change, "action", "?")
        risk = getattr(change, "risk", "?")
        detail = getattr(change, "detail", "")
        items.append({"path": path, "action": action, "risk": risk, "detail": detail})
        tiers.add(risk)
    conflict_items = [
        {"path": getattr(c, "path", "?"), "action": getattr(c, "action", "conflict"),
         "risk": getattr(c, "risk", "?"), "detail": getattr(c, "detail", "")}
        for c in conflicts
    ]
    return {
        "from_version": getattr(plan, "from_version", "unknown"),
        "items": items,
        "conflicts": conflict_items,
        "checkpoints": list(checkpoints),
        "tiers": sorted(str(t) for t in tiers),
    }


def _commit_message(to_version: str, plan: object) -> str:
    summary = _plan_summary(plan)
    lines = [
        f"chore(upgrade): LACD {summary['from_version']} -> {to_version}",
        "",
        f"Plan: {len(summary['items'])} change(s), "
        f"{len(summary['conflicts'])} conflict(s), "
        f"risk tiers: {', '.join(summary['tiers']) or 'none'}.",
        "Generated by lacd upgrade --prepare-pr; human merge required.",
    ]
    return "\n".join(lines)


def _pr_body(to_version: str, plan: object, head_sha: str, base: str,
             branch: str) -> str:
    summary = _plan_summary(plan)
    lines = [
        "## LACD upgrade PR",
        "",
        f"- **From:** `{summary['from_version']}` → **To:** `{to_version}`",
        f"- **Branch:** `{branch}` (base: `{base}`)",
        f"- **HEAD SHA:** `{head_sha}`",
        f"- **Risk tiers:** {', '.join(summary['tiers']) or 'none'}",
        "",
        "### Migration items",
    ]
    if summary["items"]:
        for item in summary["items"]:
            lines.append(
                f"- `{item['path']}` — {item['action']} (risk {item['risk']})"
                + (f": {item['detail']}" if item["detail"] else "")
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("### Unresolved conflicts")
    if summary["conflicts"]:
        for item in summary["conflicts"]:
            lines.append(f"- `{item['path']}`: {item['detail'] or item['action']}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("### Test plan")
    lines.extend([
        "- `lacd verify --strict` on the upgrade branch",
        "- `pytest tests/` (full suite) on the upgrade branch",
        "- `lacd status --json` shows expected state before merge",
    ])
    lines.append("")
    lines.append("### Human checkpoints")
    if summary["checkpoints"]:
        for checkpoint in summary["checkpoints"]:
            lines.append(f"- [ ] {checkpoint}")
    else:
        lines.append("- [ ] Review the migration items above")
    lines.extend([
        "",
        "---",
        "Created by `lacd upgrade --prepare-pr`. LACD never auto-merges: "
        "a human must review and merge this PR.",
    ])
    return "\n".join(lines)


def _create_pr(repo: str, branch: str, base: str, to_version: str,
               plan: object, head_sha: str, token: str) -> str:
    summary = _plan_summary(plan)
    title = f"chore(upgrade): LACD {summary['from_version']} -> {to_version}"
    payload = {
        "title": title,
        "head": branch,
        "base": base,
        "body": _pr_body(to_version, plan, head_sha, base, branch),
        "maintainer_can_modify": False,
    }
    try:
        status, data = _api_request(
            "POST", f"{API_BASE}/repos/{repo}/pulls", token, payload=payload
        )
    except _NetworkError as exc:
        raise PrError(f"create PR failed: {exc}") from exc
    if status not in (200, 201) or not isinstance(data, dict):
        raise PrError(f"create PR failed: HTTP {status}")
    url = data.get("html_url")
    if not url:
        raise PrError("create PR succeeded but returned no html_url")
    return str(url)


def prepare_upgrade_pr(project_dir: str | Path, to_version: str,
                       plan: object, bundle: object, repo: str,
                       token: str | None,
                       base: str = "main") -> PrReport:
    """Prepare an upgrade PR (create-only; never merges).

    1. Reuse an existing open upgrade PR for this version if one exists.
    2. Check the working tree is clean, create branch
       `lacd/upgrade-to-<version>` from the current HEAD, apply the
       migration **on the new branch**, then stage and commit. The clean-tree
       check guarantees the commit contains exactly the migration (plus the
       lock update); nothing is pushed to `base`.
    3. With a token and a successful push: push the branch and open the PR.
       Without a token or push ability: write `.lacd/upgrade-<version>.patch`
       and return human steps.

    The `.lacd/backup/` snapshot written by `apply_migration` is removed
    before staging: on the PR branch the git history itself is the safety
    net, and the commit must contain exactly the migration. (Local
    `lacd upgrade --to` keeps the backup for `rollback_last`.)
    """
    project_dir = Path(project_dir)
    repo = _validate_repo(repo)
    if not base:
        raise ValueError("base must be non-empty")
    branch = upgrade_branch_name(to_version)
    if branch == base:
        raise ValueError("upgrade branch must differ from base branch")

    # 1. Deduplicate: reuse an existing open upgrade PR.
    if token:
        existing = find_existing_upgrade_pr(repo, to_version, token)
        if existing:
            return PrReport(
                created=False,
                reused=True,
                pr_url=existing.get("html_url"),
                branch=branch,
            )

    # 2. Branch + migrate + commit. Working tree must be clean beforehand so
    #    the commit contains exactly the pending migration.
    try:
        original_branch = _git(project_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except PrError as exc:
        raise PrError(f"not a usable git repository: {exc}") from exc
    if _git(project_dir, "status", "--porcelain").strip():
        raise PrError("working tree is not clean; commit or stash changes first")

    try:
        _git(project_dir, "checkout", "-b", branch)
        try:
            result = apply_migration(project_dir, plan, bundle)
        except Exception as exc:
            raise PrError(f"migration failed on upgrade branch: {exc}") from exc
        # The backup snapshot must not enter the PR commit; git history is
        # the rollback mechanism on the branch.
        if getattr(result, "backup_dir", ""):
            shutil.rmtree(result.backup_dir, ignore_errors=True)
        _git(project_dir, "add", "-A")
        staged = _git(project_dir, "diff", "--cached", "--name-only").strip()
        if not staged:
            raise PrError("no changes to commit for the upgrade PR")
        _git(project_dir, "commit", "-m", _commit_message(to_version, plan))
        head_sha = _git(project_dir, "rev-parse", "HEAD").strip()
    except Exception:
        # Best-effort restore; never leave the caller on a half-made branch.
        try:
            _git(project_dir, "checkout", original_branch)
            _git(project_dir, "branch", "-D", branch)
        except PrError:
            pass
        raise

    # 3. Push + open PR, or fall back to a local patch.
    pushed = False
    if token:
        try:
            _git_push(project_dir, branch, token)
            pushed = True
        except PrError:
            pushed = False

    if pushed and token:
        pr_url = _create_pr(repo, branch, base, to_version, plan, head_sha, token)
        return PrReport(created=True, reused=False, pr_url=pr_url, branch=branch)

    safe_version = re.sub(r"[^A-Za-z0-9._-]", "-", str(to_version).strip())
    patch_path = project_dir / ".lacd" / f"upgrade-{safe_version}.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_content = _git(project_dir, "format-patch", "--stdout", "-1", "HEAD")
    patch_path.write_text(patch_content, encoding="utf-8")
    summary = _plan_summary(plan)
    title = f"chore(upgrade): LACD {summary['from_version']} -> {to_version}"
    steps = [
        f"Review the patch: git apply --stat {patch_path.name} "
        f"(in {patch_path.parent})",
        f"From a clean clone: git checkout -b {branch} {base}",
        f"Apply the patch: git apply {patch_path}",
        f"Push the branch: git push -u origin {branch}",
        f"Open a PR against `{base}` with title: {title}",
        "A human must review and merge the PR; LACD never auto-merges.",
    ]
    return PrReport(
        created=False,
        reused=False,
        pr_url=None,
        branch=branch,
        local_patch=str(patch_path),
        steps=steps,
    )
