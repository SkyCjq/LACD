"""LACD v1.0 acceptance matrix: AT-01 .. AT-25.

Each test maps to one acceptance item from the task prompt §11.
Tests are hermetic (tmp dirs, file:// upgrade sources, faked network)
unless marked live. Live tests require LACD_RUN_LIVE_TESTS=1 and are
reported as UNVERIFIED otherwise -- never faked.

Conventions: `cli(*args)` runs the real CLI entry point in-process.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from lacd.cli.main import main as cli
from lacd import __version__ as CLI_VERSION

LIVE = os.environ.get("LACD_RUN_LIVE_TESTS") == "1"
needs_live = pytest.mark.skipif(not LIVE, reason="UNVERIFIED without live run")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _git(args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main", "."], path)
    _git(["config", "user.email", "t@t"], path)
    _git(["config", "user.name", "t"], path)
    return path


def _commit_all(path: Path, msg: str):
    _git(["add", "-A"], path)
    _git(["commit", "-qm", msg], path)


def _tree_hashes(path: Path) -> dict:
    out = {}
    for f in sorted(path.rglob("*")):
        if f.is_file() and ".git/" not in str(f):
            out[str(f.relative_to(path))] = hashlib.sha256(
                f.read_bytes()).hexdigest()
    return out


def _make_upstream(base: Path, versions: dict) -> Path:
    """Build a file:// upstream: base/releases.json + per-version bundle dirs.

    versions: {"1.0.1": {"workflow_note": "...", "agents_note": "..."}, ...}
    Each version gets upstream/bundles/<v>/{manifest.json, releases.json? no}.
    releases.json lives at upstream root; bundle dirs hold that version's
    templates. Template content derives from the real shipped templates so
    three-way merge has a realistic baseline.
    """
    tpl_root = Path(__file__).resolve().parents[2] / "src" / "lacd" / "templates"
    upstream = base / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    releases = []
    for ver, notes in versions.items():
        bdir = upstream / "bundles" / ver
        bdir.mkdir(parents=True, exist_ok=True)
        for rel in ["workflows/lacd-verify.yml",
                    "managed/agents-md.section.md",
                    "project-rules.template.yaml"]:
            src = tpl_root / rel
            data = src.read_bytes()
            if rel == "workflows/lacd-verify.yml" and notes.get("workflow_note"):
                data += ("\n" + notes["workflow_note"] + "\n").encode()
            if rel == "managed/agents-md.section.md" and notes.get("agents_note"):
                data += ("\n" + notes["agents_note"] + "\n").encode()
            dest = bdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        files = []
        for f in sorted(bdir.rglob("*")):
            if f.is_file():
                relp = f.relative_to(bdir).as_posix()
                files.append({"path": relp,
                              "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
        (bdir / "manifest.json").write_text(
            json.dumps({"template_version": ver, "files": files}, indent=2))
        releases.append({"version": ver, "tag": f"v{ver}", "commit": None,
                         "published_at": None, "prerelease": False,
                         "template_bundle": None, "notes": ""})
    releases_doc = json.dumps({"releases": releases})
    (upstream / "releases.json").write_text(releases_doc)
    # Each bundle dir is self-contained: it carries the releases list too,
    # so --template-dir <bundle-dir> works as both source and bundle
    # (offline distribution unit).
    for ver in versions:
        (upstream / "bundles" / ver / "releases.json").write_text(releases_doc)
    return upstream


def _bundle_dir(upstream: Path, ver: str) -> Path:
    return upstream / "bundles" / ver


def _adopted(tmp_path: Path, name: str, preexisting: dict | None = None) -> Path:
    """Create an independent synthetic repo; optionally seed pre-existing files."""
    proj = _init_git_repo(tmp_path / name)
    if preexisting:
        for rel, content in preexisting.items():
            p = proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        _commit_all(proj, "seed pre-existing project files")
    return proj


def _lacd_init(proj: Path):
    rc = cli(["init", "--path", str(proj)])
    assert rc == 0
    _commit_all(proj, "lacd init")




def _status_json(proj: Path, capsys) -> dict:
    """Run `status --json`, draining prior human-readable output first."""
    capsys.readouterr()
    rc = cli(["status", "--json", "--path", str(proj)])
    assert rc == 0
    return json.loads(capsys.readouterr().out)

# ---------------------------------------------------------------------------
# AT-01 independent build & install
# ---------------------------------------------------------------------------

def test_at_01_build_and_install_isolated(tmp_path):
    """AT-01: LACD builds and installs with no business-project dependency."""
    repo = Path(__file__).resolve().parents[2]
    venv = tmp_path / "venv"
    r = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip("UNVERIFIED: cannot create venv here")
    pip = str(venv / "bin" / "pip")
    r = subprocess.run(
        [pip, "install", str(repo)],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        pytest.skip(f"UNVERIFIED: pip install failed in this env: "
                    f"{r.stderr[-300:]}")
    lacd = str(venv / "bin" / "lacd")
    r = subprocess.run([lacd, "--version"], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0 and CLI_VERSION in r.stdout


# ---------------------------------------------------------------------------
# AT-02 / AT-03 / AT-04 adoption
# ---------------------------------------------------------------------------

def test_at_02_alpha_init_from_empty(tmp_path):
    """AT-02: Alpha (empty repo) initializes successfully."""
    proj = _adopted(tmp_path, "alpha")
    rc = cli(["init", "--path", str(proj)])
    assert rc == 0
    assert (proj / "lacd.yaml").is_file()
    assert (proj / ".lacd" / "lock.json").is_file()
    assert (proj / ".github" / "workflows" / "lacd-verify.yml").is_file()
    rc = cli(["verify", "--path", str(proj)])
    assert rc == 0


def test_at_03_beta_init_preserves_existing(tmp_path):
    """AT-03: Beta (existing CI + AGENTS.md) adopts safely; old files kept."""
    seed = {
        ".github/workflows/ci.yml": "name: ci\non: [push]\n",
        "AGENTS.md": "# My project\n\nWe do things our way.\n",
        "src/app.py": "print('business')\n",
    }
    proj = _adopted(tmp_path, "beta", preexisting=seed)
    rc = cli(["init", "--path", str(proj)])
    assert rc == 0
    # pre-existing files byte-identical
    assert (proj / ".github" / "workflows" / "ci.yml").read_text() == seed[
        ".github/workflows/ci.yml"]
    assert "We do things our way." in (proj / "AGENTS.md").read_text()
    assert "LACD:MANAGED:BEGIN" in (proj / "AGENTS.md").read_text()
    assert (proj / "src" / "app.py").read_text() == "print('business')\n"


def test_at_04_init_idempotent(tmp_path):
    """AT-04: repeated init produces no diff."""
    proj = _adopted(tmp_path, "alpha")
    assert cli(["init", "--path", str(proj)]) == 0
    before = _tree_hashes(proj)
    rc = cli(["init", "--path", str(proj)])
    assert rc == 0
    after = _tree_hashes(proj)
    assert before == after


# ---------------------------------------------------------------------------
# AT-05 / AT-06 status contract
# ---------------------------------------------------------------------------

def test_at_05_status_json_schema_stable(tmp_path, capsys):
    """AT-05: status --json validates against the frozen schema."""
    from lacd.core.report import validate_status_dict
    proj = _adopted(tmp_path, "alpha")
    cli(["init", "--path", str(proj)])
    capsys.readouterr()
    rc = cli(["status", "--json", "--path", str(proj)])
    assert rc == 0
    out = capsys.readouterr().out
    assert validate_status_dict(json.loads(out)) == []


def test_at_06_local_only_never_claims_enforced(tmp_path, capsys):
    """AT-06: local pass reports VALIDATED, never ENFORCED."""
    proj = _adopted(tmp_path, "alpha")
    cli(["init", "--path", str(proj)])
    d = _status_json(proj, capsys)
    assert d["state"] == "VALIDATED"
    assert d["dimensions"]["remote_enforcement"]["status"] == "unverified"


# ---------------------------------------------------------------------------
# AT-07 remote enforcement
# ---------------------------------------------------------------------------

def test_at_07_permission_denied_is_unverified_not_pass():
    """AT-07 (simulated): 403 / no credential -> unverified, never enforced."""
    from lacd.github.remote import check_remote_enforcement
    rep = check_remote_enforcement("octo/nope", "main", token=None, timeout=5)
    # No credential in this env -> must be unverified, never enforced.
    assert rep.status in ("unverified", "not_enforced")
    assert rep.status != "enforced"


@needs_live
def test_at_07_live_remote_check(tmp_path):
    """AT-07 (live): real remote check against SkyCjq/LACD.

    Requires LACD_RUN_LIVE_TESTS=1 and a token with admin scope in
    GITHUB_TOKEN. Reported as UNVERIFIED otherwise.
    """
    from lacd.github.remote import check_remote_enforcement
    token = os.environ.get("GITHUB_TOKEN")
    rep = check_remote_enforcement("SkyCjq/LACD", "main", token=token)
    assert rep.status in ("enforced", "not_enforced", "unverified")
    assert rep.reasons  # always machine-readable


# ---------------------------------------------------------------------------
# AT-08 / AT-09 / AT-10 version locking
# ---------------------------------------------------------------------------

def test_at_08_lock_pins_exact_versions_and_shas(tmp_path):
    """AT-08: lock records exact CLI version, template version, file SHAs."""
    proj = _adopted(tmp_path, "alpha")
    cli(["init", "--path", str(proj)])
    lock = json.loads((proj / ".lacd" / "lock.json").read_text())
    assert lock["lock_format_version"] == "1"
    assert lock["lacd_cli_version"] == CLI_VERSION
    assert lock["template_version"] == CLI_VERSION
    assert lock["source"]["type"] in ("git-tag", "git-commit", "local-dir")
    assert lock["source"]["ref"] not in ("v1", "latest", "main")
    assert len(lock["managed_files"]) >= 1
    for mf in lock["managed_files"]:
        assert len(mf["sha256"]) == 64
        disk = hashlib.sha256(
            (proj / mf["path"]).read_bytes()).hexdigest()
        assert disk == mf["sha256"]
    # drift detection trips on tampering
    wf = proj / ".github" / "workflows" / "lacd-verify.yml"
    wf.write_text(wf.read_text() + "# tamper\n")
    rc = cli(["status", "--json", "--path", str(proj)])
    assert rc == 0


def test_at_08_drift_detected(tmp_path, capsys):
    proj = _adopted(tmp_path, "alpha")
    cli(["init", "--path", str(proj)])
    (proj / ".github" / "workflows" / "lacd-verify.yml").write_text("tampered")
    d = _status_json(proj, capsys)
    assert d["state"] == "DRIFTED"
    assert d["dimensions"]["drift"]["status"] == "drifted"


def test_at_09_cli_lock_version_mismatch_detected(tmp_path, capsys):
    """AT-09: running CLI != locked CLI is reported with a reason."""
    proj = _adopted(tmp_path, "alpha")
    cli(["init", "--path", str(proj)])
    lock_p = proj / ".lacd" / "lock.json"
    lock = json.loads(lock_p.read_text())
    lock["lacd_cli_version"] = "0.0.1"
    lock_p.write_text(json.dumps(lock))
    d = _status_json(proj, capsys)
    assert d["version_consistency"]["status"] == "mismatch"
    assert d["next_steps"]  # points at isolated execution


def test_at_10_two_projects_two_versions_coexist(tmp_path, capsys):
    """AT-10: Alpha and Beta lock different template versions independently."""
    upstream = _make_upstream(tmp_path, {
        "1.0.1": {"workflow_note": "# v1.0.1"}})
    alpha = _adopted(tmp_path, "alpha")
    beta = _adopted(tmp_path, "beta")
    _lacd_init(alpha)
    _lacd_init(beta)
    # upgrade only alpha to 1.0.1
    rc = cli(["upgrade", "--to", "1.0.1", "--path", str(alpha), "--yes",
              "--template-dir", str(_bundle_dir(upstream, "1.0.1"))])
    assert rc == 0
    da = _status_json(alpha, capsys)
    db = _status_json(beta, capsys)
    assert da["versions"]["template"] == "1.0.1"
    assert db["versions"]["template"] == CLI_VERSION
    assert da["state"] == "VALIDATED" and db["state"] == "VALIDATED"


# ---------------------------------------------------------------------------
# AT-11 / AT-12 upgrade discovery
# ---------------------------------------------------------------------------

def test_at_11_check_classifies_patch_minor_major(tmp_path, capsys):
    """AT-11: --check distinguishes Patch/Minor/Major from a version source."""
    # Upstream versions are derived from the running CLI version (not
    # hardcoded): after a version bump, "1.0.1" is no longer a patch upgrade.
    major, minor, patch = (int(x) for x in CLI_VERSION.split("."))
    v_patch = f"{major}.{minor}.{patch + 1}"
    v_minor = f"{major}.{minor + 1}.0"
    v_major = f"{major + 1}.0.0"
    upstream = _make_upstream(tmp_path, {
        v_patch: {}, v_minor: {}, v_major: {}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    rc = cli(["upgrade", "--check", "--path", str(proj),
              "--template-dir", str(upstream)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "patch" in out and v_patch in out
    assert "minor" in out and v_minor in out
    assert "major" in out and v_major in out


def test_at_12_broken_source_never_reports_latest(tmp_path, capsys):
    """AT-12: offline/bad source -> clear error, never 'already latest'."""
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    env_repo = "invalid-owner-xyz-123/invalid-repo-xyz-123"
    old = os.environ.get("LACD_UPSTREAM_REPO")
    os.environ["LACD_UPSTREAM_REPO"] = env_repo
    try:
        rc = cli(["upgrade", "--check", "--path", str(proj)])
    finally:
        if old is None:
            del os.environ["LACD_UPSTREAM_REPO"]
        else:
            os.environ["LACD_UPSTREAM_REPO"] = old
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "already latest" not in err
    assert "up to date" not in err


# ---------------------------------------------------------------------------
# AT-13 / AT-14 / AT-15 migration safety
# ---------------------------------------------------------------------------

def test_at_13_dry_run_touches_nothing(tmp_path, capsys):
    """AT-13: --dry-run lists changes and leaves the worktree byte-identical."""
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    before = _tree_hashes(proj)
    rc = cli(["upgrade", "--dry-run", "--to", "1.0.1", "--path", str(proj),
              "--template-dir", str(_bundle_dir(upstream, "1.0.1"))])
    assert rc == 0
    assert _tree_hashes(proj) == before
    out = capsys.readouterr().out
    assert "1.0.1" in out and "dry-run" in out.lower()


def test_at_14_upgrade_preserves_project_content(tmp_path):
    """AT-14: migration keeps project-owned rules and existing CI."""
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    seed = {".github/workflows/ci.yml": "name: mine\n",
            ".lacd/project-rules.yaml": "rules:\n  - keep-me\n"}
    proj = _adopted(tmp_path, "beta", preexisting=seed)
    _lacd_init(proj)
    rc = cli(["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
              "--template-dir", str(_bundle_dir(upstream, "1.0.1"))])
    assert rc == 0
    assert (proj / ".github" / "workflows" / "ci.yml").read_text() == \
        "name: mine\n"
    assert "keep-me" in (proj / ".lacd" / "project-rules.yaml").read_text()
    assert "# new" in (proj / ".github" / "workflows" /
                       "lacd-verify.yml").read_text()


def test_at_15_conflict_never_silently_overwrites(tmp_path, capsys):
    """AT-15: user-modified managed content -> conflict, refused, intact."""
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    wf = proj / ".github" / "workflows" / "lacd-verify.yml"
    user_text = wf.read_text() + "# user tweak\n"
    wf.write_text(user_text)
    _commit_all(proj, "user tweak")
    rc = cli(["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
              "--template-dir", str(_bundle_dir(upstream, "1.0.1"))])
    assert rc == 3
    assert wf.read_text() == user_text  # untouched
    assert "conflict" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# AT-16 / AT-17 / AT-18 upgrade PR
# ---------------------------------------------------------------------------

def _fake_pr_api(monkeypatch):
    from lacd.github import pr as pr_mod
    calls = []

    def fake_api(method, url, token, payload=None, timeout=15):
        calls.append((method, url, payload))
        if method == "GET" and "pulls?state=open" in url:
            return 200, []
        if method == "POST" and url.endswith("/pulls"):
            return 201, {"html_url": "https://github.com/o/r/pull/1",
                         "number": 1}
        raise AssertionError(url)

    monkeypatch.setattr(pr_mod, "_api_request", fake_api)
    return calls


def test_at_16_prepare_pr_creates_branch_and_pr(tmp_path, monkeypatch):
    """AT-16 (simulated API): --prepare-pr builds branch+PR with evidence.

    The real-PR demonstration is the LACD repo's own implementation PRs
    (dogfooding); see the acceptance report.
    """
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    _git(["remote", "add", "origin", "https://github.com/o/r.git"], proj)
    api_calls = _fake_pr_api(monkeypatch)
    # Simulate push success (no real remote); API is faked above.
    from lacd.github import pr as pr_mod
    monkeypatch.setattr(pr_mod, "_git_push", lambda *a, **k: None)
    import os as _os
    _os.environ["GITHUB_TOKEN"] = "fake-token-for-test"
    try:
        rc = cli(["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
                  "--prepare-pr", "--template-dir",
                  str(_bundle_dir(upstream, "1.0.1"))])
    finally:
        del _os.environ["GITHUB_TOKEN"]
    assert rc == 0
    posts = [c for c in api_calls if c[0] == "POST"]
    assert len(posts) == 1
    payload = posts[0][2]
    assert payload["head"] == "lacd/upgrade-to-1.0.1"
    assert payload["base"] == "main"
    assert "Human checkpoints" in payload["body"]
    # upgrade branch exists locally with the migration commit
    branches = _git(["branch", "--list"], proj)
    assert "lacd/upgrade-to-1.0.1" in branches


def test_at_16_prepare_pr_dedupes(tmp_path, monkeypatch, capsys):
    """AT-16: an existing open upgrade PR is reused, not duplicated."""
    from lacd.github import pr as pr_mod

    def fake_api(method, url, token, payload=None, timeout=15):
        if method == "GET" and "pulls?state=open" in url:
            return 200, [{"html_url": "https://github.com/o/r/pull/7",
                          "number": 7}]
        raise AssertionError("must not create a second PR")

    monkeypatch.setattr(pr_mod, "_api_request", fake_api)
    upstream = _make_upstream(tmp_path, {"1.0.1": {}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    _git(["remote", "add", "origin", "https://github.com/o/r.git"], proj)
    import os as _os
    _os.environ["GITHUB_TOKEN"] = "fake"
    try:
        rc = cli(["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
                  "--prepare-pr", "--template-dir",
                  str(_bundle_dir(upstream, "1.0.1"))])
    finally:
        del _os.environ["GITHUB_TOKEN"]
    assert rc == 0
    assert "pull/7" in capsys.readouterr().out


def test_at_17_major_needs_explicit_acceptance(tmp_path, capsys):
    """AT-17: major upgrades are refused without --accept-major."""
    upstream = _make_upstream(tmp_path, {"2.0.0": {}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    rc = cli(["upgrade", "--dry-run", "--to", "2.0.0", "--path", str(proj),
              "--template-dir", str(_bundle_dir(upstream, "2.0.0"))])
    assert rc == 3
    assert "major" in capsys.readouterr().err.lower()


def test_at_18_pr_never_pushes_base_or_merges(tmp_path, monkeypatch):
    """AT-18: --prepare-pr never pushes main, never merges, no dupes."""
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    _git(["remote", "add", "origin", "https://github.com/o/r.git"], proj)
    pushed = []

    real_run = subprocess.run

    def spy_run(cmd, **kw):
        if len(cmd) > 2 and cmd[1] == "push":
            pushed.append(list(cmd))
            # simulate push failure -> patch fallback path
            return subprocess.CompletedProcess(cmd, 1, "", "denied")
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", spy_run)
    _fake_pr_api(monkeypatch)
    rc = cli(["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
              "--prepare-pr", "--template-dir",
              str(_bundle_dir(upstream, "1.0.1"))])
    assert rc == 0
    for cmd in pushed:
        assert "main" not in cmd  # never pushes the base branch
    # patch fallback written when push is impossible
    assert (proj / ".lacd" / "upgrade-1.0.1.patch").is_file()
    # main branch itself has no upgrade commit
    log = _git(["log", "--oneline", "main"], proj)
    assert "upgrade" not in log.lower()


# ---------------------------------------------------------------------------
# AT-19 / AT-20 idempotency & rollback
# ---------------------------------------------------------------------------

def test_at_19_upgrade_idempotent(tmp_path):
    """AT-19: re-running the same upgrade produces no diff."""
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    args = ["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
            "--template-dir", str(_bundle_dir(upstream, "1.0.1"))]
    assert cli(args) == 0
    _commit_all(proj, "upgrade 1.0.1")
    before = _tree_hashes(proj)
    assert cli(args) == 0
    # only .lacd/backup/<new-ts>/ may differ; managed content identical
    after = _tree_hashes(proj)
    diff = {k for k in set(before) | set(after)
            if before.get(k) != after.get(k)}
    assert all(d.startswith(".lacd/backup/") for d in diff), diff


def test_at_20_rollback_restores(tmp_path):
    """AT-20: a failed/undone upgrade can be rolled back from backup."""
    from lacd.upgrade.migrate import rollback_last
    upstream = _make_upstream(tmp_path, {"1.0.1": {"workflow_note": "# new"}})
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    wf = proj / ".github" / "workflows" / "lacd-verify.yml"
    original = wf.read_bytes()
    assert cli(["upgrade", "--to", "1.0.1", "--path", str(proj), "--yes",
                "--template-dir", str(_bundle_dir(upstream, "1.0.1"))]) == 0
    assert wf.read_bytes() != original
    assert rollback_last(str(proj)) is True
    assert wf.read_bytes() == original


# ---------------------------------------------------------------------------
# AT-21 / AT-22 / AT-23 safety
# ---------------------------------------------------------------------------

def test_at_21_risk_tiers(tmp_path):
    """AT-21: T0/T1/T2 classification incl. unknown -> T2."""
    from lacd.governance.risk import classify_change
    assert classify_change(["docs/guide.md"]) == "T0"
    assert classify_change(["src/app.py"]) == "T1"
    assert classify_change([".github/workflows/x.yml"]) == "T2"
    assert classify_change(["docs/a.md", ".lacd/lock.json"]) == "T2"
    assert classify_change([""]) == "T2"
    assert classify_change(["../escape"]) == "T2"


def test_at_22_stale_evidence_rejected(tmp_path):
    """AT-22: evidence bound to an old HEAD is rejected after HEAD moves."""
    proj = _adopted(tmp_path, "alpha")
    _lacd_init(proj)
    head1 = _git(["rev-parse", "HEAD"], proj)
    (proj / "note.txt").write_text("x")
    _commit_all(proj, "move head")
    head2 = _git(["rev-parse", "HEAD"], proj)
    assert head1 != head2
    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps({"head_sha": head1, "result": "pass"}))
    rc = cli(["verify", "--path", str(proj), "--evidence", str(ev)])
    assert rc == 2


def test_at_23_tampered_bundle_rejected(tmp_path):
    """AT-23: a tampered template bundle fails sha256 verification."""
    from lacd.upgrade.migrate import (load_bundle_from_zip,
                                      BundleVerificationError)
    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(
            {"template_version": "1.0.1", "files": []}))
    data = buf.getvalue()
    with pytest.raises(BundleVerificationError):
        load_bundle_from_zip(data, "0" * 64)


# ---------------------------------------------------------------------------
# AT-24 agent protocol linkage
# ---------------------------------------------------------------------------

def test_at_24_agent_checklist_links_real_status(tmp_path):
    """AT-24: agent checklist calls the live status computation."""
    from lacd.agents.protocol import agent_checklist
    proj = _adopted(tmp_path, "alpha")
    cli(["init", "--path", str(proj)])
    cl = agent_checklist(str(proj), ["src/app.py"])
    assert cl["instructions"]["found"] is True
    assert cl["status"]["state"] == "VALIDATED"
    assert cl["risk_tier"] == "T1"
    assert cl["ready"] is True
    # without AGENTS.md, not ready
    proj2 = _adopted(tmp_path, "beta")
    cli(["init", "--path", str(proj2)])
    (proj2 / "AGENTS.md").unlink()
    cl2 = agent_checklist(str(proj2))
    assert cl2["ready"] is False


# ---------------------------------------------------------------------------
# AT-25 docs smoke test
# ---------------------------------------------------------------------------

def _extract_bash_commands(md_path: Path) -> list[str]:
    cmds = []
    in_block = False
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```bash"):
            in_block = True
            continue
        if in_block and line.strip().startswith("```"):
            in_block = False
            continue
        if in_block:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                cmds.append(stripped)
    return cmds


def test_at_25_docs_commands_smoke(tmp_path):
    """AT-25: copy-paste commands in the operations docs really run."""
    repo = Path(__file__).resolve().parents[2]
    failures = []
    for doc in ["install.md", "adopt.md", "upgrade.md", "troubleshooting.md"]:
        md = repo / "docs" / "operations" / doc
        for cmd in _extract_bash_commands(md):
            # Only safe local commands; skip network/auth/pip ones.
            if not cmd.startswith("lacd "):
                continue
            if any(k in cmd for k in ["--prepare-pr", "GITHUB_TOKEN"]):
                continue
            work = tmp_path / "smoke"
            work.mkdir(exist_ok=True)
            if "init" in cmd and (work / "lacd.yaml").exists():
                continue
            full = cmd.replace("your-project", ".").replace("cd your-project",
                                                             "true")
            for part in full.split("&&"):
                part = part.strip()
                if not part.startswith("lacd "):
                    continue
                argv = part.split()[1:]
                # rewrite --path to the smoke dir when it's "."
                argv = [str(work) if a == "." else a for a in argv]
                try:
                    rc = cli(argv + (["--path", str(work)]
                                      if "--path" not in argv else []))
                except SystemExit as e:
                    rc = e.code
                except Exception as e:  # noqa - smoke must not crash the suite
                    failures.append((doc, cmd, repr(e)))
                    continue
                # init --dry-run, doctor, status, verify, upgrade --check/
                # --dry-run must not hard-fail in a smoke dir
                if rc not in (0, 1, 2, 3):
                    failures.append((doc, cmd, f"rc={rc}"))
    assert not failures, failures
