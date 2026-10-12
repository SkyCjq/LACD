"""Unit tests for `lacd status` / `lacd verify` / `lacd doctor` / `lacd upgrade` args.

The sibling contract modules (Agent-A: core/config, Agent-D: github.remote)
are not implemented yet, so these tests install faithful fakes through the
CLI's lazy-import seams (`_contract`, `_get_check_remote_enforcement`) and
assert the CLI layer's real behavior: JSON validation gating, exit codes,
evidence binding, strict semantics, and degradation notes.
"""

import json
import shutil
import socket
from types import SimpleNamespace

import pytest

from lacd import __version__
from lacd.cli import main as cli_main

main = cli_main.main
HEAD = "a" * 40


def _ns(**kw):
    return SimpleNamespace(**kw)


def _dim(status, reasons=()):
    return _ns(status=status, reasons=list(reasons))


def install_fake_contract(monkeypatch, *, local="pass", drift="clean",
                          blocking="none", enforcement_required=True,
                          head_sha=HEAD, branch="main", validate_errors=(),
                          remote_default=None):
    """Install a fake _contract() honoring the `remote` kwarg like the real one."""
    def fake_compute_state(project_dir, remote=None):
        if remote is not None:
            rstat, rreas = remote.status, list(remote.reasons)
        elif remote_default is not None:
            rstat, rreas = remote_default
        else:
            rstat, rreas = "unverified", ["REMOTE_NOT_CHECKED"]
        return _ns(
            state="VALIDATED",
            local_validation=_dim(local, [] if local == "pass" else ["LOCAL_CHECKS_FAILED"]),
            remote_enforcement=_dim(rstat, rreas),
            drift=_dim(drift, [] if drift == "clean" else ["DRIFT_DETECTED"]),
            blocking=_dim(blocking, [] if blocking == "none" else ["CONFIG_INVALID"]),
            version_consistency={"status": "consistent",
                                 "detail": f"cli {__version__} == locked {__version__}"},
            next_steps=[],
        )

    status_dict = {
        "schema": "lacd-status/1",
        "lacd_cli_version": __version__,
        "generated_at": "2026-10-12T00:00:00+00:00",
        "project_dir": "/tmp/x",
        "branch": branch,
        "head_sha": head_sha,
        "mode": "lacd",
        "versions": {"config_schema": "1", "policy": "1", "lock_format": "1",
                     "locked_cli": __version__, "template": __version__},
        "state": "VALIDATED",
        "dimensions": {
            "local_validation": {"status": local, "reasons": []},
            "remote_enforcement": {"status": "unverified",
                                   "reasons": ["REMOTE_NOT_CHECKED"]},
            "drift": {"status": drift, "reasons": []},
            "blocking": {"status": blocking, "reasons": []},
        },
        "version_consistency": {"status": "consistent", "detail": "ok"},
        "next_steps": [],
    }
    calls = {"validate_with": []}

    def fake_validate(d):
        calls["validate_with"].append(d)
        return list(validate_errors)

    fake = _ns(
        compute_state=fake_compute_state,
        build_status_dict=lambda project_dir, remote=None: status_dict,
        validate_status_dict=fake_validate,
        load_config=lambda project_dir: _ns(
            verification=_ns(github_enforcement_required=enforcement_required)),
        DimensionResult=lambda status, reasons: _dim(status, reasons),
    )
    monkeypatch.setattr(cli_main, "_contract", lambda: fake)
    return fake, calls, status_dict


def raise_import_error(*a, **k):
    raise ImportError("no module named 'lacd.github.remote'")


# ---------------------------------------------------------------- status ---


def test_status_json_is_validated_before_output(tmp_path, monkeypatch, capsys):
    _, calls, status_dict = install_fake_contract(monkeypatch)
    rc = main(["status", "--path", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["head_sha"] == HEAD
    # The dict that was validated is exactly the dict that was printed.
    assert calls["validate_with"] == [status_dict]


def test_status_json_invalid_dict_exits_1_without_bad_json(
        tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, validate_errors=["dimensions: missing"])
    rc = main(["status", "--path", str(tmp_path), "--json"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "failed schema validation" in captured.err
    assert captured.out.strip() == ""  # never emit the bad JSON


def test_status_human_summary(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    rc = main(["status", "--path", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "state: VALIDATED" in out
    assert "local_validation: pass" in out
    assert "REMOTE_NOT_CHECKED" in out


def test_status_without_contract_modules_exits_1(tmp_path, monkeypatch, capsys):
    def boom():
        raise ImportError("core not implemented")
    monkeypatch.setattr(cli_main, "_contract", boom)
    rc = main(["status", "--path", str(tmp_path)])
    assert rc == 1
    assert "error" in capsys.readouterr().err


def test_status_build_failure_exits_1(tmp_path, monkeypatch, capsys):
    fake, _, _ = install_fake_contract(monkeypatch)
    fake.build_status_dict = lambda project_dir, remote=None: (_ for _ in ()).throw(
        RuntimeError("disk exploded"))
    rc = main(["status", "--path", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------- verify ---


def test_verify_reports_dimensions_and_exits_0(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "local_validation: pass" in out
    assert "REMOTE_NOT_CHECKED" in out
    assert "degraded" in out  # degradation note is explicit, not silent


def test_verify_strict_remote_unverified_required_exits_2(
        tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, enforcement_required=True)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path), "--strict"])
    assert rc == 2
    assert "STRICT FAILED" in capsys.readouterr().out


def test_verify_strict_local_fail_exits_2(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, local="fail", enforcement_required=False)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path), "--strict"])
    assert rc == 2


def test_verify_strict_drifted_exits_2(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, drift="drifted", enforcement_required=False)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path), "--strict"])
    assert rc == 2


def test_verify_strict_all_green_with_enforced_remote_exits_0(
        tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, enforcement_required=True)

    def fake_check(repo, branch, policy=None, token=None, timeout=15):
        assert repo == "example/repo" and branch == "main"
        assert policy is not None, "_remote_dimension must pass EnforcementPolicy (FR-102-02)"
        return _ns(status="enforced", reasons=["REMOTE_PROTECTION_VERIFIED"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
    monkeypatch.setattr(cli_main, "_get_required_checks",
                        lambda project_dir: ["ci-check"])
    rc = main(["verify", "--path", str(tmp_path), "--strict"])
    assert rc == 0
    assert "STRICT: all key dimensions satisfied" in capsys.readouterr().out


def test_verify_strict_remote_not_enforced_exits_2(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, enforcement_required=False)

    def fake_check(repo, branch, policy=None, token=None, timeout=15):
        assert policy is not None, "_remote_dimension must pass EnforcementPolicy (FR-102-02)"
        return _ns(status="not_enforced", reasons=["REMOTE_PROTECTION_ABSENT"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
    monkeypatch.setattr(cli_main, "_get_required_checks",
                        lambda project_dir: ["ci-check"])
    rc = main(["verify", "--path", str(tmp_path), "--strict"])
    assert rc == 2


def test_verify_evidence_match_exits_0(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, head_sha=HEAD)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps({"head_sha": HEAD}), encoding="utf-8")
    rc = main(["verify", "--path", str(tmp_path), "--evidence", str(ev)])
    assert rc == 0
    assert "evidence: OK" in capsys.readouterr().out


def test_verify_evidence_mismatch_exits_2(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, head_sha=HEAD)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps({"head_sha": "b" * 40}), encoding="utf-8")
    rc = main(["verify", "--path", str(tmp_path), "--evidence", str(ev)])
    assert rc == 2
    assert "EVIDENCE_SHA_MISMATCH" in capsys.readouterr().err


def test_verify_evidence_missing_file_exits_1(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path), "--evidence",
               str(tmp_path / "nope.json")])
    assert rc == 1


def test_verify_evidence_bad_json_exits_1(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    ev = tmp_path / "evidence.json"
    ev.write_text("{not json", encoding="utf-8")
    rc = main(["verify", "--path", str(tmp_path), "--evidence", str(ev)])
    assert rc == 1


# ---------------------------------------------------------------- doctor ---


def test_doctor_reports_items(tmp_path, capsys):
    rc = main(["doctor", "--path", str(tmp_path)])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "[PASS] python" in out  # this env runs 3.12
    assert "[PASS] git" in out
    assert "[PASS] write-permission" in out


def test_doctor_network_failure_is_warn_not_fail(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("no route to host")
    monkeypatch.setattr(socket, "create_connection", boom)
    rc = main(["doctor", "--path", str(tmp_path)])
    assert rc == 0  # WARN does not fail the run
    out = capsys.readouterr().out
    assert "[WARN] github-connectivity" in out


def test_doctor_missing_git_is_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc = main(["doctor", "--path", str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL] git" in out


# --------------------------------------------------------------- upgrade ---


def _make_init_project(tmp_path):
    """Run `lacd init` in tmp_path; return the path."""
    from lacd.cli.main import main as cli_main
    rc = cli_main(["init", "--path", str(tmp_path)])
    assert rc == 0
    return tmp_path


def _next_versions():
    """Derive a patch/minor pair above the current CLI version (version-agnostic)."""
    from lacd.core.versioning import parse_version
    major, minor, patch = parse_version(__version__)
    return f"{major}.{minor}.{patch + 1}", f"{major}.{minor + 1}.0"


def _write_releases(tmp_path):
    """A file:// upgrade source with a patch and a minor above locked version."""
    import json
    patch_v, minor_v = _next_versions()
    src = tmp_path / "upstream"
    src.mkdir()
    (src / "releases.json").write_text(json.dumps({
        "releases": [
            {"version": patch_v, "tag": f"v{patch_v}", "commit": None,
             "published_at": None, "prerelease": False,
             "template_bundle": None, "notes": ""},
            {"version": minor_v, "tag": f"v{minor_v}", "commit": None,
             "published_at": None, "prerelease": False,
             "template_bundle": None, "notes": ""},
        ]}), encoding="utf-8")
    return src


def test_upgrade_check_lists_versions(tmp_path, capsys):
    proj = _make_init_project(tmp_path / "proj")
    src = _write_releases(tmp_path)
    rc = main(["upgrade", "--check", "--path", str(proj),
               "--template-dir", str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    patch_v, minor_v = _next_versions()
    assert patch_v in out and "patch" in out
    assert minor_v in out and "minor" in out


def test_upgrade_check_source_failure_is_not_latest(tmp_path, capsys):
    proj = _make_init_project(tmp_path / "proj")
    src = tmp_path / "empty-upstream"
    src.mkdir()  # no releases.json -> falls back to GitHub source? no:
    # point template_dir at a dir without releases.json AND break network
    # by using an unreachable repo via env override.
    import os
    os.environ["LACD_UPSTREAM_REPO"] = "no-such-owner-xyz/no-such-repo-xyz"
    try:
        rc = main(["upgrade", "--check", "--path", str(proj)])
    finally:
        del os.environ["LACD_UPSTREAM_REPO"]
    assert rc == 1
    err = capsys.readouterr().err
    assert "already latest" not in err.lower()


def test_upgrade_to_unknown_version_refused(tmp_path, capsys):
    proj = _make_init_project(tmp_path / "proj")
    src = _write_releases(tmp_path)
    rc = main(["upgrade", "--to", "9.9.9", "--path", str(proj),
               "--template-dir", str(src), "--dry-run"])
    assert rc == 3
    assert "refused" in capsys.readouterr().err


def test_upgrade_dry_run_does_not_touch_worktree(tmp_path, capsys):
    import hashlib
    proj = _make_init_project(tmp_path / "proj")
    src = _write_releases(tmp_path)
    before = {}
    for f in proj.rglob("*"):
        if f.is_file():
            before[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
    patch_v, _ = _next_versions()
    rc = main(["upgrade", "--dry-run", "--to", patch_v, "--path", str(proj),
               "--template-dir", str(src)])
    assert rc == 0
    for f in proj.rglob("*"):
        if f.is_file() and str(f) in before:
            assert hashlib.sha256(f.read_bytes()).hexdigest() == before[str(f)]
    assert "dry-run" in capsys.readouterr().out


def test_upgrade_dry_run_requires_to(tmp_path, capsys):
    rc = main(["upgrade", "--dry-run", "--path", str(tmp_path)])
    assert rc == 1  # usage error
    assert "--to" in capsys.readouterr().err


def test_upgrade_check_rejects_conflicting_flags(tmp_path, capsys):
    rc = main(["upgrade", "--check", "--to", "1.0.1", "--path", str(tmp_path)])
    assert rc == 1
    assert "--check cannot be combined" in capsys.readouterr().err


def test_upgrade_requires_check_or_to(tmp_path, capsys):
    rc = main(["upgrade", "--path", str(tmp_path)])
    assert rc == 1
    assert "one of --check or --to" in capsys.readouterr().err


def test_upgrade_help_lists_all_contract_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["upgrade", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--check", "--dry-run", "--to", "--prepare-pr",
                 "--accept-major", "--template-dir", "--yes"):
        assert flag in out, f"{flag} missing from upgrade --help"


# ------------------------------------------------- verify --local/--remote ---


def _block_network(monkeypatch):
    """Forbid all network: any socket use raises. Proves --local is offline."""
    def _boom(*a, **k):
        raise AssertionError("network call attempted in --local mode")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)


def test_verify_local_remote_mutually_exclusive(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    rc = main(["verify", "--path", str(tmp_path), "--local", "--remote"])
    assert rc == 1  # UsageError -> exit 1, not argparse's 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_verify_local_makes_zero_network_calls(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    _block_network(monkeypatch)

    def _must_not_run(*a, **k):
        raise AssertionError("_remote_dimension must not run under --local")

    monkeypatch.setattr(cli_main, "_remote_dimension", _must_not_run)
    rc = main(["verify", "--path", str(tmp_path), "--local"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LOCAL_ONLY" in out
    assert "local-only" in out


def test_verify_local_never_claims_enforced(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch)
    _block_network(monkeypatch)
    rc = main(["verify", "--path", str(tmp_path), "--local"])
    assert rc == 0
    out = capsys.readouterr().out
    remote_line = next(l for l in out.splitlines()
                       if l.strip().startswith("remote_enforcement:"))
    assert "unverified" in remote_line and "LOCAL_ONLY" in remote_line
    assert "pass" not in remote_line and "enforced:" not in remote_line


def test_verify_local_strict_green_when_local_clean(tmp_path, monkeypatch, capsys):
    """--local --strict passes on local dimensions; remote is not counted."""
    install_fake_contract(monkeypatch, enforcement_required=True)
    _block_network(monkeypatch)
    rc = main(["verify", "--path", str(tmp_path), "--local", "--strict"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STRICT" in out and "local" in out.lower()


def test_verify_local_strict_still_fails_on_drift(tmp_path, monkeypatch, capsys):
    """--local --strict keeps teeth on local dimensions."""
    install_fake_contract(monkeypatch, drift="drifted", enforcement_required=True)
    _block_network(monkeypatch)
    rc = main(["verify", "--path", str(tmp_path), "--local", "--strict"])
    assert rc == 2
    assert "STRICT FAILED" in capsys.readouterr().out


def test_verify_no_flag_keeps_v100_behavior(tmp_path, monkeypatch, capsys):
    """Backward compat: no flag still attempts remote and degrades."""
    install_fake_contract(monkeypatch)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "REMOTE_NOT_CHECKED" in out
    assert "LOCAL_ONLY" not in out


def test_verify_remote_flag_attempts_remote(tmp_path, monkeypatch, capsys):
    """--remote explicitly requests the v1.0.0 remote attempt path."""
    install_fake_contract(monkeypatch)
    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        raise_import_error)
    rc = main(["verify", "--path", str(tmp_path), "--remote"])
    assert rc == 0
    assert "REMOTE_NOT_CHECKED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# FR-102-02: _remote_dimension must always pass an explicit EnforcementPolicy.
# The policy=None legacy path is never used on the CLI main path.
# ---------------------------------------------------------------------------

def test_remote_dimension_never_passes_policy_none(tmp_path, monkeypatch):
    """Iron test: _remote_dimension must not call check_remote_enforcement
    with policy=None (MUST pass EnforcementPolicy per FR-102-02)."""
    install_fake_contract(monkeypatch)
    captured = {}

    def fake_check(repo, branch, *, policy=None, token=None, timeout=15):
        captured["policy"] = policy
        return _ns(status="enforced", reasons=["REMOTE_PROTECTION_VERIFIED"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
    monkeypatch.setattr(cli_main, "_get_required_checks",
                        lambda project_dir: ["ci-check", "lint"])

    dim, note = cli_main._remote_dimension(cli_main._contract(), tmp_path)
    assert "policy" in captured, "check_remote_enforcement was not called"
    assert captured["policy"] is not None, \
        "policy=None is forbidden on the CLI main path (FR-102-02)"
    # Policy fields per MODULE_CONTRACT §10
    p = captured["policy"]
    assert p.policy_version == "1"
    assert p.require_pull_request is True
    assert p.required_checks == ["ci-check", "lint"]
    assert p.allow_bypass is False
    assert p.human_merge_required is True
    assert dim.status == "enforced"


def test_remote_dimension_empty_required_checks_is_unverified(tmp_path, monkeypatch):
    """No configured required checks -> an "enforced" verdict is downgraded to
    UNVERIFIED (fail-closed, no false positive). The API is still called with
    an explicit policy (never policy=None)."""
    install_fake_contract(monkeypatch)
    captured = {}

    def fake_check(repo, branch, *, policy=None, token=None, timeout=15):
        captured["policy"] = policy
        # Simulate a repo that would otherwise look "enforced"
        return _ns(status="enforced", reasons=["REMOTE_PROTECTION_VERIFIED"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
    monkeypatch.setattr(cli_main, "_get_required_checks",
                        lambda project_dir: [])

    dim, note = cli_main._remote_dimension(cli_main._contract(), tmp_path)
    assert captured["policy"] is not None, "policy must always be passed"
    assert captured["policy"].required_checks == []
    assert dim.status == "unverified"
    assert "REQUIRED_CHECKS_UNCONFIGURED" in dim.reasons
    assert "fail-closed" in note


def test_remote_dimension_empty_checks_keeps_unverified(tmp_path, monkeypatch):
    """Non-enforced verdicts pass through unchanged with empty checks."""
    install_fake_contract(monkeypatch)

    def fake_check(repo, branch, *, policy=None, token=None, timeout=15):
        return _ns(status="not_enforced", reasons=["REMOTE_PROTECTION_ABSENT"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
    monkeypatch.setattr(cli_main, "_get_required_checks",
                        lambda project_dir: [])

    dim, note = cli_main._remote_dimension(cli_main._contract(), tmp_path)
    assert dim.status == "not_enforced"
    assert note is None


def test_get_required_checks_empty_when_no_config(tmp_path):
    """v1 schema has no required_checks field -> [] (conservative)."""
    assert cli_main._get_required_checks(tmp_path) == []


def test_get_required_checks_ignores_non_string_entries(tmp_path, monkeypatch):
    """Non-string entries are filtered out (defensive)."""
    fake_gov = _ns(required_checks=["ok-check", 123, None, ""])
    fake_cfg = _ns(governance=fake_gov)
    monkeypatch.setattr("lacd.config.schema.load_config", lambda p: fake_cfg)
    assert cli_main._get_required_checks(tmp_path) == ["ok-check"]
