"""Unit tests for lacd.github.pr (MODULE_CONTRACT §6.2).

git subprocess calls and the GitHub API are faked; no real remote or
repository is touched.
"""

import subprocess
from types import SimpleNamespace

import pytest

from lacd.github import pr
from lacd.github.pr import (
    PrError,
    find_existing_upgrade_pr,
    prepare_upgrade_pr,
    upgrade_branch_name,
)

TOKEN = "tok-test-123"
REPO = "octo/hello"


def _plan():
    return SimpleNamespace(
        from_version="1.0.0",
        to_version="1.0.1",
        changes=[
            SimpleNamespace(path="src/x.py", action="update", risk="T1",
                            detail="template sync"),
        ],
        conflicts=[],
        human_checkpoints=["Review the managed workflow diff"],
    )


def _bundle():
    return SimpleNamespace(version="1.0.1", files={}, manifest={})


@pytest.fixture(autouse=True)
def _fake_apply(monkeypatch):
    """Fake apply_migration: records calls, returns an empty backup dir."""
    calls = []

    def fake_apply(project_dir, plan, bundle):
        calls.append((str(project_dir), plan, bundle))
        return SimpleNamespace(applied=["src/x.py"], backup_dir="",
                              lock_updated=True)

    monkeypatch.setattr(pr, "apply_migration", fake_apply)
    return calls


class _FakeGit:
    """Fake subprocess.run for git. Records calls; simulates a repo."""

    def __init__(self, dirty=False, push_ok=True):
        self.calls = []
        self.dirty = dirty
        self.push_ok = push_ok

    def __call__(self, cmd, cwd=None, capture_output=False, text=False,
                 timeout=None, env=None):
        self.calls.append((list(cmd), env))
        args = list(cmd)[1:]
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["rev-parse", "HEAD"]:
            result.stdout = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        elif args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            result.stdout = "feature\n"
        elif args == ["status", "--porcelain"]:
            result.stdout = " M src/x.py\n" if self.dirty else ""
        elif args[:2] == ["diff", "--cached"]:
            result.stdout = "src/x.py\n"
        elif args[0] == "format-patch":
            result.stdout = "From deadbeef Mon Sep 17 00:00:00 2001\nfake patch\n"
        elif args[0] == "push" and not self.push_ok:
            result.returncode = 1
            result.stderr = "remote: permission denied"
        return result

    def commands(self):
        return [c[0] for c in self.calls]


def _install_git(monkeypatch, **kwargs):
    fake = _FakeGit(**kwargs)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def _install_api(monkeypatch, existing=None, create_url="https://github.com/octo/hello/pull/5"):
    calls = []

    def fake_api(method, url, token, payload=None, timeout=15):
        calls.append((method, url, payload))
        if method == "GET" and "pulls?state=open" in url:
            if existing is None:
                return 200, []
            return 200, [existing]
        if method == "POST" and url.endswith("/pulls"):
            return 201, {"html_url": create_url, "number": 5}
        raise AssertionError(f"unexpected API call: {method} {url}")

    monkeypatch.setattr(pr, "_api_request", fake_api)
    return calls


# ---------- branch naming ----------

def test_upgrade_branch_name():
    assert upgrade_branch_name("1.0.1") == "lacd/upgrade-to-1.0.1"


def test_upgrade_branch_name_rejects_empty():
    with pytest.raises(ValueError):
        upgrade_branch_name("")


# ---------- find_existing_upgrade_pr ----------

def test_find_existing_pr_returns_first(monkeypatch):
    _install_api(monkeypatch, existing={
        "number": 3, "html_url": "https://github.com/octo/hello/pull/3",
        "title": "chore(upgrade): LACD 1.0.0 -> 1.0.1", "state": "open",
    })
    found = find_existing_upgrade_pr(REPO, "1.0.1", TOKEN)
    assert found["number"] == 3
    assert found["html_url"] == "https://github.com/octo/hello/pull/3"
    assert found["head"] == "lacd/upgrade-to-1.0.1"


def test_find_existing_pr_none_when_empty(monkeypatch):
    _install_api(monkeypatch, existing=None)
    assert find_existing_upgrade_pr(REPO, "1.0.1", TOKEN) is None


def test_find_existing_pr_no_token_makes_no_request(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("no API call without token")

    monkeypatch.setattr(pr, "_api_request", _boom)
    assert find_existing_upgrade_pr(REPO, "1.0.1", None) is None


# ---------- prepare_upgrade_pr: reuse ----------

def test_prepare_reuses_existing_pr(tmp_path, monkeypatch):
    fake_git = _install_git(monkeypatch)
    monkeypatch.setattr(
        pr, "find_existing_upgrade_pr",
        lambda repo, to_version, token: {
            "html_url": "https://github.com/octo/hello/pull/3"},
    )
    report = prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)
    assert report.reused is True
    assert report.created is False
    assert report.pr_url == "https://github.com/octo/hello/pull/3"
    assert report.branch == "lacd/upgrade-to-1.0.1"
    # No branch was created: git was never invoked.
    assert fake_git.calls == []


# ---------- prepare_upgrade_pr: push + create ----------

def test_prepare_pushes_and_creates_pr(tmp_path, monkeypatch):
    fake_git = _install_git(monkeypatch, push_ok=True)
    api_calls = _install_api(monkeypatch, existing=None)
    report = prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)

    assert report.created is True
    assert report.reused is False
    assert report.pr_url == "https://github.com/octo/hello/pull/5"
    assert report.branch == "lacd/upgrade-to-1.0.1"
    assert report.local_patch is None

    cmds = fake_git.commands()
    # Branch created from current HEAD, then committed.
    assert ["git", "checkout", "-b", "lacd/upgrade-to-1.0.1"] in cmds
    assert any(c[0:2] == ["git", "commit"] for c in cmds)
    # Commit message carries version + plan summary.
    commit_call = next(c for c in fake_git.calls if c[0][1] == "commit")
    msg = commit_call[0][commit_call[0].index("-m") + 1]
    assert "1.0.1" in msg and "1.0.0" in msg
    # Pushed exactly the upgrade branch -- never the base branch.
    pushes = [c for c in fake_git.calls if c[0][1] == "push"]
    assert len(pushes) == 1
    assert pushes[0][0][-1] == "lacd/upgrade-to-1.0.1"
    assert not any("main" in c[0] for c in pushes)
    # PR created against base main with the right title/body.
    posts = [c for c in api_calls if c[0] == "POST"]
    assert len(posts) == 1
    payload = posts[0][2]
    assert payload["head"] == "lacd/upgrade-to-1.0.1"
    assert payload["base"] == "main"
    assert payload["title"] == "chore(upgrade): LACD 1.0.0 -> 1.0.1"
    assert "deadbeef" in payload["body"]  # HEAD SHA recorded
    assert "Human checkpoints" in payload["body"]


def test_prepare_rejects_dirty_tree(tmp_path, monkeypatch):
    _install_git(monkeypatch, dirty=True)
    _install_api(monkeypatch, existing=None)
    with pytest.raises(PrError, match="not clean"):
        prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)


def test_prepare_rejects_bad_repo(tmp_path, monkeypatch):
    _install_git(monkeypatch)
    with pytest.raises(ValueError):
        prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), "bad", TOKEN)


# ---------- prepare_upgrade_pr: offline patch mode ----------

def test_prepare_no_token_writes_patch_and_steps(tmp_path, monkeypatch):
    fake_git = _install_git(monkeypatch, push_ok=True)
    report = prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, None)

    assert report.created is False
    assert report.reused is False
    assert report.pr_url is None
    assert report.local_patch is not None
    patch = tmp_path / ".lacd" / "upgrade-1.0.1.patch"
    assert report.local_patch == str(patch)
    assert patch.is_file()
    assert "fake patch" in patch.read_text()
    # No push attempted without a token.
    assert not any(c[0][1] == "push" for c in fake_git.calls)
    # Human steps are actionable and mention human merge.
    assert len(report.steps) >= 4
    assert any("merge" in step for step in report.steps)


def test_prepare_push_failure_falls_back_to_patch(tmp_path, monkeypatch):
    _install_git(monkeypatch, push_ok=False)
    _install_api(monkeypatch, existing=None)
    report = prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)
    assert report.created is False
    assert report.local_patch is not None
    assert (tmp_path / ".lacd" / "upgrade-1.0.1.patch").is_file()
    assert report.steps


def test_prepare_never_merges_or_pushes_base(tmp_path, monkeypatch):
    # There is no merge/push-to-base code path: assert on the fake's log.
    _install_git(monkeypatch, push_ok=True)
    _install_api(monkeypatch, existing=None)
def test_prepare_never_merges_or_pushes_base(tmp_path, monkeypatch):
    # Iron rule: prepare_upgrade_pr never merges and never pushes to base.
    fake_git = _install_git(monkeypatch, push_ok=True)
    _install_api(monkeypatch, existing=None)
    prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)
    cmds = fake_git.commands()
    assert not any(c[1] == "merge" for c in cmds)
    for cmd in cmds:
        if cmd[1] == "push":
            assert "main" not in cmd, f"must never push to base: {cmd}"


def test_prepare_applies_migration_on_branch(tmp_path, monkeypatch, _fake_apply):
    """apply_migration runs after checkout -b, before commit."""
    fake_git = _install_git(monkeypatch, push_ok=True)
    _install_api(monkeypatch, existing=None)
    prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)
    assert len(_fake_apply) == 1
    proj, plan, bundle = _fake_apply[0]
    assert proj == str(tmp_path)
    assert bundle.version == "1.0.1"
    cmds = fake_git.commands()
    co_idx = cmds.index(["git", "checkout", "-b", "lacd/upgrade-to-1.0.1"])
    commit_idx = next(i for i, c in enumerate(cmds) if c[1] == "commit")
    assert co_idx < commit_idx  # migration applied on the new branch


def test_prepare_removes_backup_before_commit(tmp_path, monkeypatch):
    """The .lacd/backup snapshot must not enter the PR commit."""
    from lacd.upgrade.migrate import MigrationConflict

    backup = tmp_path / ".lacd" / "backup" / "20260101T000000Z"
    backup.mkdir(parents=True)
    (backup / "x.txt").write_text("orig")

    def fake_apply(project_dir, plan, bundle):
        return SimpleNamespace(applied=["src/x.py"], backup_dir=str(backup),
                              lock_updated=True)

    monkeypatch.setattr(pr, "apply_migration", fake_apply)
    _install_git(monkeypatch, push_ok=True)
    _install_api(monkeypatch, existing=None)
    prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)
    assert not backup.exists()


def test_prepare_migration_failure_cleans_branch(tmp_path, monkeypatch):
    """A failing migration restores the original branch and deletes the new one."""
    from lacd.upgrade.migrate import MigrationConflict

    def boom(project_dir, plan, bundle):
        raise MigrationConflict("conflict: src/x.py")

    monkeypatch.setattr(pr, "apply_migration", boom)
    fake_git = _install_git(monkeypatch, push_ok=True)
    _install_api(monkeypatch, existing=None)
    with pytest.raises(PrError, match="migration failed"):
        prepare_upgrade_pr(tmp_path, "1.0.1", _plan(), _bundle(), REPO, TOKEN)
    cmds = fake_git.commands()
    assert ["git", "checkout", "feature"] in cmds  # back to original branch
    assert ["git", "branch", "-D", "lacd/upgrade-to-1.0.1"] in cmds
