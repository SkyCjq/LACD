"""Unit tests for upgrade/migrate.py and upgrade/planner.py.

The parallel modules (core.versioning, config.lockfile) may not exist yet;
install contract-exact stubs only when the real ones are absent.
"""
import hashlib
import io
import json
import re
import sys
import types
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest


def _ensure_upgrade_stubs():
    try:
        import lacd.core.versioning  # noqa: F401
        have_versioning = True
    except ImportError:
        have_versioning = False
    try:
        import lacd.config.lockfile  # noqa: F401
        have_lockfile = True
    except ImportError:
        have_lockfile = False
    if have_versioning and have_lockfile:
        return

    import lacd

    def _get_or_create(name):
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            sys.modules[name] = mod
            parent, _, child = name.rpartition(".")
            if parent:
                setattr(sys.modules[parent], child, mod)
        return mod

    if not have_versioning:
        _get_or_create("lacd.core")
        ver = _get_or_create("lacd.core.versioning")

        def parse_version(s):
            m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", s.strip() if isinstance(s, str) else "")
            if not m:
                raise ValueError(f"illegal version: {s!r}")
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

        def compare(a, b):
            pa, pb = parse_version(a), parse_version(b)
            return (pa > pb) - (pa < pb)

        def classify_bump(from_v, to_v):
            pf, pt = parse_version(from_v), parse_version(to_v)
            if pt == pf:
                return "same"
            if pt < pf:
                return "downgrade"
            if pt[0] != pf[0]:
                return "major"
            if pt[1] != pf[1]:
                return "minor"
            return "patch"

        ver.parse_version = parse_version
        ver.compare = compare
        ver.classify_bump = classify_bump

    if not have_lockfile:
        _get_or_create("lacd.config")
        lf = _get_or_create("lacd.config.lockfile")

        @dataclass
        class LockFile:
            _data: dict

            @classmethod
            def load(cls, project_dir):
                p = Path(project_dir) / ".lacd" / "lock.json"
                return cls(json.loads(p.read_text(encoding="utf-8")))

            def save(self, project_dir):
                p = Path(project_dir) / ".lacd" / "lock.json"
                p.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")

            def validate(self):
                return []

            @property
            def template_version(self):
                return self._data.get("template_version")

            @property
            def managed_files(self):
                return self._data.get("managed_files", [])

            @property
            def shared_files(self):
                return self._data.get("shared_files", [])

        lf.LockFile = LockFile


_ensure_upgrade_stubs()

from lacd.config.lockfile import LockFile
from lacd.upgrade.migrate import (
    ApplyResult,
    BundleVerificationError,
    FileChange,
    MigrationConflict,
    MigrationError,
    MigrationPlan,
    TemplateBundle,
    apply_migration,
    load_bundle_from_dir,
    load_bundle_from_zip,
    plan_migration,
    rollback_last,
)
from lacd.upgrade.planner import UpgradeRefused, build_upgrade_plan
from lacd.upgrade.source import ReleaseInfo, UpgradeSource


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


OLD_WORKFLOW = b"old workflow content\n"
NEW_WORKFLOW = b"new workflow content v1.1.0\n"
OLD_SECTION = b"old managed section\n"
NEW_SECTION = b"new managed section v1.1.0\n"

BEGIN_OLD = '<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol" version="1.0.0" -->'
END = "<!-- LACD:MANAGED:END -->"


def _agents_with_section(section_body: bytes) -> bytes:
    # bytes between the markers are exactly section_body (canonical rule)
    return (b"# User notes\n\nkeep me\n\n" + BEGIN_OLD.encode()
            + section_body + END.encode() + b"\n")


def _write_lock(tmp_path, template_version="1.0.0", managed=None, shared=None):
    (tmp_path / ".lacd").mkdir(parents=True, exist_ok=True)
    lock = {
        "lock_format_version": "1",
        "lacd_cli_version": "1.0.0",
        "source": {"type": "git-tag", "repo": "SkyCjq/LACD",
                   "ref": "v1.0.0", "commit": None},
        "schema_version": "1",
        "policy_version": "1",
        "template_version": template_version,
        "managed_files": managed or [],
        "shared_files": shared or [],
        "generated_at": "2026-10-12T00:00:00+00:00",
        "generated_by": "lacd 1.0.0",
    }
    (tmp_path / ".lacd" / "lock.json").write_text(json.dumps(lock), encoding="utf-8")


def _managed_entry(old: bytes):
    return {"path": ".github/workflows/lacd-verify.yml",
            "sha256": _sha(old),
            "template": "workflows/lacd-verify.yml",
            "template_version": "1.0.0",
            "merge": "replace"}


def _shared_entry(old_section: bytes):
    return {"path": "AGENTS.md",
            "section": "lacd-agent-protocol",
            "sha256": _sha(old_section),
            "template": "managed/agents-md.section.md",
            "template_version": "1.0.0",
            "merge": "marked-section"}


def _bundle(version="1.1.0"):
    return TemplateBundle(
        version=version,
        files={"workflows/lacd-verify.yml": NEW_WORKFLOW,
               "managed/agents-md.section.md": NEW_SECTION},
        manifest={"template_version": version, "files": []},
    )


def _project(tmp_path, workflow: bytes | None = OLD_WORKFLOW,
             agents: bytes | None = None):
    if workflow is not None:
        p = tmp_path / ".github" / "workflows" / "lacd-verify.yml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(workflow)
    if agents is None:
        agents = _agents_with_section(OLD_SECTION)
    (tmp_path / "AGENTS.md").write_text(agents.decode("utf-8"), encoding="utf-8")
    _write_lock(tmp_path, managed=[_managed_entry(OLD_WORKFLOW)],
                shared=[_shared_entry(OLD_SECTION)])
    return tmp_path


def _action(plan, path):
    for c in plan.changes + plan.conflicts:
        if c.path == path:
            return c.action
    return None


# ---------------------------------------------------------------------------
# plan_migration: three-way comparison (pure)
# ---------------------------------------------------------------------------

def test_plan_update_when_unmodified(tmp_path):
    _project(tmp_path)
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    assert _action(plan, ".github/workflows/lacd-verify.yml") == "update"
    assert _action(plan, "AGENTS.md") == "update"
    assert plan.conflicts == []


def test_plan_noop_when_already_new(tmp_path):
    _project(tmp_path, workflow=NEW_WORKFLOW,
             agents=_agents_with_section(NEW_SECTION))
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    assert _action(plan, ".github/workflows/lacd-verify.yml") == "noop"
    assert _action(plan, "AGENTS.md") == "noop"
    assert plan.conflicts == []


def test_plan_noop_when_old_equals_new(tmp_path):
    # bundle identical to locked baseline -> nothing to do
    _project(tmp_path)
    same = TemplateBundle(version="1.0.0",
                          files={"workflows/lacd-verify.yml": OLD_WORKFLOW,
                                 "managed/agents-md.section.md": OLD_SECTION},
                          manifest={})
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), same)
    assert _action(plan, ".github/workflows/lacd-verify.yml") == "noop"
    assert _action(plan, "AGENTS.md") == "noop"


def test_plan_conflict_when_managed_file_missing(tmp_path):
    _project(tmp_path, workflow=None)  # file absent on disk
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    conflict = [c for c in plan.conflicts
                if c.path == ".github/workflows/lacd-verify.yml"]
    assert len(conflict) == 1
    assert "missing" in conflict[0].detail


def test_plan_conflict_when_locally_modified(tmp_path):
    _project(tmp_path, workflow=b"user edited workflow\n")
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    conflict = [c for c in plan.conflicts
                if c.path == ".github/workflows/lacd-verify.yml"]
    assert len(conflict) == 1
    assert "locally-modified" in conflict[0].detail


def test_plan_shared_add_section_when_no_markers(tmp_path):
    _project(tmp_path, agents=b"# just user notes\n")
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    assert _action(plan, "AGENTS.md") == "add"
    # add-section is explicitly NOT a conflict
    assert all(c.path != "AGENTS.md" for c in plan.conflicts)


def test_plan_shared_conflict_when_section_edited(tmp_path):
    _project(tmp_path, agents=_agents_with_section(b"user edited section\n"))
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    conflict = [c for c in plan.conflicts if c.path == "AGENTS.md"]
    assert len(conflict) == 1
    assert "section-locally-modified" in conflict[0].detail


def test_plan_template_missing_in_bundle_is_conflict(tmp_path):
    _project(tmp_path)
    bundle = TemplateBundle(version="1.1.0", files={}, manifest={})
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), bundle)
    assert len(plan.conflicts) == 2
    assert all("template-missing-in-bundle" in c.detail for c in plan.conflicts)


def test_plan_is_pure_no_disk_writes(tmp_path):
    _project(tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    after = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert set(after) == set(before)
    assert all(after[rel] == data for rel, data in before.items())


def test_plan_carries_risk_tiers(tmp_path):
    _project(tmp_path)
    plan = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    risks = {c.path: c.risk for c in plan.changes}
    assert risks[".github/workflows/lacd-verify.yml"] == "T2"
    assert risks["AGENTS.md"] == "T1"


# ---------------------------------------------------------------------------
# apply_migration
# ---------------------------------------------------------------------------

def test_apply_conflict_writes_nothing(tmp_path):
    _project(tmp_path, workflow=b"user edited workflow\n")
    lock = LockFile.load(tmp_path)
    plan = plan_migration(tmp_path, lock, _bundle())
    assert plan.conflicts
    wf = tmp_path / ".github" / "workflows" / "lacd-verify.yml"
    before = wf.read_bytes()
    with pytest.raises(MigrationConflict):
        apply_migration(tmp_path, plan, _bundle())
    assert wf.read_bytes() == before  # untouched
    assert not (tmp_path / ".lacd" / "backup").exists()  # no backup either


def test_apply_success_updates_files_lock_and_backup(tmp_path):
    _project(tmp_path)
    lock = LockFile.load(tmp_path)
    plan = plan_migration(tmp_path, lock, _bundle())
    result = apply_migration(tmp_path, plan, _bundle())
    assert isinstance(result, ApplyResult)
    assert result.to_version == "1.1.0"
    assert result.lock_updated is True
    # files updated
    wf = tmp_path / ".github" / "workflows" / "lacd-verify.yml"
    assert wf.read_bytes() == NEW_WORKFLOW
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "new managed section v1.1.0" in agents
    assert "keep me" in agents  # user content preserved
    assert 'version="1.1.0"' in agents  # marker version bumped
    # lock updated
    data = json.loads((tmp_path / ".lacd" / "lock.json").read_text(encoding="utf-8"))
    assert data["template_version"] == "1.1.0"
    assert data["generated_at"] != "2026-10-12T00:00:00+00:00"
    managed = {e["path"]: e for e in data["managed_files"]}
    assert managed[".github/workflows/lacd-verify.yml"]["sha256"] == _sha(NEW_WORKFLOW)
    shared = {e["path"]: e for e in data["shared_files"]}
    assert shared["AGENTS.md"]["sha256"] == _sha(NEW_SECTION)
    # backup holds the originals
    backup = tmp_path / result.backup_dir
    assert (backup / ".github" / "workflows" / "lacd-verify.yml").read_bytes() == OLD_WORKFLOW
    assert (backup / ".lacd" / "lock.json").is_file()


def test_apply_failure_rolls_back(tmp_path, monkeypatch):
    _project(tmp_path)
    lock = LockFile.load(tmp_path)
    plan = plan_migration(tmp_path, lock, _bundle())
    wf = tmp_path / ".github" / "workflows" / "lacd-verify.yml"
    agents = tmp_path / "AGENTS.md"
    old_agents = agents.read_bytes()

    calls = {"n": 0}
    real_write_bytes = Path.write_bytes

    def flaky(self, data):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("simulated disk failure")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(MigrationError):
        apply_migration(tmp_path, plan, _bundle())
    # first file restored, second never written, lock untouched
    assert wf.read_bytes() == OLD_WORKFLOW
    assert agents.read_bytes() == old_agents
    data = json.loads((tmp_path / ".lacd" / "lock.json").read_text(encoding="utf-8"))
    assert data["template_version"] == "1.0.0"


def test_apply_add_section_to_missing_agents(tmp_path):
    _project(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    lock = LockFile.load(tmp_path)
    plan = plan_migration(tmp_path, lock, _bundle())
    assert _action(plan, "AGENTS.md") == "add"
    apply_migration(tmp_path, plan, _bundle())
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "new managed section v1.1.0" in text
    assert "LACD:MANAGED:BEGIN" in text


def test_idempotent_rerun_is_all_noop(tmp_path):
    _project(tmp_path)
    lock = LockFile.load(tmp_path)
    plan = plan_migration(tmp_path, lock, _bundle())
    apply_migration(tmp_path, plan, _bundle())
    plan2 = plan_migration(tmp_path, LockFile.load(tmp_path), _bundle())
    assert plan2.conflicts == []
    assert plan2.changes
    assert all(c.action == "noop" for c in plan2.changes)


# ---------------------------------------------------------------------------
# rollback_last
# ---------------------------------------------------------------------------

def test_rollback_last_restores(tmp_path):
    _project(tmp_path)
    lock = LockFile.load(tmp_path)
    plan = plan_migration(tmp_path, lock, _bundle())
    apply_migration(tmp_path, plan, _bundle())
    assert rollback_last(tmp_path) is True
    wf = tmp_path / ".github" / "workflows" / "lacd-verify.yml"
    assert wf.read_bytes() == OLD_WORKFLOW
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "old managed section" in agents
    data = json.loads((tmp_path / ".lacd" / "lock.json").read_text(encoding="utf-8"))
    assert data["template_version"] == "1.0.0"


def test_rollback_last_no_backup_returns_false(tmp_path):
    tmp_path.joinpath(".lacd").mkdir(parents=True, exist_ok=True)
    assert rollback_last(tmp_path) is False


# ---------------------------------------------------------------------------
# bundle loading: sha256 is mandatory
# ---------------------------------------------------------------------------

def _zip_bytes(files: dict[str, bytes], manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _manifest_for(files: dict[str, bytes], version="1.1.0"):
    return {"template_version": version,
            "files": [{"path": p, "sha256": _sha(d)} for p, d in files.items()]}


def test_load_bundle_from_zip_ok():
    files = {"workflows/lacd-verify.yml": NEW_WORKFLOW}
    data = _zip_bytes(files, _manifest_for(files))
    bundle = load_bundle_from_zip(data, _sha(data))
    assert bundle.version == "1.1.0"
    assert bundle.files["workflows/lacd-verify.yml"] == NEW_WORKFLOW


def test_load_bundle_from_zip_sha_mismatch():
    files = {"workflows/lacd-verify.yml": NEW_WORKFLOW}
    data = _zip_bytes(files, _manifest_for(files))
    with pytest.raises(BundleVerificationError):
        load_bundle_from_zip(data, "00" * 32)


def test_load_bundle_from_zip_tampered_file():
    files = {"workflows/lacd-verify.yml": NEW_WORKFLOW}
    manifest = _manifest_for({"workflows/lacd-verify.yml": b"original"})
    data = _zip_bytes(files, manifest)  # manifest hash != actual bytes
    with pytest.raises(BundleVerificationError):
        load_bundle_from_zip(data, _sha(data))


def test_load_bundle_from_dir_ok(tmp_path):
    files = {"workflows/lacd-verify.yml": NEW_WORKFLOW}
    for name, data in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (tmp_path / "manifest.json").write_text(json.dumps(_manifest_for(files)),
                                            encoding="utf-8")
    bundle = load_bundle_from_dir(str(tmp_path))
    assert bundle.version == "1.1.0"
    assert bundle.files["workflows/lacd-verify.yml"] == NEW_WORKFLOW


def test_load_bundle_from_dir_hash_mismatch(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"tampered")
    (tmp_path / "manifest.json").write_text(
        json.dumps(_manifest_for({"f.txt": b"original"})), encoding="utf-8")
    with pytest.raises(BundleVerificationError):
        load_bundle_from_dir(str(tmp_path))


def test_load_bundle_from_dir_missing_manifest(tmp_path):
    with pytest.raises(BundleVerificationError):
        load_bundle_from_dir(str(tmp_path))


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------

class _StaticSource(UpgradeSource):
    def __init__(self, versions):
        self._versions = versions

    def list_releases(self):
        return [ReleaseInfo(version=v, tag=f"v{v}") for v in self._versions]


def test_planner_minor_plan(tmp_path):
    _project(tmp_path)
    plan = build_upgrade_plan(tmp_path, "1.1.0",
                              source=_StaticSource(["1.0.0", "1.0.1", "1.1.0"]),
                              bundle=_bundle("1.1.0"))
    assert isinstance(plan, MigrationPlan)
    assert plan.from_version == "1.0.0"
    assert plan.to_version == "1.1.0"
    assert plan.conflicts == []
    # T2 change gets a human checkpoint
    assert any("T2" in cp and "lacd-verify.yml" in cp
               for cp in plan.human_checkpoints)
    # minor preconditions surfaced
    assert any("minor-new-fields-review" in cp for cp in plan.human_checkpoints)


def test_planner_major_refused_without_accept(tmp_path):
    _project(tmp_path)
    with pytest.raises(UpgradeRefused) as excinfo:
        build_upgrade_plan(tmp_path, "2.0.0",
                            source=_StaticSource(["1.0.0", "2.0.0"]),
                            bundle=_bundle("2.0.0"))
    assert "major" in excinfo.value.reason


def test_planner_major_accepted_adds_migration_checkpoint(tmp_path):
    _project(tmp_path)
    plan = build_upgrade_plan(tmp_path, "2.0.0",
                              source=_StaticSource(["1.0.0", "2.0.0"]),
                              bundle=_bundle("2.0.0"),
                              accept_major=True)
    assert plan.to_version == "2.0.0"
    assert any("migration-plan-required" in cp for cp in plan.human_checkpoints)


def test_planner_downgrade_refused(tmp_path):
    _project(tmp_path)
    # lock the project at 1.1.0, then attempt to "upgrade" to 1.0.0
    data = json.loads((tmp_path / ".lacd" / "lock.json").read_text(encoding="utf-8"))
    data["template_version"] = "1.1.0"
    (tmp_path / ".lacd" / "lock.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UpgradeRefused) as excinfo:
        build_upgrade_plan(tmp_path, "1.0.0",
                            source=_StaticSource(["1.0.0", "1.1.0"]),
                            bundle=_bundle("1.0.0"))
    # downgrade never goes through migration: refused, not planned
    assert "downgrade" in excinfo.value.reason


def test_planner_unknown_version_refused(tmp_path):
    _project(tmp_path)
    with pytest.raises(UpgradeRefused) as excinfo:
        build_upgrade_plan(tmp_path, "9.9.9",
                            source=_StaticSource(["1.0.0"]),
                            bundle=_bundle("9.9.9"))
    assert excinfo.value.reason == "unknown-target-version"


def test_planner_bundle_none_adds_checkpoint(tmp_path):
    _project(tmp_path)
    plan = build_upgrade_plan(tmp_path, "1.1.0",
                              source=_StaticSource(["1.0.0", "1.1.0"]),
                              bundle=None)
    assert plan.changes == []
    assert any("bundle-missing" in cp for cp in plan.human_checkpoints)


def test_planner_conflicts_add_checkpoint(tmp_path):
    _project(tmp_path, workflow=b"user edited workflow\n")
    plan = build_upgrade_plan(tmp_path, "1.1.0",
                              source=_StaticSource(["1.0.0", "1.1.0"]),
                              bundle=_bundle("1.1.0"))
    assert plan.conflicts
    assert any("conflict" in cp for cp in plan.human_checkpoints)
