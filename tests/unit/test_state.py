"""Unit tests: core/state.py — the 9-level priority ladder."""

import pytest
import yaml

import lacd.core.state as state_mod
from lacd.core.state import (
    AdoptionState,
    DimensionResult,
    compute_state,
)
from .conftest import valid_config_dict, write_project


def _remote(status, reasons):
    return DimensionResult(status=status, reasons=reasons)


# Priority 1: no lacd.yaml -> NOT_INSTALLED -------------------------------
def test_p1_no_config_not_installed(tmp_path):
    report = compute_state(tmp_path)
    assert report.state == AdoptionState.NOT_INSTALLED
    assert report.local_validation.status == "not-run"
    assert "CONFIG_MISSING" in report.local_validation.reasons
    assert report.blocking.status == "none"


# Priority 2a: invalid config -> BLOCKED ----------------------------------
def test_p2_invalid_config_blocked(project_factory):
    d = project_factory(lock=False)
    cfg = valid_config_dict()
    cfg["agents"]["protocol"] = "bogus"
    (d / "lacd.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    report = compute_state(d)
    assert report.state == AdoptionState.BLOCKED
    assert "CONFIG_INVALID" in report.blocking.reasons
    assert report.blocking.status == "blocked"
    assert report.local_validation.status == "fail"


def test_p2_unparseable_yaml_blocked(project_factory):
    d = project_factory(lock=False)
    (d / "lacd.yaml").write_text(":\n\tbad", encoding="utf-8")
    report = compute_state(d)
    assert report.state == AdoptionState.BLOCKED
    assert "CONFIG_INVALID" in report.blocking.reasons


# Priority 2b: corrupt lock -> BLOCKED ------------------------------------
def test_p2_corrupt_lock_blocked(project_factory):
    d = project_factory()
    (d / ".lacd" / "lock.json").write_text("{corrupt", encoding="utf-8")
    report = compute_state(d)
    assert report.state == AdoptionState.BLOCKED
    assert "LOCK_CORRUPT" in report.blocking.reasons
    assert report.local_validation.status == "fail"


# Priority 3: I/O failure establishing basic facts -> UNVERIFIED -----------
def test_p3_io_error_unverified(project_factory, monkeypatch):
    d = project_factory(lock=False)

    def boom(_base):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(state_mod, "load_config", boom)
    report = compute_state(d)
    assert report.state == AdoptionState.UNVERIFIED
    assert "IO_ERROR" in report.local_validation.reasons


def test_p3_non_git_repo_is_not_unverified(project_factory):
    # A non-git checkout must not, by itself, produce UNVERIFIED.
    d = project_factory()
    report = compute_state(d)
    assert report.state in (AdoptionState.VALIDATED, AdoptionState.ENFORCED)
    assert report.state != AdoptionState.UNVERIFIED


# Priority 4: valid config, no lock -> DECLARED ---------------------------
def test_p4_no_lock_declared(project_factory):
    d = project_factory(lock=False)
    report = compute_state(d)
    assert report.state == AdoptionState.DECLARED
    assert report.local_validation.status == "fail"
    assert "LOCK_MISSING" in report.local_validation.reasons
    assert report.blocking.status == "none"


# Priority 5: local validation fails -> DECLARED --------------------------
def test_p5_local_check_failure_declared(project_factory):
    d = project_factory(extensions=False)  # declared extensions file missing
    report = compute_state(d)
    assert report.state == AdoptionState.DECLARED
    assert report.local_validation.status == "fail"
    assert "EXTENSIONS_MISSING" in report.local_validation.reasons


# Priority 6: blocking reasons -> BLOCKED ----------------------------------
def test_p6_remote_proven_absent_blocked(project_factory):
    d = project_factory()  # github_enforcement_required=true
    remote = _remote("not_enforced", ["REMOTE_PROTECTION_ABSENT"])
    report = compute_state(d, remote=remote)
    assert report.state == AdoptionState.BLOCKED
    assert "REMOTE_NOT_ENFORCED" in report.blocking.reasons
    assert report.remote_enforcement.status == "not_enforced"


def test_p6_remote_absent_not_blocking_when_not_required(project_factory):
    d = project_factory()
    cfg = valid_config_dict()
    cfg["verification"]["github_enforcement_required"] = False
    (d / "lacd.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    remote = _remote("not_enforced", ["REMOTE_PROTECTION_ABSENT"])
    report = compute_state(d, remote=remote)
    assert report.state == AdoptionState.VALIDATED
    assert report.blocking.status == "none"


# Priority 7: drift -> DRIFTED --------------------------------------------
def test_p7_drift_detected_drifted(project_factory):
    d = project_factory(managed_files=[("wf/x.yml", "v1\n")])
    (d / "wf" / "x.yml").write_text("v1-tampered\n", encoding="utf-8")
    report = compute_state(d)
    assert report.state == AdoptionState.DRIFTED
    assert report.drift.status == "drifted"
    assert "DRIFT_DETECTED" in report.drift.reasons
    assert report.local_validation.status == "pass"


# Priority 8: remote enforced -> ENFORCED ----------------------------------
def test_p8_remote_enforced(project_factory):
    d = project_factory()
    remote = _remote("enforced", ["REMOTE_ENFORCED"])
    report = compute_state(d, remote=remote)
    assert report.state == AdoptionState.ENFORCED
    assert report.remote_enforcement.status == "enforced"


# Priority 9: otherwise -> VALIDATED --------------------------------------
def test_p9_clean_validated(project_factory):
    d = project_factory()
    report = compute_state(d)
    assert report.state == AdoptionState.VALIDATED
    assert report.local_validation.status == "pass"
    assert report.drift.status == "clean"
    assert report.blocking.status == "none"


def test_p9_unverified_remote_does_not_claim_enforced(project_factory):
    # AT-06: unverified remote must keep VALIDATED, never ENFORCED.
    d = project_factory()
    report = compute_state(d, remote=None)
    assert report.state == AdoptionState.VALIDATED
    assert report.remote_enforcement.status == "unverified"
    assert "REMOTE_NOT_CHECKED" in report.remote_enforcement.reasons


def test_p9_explicit_unverified_remote_stays_validated(project_factory):
    d = project_factory()
    remote = _remote("unverified", ["REMOTE_NO_CREDENTIAL"])
    report = compute_state(d, remote=remote)
    assert report.state == AdoptionState.VALIDATED


# Ordering interactions ---------------------------------------------------
def test_blocking_beats_drift(project_factory):
    d = project_factory(managed_files=[("wf/x.yml", "v1\n")])
    (d / "wf" / "x.yml").write_text("tampered\n", encoding="utf-8")
    remote = _remote("not_enforced", ["REMOTE_PROTECTION_ABSENT"])
    report = compute_state(d, remote=remote)
    assert report.state == AdoptionState.BLOCKED  # priority 6 > 7
    assert report.drift.status == "drifted"  # dimension still honest


def test_local_failure_beats_blocking(project_factory):
    d = project_factory(extensions=False)
    remote = _remote("not_enforced", ["REMOTE_PROTECTION_ABSENT"])
    report = compute_state(d, remote=remote)
    assert report.state == AdoptionState.DECLARED  # priority 5 > 6


def test_version_consistency_mismatch_does_not_change_state(project_factory):
    d = project_factory(lock_cli_version="0.9.0")
    report = compute_state(d)
    assert report.state == AdoptionState.VALIDATED
    assert report.version_consistency["status"] == "mismatch"
    assert any("isolat" in s for s in report.next_steps)


def test_version_consistency_consistent(project_factory):
    # Must track the running CLI version, not a hardcoded literal,
    # or the version bump itself breaks this test.
    from lacd import __version__ as _v
    d = project_factory(lock_cli_version=_v)
    report = compute_state(d)
    assert report.version_consistency["status"] == "consistent"
