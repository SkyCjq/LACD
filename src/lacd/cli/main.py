"""LACD command-line interface.

Implements the CLI contract in docs/architecture/MODULE_CONTRACT.md section 3:
subcommands ``init`` / ``doctor`` / ``status`` / ``verify`` / ``upgrade``.

Exit codes (frozen):
    0  success
    1  usage / general error
    2  verification failure (incl. ``--strict`` key dimensions, evidence SHA mismatch)
    3  upgrade rejected (major not accepted, conflict, security policy)

Sibling modules owned by other agents are imported lazily through
:func:`_contract` so that this CLI always calls the frozen contract
signatures (``core.state.compute_state``, ``core.report.build_status_dict`` /
``validate_status_dict``, ``config.schema.load_config``) instead of
re-implementing them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from lacd import __version__
from lacd.cli import templates_dir

DEFAULT_UPSTREAM_REPO = "SkyCjq/LACD"
AGENTS_SECTION_ID = "lacd-agent-protocol"
AGENTS_BEGIN_RE = re.compile(
    r'<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol"[^>]*-->'
    r".*?"
    r"<!-- LACD:MANAGED:END -->",
    re.DOTALL,
)

MANAGED_WORKFLOW_PATH = ".github/workflows/lacd-verify.yml"
MANAGED_WORKFLOW_TEMPLATE = "workflows/lacd-verify.yml"
AGENTS_SECTION_TEMPLATE = "managed/agents-md.section.md"
PROJECT_RULES_PATH = ".lacd/project-rules.yaml"
PROJECT_RULES_TEMPLATE = "project-rules.template.yaml"
LOCK_PATH = ".lacd/lock.json"
CONFIG_PATH = "lacd.yaml"
AGENTS_MD_PATH = "AGENTS.md"


# ---------------------------------------------------------------------------
# Errors / parser
# ---------------------------------------------------------------------------


class UsageError(Exception):
    """Raised for CLI usage errors; mapped to exit code 1."""


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit 1 (contract), not argparse's 2."""

    def error(self, message: str) -> None:  # noqa: D102
        raise UsageError(message)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _template_bytes(rel: str) -> bytes:
    # Single template entry point (MODULE_CONTRACT §9): never hard-code
    # Path(__file__) relatives here.
    return templates_dir().joinpath(*rel.split("/")).read_bytes()


def _resolve_project_dir(path_str: str | None) -> Path:
    p = Path(path_str) if path_str else Path(".")
    return p.expanduser().resolve()


def _confined(path: Path, project_dir: Path) -> Path:
    """Resolve *path* and fail closed if it escapes *project_dir*."""
    resolved = path.resolve()
    try:
        resolved.relative_to(project_dir)
    except ValueError:
        raise UsageError(f"refusing path outside project dir: {path}")
    return resolved


def _contract() -> SimpleNamespace:
    """Lazy import of sibling contract modules (owned by Agent-A).

    Always returns the frozen contract signatures; never re-implements them.
    """
    try:
        from lacd.core.state import DimensionResult, compute_state
        from lacd.core.report import build_status_dict, validate_status_dict
        from lacd.config.schema import load_config
    except ImportError as exc:
        raise ImportError(
            "lacd.core / lacd.config modules are not implemented yet "
            "(owned by Agent-A); status/verify require them"
        ) from exc
    return SimpleNamespace(
        compute_state=compute_state,
        DimensionResult=DimensionResult,
        build_status_dict=build_status_dict,
        validate_status_dict=validate_status_dict,
        load_config=load_config,
    )


def _get_check_remote_enforcement():
    """Lazy import of the Agent-D remote-enforcement check."""
    from lacd.github.remote import check_remote_enforcement

    return check_remote_enforcement


def _state_name(state) -> str:
    return getattr(state, "value", state)


# ---------------------------------------------------------------------------
# init: desired state
# ---------------------------------------------------------------------------


def _default_config_dict() -> dict:
    # Frozen defaults, MODULE_CONTRACT.md section 2.1.
    return {
        "schema_version": "1",
        "lacd": {
            "enabled": True,
            "mode": "lacd",
            "policy_version": "1",
            "upstream_repo": DEFAULT_UPSTREAM_REPO,
        },
        "governance": {
            "pull_request_required": True,
            "human_merge_required": True,
            "risk_tiers": ["T0", "T1", "T2"],
        },
        "verification": {
            "bind_to_commit_sha": True,
            "fail_closed": True,
            "github_enforcement_required": True,
        },
        "agents": {"protocol": "lacd", "auto_detect": True},
        "project": {"extensions": PROJECT_RULES_PATH},
    }


def _canonical_lacd_yaml() -> bytes:
    import yaml

    header = (
        "# LACD configuration (schema_version \"1\").\n"
        "# Created by `lacd init`. See docs/specification/PROTOCOL.md.\n"
        "# Required keys must not be removed.\n"
    )
    body = yaml.safe_dump(
        _default_config_dict(), sort_keys=False, allow_unicode=True
    )
    return (header + body).encode("utf-8")


def _build_lock_dict() -> dict:
    # Frozen lock format, MODULE_CONTRACT.md section 2.2.
    wf_template = _template_bytes(MANAGED_WORKFLOW_TEMPLATE)
    # Shared-file hash follows the drift-check definition in
    # lacd.config.lockfile (Agent-A): exact embedded section text.
    section_inner = _agents_section_inner().encode("utf-8")
    return {
        "lock_format_version": "1",
        "lacd_cli_version": __version__,
        "source": {
            "type": "git-tag",
            "repo": DEFAULT_UPSTREAM_REPO,
            "ref": "v" + __version__,
            # commit is unknown at init time: record null honestly, never invent one.
            "commit": None,
        },
        "schema_version": "1",
        "policy_version": "1",
        "template_version": __version__,
        "managed_files": [
            {
                "path": MANAGED_WORKFLOW_PATH,
                "sha256": _sha256_bytes(wf_template),
                "template": MANAGED_WORKFLOW_TEMPLATE,
                "template_version": __version__,
                "merge": "replace",
            }
        ],
        "shared_files": [
            {
                "path": AGENTS_MD_PATH,
                "section": AGENTS_SECTION_ID,
                "sha256": _sha256_bytes(section_inner),
                "template": AGENTS_SECTION_TEMPLATE,
                "template_version": __version__,
                "merge": "marked-section",
            }
        ],
        "generated_at": _utc_now_iso(),
        "generated_by": f"lacd {__version__}",
    }


def _agents_section_inner() -> str:
    """Exact text embedded between the BEGIN and END markers.

    This is the canonical "section content" whose SHA-256 goes into
    lock.json ``shared_files`` entries. It matches the drift-check
    definition in ``lacd.config.lockfile`` (Agent-A): the exact text
    between the end of the BEGIN marker and the start of the END marker,
    no trimming or newline normalisation.
    """
    body = _template_bytes(AGENTS_SECTION_TEMPLATE).decode("utf-8")
    if not body.endswith("\n"):
        body += "\n"
    return "\n" + body


def _agents_block() -> str:
    begin = (
        f'<!-- LACD:MANAGED:BEGIN section="{AGENTS_SECTION_ID}" '
        f'version="{__version__}" -->'
    )
    end = "<!-- LACD:MANAGED:END -->"
    return f"{begin}{_agents_section_inner()}{end}\n"


def _agents_section_status(agents_path: Path) -> str:
    """One of: missing | current | stale | no-markers."""
    if not agents_path.exists():
        return "missing"
    text = agents_path.read_text(encoding="utf-8")
    m = AGENTS_BEGIN_RE.search(text)
    if not m:
        return "no-markers"
    block = _agents_block()
    if m.group(0).strip() == block.strip():
        return "current"
    return "stale"


def _init_is_current(project_dir: Path) -> bool:
    """True when a previous init already produced exactly the desired state."""
    lock_path = project_dir / LOCK_PATH
    if not lock_path.exists():
        return False
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if lock.get("template_version") != __version__:
        return False
    wf_path = project_dir / MANAGED_WORKFLOW_PATH
    if not wf_path.exists():
        return False
    if _sha256_file(wf_path) != _sha256_bytes(_template_bytes(MANAGED_WORKFLOW_TEMPLATE)):
        return False
    if _agents_section_status(project_dir / AGENTS_MD_PATH) != "current":
        return False
    if not (project_dir / PROJECT_RULES_PATH).exists():
        return False
    return True


def _plan_init(project_dir: Path, force: bool) -> list[dict]:
    """Compute the per-file init plan without writing anything."""
    plans: list[dict] = []

    cfg_path = project_dir / CONFIG_PATH
    if cfg_path.exists():
        if cfg_path.read_bytes() == _canonical_lacd_yaml() and not force:
            plans.append({"path": CONFIG_PATH, "action": "skip",
                          "detail": "already matches lacd init defaults"})
        elif force:
            plans.append({"path": CONFIG_PATH, "action": "overwrite",
                          "detail": "existing file backed up first (--force)"})
        else:
            plans.append({"path": CONFIG_PATH, "action": "refuse",
                          "detail": "exists with different content; use --force to overwrite"})
    else:
        plans.append({"path": CONFIG_PATH, "action": "create",
                      "detail": "default lacd.yaml (schema_version 1)"})

    lock_path = project_dir / LOCK_PATH
    if lock_path.exists() and not force:
        plans.append({"path": LOCK_PATH, "action": "refresh",
                      "detail": "rewrite with current CLI/template versions"})
    else:
        plans.append({"path": LOCK_PATH, "action": "create",
                      "detail": f"source git-tag {DEFAULT_UPSTREAM_REPO}@v{__version__}"})

    rules_path = project_dir / PROJECT_RULES_PATH
    if rules_path.exists():
        plans.append({"path": PROJECT_RULES_PATH, "action": "skip",
                      "detail": "project-owned; never overwritten"})
    else:
        plans.append({"path": PROJECT_RULES_PATH, "action": "create",
                      "detail": "empty project-rules template"})

    wf_path = project_dir / MANAGED_WORKFLOW_PATH
    if wf_path.exists():
        if _sha256_file(wf_path) == _sha256_bytes(_template_bytes(MANAGED_WORKFLOW_TEMPLATE)):
            plans.append({"path": MANAGED_WORKFLOW_PATH, "action": "skip",
                          "detail": "already up to date"})
        else:
            plans.append({"path": MANAGED_WORKFLOW_PATH, "action": "update",
                          "detail": "managed file (merge: replace)"})
    else:
        plans.append({"path": MANAGED_WORKFLOW_PATH, "action": "create",
                      "detail": "managed CI workflow template"})

    agents_path = project_dir / AGENTS_MD_PATH
    sec = _agents_section_status(agents_path)
    if sec == "current":
        plans.append({"path": AGENTS_MD_PATH, "action": "skip",
                      "detail": "managed section already current"})
    elif sec == "missing":
        plans.append({"path": AGENTS_MD_PATH, "action": "create",
                      "detail": "new file with managed section"})
    elif sec == "no-markers":
        plans.append({"path": AGENTS_MD_PATH, "action": "merge-section",
                      "detail": "append managed section; user content untouched"})
    else:
        plans.append({"path": AGENTS_MD_PATH, "action": "merge-section",
                      "detail": "refresh managed section in place; user content untouched"})
    return plans


def _backup_init(project_dir: Path) -> Path:
    """Back up pre-existing init-managed files before --force overwrite."""
    dest = project_dir / ".lacd" / "backup" / f"init-{_utc_stamp()}"
    for rel in (CONFIG_PATH, LOCK_PATH, PROJECT_RULES_PATH, AGENTS_MD_PATH):
        src = project_dir / rel
        if src.exists():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    return dest


def _merge_agents_md(agents_path: Path) -> str:
    """Create/append/refresh the managed section. Returns the action taken."""
    block = _agents_block()
    if not agents_path.exists():
        agents_path.write_text(block, encoding="utf-8")
        return "created"
    text = agents_path.read_text(encoding="utf-8")
    m = AGENTS_BEGIN_RE.search(text)
    if m:
        if m.group(0).strip() == block.strip():
            return "unchanged"
        new_text = AGENTS_BEGIN_RE.sub(lambda _m: block.strip(), text, count=1)
        if not new_text.endswith("\n"):
            new_text += "\n"
        agents_path.write_text(new_text, encoding="utf-8")
        return "updated-section"
    if text and not text.endswith("\n"):
        text += "\n"
    agents_path.write_text(text + "\n" + block, encoding="utf-8")
    return "appended-section"


def _write_text_confined(project_dir: Path, rel: str, data: bytes) -> None:
    target = _confined(project_dir / rel, project_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def cmd_init(args: argparse.Namespace) -> int:
    project_dir = _resolve_project_dir(args.path)
    project_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = project_dir / CONFIG_PATH
    canonical = _canonical_lacd_yaml()

    if cfg_path.exists() and not args.force:
        if cfg_path.read_bytes() == canonical and _init_is_current(project_dir):
            print(f"already-initialized: {project_dir}")
            return 0
        if cfg_path.read_bytes() != canonical:
            print(
                f"error: {CONFIG_PATH} already exists with different content; "
                "refusing to overwrite (use --force to replace with a backup)",
                file=sys.stderr,
            )
            return 1
        # Canonical lacd.yaml but the rest is stale/partial: refresh it below.

    plans = _plan_init(project_dir, force=args.force)

    if args.dry_run:
        print(f"DRY-RUN init for {project_dir}: no files will be written")
        for p in plans:
            print(f"  {p['action']:14s} {p['path']}  ({p['detail']})")
        if args.force and cfg_path.exists():
            print("  backup          .lacd/backup/init-<timestamp>/  (existing files first)")
        return 0

    backup_dir = None
    if args.force and (
        cfg_path.exists()
        or (project_dir / LOCK_PATH).exists()
        or (project_dir / AGENTS_MD_PATH).exists()
    ):
        backup_dir = _backup_init(project_dir)

    # 1. lacd.yaml
    _write_text_confined(project_dir, CONFIG_PATH, canonical)
    # 2. lock.json
    _write_text_confined(
        project_dir, LOCK_PATH,
        (json.dumps(_build_lock_dict(), indent=2) + "\n").encode("utf-8"),
    )
    # 3. project-rules.yaml (only when absent: project-owned)
    rules_path = project_dir / PROJECT_RULES_PATH
    if not rules_path.exists():
        _write_text_confined(
            project_dir, PROJECT_RULES_PATH, _template_bytes(PROJECT_RULES_TEMPLATE)
        )
    # 4. managed workflow (merge: replace)
    _write_text_confined(
        project_dir, MANAGED_WORKFLOW_PATH,
        _template_bytes(MANAGED_WORKFLOW_TEMPLATE),
    )
    # 5. AGENTS.md managed section (merge: marked-section; never delete user content)
    agents_action = _merge_agents_md(project_dir / AGENTS_MD_PATH)

    print(f"initialized LACD {__version__} in {project_dir}")
    print(f"  wrote {CONFIG_PATH}, {LOCK_PATH}, {MANAGED_WORKFLOW_PATH}")
    print(f"  AGENTS.md managed section: {agents_action}")
    if not rules_path.exists():
        print(f"  wrote {PROJECT_RULES_PATH} (project-owned template)")
    if backup_dir is not None:
        print(f"  backup of previous files: {backup_dir}")
    print("Note: lacd never runs git add/commit/push; review and commit yourself.")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _check_python() -> tuple[str, str]:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return ("PASS" if ok else "FAIL",
            f"python {v.major}.{v.minor}.{v.micro} (>= 3.11 required)")


def _check_git() -> tuple[str, str]:
    if shutil.which("git") is None:
        return "FAIL", "git not found on PATH"
    try:
        out = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=10
        )
        if out.returncode == 0:
            return "PASS", out.stdout.strip()
        return "FAIL", "git --version failed"
    except (OSError, subprocess.SubprocessError) as exc:
        return "FAIL", f"git not runnable: {exc}"


def _check_gh() -> tuple[str, str]:
    if shutil.which("gh") is None:
        return "WARN", "gh CLI not found; remote checks will degrade to unverified"
    try:
        out = subprocess.run(
            ["gh", "--version"], capture_output=True, text=True, timeout=10
        )
        first = out.stdout.strip().splitlines()[0] if out.stdout else "gh"
        return "PASS", first
    except (OSError, subprocess.SubprocessError) as exc:
        return "WARN", f"gh not runnable: {exc}"


def _check_write(project_dir: Path) -> tuple[str, str]:
    try:
        fd, tmp = tempfile.mkstemp(prefix=".lacd-doctor-", dir=str(project_dir))
        os.close(fd)
        os.unlink(tmp)
        return "PASS", f"directory is writable: {project_dir}"
    except OSError as exc:
        return "FAIL", f"directory not writable: {exc}"


def _check_github_tcp() -> tuple[str, str]:
    try:
        with socket.create_connection(("api.github.com", 443), timeout=10):
            pass
        return "PASS", "TCP api.github.com:443 reachable (10s timeout)"
    except OSError as exc:
        return "WARN", f"api.github.com:443 unreachable ({exc}); remote checks degrade"


def cmd_doctor(args: argparse.Namespace) -> int:
    project_dir = _resolve_project_dir(args.path)
    project_dir.mkdir(parents=True, exist_ok=True)

    checks = [
        ("python", *_check_python()),
        ("git", *_check_git()),
        ("gh", *_check_gh()),
        ("write-permission", *_check_write(project_dir)),
        ("github-connectivity", *_check_github_tcp()),
    ]
    print(f"lacd doctor for {project_dir}")
    failed = 0
    for name, status, detail in checks:
        print(f"[{status}] {name}: {detail}")
        if status == "FAIL":
            failed += 1
    if failed:
        print(f"doctor: {failed} failing check(s)")
        return 1
    print("doctor: all required checks passed")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _fmt_dim(status: str, reasons: list) -> str:
    if reasons:
        return f"{status} ({', '.join(str(r) for r in reasons)})"
    return status


def cmd_status(args: argparse.Namespace) -> int:
    project_dir = _resolve_project_dir(args.path)
    try:
        contract = _contract()
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        d = contract.build_status_dict(str(project_dir), remote=None)
    except Exception as exc:  # fail-closed: never print a fabricated status
        print(f"error: could not build status: {exc}", file=sys.stderr)
        return 1

    if args.json:
        errors = contract.validate_status_dict(d)
        if errors:
            print("error: built status dict failed schema validation:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    dims = d.get("dimensions", {})
    vc = d.get("version_consistency", {})
    head = d.get("head_sha") or "unknown"
    if isinstance(head, str) and len(head) > 12:
        head = head[:12]
    print(f"LACD status for {d.get('project_dir', project_dir)}")
    print(f"  state: {_state_name(d.get('state'))}")
    print(f"  cli: {d.get('lacd_cli_version')} | branch: {d.get('branch') or 'unknown'} "
          f"| head: {head} | mode: {d.get('mode')}")
    for name in ("local_validation", "remote_enforcement", "drift", "blocking"):
        dim = dims.get(name, {})
        print(f"  {name}: {_fmt_dim(dim.get('status', '?'), dim.get('reasons', []))}")
    print(f"  version_consistency: {vc.get('status')} - {vc.get('detail')}")
    steps = d.get("next_steps", [])
    if steps:
        print("  next_steps:")
        for s in steps:
            print(f"    - {s}")
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def _detect_repo_branch(project_dir: Path) -> tuple[str | None, str | None]:
    """Return (owner/repo, branch) from git, or (None, None) when unavailable."""

    def _run(*git_args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(project_dir), *git_args],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None

    branch = _run("rev-parse", "--abbrev-ref", "HEAD")
    url = _run("remote", "get-url", "origin")
    repo = None
    if url:
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url.strip())
        if m:
            repo = m.group(1)
    return repo, branch


def _get_trusted_required_checks(project_dir: Path, trusted_sha: str) -> tuple[list, list]:
    """Read required_checks from the TRUSTED BASE SHA, not working tree.

    This is the core trust boundary fix (Q2 B2): the PR cannot lower its own
    bar by committing weakened config to its own HEAD, because we read from
    origin/main (or other verified base), not from the PR's files.

    Returns (checks, warnings). Uses git show <sha>:<path>.
    """
    warnings: list = []

    def _validate(raw) -> tuple[list | None, bool]:
        if raw is None:
            return None, False
        if isinstance(raw, str):
            warnings.append("POLICY_CONFIG_UNPARSEABLE")
            return None, True
        if not isinstance(raw, list):
            warnings.append("POLICY_CONFIG_UNPARSEABLE")
            return None, True
        cleaned = []
        for c in raw:
            if isinstance(c, str) and c:
                cleaned.append(c)
            elif "INVALID_CHECK_ENTRY" not in warnings:
                warnings.append("INVALID_CHECK_ENTRY")
        seen, deduped = set(), []
        for c in cleaned:
            if c not in seen:
                seen.add(c); deduped.append(c)
            elif "DUPLICATE_CHECKS_REMOVED" not in warnings:
                warnings.append("DUPLICATE_CHECKS_REMOVED")
        return deduped, True

    # 1. lacd.yaml from trusted SHA (SEC-R4-01: git errors are fail-closed)
    yaml_text, yaml_err = _read_file_at_sha(project_dir, trusted_sha, "lacd.yaml")
    if yaml_err:
        # Git read failed -> UNVERIFIED, do NOT fall back to weaker policy
        return [], warnings + [yaml_err]
    if yaml_text is not None:
        try:
            import yaml as _yaml
            from lacd.config.schema import validate_config_dict
            raw_data = _yaml.safe_load(yaml_text)
            errors = validate_config_dict(raw_data)
            if errors:
                return [], warnings + ["POLICY_CONFIG_UNPARSEABLE"]
            gov_raw = (raw_data.get("governance") or {}) if isinstance(raw_data, dict) else {}
            yaml_raw = gov_raw.get("required_checks", None)
            checks, explicit = _validate(yaml_raw)
            if explicit:
                return checks if checks is not None else [], warnings
        except Exception:
            return [], warnings + ["POLICY_CONFIG_UNPARSEABLE"]

    # 2. lock.json from trusted SHA (SEC-R4-01: git errors are fail-closed)
    lock_text, lock_err = _read_file_at_sha(project_dir, trusted_sha, ".lacd/lock.json")
    if lock_err:
        return [], warnings + [lock_err]
    if lock_text is not None:
        try:
            import json
            from lacd.config.lockfile import validate_lock_dict, LockFile
            data = json.loads(lock_text)
            errors = validate_lock_dict(data)
            if errors:
                return [], warnings + ["POLICY_CONFIG_UNPARSEABLE"]
            lock = LockFile.from_dict(data)
            checks, explicit = _validate(lock.required_checks)
            if checks is not None:
                return checks, warnings
        except Exception:
            return [], warnings + ["POLICY_CONFIG_UNPARSEABLE"]

    return [], warnings


def _get_required_checks(project_dir: Path) -> tuple[list, list]:
    """Return (checks, warnings) for the enforcement policy.

    Source priority (D2 v3): ``lacd.yaml`` governance.required_checks >
    ``.lacd/lock.json`` required_checks (via LockFile.load) > empty.

    Explicit empty list [] in yaml does NOT fall back to lock (explicit wins).
    Missing/None in yaml DOES fall back to lock.

    Input validation:
    - String "ci" (not list) -> invalid, POLICY_CONFIG_UNPARSEABLE
    - Non-string entries -> filtered with INVALID_CHECK_ENTRY warning
    - Parse errors -> ([], ["POLICY_CONFIG_UNPARSEABLE"])
    - Duplicates removed with "DUPLICATE_CHECKS_REMOVED" warning
    """
    warnings: list = []

    def _validate(raw, source: str) -> tuple[list | None, bool]:
        """Return (checks, is_explicit). None checks = not present/invalid."""
        if raw is None:
            return None, False  # not present -> fall through
        if isinstance(raw, str):
            # "ci" is not ["ci"]; reject to avoid ["c","i"] bug
            warnings.append("POLICY_CONFIG_UNPARSEABLE")
            return None, True  # explicit but invalid -> do not fall back
        if not isinstance(raw, list):
            warnings.append("POLICY_CONFIG_UNPARSEABLE")
            return None, True
        # raw is a list (possibly empty)
        cleaned = []
        for c in raw:
            if isinstance(c, str) and c:
                cleaned.append(c)
            else:
                if "INVALID_CHECK_ENTRY" not in warnings:
                    warnings.append("INVALID_CHECK_ENTRY")
        # Deduplicate
        seen = set()
        deduped = []
        for c in cleaned:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
            elif "DUPLICATE_CHECKS_REMOVED" not in warnings:
                warnings.append("DUPLICATE_CHECKS_REMOVED")
        return deduped, True  # explicit (even if [])

    # 1. lacd.yaml governance.required_checks
    # NOTE: check raw yaml dict (not parsed dataclass) to distinguish
    # explicit [] from missing field (dataclass default is []).
    try:
        from lacd.config.schema import load_config, ConfigNotFound
        import yaml as _yaml
        yaml_path = Path(project_dir) / "lacd.yaml"
        if not yaml_path.is_file():
            raise ConfigNotFound(f"no lacd.yaml in {project_dir}")
        raw_data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        # Validate via load_config (raises on invalid; mockable in tests)
        load_config(str(project_dir))
        gov_raw = (raw_data.get("governance") or {}) if isinstance(raw_data, dict) else {}
        yaml_raw = gov_raw.get("required_checks", None)  # None = not present
        checks, explicit = _validate(yaml_raw, "yaml")
        if explicit:
            return checks if checks is not None else [], warnings
        # not explicit -> fall through to lock
    except ConfigNotFound:
        pass  # no yaml -> try lock
    except Exception:
        return [], ["POLICY_CONFIG_UNPARSEABLE"]

    # 2. lock.json via LockFile.load() (legitimate chain, not raw JSON)
    try:
        from lacd.config.lockfile import LockFile
        lock = LockFile.load(project_dir)
        lock_raw = lock.required_checks
        checks, explicit = _validate(lock_raw, "lock")
        if checks is not None:
            return checks, warnings
        return [], warnings
    except FileNotFoundError:
        return [], warnings  # no lock -> empty, no warning
    except Exception:
        return [], warnings + ["POLICY_CONFIG_UNPARSEABLE"]


def _check_config_trust(project_dir: Path) -> tuple[str, list, str | None]:
    """Resolve trusted baseline for policy.

    Returns (trusted_sha, warnings, error).
    - trusted_sha: 40-char SHA of origin/main or merge-base, or None
    - warnings: list of warning codes
    - error: error code if trust cannot be established, else None

    Trust root: the protected base branch at a verified SHA.
    Priority (SEC-R5-03):
    1. LACD_TRUSTED_BASE_SHA env var (CI binds from GitHub PR metadata;
       not forgeable by PR content since env comes from workflow, not PR files)
    2. git rev-parse origin/main (local ref; documented limitation: a local
       attacker with git write access can move this ref - see docs)

    The PR's own HEAD is NEVER the trust root.
    """
    import os
    import subprocess

    def _valid_sha(s: str) -> bool:
        return len(s) == 40 and all(c in "0123456789abcdef" for c in s.lower())

    def _verify_commit(project_dir: Path, sha: str) -> bool:
        try:
            r = subprocess.run(
                ["git", "cat-file", "-t", sha],
                cwd=str(project_dir), capture_output=True, text=True, timeout=10)
            return r.returncode == 0 and r.stdout.strip() == "commit"
        except Exception:
            return False

    # 1. Explicit env binding (preferred in CI)
    env_sha = (os.environ.get("LACD_TRUSTED_BASE_SHA") or "").strip().lower()
    if env_sha:
        if not _valid_sha(env_sha):
            return None, [], "POLICY_BASE_SHA_UNVERIFIED"
        if not _verify_commit(project_dir, env_sha):
            return None, [], "POLICY_BASE_SHA_UNVERIFIED"
        return env_sha, ["TRUSTED_BASE_FROM_ENV"], None

    # 2. Local origin/main (fallback; see documented limitation)
    try:
        r = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None, [], "POLICY_TRUST_ANCHOR_MISSING"
        base_sha = r.stdout.strip()
        if len(base_sha) != 40 or not all(c in "0123456789abcdef" for c in base_sha):
            return None, [], "POLICY_BASE_SHA_UNVERIFIED"

        # Verify the SHA is actually reachable (not a stale ref)
        r2 = subprocess.run(
            ["git", "cat-file", "-t", base_sha],
            cwd=str(project_dir), capture_output=True, text=True, timeout=10)
        if r2.returncode != 0 or r2.stdout.strip() != "commit":
            return None, [], "POLICY_BASE_SHA_UNVERIFIED"

        return base_sha, [], None
    except subprocess.TimeoutExpired:
        return None, [], "POLICY_GIT_TIMEOUT"
    except Exception:
        return None, [], "POLICY_GIT_ERROR"


def _read_file_at_sha(project_dir: Path, sha: str, rel_path: str) -> tuple[str | None, str | None]:
    """Read file content at a specific commit SHA.

    Returns (content, error):
    - (text, None): file read successfully
    - (None, None): file confirmed not to exist at this SHA (git show exit 128 with "does not exist")
    - (None, "POLICY_GIT_ERROR"): git failed (other non-zero exit or exception)
    - (None, "POLICY_GIT_TIMEOUT"): git timed out

    Callers MUST NOT treat git errors as "file not found" (SEC-R4-01).
    """
    import subprocess
    try:
        r = subprocess.run(
            ["git", "show", f"{sha}:{rel_path}"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout, None
        # Distinguish "not exist" from other git errors (SEC-R5-01).
        # ONLY "does not exist" / "path does not exist" means confirmed absent.
        # "bad revision" is a revision parse error -> git error, NOT "not found".
        stderr = (r.stderr or "").lower()
        if "does not exist" in stderr or "path does not exist" in stderr:
            return None, None  # confirmed absent
        return None, "POLICY_GIT_ERROR"
    except subprocess.TimeoutExpired:
        return None, "POLICY_GIT_TIMEOUT"
    except Exception:
        return None, "POLICY_GIT_ERROR"


def _remote_dimension(contract, project_dir: Path):
    """Compute the remote_enforcement dimension.

    Returns (DimensionResult, note). Never raises for a missing module or a
    missing repo: those degrade to unverified with a reason code (AT-07).

    v1.0.2 (FR-102-02): always constructs an explicit ``EnforcementPolicy``
    from the project's verified configuration and passes it to
    ``check_remote_enforcement``. The ``policy=None`` legacy path is never
    used on this path. When no required checks are configured, the dimension
    degrades to UNVERIFIED (fail-closed, no false ENFORCED).
    """
    try:
        check_remote_enforcement = _get_check_remote_enforcement()
    except ImportError:
        dim = contract.DimensionResult(
            status="unverified", reasons=["REMOTE_NOT_CHECKED"]
        )
        note = ("remote check module lacd.github.remote is not implemented yet "
                "(Agent-D pending); remote dimension degraded to unverified")
        return dim, note
    repo, branch = _detect_repo_branch(project_dir)
    if repo is None:
        return (contract.DimensionResult(status="unverified",
                                          reasons=["NO_GITHUB_REMOTE"]),
                "no git origin remote pointing at GitHub; remote enforcement "
                "cannot be checked")

    # Trust boundary (Q2 B2): resolve trusted base SHA FIRST.
    # Policy MUST come from origin/main (protected base), NEVER from PR working tree.
    trusted_sha, trust_warnings, trust_error = _check_config_trust(project_dir)
    if trust_error:
        # Cannot establish trust root -> UNVERIFIED (fail-closed, never ENFORCED)
        return (contract.DimensionResult(
                    status="unverified",
                    reasons=[trust_error] + trust_warnings),
                f"cannot establish trusted policy baseline: {trust_error}")

    # Read policy from TRUSTED SHA, not working tree
    required_checks, check_warnings = _get_trusted_required_checks(project_dir, trusted_sha)
    all_warnings = trust_warnings + check_warnings

    # SEC-R5-02: any policy read/config error -> UNVERIFIED immediately.
    # Do NOT continue to remote enforcement check with broken policy.
    _fatal_policy_errors = {"POLICY_GIT_ERROR", "POLICY_GIT_TIMEOUT",
                            "POLICY_CONFIG_UNPARSEABLE"}
    _hit = _fatal_policy_errors.intersection(all_warnings)
    if _hit:
        return (contract.DimensionResult(
                    status="unverified",
                    reasons=sorted(_hit) + [w for w in all_warnings if w not in _hit]),
                f"policy resolution failed ({', '.join(sorted(_hit))}); "
                "remote enforcement cannot be verified (fail-closed)")

    from lacd.github.remote import EnforcementPolicy
    policy = EnforcementPolicy(
        policy_version="1",
        require_pull_request=True,
        required_checks=required_checks,
        allow_bypass=False,
        human_merge_required=True,
    )
    try:
        rep = check_remote_enforcement(repo, branch or "main", policy=policy,
                                       token=None, timeout=15)
    except Exception as exc:
        return (contract.DimensionResult(status="unverified",
                                          reasons=["REMOTE_NETWORK_ERROR"]),
                f"remote check raised {type(exc).__name__}: {exc}")
    # Fail-closed: with no configured required checks, an "enforced" verdict
    # would be a false positive (nothing was actually required). Downgrade.
    if not required_checks and rep.status == "enforced":
        return (contract.DimensionResult(
                    status="unverified",
                    reasons=["REQUIRED_CHECKS_UNCONFIGURED"] + all_warnings + list(rep.reasons)),
                "no required checks configured for this project; remote "
                "enforcement cannot be verified (fail-closed)")
    # Propagate warnings (trusted base SHA is in all_warnings via trust resolution)
    all_reasons = all_warnings + list(rep.reasons)
    return (contract.DimensionResult(status=rep.status,
                                      reasons=all_reasons),
            None)


def _check_evidence(args: argparse.Namespace, contract, project_dir: Path) -> int:
    ev_path = Path(args.evidence)
    try:
        raw = ev_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read evidence file {ev_path}: {exc}", file=sys.stderr)
        return 1
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: evidence file is not valid JSON: {exc}", file=sys.stderr)
        return 1
    ev_sha = ev.get("head_sha") if isinstance(ev, dict) else None
    if not ev_sha:
        print("error: evidence file has no head_sha field", file=sys.stderr)
        return 1
    try:
        current = contract.build_status_dict(str(project_dir), remote=None).get("head_sha")
    except Exception as exc:
        print(f"error: could not determine current head_sha: {exc}", file=sys.stderr)
        return 1
    if ev_sha != current:
        print(
            f"EVIDENCE_SHA_MISMATCH: evidence head_sha={ev_sha} "
            f"!= current head_sha={current}",
            file=sys.stderr,
        )
        return 2
    print(f"evidence: OK (head_sha={current})")
    return 0


def _strict_failures(contract, project_dir: Path, report,
                     local_only: bool = False) -> list[str]:
    failures: list[str] = []
    lv = report.local_validation
    if lv.status != "pass":
        failures.append(f"local_validation={_fmt_dim(lv.status, lv.reasons)}")
    bl = report.blocking
    if bl.status != "none":
        failures.append(f"blocking={_fmt_dim(bl.status, bl.reasons)}")
    dr = report.drift
    if dr.status != "clean":
        failures.append(f"drift={_fmt_dim(dr.status, dr.reasons)}")
    if local_only:
        # --local is an explicit, auditable choice to skip the remote check
        # (ADR-0009). The remote dimension stays unverified/LOCAL_ONLY and is
        # never counted as a pass -- but it must not fail strict either,
        # otherwise low-privilege CI (R07) could never go green.
        return failures
    try:
        cfg = contract.load_config(str(project_dir))
        required = bool(cfg.verification.github_enforcement_required)
    except Exception:
        # Fail closed: when the policy cannot be read, treat enforcement as required.
        required = True
    rs = report.remote_enforcement.status
    if required and rs != "enforced":
        failures.append(
            f"remote_enforcement={_fmt_dim(rs, report.remote_enforcement.reasons)} "
            "but github_enforcement_required=true"
        )
    elif not required and rs == "not_enforced":
        failures.append(f"remote_enforcement={_fmt_dim(rs, report.remote_enforcement.reasons)}")
    return failures


def _local_only_dimension(contract):
    """The remote_enforcement dimension for `verify --local`.

    No network is touched; the dimension is honestly unverified with the
    LOCAL_ONLY reason code (ADR-0009, D3). Never claims ENFORCED.
    """
    dim = contract.DimensionResult(status="unverified", reasons=["LOCAL_ONLY"])
    note = ("remote check skipped (--local): no network requests were made; "
            "remote enforcement is NOT verified, only local dimensions")
    return dim, note


def cmd_verify(args: argparse.Namespace) -> int:
    if args.local and args.remote:
        raise UsageError("--local and --remote are mutually exclusive")
    project_dir = _resolve_project_dir(args.path)
    try:
        contract = _contract()
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.evidence:
        rc = _check_evidence(args, contract, project_dir)
        if rc != 0:
            return rc

    try:
        if args.local:
            remote_dim, remote_note = _local_only_dimension(contract)
        else:
            # --remote or no flag: v1.0.0 behavior (attempt remote, degrade on
            # failure). Backward compatible.
            remote_dim, remote_note = _remote_dimension(contract, project_dir)
        report = contract.compute_state(str(project_dir), remote=remote_dim)
    except Exception as exc:
        print(f"error: could not compute state: {exc}", file=sys.stderr)
        return 1

    print(f"LACD verify for {project_dir}")
    if args.local:
        print("  mode: local-only (remote enforcement not queried)")
    print(f"  state: {_state_name(report.state)}")
    print(f"  local_validation: {_fmt_dim(report.local_validation.status, report.local_validation.reasons)}")
    remote_line = (f"  remote_enforcement: "
                   f"{_fmt_dim(report.remote_enforcement.status, report.remote_enforcement.reasons)}")
    if remote_note:
        remote_line += f"  [{remote_note}]"
    print(remote_line)
    print(f"  drift: {_fmt_dim(report.drift.status, report.drift.reasons)}")
    print(f"  blocking: {_fmt_dim(report.blocking.status, report.blocking.reasons)}")
    vc = report.version_consistency or {}
    print(f"  version_consistency: {vc.get('status')} - {vc.get('detail')}")

    if args.strict:
        failures = _strict_failures(contract, project_dir, report,
                                    local_only=args.local)
        if failures:
            print("STRICT FAILED:")
            for f in failures:
                print(f"  - {f}")
            return 2
        if args.local:
            print("STRICT: all local dimensions satisfied "
                  "(remote not queried -- --local mode)")
        else:
            print("STRICT: all key dimensions satisfied")
        return 0
    return 0


# ---------------------------------------------------------------------------
# upgrade (Agent-C owns lacd.upgrade.*; CLI only parses args per contract §3)
# ---------------------------------------------------------------------------


def cmd_upgrade(args: argparse.Namespace) -> int:
    if args.check and (args.dry_run or args.to or args.prepare_pr
                       or args.accept_major or args.yes):
        # --template-dir is a source selector, allowed with --check for
        # offline/test use (refinement of the frozen surface, documented).
        raise UsageError("--check cannot be combined with --dry-run/--to/--prepare-pr/--accept-major/--yes")
    if args.dry_run and not args.to:
        raise UsageError("--dry-run requires --to VERSION")
    if args.prepare_pr and (not args.to or args.dry_run):
        raise UsageError("--prepare-pr requires a real upgrade run: --to VERSION without --dry-run")
    if not args.check and not args.to:
        raise UsageError("one of --check or --to VERSION is required")

    # ---- real upgrade engine (contract §5) ----
    project_dir = _resolve_project_dir(args.path)
    return _run_upgrade(project_dir, args)


def _load_project_config(project_dir: Path):
    """Load lacd.yaml + lock, or raise UpgradeSetupError with a clear message."""
    from lacd.config.schema import load_config, ConfigNotFound, ConfigInvalid
    from lacd.config.lockfile import LockFile, LockCorruptError
    try:
        config = load_config(project_dir)
    except ConfigNotFound:
        raise UpgradeSetupError(
            "no lacd.yaml found; run 'lacd init' first")
    except ConfigInvalid as exc:
        raise UpgradeSetupError(f"invalid lacd.yaml: {exc}")
    try:
        lock = LockFile.load(project_dir)
    except FileNotFoundError:
        raise UpgradeSetupError(
            "no .lacd/lock.json found; run 'lacd init' first")
    except LockCorruptError as exc:
        raise UpgradeSetupError(f"corrupt .lacd/lock.json: {exc}")
    return config, lock


def _list_releases_or_fail(source):
    """List releases; on source failure raise UpgradeSetupError (never
    claim 'already latest' when the source is unreachable)."""
    from lacd.upgrade.source import SourceUnavailable, SourceInvalid
    try:
        return source.list_releases()
    except SourceUnavailable as exc:
        raise UpgradeSetupError(
            f"upgrade source unavailable: {exc}; cannot determine latest "
            f"version (state: UNVERIFIED, not 'already latest')")
    except SourceInvalid as exc:
        raise UpgradeSetupError(
            f"upgrade source returned invalid metadata: {exc}")


def _upgrade_check(project_dir: Path, args) -> int:
    from lacd.core.versioning import parse_version, classify_bump
    from lacd.upgrade.source import UpgradeSource
    from lacd.upgrade.compat import CompatPolicy, check_compatibility
    try:
        config, lock = _load_project_config(project_dir)
    except UpgradeSetupError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1
    source = UpgradeSource.from_config(config, template_dir=args.template_dir)
    try:
        releases = _list_releases_or_fail(source)
    except UpgradeSetupError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1
    if not releases:
        print("lacd: error: upgrade source returned no releases; "
              "cannot determine whether an upgrade exists", file=sys.stderr)
        return 1
    current = lock.template_version
    print(f"locked version: {current}")
    print(f"{'version':<10}{'bump':<10}{'support':<12}{'notes'}")
    policy = CompatPolicy()
    shown = 0
    for rel in sorted(releases, key=lambda r: parse_version(r.version),
                      reverse=True):
        if rel.prerelease:
            continue
        try:
            bump = classify_bump(current, rel.version)
        except ValueError:
            continue
        compat = check_compatibility(current, rel.version, policy, releases)
        note = ""
        if bump == "major":
            note = "requires --accept-major"
        elif bump == "same":
            note = "current"
        elif bump == "downgrade":
            note = "older than locked"
        print(f"{rel.version:<10}{bump:<10}{compat.support_status:<12}{note}")
        shown += 1
    if shown == 0:
        print("(no comparable releases found)")
    return 0


def _resolve_bundle(args, source, to_version: str, releases):
    """Return a TemplateBundle for to_version, or None when unavailable.

    --template-dir wins (offline/test). Otherwise download the release's
    template_bundle asset and verify its sha256 before use. Never executes
    downloaded content.
    """
    from lacd.upgrade.migrate import (load_bundle_from_dir,
                                      load_bundle_from_zip,
                                      BundleVerificationError)
    if args.template_dir:
        manifest = os.path.join(args.template_dir, "manifest.json")
        if not os.path.isfile(manifest):
            # No bundle staged here (e.g. source-only dir): the planner will
            # record a bundle-missing checkpoint instead of failing.
            return None
        try:
            return load_bundle_from_dir(args.template_dir)
        except BundleVerificationError as exc:
            raise UpgradeSetupError(f"bad template dir: {exc}")
    target = next((r for r in releases if r.version == to_version), None)
    tb = (target.template_bundle or {}) if target else {}
    url, sha256 = tb.get("url"), tb.get("sha256")
    if not (url and sha256):
        return None
    data = _download_bytes(url)
    try:
        return load_bundle_from_zip(data, sha256)
    except BundleVerificationError as exc:
        raise UpgradeSetupError(
            f"template bundle failed verification: {exc}")


def _download_bytes(url: str, timeout: int = 30) -> bytes:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        raise UpgradeSetupError(f"could not download {url}: {exc}")


def _print_plan(plan) -> None:
    print(f"upgrade plan: {plan.from_version} -> {plan.to_version}")
    if not plan.changes and not plan.conflicts:
        print("  no changes (already at target)")
        return
    for ch in plan.changes:
        print(f"  [{ch.risk}] {ch.action:<8} {ch.path}  ({ch.detail})")
    for cf in plan.conflicts:
        print(f"  [{cf.risk}] CONFLICT {cf.path}  ({cf.detail})")
    if plan.human_checkpoints:
        print("human checkpoints:")
        for cp in plan.human_checkpoints:
            print(f"  - {cp}")


def _tree_is_clean(project_dir: Path) -> bool:
    try:
        out = subprocess.run(["git", "-C", str(project_dir), "status",
                              "--porcelain"], capture_output=True, text=True,
                             timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and not out.stdout.strip()


def _run_upgrade(project_dir: Path, args) -> int:
    from lacd.upgrade.source import UpgradeSource
    from lacd.upgrade.planner import build_upgrade_plan, UpgradeRefused
    from lacd.upgrade.migrate import (apply_migration, MigrationConflict,
                                      MigrationError)
    try:
        config, lock = _load_project_config(project_dir)
    except UpgradeSetupError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return _upgrade_check(project_dir, args)

    source = UpgradeSource.from_config(config, template_dir=args.template_dir)
    try:
        releases = _list_releases_or_fail(source)
    except UpgradeSetupError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1
    try:
        bundle = _resolve_bundle(args, source, args.to, releases)
    except UpgradeSetupError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1

    try:
        plan = build_upgrade_plan(project_dir, args.to, source=source,
                                  bundle=bundle,
                                  accept_major=args.accept_major,
                                  require_bundle=not args.dry_run)
    except UpgradeRefused as exc:
        print(f"lacd: {exc} (exit 3: refused)", file=sys.stderr)
        return 3

    if args.dry_run:
        _print_plan(plan)
        print("(dry-run: worktree untouched)")
        return 0

    if plan.conflicts:
        print("lacd: error: upgrade has unresolved conflicts; refusing to "
              "apply/prepare:", file=sys.stderr)
        for cf in plan.conflicts:
            print(f"  CONFLICT {cf.path}: {cf.detail}", file=sys.stderr)
        print("resolve the conflicts manually, then re-run", file=sys.stderr)
        return 3

    if args.prepare_pr:
        return _upgrade_prepare_pr(project_dir, args, config, plan, bundle)

    # Local apply.
    if bundle is None:
        print("lacd: error: no template bundle available for "
              f"{args.to}; refusing to apply without verified new content",
              file=sys.stderr)
        return 3
    _print_plan(plan)
    if not args.yes and not _confirm("Apply this migration?"):
        print("aborted")
        return 1
    if not _tree_is_clean(project_dir):
        print("lacd: error: working tree is not clean; commit or stash "
              "changes first", file=sys.stderr)
        return 1
    try:
        result = apply_migration(project_dir, plan, bundle)
    except MigrationConflict as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 3
    except MigrationError as exc:
        print(f"lacd: error: migration failed and was rolled back: {exc}",
              file=sys.stderr)
        return 1
    print(f"upgraded to {args.to}: {len(result.applied)} file(s) applied")
    print(f"backup at {result.backup_dir} (use for manual rollback)")
    print("next: lacd status --json && lacd verify --strict")
    return 0


def _upgrade_prepare_pr(project_dir: Path, args, config, plan, bundle) -> int:
    from lacd.github.pr import prepare_upgrade_pr, PrError
    if bundle is None:
        print("lacd: error: no template bundle available for "
              f"{args.to}; cannot prepare a PR without verified new content",
              file=sys.stderr)
        return 3
    repo, branch = _detect_repo_branch(project_dir)
    if repo is None:
        print("lacd: error: cannot determine the GitHub repo (no origin "
              "remote); --prepare-pr needs a git remote pointing at GitHub",
              file=sys.stderr)
        return 1
    token = os.environ.get("GITHUB_TOKEN")
    try:
        report = prepare_upgrade_pr(project_dir, args.to, plan, bundle,
                                    repo, token, base=branch or "main")
    except PrError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1
    if report.reused:
        print(f"reusing existing upgrade PR: {report.pr_url}")
    elif report.pr_url:
        print(f"upgrade PR created: {report.pr_url}")
        print(f"branch: {report.branch} (never auto-merged; human must review)")
    else:
        print(f"no push permission: patch written to {report.local_patch}")
        for step in report.steps:
            print(f"  - {step}")
    return 0


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


class UpgradeSetupError(Exception):
    """Configuration/source problem that prevents an upgrade operation."""


# ---------------------------------------------------------------------------
# parser assembly
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="lacd", description="LACD: local-first adoption & change governance")
    parser.add_argument("--version", action="version", version=f"lacd {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser("init", help="initialize LACD in a project directory")
    p_init.add_argument("--path", default=".", help="project directory (default: .)")
    p_init.add_argument("--dry-run", action="store_true",
                        help="print the planned file changes without writing")
    p_init.add_argument("--force", action="store_true",
                        help="overwrite an existing lacd.yaml (backs it up first)")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="check the local environment")
    p_doctor.add_argument("--path", default=".", help="project directory (default: .)")
    p_doctor.set_defaults(func=cmd_doctor)

    p_status = sub.add_parser("status", help="show the adoption state")
    p_status.add_argument("--path", default=".", help="project directory (default: .)")
    p_status.add_argument("--json", action="store_true",
                          help="emit machine-readable JSON (schema-validated)")
    p_status.set_defaults(func=cmd_status)

    p_verify = sub.add_parser("verify", help="verify local dimensions (and remote when possible)")
    p_verify.add_argument("--path", default=".", help="project directory (default: .)")
    p_verify.add_argument("--strict", action="store_true",
                          help="exit 2 when any key dimension is not pass/enforced")
    p_verify.add_argument("--evidence", metavar="FILE",
                          help="check an evidence JSON file's head_sha against the current HEAD")
    p_verify.add_argument("--local", action="store_true",
                          help="local dimensions only: no network requests, "
                               "remote_enforcement=unverified/LOCAL_ONLY")
    p_verify.add_argument("--remote", action="store_true",
                          help="attempt remote enforcement check (default v1.0.0 "
                               "behavior when neither --local nor --remote is given)")
    p_verify.set_defaults(func=cmd_verify)

    p_up = sub.add_parser("upgrade", help="check/plan/apply template upgrades")
    p_up.add_argument("--path", default=".", help="project directory (default: .)")
    p_up.add_argument("--check", action="store_true",
                      help="list current vs available versions from the trusted source")
    p_up.add_argument("--dry-run", action="store_true",
                      help="print the migration plan without modifying the worktree (needs --to)")
    p_up.add_argument("--to", metavar="VERSION", help="target version, e.g. 1.1.0")
    p_up.add_argument("--prepare-pr", action="store_true",
                      help="create an upgrade PR instead of applying locally")
    p_up.add_argument("--accept-major", action="store_true",
                      help="explicitly accept a major-version migration")
    p_up.add_argument("--template-dir", metavar="DIR",
                      help="use a local template bundle instead of the remote source")
    p_up.add_argument("--yes", action="store_true",
                      help="answer yes to confirmation prompts")
    p_up.set_defaults(func=cmd_upgrade)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if not hasattr(args, "func"):
            parser.print_help()
            return 1
        return args.func(args)
    except UsageError as exc:
        print(f"lacd: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
