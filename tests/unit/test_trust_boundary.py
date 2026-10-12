"""SEC-02/SEC-08: Trust boundary negative tests with real Git.

These tests use real git repos (not mocks for the trust logic).
Only the GitHub network response is controlled.
"""
import subprocess
import pytest
from pathlib import Path


def _git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True, timeout=30)
    return r


def _make_repo(tmp_path):
    """Create a git repo with origin/main and a PR branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    # Baseline: trusted config on main
    (repo / "lacd.yaml").write_text(
        "schema_version: '1'\n"
        "lacd: {enabled: true, mode: lacd, policy_version: '1'}\n"
        "governance:\n"
        "  pull_request_required: true\n"
        "  human_merge_required: true\n"
        "  risk_tiers: [T0]\n"
        "  required_checks: [ci, security]\n"
        "verification: {bind_to_commit_sha: true, fail_closed: true, github_enforcement_required: true}\n"
        "agents: {protocol: lacd, auto_detect: true}\n"
        "project: {extensions: .lacd/project-rules.yaml}\n"
    )
    _git(repo, "add", "lacd.yaml")
    _git(repo, "commit", "-m", "baseline")
    # Create origin/main ref (simulate protected base)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def test_sec02_pr_committed_weakened_config_not_used(tmp_path, monkeypatch):
    """SEC-02: PR commits weakened checks to its HEAD; trust boundary must
    still use origin/main baseline, not the PR's weakened config."""
    import sys
    sys.path.insert(0, "src")
    from lacd.cli import main as cli_main

    repo = _make_repo(tmp_path)
    # PR branch: weaken checks and COMMIT (git status will be clean!)
    _git(repo, "checkout", "-b", "pr-branch")
    (repo / "lacd.yaml").write_text(
        (repo / "lacd.yaml").read_text().replace(
            "required_checks: [ci, security]", "required_checks: [ci]"))
    _git(repo, "add", "lacd.yaml")
    _git(repo, "commit", "-m", "weaken checks")

    # Verify git status is clean (the old check would pass!)
    r = _git(repo, "status", "--porcelain")
    assert r.stdout.strip() == "", "PR HEAD should be clean"

    # The trust boundary must read from origin/main, not PR HEAD
    sha, warnings, error = cli_main._check_config_trust(repo)
    assert error is None, f"trust error: {error}"
    assert len(sha) == 40

    checks, check_warnings = cli_main._get_trusted_required_checks(repo, sha)
    # Must be baseline [ci, security], NOT PR's [ci]
    assert checks == ["ci", "security"], f"got {checks} - TRUST BOUNDARY BROKEN"


def test_sec08_git_failure_is_unverified_not_enforced(tmp_path, monkeypatch):
    """SEC-08: Git returns 128 -> UNVERIFIED, never ENFORCED."""
    import sys
    sys.path.insert(0, "src")
    from lacd.cli import main as cli_main

    # Non-git directory
    plain = tmp_path / "plain"
    plain.mkdir()

    sha, warnings, error = cli_main._check_config_trust(plain)
    assert sha is None
    assert error in ("POLICY_TRUST_ANCHOR_MISSING", "POLICY_GIT_ERROR")
