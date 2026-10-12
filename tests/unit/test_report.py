"""Unit tests: core/report.py."""

import json
from pathlib import Path

from lacd import __version__
from lacd.core.report import build_status_dict, validate_status_dict
from lacd.core.state import AdoptionState, DimensionResult
from .conftest import write_project

REQUIRED_FIELDS = [
    "schema", "lacd_cli_version", "generated_at", "project_dir", "branch",
    "head_sha", "mode", "versions", "state", "dimensions",
    "version_consistency", "next_steps",
]


def test_status_document_fields_exact(project_factory):
    d = project_factory()
    doc = build_status_dict(d)
    assert list(doc.keys()) == REQUIRED_FIELDS
    assert doc["schema"] == "lacd-status/1"
    assert doc["lacd_cli_version"] == __version__ == "1.0.0"
    assert doc["project_dir"] == str(Path(d).resolve())  # absolute
    assert doc["mode"] == "lacd"
    assert doc["state"] == AdoptionState.VALIDATED.value
    assert set(doc["dimensions"]) == {
        "local_validation", "remote_enforcement", "drift", "blocking"}
    for dim in doc["dimensions"].values():
        assert set(dim) == {"status", "reasons"}
        assert isinstance(dim["reasons"], list)
    assert set(doc["versions"]) == {
        "config_schema", "policy", "lock_format", "locked_cli", "template"}
    assert doc["versions"]["locked_cli"] == "1.0.0"
    assert doc["versions"]["config_schema"] == "1"
    assert set(doc["version_consistency"]) == {"status", "detail"}


def test_status_document_validates_against_schema(project_factory):
    d = project_factory()
    doc = build_status_dict(d)
    assert validate_status_dict(doc) == []


def test_not_installed_status_validates(project_factory, tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    doc = build_status_dict(d)
    assert doc["state"] == "NOT_INSTALLED"
    assert validate_status_dict(doc) == []
    assert doc["versions"]["locked_cli"] is None


def test_non_git_repo_branch_and_sha_are_null(project_factory):
    d = project_factory()
    doc = build_status_dict(d)
    # tmp_path is not inside a git checkout in this environment
    assert doc["branch"] is None
    assert doc["head_sha"] is None
    assert validate_status_dict(doc) == []


def test_version_mismatch_detected(project_factory):
    d = project_factory(lock_cli_version="0.9.0")
    doc = build_status_dict(d)
    vc = doc["version_consistency"]
    assert vc["status"] == "mismatch"
    assert "0.9.0" in vc["detail"] and __version__ in vc["detail"]
    assert doc["versions"]["locked_cli"] == "0.9.0"
    assert any("isolat" in s for s in doc["next_steps"])
    assert validate_status_dict(doc) == []


def test_version_unknown_without_lock(project_factory):
    d = project_factory(lock=False)
    doc = build_status_dict(d)
    assert doc["version_consistency"]["status"] == "unknown"
    assert doc["state"] == "DECLARED"


def test_remote_enforced_flows_into_document(project_factory):
    d = project_factory()
    remote = DimensionResult(status="enforced", reasons=["REMOTE_ENFORCED"])
    doc = build_status_dict(d, remote=remote)
    assert doc["state"] == "ENFORCED"
    assert doc["dimensions"]["remote_enforcement"]["status"] == "enforced"


def test_validate_status_dict_rejects_garbage():
    assert validate_status_dict({}) != []
    assert validate_status_dict({"schema": "lacd-status/1"}) != []
    assert validate_status_dict(["not", "a", "dict"]) != []
    assert validate_status_dict(None) != []


def test_validate_status_dict_rejects_bad_state_value(project_factory):
    d = project_factory()
    doc = build_status_dict(d)
    doc["state"] = "SOMEWHAT_INSTALLED"
    errors = validate_status_dict(doc)
    assert any("state" in e for e in errors)


def test_no_secrets_leak_in_document(project_factory):
    # The document must never carry credential-like values. project_dir is
    # excluded: pytest embeds the test's own name ("..._secrets_...") in it.
    d = project_factory()
    doc = build_status_dict(d)
    scrubbed = {k: v for k, v in doc.items() if k != "project_dir"}
    blob = json.dumps(scrubbed).lower()
    for token_word in ("token", "secret", "password", "private_key"):
        assert token_word not in blob
