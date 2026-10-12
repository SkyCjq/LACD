"""Machine-readable status document (LACD core).

:func:`build_status_dict` produces the ``lacd status --json`` document whose
fields match MODULE_CONTRACT §2.4 exactly; :func:`validate_status_dict`
checks such a document against ``schemas/status.schema.json``.
"""

from __future__ import annotations

import importlib.resources
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import jsonschema

from lacd import __version__
from lacd.config.lockfile import LockCorruptError, LockFile
from lacd.config.schema import ConfigInvalid, ConfigNotFound, load_config
from lacd.core.state import (
    AdoptionState,
    StateReport,
    _dist_version,
    compute_state,
)

PathLike = Union[str, Path]

_STATUS_SCHEMA_ID = "status.schema.json"


def _status_schema() -> dict:
    schema_path = importlib.resources.files("lacd.schemas") / _STATUS_SCHEMA_ID
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _git_info(project_dir: Path) -> tuple[str | None, str | None]:
    """Return ``(branch, head_sha)`` or ``(None, None)`` outside a git checkout.

    A non-git directory is *not* an error here: per the state-machine notes,
    missing SHA facts alone never yield UNVERIFIED.
    """
    try:
        branch_p = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha_p = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if branch_p.returncode != 0 or sha_p.returncode != 0:
        return None, None
    branch = branch_p.stdout.strip() or None
    if branch == "HEAD":  # detached HEAD: no branch name to report
        branch = None
    sha = sha_p.stdout.strip().lower()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        sha = None
    return branch, sha


def build_status_dict(
    project_dir: PathLike, remote=None, remote_queried: bool = False
) -> dict:
    """Build the ``lacd status --json`` document for ``project_dir``.

    ``remote`` is passed through to :func:`compute_state` (a
    ``DimensionResult`` or ``None``). ``remote_queried`` records whether the
    ``remote_enforcement`` dimension in *this* document came from a live
    network query (``"live"``) or not (``"not_queried"``). ``status`` itself
    never triggers network I/O (R11); remote semantics belong to ``verify``.
    Never raises on missing/invalid local facts; those are reflected in the
    dimensions and state instead.
    """
    base = Path(project_dir).resolve()
    report: StateReport = compute_state(base, remote)

    try:
        config = load_config(base)
    except (ConfigNotFound, ConfigInvalid, OSError):
        config = None

    lock: LockFile | None = None
    try:
        lock = LockFile.load(base)
    except (FileNotFoundError, LockCorruptError, OSError, ValueError):
        lock = None

    branch, head_sha = _git_info(base)

    def dim(d) -> dict:
        return {"status": d.status, "reasons": list(d.reasons)}

    remote_dim = dim(report.remote_enforcement)
    # D3/FR-03: which query mode produced this document's remote dimension.
    remote_dim["query"] = "live" if remote_queried else "not_queried"

    source_pin = None
    if lock is not None:
        source_pin = {
            "strength": lock.source.pin_strength,
            "commit": lock.source.commit,
        }

    return {
        "schema": "lacd-status/1",
        "lacd_cli_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_dir": str(base),
        "branch": branch,
        "head_sha": head_sha,
        "mode": config.lacd.mode if config is not None else "lacd",
        "versions": {
            "config_schema": config.schema_version if config is not None else None,
            "policy": config.lacd.policy_version if config is not None else None,
            "lock_format": lock.lock_format_version if lock is not None else None,
            "locked_cli": lock.lacd_cli_version if lock is not None else None,
            "template": lock.template_version if lock is not None else None,
            # R12: installed distribution version; null when running from a
            # source tree (not an error, just unevaluable).
            "package": _dist_version(),
        },
        "source_pin": source_pin,
        "state": report.state.value,
        "dimensions": {
            "local_validation": dim(report.local_validation),
            "remote_enforcement": remote_dim,
            "drift": dim(report.drift),
            "blocking": dim(report.blocking),
        },
        "version_consistency": dict(report.version_consistency),
        "next_steps": list(report.next_steps),
    }


def validate_status_dict(d: dict) -> list[str]:
    """Validate a status document against ``schemas/status.schema.json``.

    Returns a list of error strings; an empty list means the document is
    valid. Never raises on bad input.
    """
    if not isinstance(d, dict):
        return ["<root>: status document must be a JSON object"]
    schema = _status_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(d), key=lambda e: list(e.absolute_path))
    out: list[str] = []
    for e in errors:
        location = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"{location}: {e.message}")
    return out
