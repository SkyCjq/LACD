"""End-to-end lifecycles on two independent synthetic repos.

Project Alpha: empty repo -> init -> check -> dry-run -> apply -> idempotent
re-run -> verify -> rollback.
Project Beta: pre-existing CI/AGENTS.md -> init (preserved) -> conflict ->
resolve -> upgrade -> simulated --prepare-pr.

Both repos are created from scratch in tmp, share nothing, and contain no
business content. Helpers live in tests.acceptance.test_at_matrix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acceptance.test_at_matrix import (  # noqa: E402
    _adopted, _bundle_dir, _commit_all, _lacd_init, _make_upstream,
    _status_json, _tree_hashes,
)
from lacd.cli.main import main as cli  # noqa: E402
from lacd.upgrade.migrate import rollback_last  # noqa: E402


def _uargs(proj, upstream, ver, *extra):
    return ["upgrade", "--to", ver, "--path", str(proj), "--yes",
            "--template-dir", str(_bundle_dir(upstream, ver)), *extra]


def test_alpha_full_lifecycle(tmp_path, capsys):
    upstream = _make_upstream(tmp_path, {
        "1.0.3": {"workflow_note": "# alpha-1.0.1"},
        "1.1.0": {"workflow_note": "# alpha-1.1.0"},
    })
    alpha = _adopted(tmp_path, "alpha")
    assert _status_json(alpha, capsys)["state"] == "NOT_INSTALLED"

    # init -> VALIDATED
    _lacd_init(alpha)
    assert _status_json(alpha, capsys)["state"] == "VALIDATED"

    # check discovers both versions
    capsys.readouterr()
    assert cli(["upgrade", "--check", "--path", str(alpha),
                "--template-dir", str(_bundle_dir(upstream, "1.1.0"))]) == 0
    out = capsys.readouterr().out
    assert "1.0.3" in out and "1.1.0" in out

    # dry-run touches nothing
    before = _tree_hashes(alpha)
    assert cli(["upgrade", "--dry-run", "--to", "1.0.3", "--path", str(alpha),
                "--template-dir", str(_bundle_dir(upstream, "1.0.3"))]) == 0
    assert _tree_hashes(alpha) == before

    # apply 1.0.1 -> template version moves
    assert cli(_uargs(alpha, upstream, "1.0.3")) == 0
    assert _status_json(alpha, capsys)["versions"]["template"] == "1.0.3"
    _commit_all(alpha, "upgrade 1.0.1")

    # idempotent re-run: zero diff outside backup dirs
    before = _tree_hashes(alpha)
    assert cli(_uargs(alpha, upstream, "1.0.3")) == 0
    after = _tree_hashes(alpha)
    assert before == after

    # verify passes; strict fails only on unverifiable remote (honest)
    assert cli(["verify", "--path", str(alpha)]) == 0

    # rollback restores the pre-upgrade template content
    wf = alpha / ".github" / "workflows" / "lacd-verify.yml"
    assert "# alpha-1.0.1" in wf.read_text()
    assert rollback_last(str(alpha)) is True
    assert "# alpha-1.0.1" not in wf.read_text()


def test_beta_conflict_then_upgrade_then_pr(tmp_path, monkeypatch, capsys):
    from lacd.github import pr as pr_mod
    import os

    upstream = _make_upstream(tmp_path, {
        "1.0.3": {"workflow_note": "# beta-1.0.1"},
        "1.1.0": {"workflow_note": "# beta-1.1.0"},
    })
    seed = {
        ".github/workflows/ci.yml": "name: existing-ci\n",
        "AGENTS.md": "# Beta\n\nOur own instructions.\n",
    }
    beta = _adopted(tmp_path, "beta", preexisting=seed)
    _lacd_init(beta)
    # pre-existing content preserved
    assert "Our own instructions." in (beta / "AGENTS.md").read_text()
    assert (beta / ".github" / "workflows" / "ci.yml").read_text() == \
        "name: existing-ci\n"

    # user edits a managed file -> upgrade conflicts, refuses, keeps content
    wf = beta / ".github" / "workflows" / "lacd-verify.yml"
    customized = wf.read_text() + "# beta team tweak\n"
    wf.write_text(customized)
    _commit_all(beta, "beta team tweak")
    assert _status_json(beta, capsys)["state"] == "DRIFTED"
    rc = cli(_uargs(beta, upstream, "1.0.3"))
    assert rc == 3
    assert wf.read_text() == customized

    # resolve: take the upstream template, re-run -> clean upgrade
    from pathlib import Path as _P
    import shutil
    bundle_wf = _bundle_dir(upstream, "1.0.3") / "workflows" / "lacd-verify.yml"
    shutil.copy(bundle_wf, wf)
    _commit_all(beta, "accept upstream workflow")
    assert cli(_uargs(beta, upstream, "1.0.3")) == 0
    assert _status_json(beta, capsys)["versions"]["template"] == "1.0.3"
    # project-owned files still intact
    assert (beta / ".github" / "workflows" / "ci.yml").read_text() == \
        "name: existing-ci\n"
    _commit_all(beta, "upgrade to 1.0.1")

    # simulated --prepare-pr to the next version
    from acceptance.test_at_matrix import _git as _git2
    _git2(["remote", "add", "origin", "https://github.com/o/beta.git"], beta)
    calls = []

    def fake_api(method, url, token, payload=None, timeout=15):
        calls.append((method, url, payload))
        if method == "GET" and "pulls?state=open" in url:
            return 200, []
        if method == "POST" and url.endswith("/pulls"):
            return 201, {"html_url": "https://github.com/o/beta/pull/1",
                         "number": 1}
        raise AssertionError(url)

    monkeypatch.setattr(pr_mod, "_api_request", fake_api)
    monkeypatch.setattr(pr_mod, "_git_push", lambda *a, **k: None)
    os.environ["GITHUB_TOKEN"] = "fake"
    try:
        rc = cli(_uargs(beta, upstream, "1.1.0", "--prepare-pr"))
    finally:
        del os.environ["GITHUB_TOKEN"]
    assert rc == 0
    assert any(c[0] == "POST" for c in calls)
