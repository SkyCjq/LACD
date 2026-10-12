"""Adoption state machine (LACD core).

Implements the 9-level priority ladder from MODULE_CONTRACT §2.3
(frozen 2026-10-12). The state is always computed from live local facts;
nothing is cached.

Dimension ``status`` vocabulary (per-dimension; see docstrings):

- ``local_validation``: ``"pass"`` | ``"fail"`` | ``"not-run"`` | ``"unverified"``
- ``remote_enforcement``: ``"enforced"`` | ``"not_enforced"`` | ``"unverified"``
- ``drift``: ``"clean"`` | ``"drifted"`` | ``"not-run"``
- ``blocking``: ``"none"`` | ``"blocked"``

Reason codes follow the contract's non-exhaustive list; this module adds
``IO_ERROR``, ``EXTENSIONS_MISSING`` and ``EXTENSIONS_PATH_UNSAFE``,
documented here.

Design notes (contract-conformance decisions):

- Version mismatch (running CLI vs ``lock.lacd_cli_version``) is reported in
  ``version_consistency`` and ``next_steps`` only; the priority ladder does
  not list it, so it never changes the state by itself.
- A non-git checkout (``head_sha`` unavailable) is *not* UNVERIFIED; only an
  I/O failure while establishing basic facts yields UNVERIFIED.
- ``remote`` unverified (including ``None``) never promotes the state to
  ENFORCED (AT-06); the state stays VALIDATED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Union

from lacd import __version__
from lacd.config.lockfile import LockCorruptError, LockFile
from lacd.config.schema import ConfigInvalid, ConfigNotFound, load_config

PathLike = Union[str, Path]


class AdoptionState(str, Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    DECLARED = "DECLARED"
    VALIDATED = "VALIDATED"
    ENFORCED = "ENFORCED"
    DRIFTED = "DRIFTED"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass
class DimensionResult:
    status: str  # see module docstring for the per-dimension vocabulary
    reasons: list[str] = field(default_factory=list)  # machine-readable codes


@dataclass
class StateReport:
    state: AdoptionState
    local_validation: DimensionResult
    remote_enforcement: DimensionResult
    drift: DimensionResult
    blocking: DimensionResult
    version_consistency: dict  # {"status": "consistent"|"mismatch"|"unknown", "detail": str}
    next_steps: list[str]


def _safe_join(project_dir: Path, rel: str) -> Path:
    """Join a config-listed relative path, fail-closed on escape attempts."""
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"invalid path: {rel!r}")
    target = (project_dir / rel).resolve()
    base = project_dir.resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes project_dir: {rel!r}")
    return target


def _version_consistency(lock: LockFile | None) -> dict:
    if lock is None:
        return {"status": "unknown", "detail": "no lock file present"}
    if lock.lacd_cli_version == __version__:
        return {
            "status": "consistent",
            "detail": f"running CLI {__version__} matches locked lacd_cli_version",
        }
    return {
        "status": "mismatch",
        "detail": (
            f"running CLI {__version__} differs from locked "
            f"lacd_cli_version {lock.lacd_cli_version}"
        ),
    }


def compute_state(
    project_dir: PathLike, remote: DimensionResult | None = None
) -> StateReport:
    """Compute the adoption state purely from local facts plus ``remote``.

    ``remote`` is the caller-supplied remote-enforcement dimension
    (``github/remote.py`` in the full CLI); ``None`` means "not checked".
    """
    base = Path(project_dir)
    remote_dim = (
        remote
        if remote is not None
        else DimensionResult(status="unverified", reasons=["REMOTE_NOT_CHECKED"])
    )
    next_steps: list[str] = []

    def finish(state, local, rmt, drift, blocking, vc) -> StateReport:
        if vc["status"] == "mismatch":
            next_steps.append(
                "CLI/lock version mismatch: run the locked CLI version in an "
                "isolated environment; do not let a different CLI version "
                "write to this project."
            )
        return StateReport(
            state=state,
            local_validation=local,
            remote_enforcement=rmt,
            drift=drift,
            blocking=blocking,
            version_consistency=vc,
            next_steps=list(next_steps),
        )

    # Priority 1: no lacd.yaml -> NOT_INSTALLED
    config_path = base / "lacd.yaml"
    if not config_path.is_file():
        next_steps.append("Run `lacd init` in this directory to adopt LACD.")
        return finish(
            AdoptionState.NOT_INSTALLED,
            DimensionResult("not-run", ["CONFIG_MISSING"]),
            remote_dim,
            DimensionResult("not-run", []),
            DimensionResult("none", []),
            {"status": "unknown", "detail": "no lacd.yaml"},
        )

    # Priority 2 (part 1): unreadable config -> UNVERIFIED (I/O, priority 3);
    # invalid config -> BLOCKED (priority 2).
    try:
        config = load_config(base)
    except ConfigNotFound:  # raced deletion between the check above and now
        next_steps.append("Run `lacd init` in this directory to adopt LACD.")
        return finish(
            AdoptionState.NOT_INSTALLED,
            DimensionResult("not-run", ["CONFIG_MISSING"]),
            remote_dim,
            DimensionResult("not-run", []),
            DimensionResult("none", []),
            {"status": "unknown", "detail": "no lacd.yaml"},
        )
    except OSError:
        next_steps.append(
            "Could not read basic facts (I/O error); check permissions and retry."
        )
        return finish(
            AdoptionState.UNVERIFIED,
            DimensionResult("unverified", ["IO_ERROR"]),
            remote_dim,
            DimensionResult("not-run", []),
            DimensionResult("none", []),
            {"status": "unknown", "detail": "basic facts unavailable (I/O error)"},
        )
    except ConfigInvalid as exc:
        reasons = ["CONFIG_INVALID", *exc.errors]
        next_steps.append(
            "Fix lacd.yaml (see reasons), then re-run `lacd verify`."
        )
        return finish(
            AdoptionState.BLOCKED,
            DimensionResult("fail", reasons),
            remote_dim,
            DimensionResult("not-run", []),
            DimensionResult("blocked", reasons),
            {"status": "unknown", "detail": "configuration invalid"},
        )

    # Priority 2 (part 2): lock present but corrupt -> BLOCKED.
    lock_path = base / ".lacd" / "lock.json"
    lock: LockFile | None = None
    if lock_path.is_file():
        try:
            lock = LockFile.load(base)
        except OSError:
            next_steps.append(
                "Could not read basic facts (I/O error); check permissions and retry."
            )
            return finish(
                AdoptionState.UNVERIFIED,
                DimensionResult("unverified", ["IO_ERROR"]),
                remote_dim,
                DimensionResult("not-run", []),
                DimensionResult("none", []),
                {"status": "unknown", "detail": "basic facts unavailable (I/O error)"},
            )
        except LockCorruptError as exc:
            reasons = ["LOCK_CORRUPT", *exc.errors]
            next_steps.append(
                "The lock file is corrupt; restore it from version control or "
                "re-run `lacd init`, then re-run `lacd verify`."
            )
            return finish(
                AdoptionState.BLOCKED,
                DimensionResult("fail", reasons),
                remote_dim,
                DimensionResult("not-run", []),
                DimensionResult("blocked", reasons),
                {"status": "unknown", "detail": "lock file corrupt"},
            )

    vc = _version_consistency(lock)

    # Priority 4: config valid but no lock -> DECLARED.
    if lock is None:
        next_steps.append(
            "Complete adoption: run `lacd init` to generate `.lacd/lock.json`, "
            "then `lacd verify`."
        )
        return finish(
            AdoptionState.DECLARED,
            DimensionResult("fail", ["LOCK_MISSING"]),
            remote_dim,
            DimensionResult("not-run", []),
            DimensionResult("none", []),
            vc,
        )

    # Priority 5: local validation failures -> DECLARED ("declared, not fulfilled").
    # Local checks beyond config/lock validity: the declared project-owned
    # extensions file must exist and stay inside the project.
    local_reasons: list[str] = []
    try:
        ext_path = _safe_join(base, config.project.extensions)
    except ValueError:
        local_reasons.append("EXTENSIONS_PATH_UNSAFE")
        ext_path = None
    if ext_path is not None and not ext_path.is_file():
        local_reasons.append("EXTENSIONS_MISSING")
    local_dim = DimensionResult(
        "pass" if not local_reasons else "fail", local_reasons
    )

    # Priority 6: blocking reasons -> BLOCKED. Currently: remote enforcement
    # is *proven* absent while the policy requires it.
    blocking_reasons: list[str] = []
    if (
        remote_dim.status == "not_enforced"
        and config.verification.github_enforcement_required
    ):
        blocking_reasons.append("REMOTE_NOT_ENFORCED")
    blocking_dim = DimensionResult(
        "blocked" if blocking_reasons else "none", blocking_reasons
    )

    # Priority 7: drift -> DRIFTED.
    try:
        drift_entries = lock.check_drift(base)
    except OSError:
        next_steps.append(
            "Could not read basic facts (I/O error); check permissions and retry."
        )
        return finish(
            AdoptionState.UNVERIFIED,
            DimensionResult("unverified", ["IO_ERROR"]),
            remote_dim,
            DimensionResult("not-run", []),
            blocking_dim,
            vc,
        )
    drift_dim = DimensionResult(
        "drifted" if drift_entries else "clean",
        ["DRIFT_DETECTED"] if drift_entries else [],
    )

    if local_reasons:
        next_steps.append(
            "Local checks failed (declaration not fulfilled); fix the reported "
            "issues, then re-run `lacd verify`."
        )
        return finish(
            AdoptionState.DECLARED, local_dim, remote_dim, drift_dim, blocking_dim, vc
        )

    if blocking_reasons:
        next_steps.append(
            "Blocking conditions are present; resolve them before proceeding."
        )
        return finish(
            AdoptionState.BLOCKED, local_dim, remote_dim, drift_dim, blocking_dim, vc
        )

    if drift_entries:
        next_steps.append(
            "Managed content drifted from the lock; restore the files or run "
            "`lacd upgrade` to adopt the new baseline."
        )
        return finish(
            AdoptionState.DRIFTED, local_dim, remote_dim, drift_dim, blocking_dim, vc
        )

    # Priority 8: remote explicitly enforced -> ENFORCED.
    if remote_dim.status == "enforced":
        return finish(
            AdoptionState.ENFORCED, local_dim, remote_dim, drift_dim, blocking_dim, vc
        )

    # Priority 9: everything else -> VALIDATED. An unverified remote never
    # promotes the state to ENFORCED (AT-06).
    if remote_dim.status == "unverified":
        next_steps.append(
            "Remote enforcement was not checked; run `lacd verify` for remote "
            "confirmation. VALIDATED does not imply remote enforcement."
        )
    return finish(
        AdoptionState.VALIDATED, local_dim, remote_dim, drift_dim, blocking_dim, vc
    )
