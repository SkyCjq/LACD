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

    def fake_check(repo, branch, token=None, timeout=15):
        assert repo == "example/repo" and branch == "main"
        return _ns(status="enforced", reasons=["REMOTE_PROTECTION_VERIFIED"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
    rc = main(["verify", "--path", str(tmp_path), "--strict"])
    assert rc == 0
    assert "STRICT: all key dimensions satisfied" in capsys.readouterr().out


def test_verify_strict_remote_not_enforced_exits_2(tmp_path, monkeypatch, capsys):
    install_fake_contract(monkeypatch, enforcement_required=False)

    def fake_check(repo, branch, token=None, timeout=15):
        return _ns(status="not_enforced", reasons=["REMOTE_PROTECTION_ABSENT"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda project_dir: ("example/repo", "main"))
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


def _write_releases(tmp_path):
    """A file:// upgrade source with 1.0.1 (patch) and 1.1.0 (minor)."""
    import json
    src = tmp_path / "upstream"
    src.mkdir()
    (src / "releases.json").write_text(json.dumps({
        "releases": [
            {"version": "1.0.1", "tag": "v1.0.1", "commit": None,
             "published_at": None, "prerelease": False,
             "template_bundle": None, "notes": ""},
            {"version": "1.1.0", "tag": "v1.1.0", "commit": None,
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
    assert "1.0.1" in out and "patch" in out
    assert "1.1.0" in out and "minor" in out


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
    rc = main(["upgrade", "--dry-run", "--to", "1.0.1", "--path", str(proj),
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
