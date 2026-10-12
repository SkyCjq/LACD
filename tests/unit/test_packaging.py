"""Packaging tests: wheel contents, resource entry points, clean install.

D1/FR-01 + ADR-0008. These tests perform REAL builds and installs:
a failure here is a failure, never a skip (the v1.0.0 defect #2 was
"wheel lacks templates" → clean install could not run ``lacd init``).
Only live-permission tests may be marked UNVERIFIED; these are not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from lacd import __version__ as LACD_VERSION

REPO = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command; raise on non-zero exit with output attached."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                          **kwargs)
    assert proc.returncode == 0, (
        f"command failed: {' '.join(cmd)}\n--- stdout ---\n{proc.stdout[-2000:]}"
        f"\n--- stderr ---\n{proc.stderr[-2000:]}"
    )
    return proc


def _clean_env() -> dict:
    """Environment for venv/pip/installed-lacd subprocesses.

    The test runner's ``PYTHONPATH=src`` must NOT leak in: it makes the
    venv's pip silently skip installing ``lacd`` (it "sees" the source
    tree) and would make ``lacd`` run from source instead of the wheel.
    """
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def _build_wheel(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    _run([sys.executable, "-m", "pip", "wheel", "--no-deps",
          "-w", str(dest), str(REPO)])
    wheels = list(dest.glob("lacd-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return wheels[0]


# ---------------------------------------------------------------------------
# wheel contents (defect #2 regression)
# ---------------------------------------------------------------------------

def test_wheel_contains_templates_and_schemas(tmp_path):
    """P0: the wheel must ship lacd/templates/** and lacd/schemas/*.json."""
    wheel = _build_wheel(tmp_path / "whl")
    names = zipfile.ZipFile(wheel).namelist()
    # every template file present (not just the directory)
    assert "lacd/templates/__init__.py" in names
    assert "lacd/templates/manifest.json" in names
    assert any(n.startswith("lacd/templates/workflows/") and n.endswith(".yml")
               for n in names), "workflow template missing from wheel"
    assert any(n.startswith("lacd/templates/managed/") for n in names), \
        "managed template missing from wheel"
    assert "lacd/templates/project-rules.template.yaml" in names
    # schemas
    assert "lacd/schemas/lock.schema.json" in names
    assert "lacd/schemas/lacd-config.schema.json" in names
    assert "lacd/schemas/status.schema.json" in names
    # version baked in
    assert f"lacd-{LACD_VERSION}-" in wheel.name, wheel.name


def test_wheel_has_no_build_artifacts(tmp_path):
    """build/, .coverage, __pycache__ must not ride along (D7)."""
    wheel = _build_wheel(tmp_path / "whl")
    names = zipfile.ZipFile(wheel).namelist()
    bad = [n for n in names
           if "__pycache__" in n or n.endswith(".pyc")
           or "/build/" in n or n.endswith(".coverage")]
    assert not bad, f"build artifacts in wheel: {bad[:5]}"


# ---------------------------------------------------------------------------
# resource entry points (MODULE_CONTRACT §9: the only two)
# ---------------------------------------------------------------------------

def test_templates_dir_entry_point():
    from lacd.cli import templates_dir
    td = templates_dir()
    assert td.is_dir(), td
    assert (td / "manifest.json").is_file()
    assert (td / "workflows").is_dir()
    assert (td / "managed").is_dir()
    manifest = json.loads((td / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["template_version"]  # non-empty
    assert manifest["files"], "manifest lists no files"


def test_schemas_dir_entry_point():
    from lacd.config import schemas_dir
    sd = schemas_dir()
    assert sd.is_dir(), sd
    for name in ("lacd-config.schema.json", "lock.schema.json",
                 "status.schema.json"):
        assert (sd / name).is_file(), name


def test_manifest_hashes_are_real():
    """Every manifest entry's sha256 must match the shipped file bytes."""
    import hashlib
    from lacd.cli import templates_dir
    td = templates_dir()
    manifest = json.loads((td / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        data = (td / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"].lower(), \
            entry["path"]


# ---------------------------------------------------------------------------
# clean install: venv + wheel + real `lacd init` (defect #2 end-to-end)
# ---------------------------------------------------------------------------

def test_clean_install_then_init_status_verify(tmp_path):
    """Full D1 chain: pip wheel -> fresh venv -> install -> git repo ->
    ``lacd init`` / ``status --json`` / ``verify``. Must genuinely pass."""
    wheel = _build_wheel(tmp_path / "whl")
    env = _clean_env()

    venv = tmp_path / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], env=env)
    vpy = venv / "bin" / "python"
    vlacd = venv / "bin" / "lacd"
    _run([str(vpy), "-m", "pip", "install", str(wheel)], env=env)
    assert vlacd.is_file(), "pip did not install the lacd entry point"

    # the installed CLI reports the bumped version (from the wheel, not src)
    proc = _run([str(vlacd), "--version"], env=env)
    assert LACD_VERSION in proc.stdout, proc.stdout

    # fresh git repo, real init (v1.0.0 raised FileNotFoundError here)
    proj = tmp_path / "proj"
    proj.mkdir()
    _run(["git", "init", "-q", "-b", "main", "."], cwd=proj, env=env)
    _run(["git", "config", "user.email", "t@t"], cwd=proj, env=env)
    _run(["git", "config", "user.name", "t"], cwd=proj, env=env)
    _run([str(vlacd), "init", "--path", str(proj)], env=env)
    assert (proj / "lacd.yaml").is_file()
    assert (proj / ".lacd" / "lock.json").is_file()
    assert (proj / ".github" / "workflows" / "lacd-verify.yml").is_file()

    # status --json validates and reports the new fields
    proc = _run([str(vlacd), "status", "--json", "--path", str(proj)], env=env)
    doc = json.loads(proc.stdout)
    assert doc["lacd_cli_version"] == LACD_VERSION
    assert doc["dimensions"]["remote_enforcement"]["query"] == "not_queried"
    assert doc["source_pin"]["strength"] == "tag-only"  # init records no SHA
    assert any("tag-only" in s for s in doc["next_steps"])
    assert doc["versions"]["package"] == LACD_VERSION

    # verify runs (local mode); remote degrades honestly without a credential
    proc = subprocess.run([str(vlacd), "verify", "--path", str(proj)],
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr[-1000:]
    assert "version_consistency" in proc.stdout




# ---------------------------------------------------------------------------
# status --json: query field (D3/FR-03), no network (R11)
# ---------------------------------------------------------------------------

def test_remote_query_field_live_vs_not_queried(project_factory):
    """dimensions.remote_enforcement.query is live|not_queried (D3)."""
    from lacd.core.report import build_status_dict, validate_status_dict
    from lacd.core.state import DimensionResult

    d = project_factory()
    doc = build_status_dict(d)
    assert doc["dimensions"]["remote_enforcement"]["query"] == "not_queried"
    assert validate_status_dict(doc) == []

    d2 = project_factory()
    remote = DimensionResult(status="unverified", reasons=["REMOTE_NO_CREDENTIAL"])
    doc2 = build_status_dict(d2, remote=remote, remote_queried=True)
    assert doc2["dimensions"]["remote_enforcement"]["query"] == "live"
    assert validate_status_dict(doc2) == []


def test_status_never_touches_network(project_factory, monkeypatch):
    """R11: build_status_dict must not open any socket (status is local)."""
    import socket

    from lacd.core.report import build_status_dict

    def _boom(*args, **kwargs):
        raise AssertionError("network access during status --json")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    d = project_factory()
    doc = build_status_dict(d)  # must not raise
    assert doc["state"] == "VALIDATED"


def test_source_pin_tag_only_annotated(project_factory):
    """D5: tag-only pins are explicitly annotated, never called strong."""
    from lacd.core.report import build_status_dict, validate_status_dict
    from lacd.core.state import compute_state

    d = project_factory()
    lock_p = d / ".lacd" / "lock.json"
    lock_d = json.loads(lock_p.read_text(encoding="utf-8"))
    lock_d["source"]["commit"] = None  # v1.0.0-style: honest null
    lock_p.write_text(json.dumps(lock_d), encoding="utf-8")

    report = compute_state(d)
    assert any("tag-only" in s for s in report.next_steps), report.next_steps

    doc = build_status_dict(d)
    assert doc["source_pin"] == {"strength": "tag-only", "commit": None}
    assert validate_status_dict(doc) == []


def test_source_pin_strong_when_sha_recorded(project_factory):
    """R13: a recorded full SHA yields strong pinning."""
    from lacd.core.report import build_status_dict

    d = project_factory()  # conftest pins commit = "a"*40
    doc = build_status_dict(d)
    assert doc["source_pin"] == {"strength": "strong", "commit": "a" * 40}
    assert not any("tag-only" in s for s in doc["next_steps"])


# ---------------------------------------------------------------------------
# R12: four-version consistency
# ---------------------------------------------------------------------------

def test_four_version_consistency_reports_all_legs(project_factory):
    """R12: cli/package/locked_cli/template legs are all reported."""
    from lacd import __version__
    from lacd.core.state import compute_state

    d = project_factory(lock_cli_version=__version__)
    report = compute_state(d)
    vc = report.version_consistency
    assert vc["status"] == "consistent"
    for leg in ("cli=", "package=", "locked_cli=", "template="):
        assert leg in vc["detail"], vc["detail"]


def test_package_dist_mismatch_is_flagged(project_factory, monkeypatch):
    """R12 negative: installed dist version != running CLI -> mismatch."""
    from lacd import __version__
    import lacd.core.state as state_mod

    monkeypatch.setattr(state_mod, "_dist_version", lambda: "0.0.0")
    d = project_factory(lock_cli_version=__version__)
    report = state_mod.compute_state(d)
    assert report.version_consistency["status"] == "mismatch"
    assert "0.0.0" in report.version_consistency["detail"]


def test_malformed_commit_is_tag_only_not_strong():
    """R13 negative: a malformed commit SHA must not yield 'strong'."""
    from lacd.config.lockfile import LockSource

    assert LockSource("git-tag", "r", "v1", "a" * 40).pin_strength == "strong"
    assert LockSource("git-tag", "r", "v1", None).pin_strength == "tag-only"
    assert LockSource("git-tag", "r", "v1", "not-a-sha").pin_strength == "tag-only"
    assert LockSource("git-tag", "r", "v1", "a" * 39).pin_strength == "tag-only"
    assert LockSource("git-tag", "r", "v1", "z" * 40).pin_strength == "tag-only"
