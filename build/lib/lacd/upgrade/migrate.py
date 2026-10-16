"""Three-way migration engine for LACD template upgrades (upgrade/migrate.py).

Contract: docs/architecture/MODULE_CONTRACT.md §5.3,
          docs/specification/UPGRADE_CONTRACT.md §6 / §10.

Three-way comparison per file:
  old = baseline recorded in .lacd/lock.json (sha256)
  cur = current bytes on disk
  new = target bytes from the TemplateBundle

managed (merge=replace):
  cur == old                  -> update
  cur == new or old == new     -> noop
  file missing (old recorded)  -> conflict (missing)
  otherwise                   -> conflict (locally modified)

shared (merge=marked-section, e.g. AGENTS.md):
  the same comparison runs on the bytes *between* the LACD managed markers;
  no markers -> add-section (non-conflict; user content is never deleted).

Fail-closed rules:
- any conflict aborts before a single byte is written (MigrationConflict);
- every apply is preceded by a full backup under .lacd/backup/<UTC ts>/;
- a failed apply restores already-written files and raises MigrationError;
- template bundles are sha256-verified on load (BundleVerificationError);
- all paths are confined to project_dir (absolute / ".." rejected).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lacd.config.lockfile import LockFile

BACKUP_ROOT = ".lacd/backup"
LOCK_REL = ".lacd/lock.json"
BACKUP_MANIFEST = "_manifest.json"

_BEGIN_RE = re.compile(
    r'<!--\s*LACD:MANAGED:BEGIN\b[^>]*?section="([^"]+)"[^>]*?-->'
)
_END_RE = re.compile(r"<!--\s*LACD:MANAGED:END\s*-->")


class BundleVerificationError(Exception):
    """A template bundle failed sha256 verification or is malformed."""


class MigrationError(Exception):
    """Migration failed; already-applied files were rolled back."""


class MigrationConflict(Exception):
    """Conflicts exist; nothing was written to disk."""


@dataclass
class FileChange:
    path: str
    action: str  # "add" | "update" | "noop" | "conflict"
    risk: str    # "T0" | "T1" | "T2"
    detail: str


@dataclass
class MigrationPlan:
    from_version: str
    to_version: str
    changes: list[FileChange] = field(default_factory=list)
    conflicts: list[FileChange] = field(default_factory=list)
    human_checkpoints: list[str] = field(default_factory=list)
    rollback_dir: str | None = None


@dataclass
class ApplyResult:
    to_version: str
    applied: list[str] = field(default_factory=list)
    backup_dir: str = ""
    lock_updated: bool = False


@dataclass
class TemplateBundle:
    version: str
    files: dict[str, bytes]   # template-relpath -> bytes
    manifest: dict


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_join(project: Path, rel: str) -> Path:
    """Join a relative path under project_dir; reject escape (fail-closed)."""
    if not rel or os.path.isabs(rel):
        raise MigrationError(f"refusing unsafe path: {rel!r}")
    target = (project / rel).resolve()
    root = project.resolve()
    if target != root and root not in target.parents:
        raise MigrationError(f"path escapes project dir: {rel!r}")
    return target


def _risk_of(path: str) -> str:
    """Local risk tier per MODULE_CONTRACT §4 (T2 > T0 > T1 default)."""
    p = path.replace("\\", "/")
    low = p.lower()
    base = low.rsplit("/", 1)[-1]
    # T2 first
    if (
        p.startswith(".github/")
        or p.startswith(".lacd/")
        or "/workflows/" in p
        or p.startswith("workflows/")
        or any(k in low for k in ("secret", "credential", "token", "private"))
        or low.endswith((".pem", ".key", ".p12", ".pfx"))
        or base == "security.md"
    ):
        return "T2"
    # T0
    if (
        p.startswith("docs/")
        or p.startswith("examples/")
        or base.startswith(("readme", "license", "notice", "changelog"))
    ):
        return "T0"
    # T1 default (includes AGENTS.md shared files, src/**, tests/**, ...)
    return "T1"


def _extract_section(text: str, section: str) -> str | None:
    """Return the bytes between the managed markers for `section`, or None."""
    for begin in _BEGIN_RE.finditer(text):
        if begin.group(1) == section:
            end = _END_RE.search(text, begin.end())
            if end is None:
                return None
            return text[begin.end():end.start()]
    return None


def _begin_marker(section: str, version: str) -> str:
    return f'<!-- LACD:MANAGED:BEGIN section="{section}" version="{version}" -->'


_END_MARKER = "<!-- LACD:MANAGED:END -->"


def _wrap_section(section: str, version: str, content: bytes) -> str:
    """BEGIN marker + section bytes verbatim + END marker.

    The bytes between the markers are exactly `content`, so the lock's
    section sha256 (hash of the bundle template bytes) stays valid after
    apply and a re-plan is a clean noop.
    """
    return _begin_marker(section, version) + content.decode("utf-8") + _END_MARKER


def _render_new_file(section: str, version: str, content: bytes) -> bytes:
    return (_wrap_section(section, version, content) + "\n").encode("utf-8")


def _append_section(text: str, section: str, version: str, content: bytes) -> bytes:
    return (text.rstrip("\n") + "\n\n"
            + _wrap_section(section, version, content) + "\n").encode("utf-8")


def _replace_section(text: str, section: str, version: str, content: bytes) -> bytes:
    new_span = _wrap_section(section, version, content)
    out: list[str] = []
    pos = 0
    replaced = False
    for begin in _BEGIN_RE.finditer(text):
        if begin.group(1) != section or replaced:
            continue
        end = _END_RE.search(text, begin.end())
        if end is None:
            continue
        out.append(text[pos:begin.start()])
        out.append(new_span)
        pos = end.end()
        replaced = True
    out.append(text[pos:])
    return "".join(out).encode("utf-8")


# ---------------------------------------------------------------------------
# planning (pure: reads disk, never writes)
# ---------------------------------------------------------------------------

@dataclass
class _WriteOp:
    rel: str
    full_bytes: bytes   # final on-disk content
    lock_sha: str       # sha256 to record in lock.json
    action: str         # "add" | "update" | "noop" | "conflict"
    risk: str
    detail: str


def _entry_field(entry, name, default=""):
    """Read a lock entry field; tolerates dataclass attributes and dicts."""
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _iter_ops(project: Path, lock: LockFile, bundle: TemplateBundle) -> list[_WriteOp]:
    ops: list[_WriteOp] = []

    for entry in lock.managed_files or []:
        rel = _entry_field(entry, "path")
        tmpl = _entry_field(entry, "template") or rel
        old = _entry_field(entry, "sha256", None)
        risk = _risk_of(rel)
        new_bytes = bundle.files.get(tmpl)
        if new_bytes is None:
            ops.append(_WriteOp(rel, b"", "", "conflict", risk,
                                f"template-missing-in-bundle: {tmpl}"))
            continue
        new_sha = _sha256_hex(new_bytes)
        target = _safe_join(project, rel)
        if not target.is_file():
            if old:
                ops.append(_WriteOp(rel, b"", "", "conflict", risk,
                                    "missing: managed file not found on disk"))
            else:
                ops.append(_WriteOp(rel, new_bytes, new_sha, "add", risk,
                                    "add: not present on disk"))
            continue
        cur_sha = _sha256_hex(target.read_bytes())
        # v1.0.3 (ADR-0015): check current content FIRST. The old
        # "old == new -> noop" shortcut masked drift when the template did
        # not change between versions but the user had edited the file.
        if cur_sha == new_sha:
            ops.append(_WriteOp(rel, new_bytes, new_sha, "noop", risk,
                                "noop: already at target content"))
        elif old is not None and cur_sha == old:
            ops.append(_WriteOp(rel, new_bytes, new_sha, "update", risk,
                                "update: unmodified since lock, applying new template"))
        else:
            ops.append(_WriteOp(rel, b"", "", "conflict", risk,
                                "locally-modified: disk differs from locked baseline"))

    for entry in lock.shared_files or []:
        rel = _entry_field(entry, "path")
        section = _entry_field(entry, "section")
        tmpl = _entry_field(entry, "template")
        old = _entry_field(entry, "sha256", None)
        risk = _risk_of(rel)
        new_bytes = bundle.files.get(tmpl) if tmpl else None
        if new_bytes is None:
            ops.append(_WriteOp(rel, b"", "", "conflict", risk,
                                f"template-missing-in-bundle: {tmpl}"))
            continue
        new_sha = _sha256_hex(new_bytes)
        target = _safe_join(project, rel)
        if not target.is_file():
            ops.append(_WriteOp(
                rel, _render_new_file(section, bundle.version, new_bytes),
                new_sha, "add", risk,
                "add-section: file missing, will create with managed section"))
            continue
        try:
            text = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            ops.append(_WriteOp(rel, b"", "", "conflict", risk,
                                "not-utf8: cannot process marked section"))
            continue
        cur_section = _extract_section(text, section)
        if cur_section is None:
            ops.append(_WriteOp(
                rel, _append_section(text, section, bundle.version, new_bytes),
                new_sha, "add", risk,
                "add-section: markers absent, will append managed section"))
            continue
        cur_sha = _sha256_hex(cur_section.encode("utf-8"))
        # v1.0.3 (ADR-0015): same fail-closed ordering as managed files —
        # current content decides first; never mask section drift.
        if cur_sha == new_sha:
            ops.append(_WriteOp(rel, b"", new_sha, "noop", risk,
                                "noop: managed section already at target"))
        elif old is not None and cur_sha == old:
            ops.append(_WriteOp(
                rel, _replace_section(text, section, bundle.version, new_bytes),
                new_sha, "update", risk,
                "update: managed section unmodified since lock"))
        else:
            ops.append(_WriteOp(rel, b"", "", "conflict", risk,
                                "section-locally-modified: user edited the managed section"))

    return ops


def plan_migration(project_dir, lock: LockFile, bundle: TemplateBundle) -> MigrationPlan:
    """Pure three-way comparison. Reads the worktree; never writes."""
    project = Path(project_dir)
    ops = _iter_ops(project, lock, bundle)
    changes = [
        FileChange(path=op.rel, action=op.action, risk=op.risk, detail=op.detail)
        for op in ops if op.action != "conflict"
    ]
    conflicts = [
        FileChange(path=op.rel, action="conflict", risk=op.risk, detail=op.detail)
        for op in ops if op.action == "conflict"
    ]
    return MigrationPlan(
        from_version=lock.template_version,
        to_version=bundle.version,
        changes=changes,
        conflicts=conflicts,
        human_checkpoints=[],
        rollback_dir=None,
    )


# ---------------------------------------------------------------------------
# apply (backup -> write -> lock update; failure rolls back)
# ---------------------------------------------------------------------------

def _update_lock_json(project: Path, bundle: TemplateBundle,
                      ops: list[_WriteOp]) -> None:
    """Update .lacd/lock.json per the frozen §2.2 schema.

    Done on the JSON document directly (not via LockFile's Python
    representation) so the on-disk contract stays authoritative.

    Every managed/shared entry covered by the bundle gets its baseline
    advanced to the new template hash -- including noops (disk already at
    target). Otherwise the next upgrade would see disk(new) != baseline(old)
    and misreport a conflict.
    """
    lock_path = project / LOCK_REL
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"cannot read lock.json for update: {exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError("lock.json is not a JSON object")
    new_hashes = {op.rel: op.lock_sha for op in ops
                  if op.action != "conflict" and op.lock_sha}
    data["template_version"] = bundle.version
    for key in ("managed_files", "shared_files"):
        entries = data.get(key) or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path") in new_hashes:
                entry["sha256"] = new_hashes[entry["path"]]
                entry["template_version"] = bundle.version
    data["generated_at"] = _utc_now_iso()
    try:
        from lacd import __version__ as cli_version
        data["generated_by"] = f"lacd {cli_version}"
        # v1.0.3 (HD-04 option A): lacd_cli_version records the CLI version
        # performing the migration, so version_consistency compares reality.
        data["lacd_cli_version"] = cli_version
    except Exception:
        data["generated_by"] = "lacd"
    lock_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def apply_migration(project_dir, plan: MigrationPlan,
                    bundle: TemplateBundle) -> ApplyResult:
    """Apply a migration plan with backup and rollback.

    - Any conflict (in `plan` or found on re-check) -> MigrationConflict,
      nothing written.
    - Backup first (.lacd/backup/<UTC ts>/), then write files, then update
      lock.json. Any failure -> restore written files from backup and raise
      MigrationError.
    """
    project = Path(project_dir)
    if plan.conflicts:
        paths = ", ".join(c.path for c in plan.conflicts)
        raise MigrationConflict(f"{len(plan.conflicts)} conflict(s): {paths}")
    if bundle.version != plan.to_version:
        raise MigrationError(
            f"bundle version {bundle.version} != plan target {plan.to_version}"
        )

    lock = LockFile.load(project_dir)  # fresh read; fail-closed on stale plan
    ops = _iter_ops(project, lock, bundle)
    fresh_conflicts = [op for op in ops if op.action == "conflict"]
    if fresh_conflicts:
        paths = ", ".join(op.rel for op in fresh_conflicts)
        raise MigrationConflict(
            f"re-check found {len(fresh_conflicts)} conflict(s): {paths}"
        )
    writes = [op for op in ops if op.action in ("add", "update")]

    # Idempotency: nothing to change -> complete no-op (no backup dir,
    # no lock rewrite), so re-running an upgrade leaves zero diff.
    if not writes:
        return ApplyResult(
            to_version=bundle.version,
            applied=[],
            backup_dir="",
            lock_updated=False,
        )

    # -- backup -----------------------------------------------------------
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = project / BACKUP_ROOT / stamp
    suffix = 0
    while backup.exists():
        suffix += 1
        backup = project / BACKUP_ROOT / f"{stamp}-{suffix}"
    added: list[str] = []
    try:
        for op in writes:
            dest = _safe_join(project, op.rel)
            if dest.exists():
                dst = backup / op.rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, dst)
            else:
                added.append(op.rel)
        lock_src = project / LOCK_REL
        if lock_src.is_file():
            dst = backup / LOCK_REL
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lock_src, dst)
        (backup / BACKUP_MANIFEST).write_text(
            json.dumps({
                "added": added,
                "to_version": bundle.version,
                "created_at": _utc_now_iso(),
            }, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise MigrationError(f"backup failed, nothing applied: {exc}") from exc

    # -- apply ------------------------------------------------------------
    applied: list[str] = []
    try:
        for op in writes:
            dest = _safe_join(project, op.rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(op.full_bytes)
            applied.append(op.rel)
        _update_lock_json(project, bundle, ops)
    except Exception as exc:
        for rel in applied:
            src = backup / rel
            dest = _safe_join(project, rel)
            try:
                if src.is_file():
                    shutil.copy2(src, dest)
                elif rel in added and dest.exists():
                    dest.unlink()  # newly created file; remove it
            except OSError:
                pass
        lock_bak = backup / LOCK_REL
        try:
            if lock_bak.is_file():
                shutil.copy2(lock_bak, project / LOCK_REL)
        except OSError:
            pass
        raise MigrationError(
            f"apply failed after {len(applied)} file(s); rolled back: {exc}"
        ) from exc

    return ApplyResult(
        to_version=bundle.version,
        applied=[op.rel for op in writes],
        backup_dir=str(backup.relative_to(project)),
        lock_updated=True,
    )


def rollback_last(project_dir) -> bool:
    """Restore the most recent backup. Returns False when no backup exists.

    Raises MigrationError if a backup exists but restoration fails.
    """
    project = Path(project_dir)
    root = project / BACKUP_ROOT
    if not root.is_dir():
        return False
    candidates = sorted(d for d in root.iterdir() if d.is_dir())
    if not candidates:
        return False
    latest = candidates[-1]

    manifest: dict = {}
    manifest_path = latest / BACKUP_MANIFEST
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
    try:
        for src in sorted(latest.rglob("*")):
            if not src.is_file() or src.name == BACKUP_MANIFEST:
                continue
            rel = src.relative_to(latest).as_posix()
            dest = _safe_join(project, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        for rel in manifest.get("added", []):
            dest = _safe_join(project, rel)
            if dest.is_file():
                dest.unlink()
    except (OSError, MigrationError) as exc:
        raise MigrationError(f"rollback from {latest} failed: {exc}") from exc
    return True


# ---------------------------------------------------------------------------
# bundle loading (always hash-verified)
# ---------------------------------------------------------------------------

def _check_manifest_entry_shapes(manifest: dict) -> list[dict]:
    if not isinstance(manifest, dict):
        raise BundleVerificationError("manifest is not a JSON object")
    version = manifest.get("template_version")
    entries = manifest.get("files")
    if not isinstance(version, str) or not version:
        raise BundleVerificationError("manifest lacks template_version")
    if not isinstance(entries, list):
        raise BundleVerificationError("manifest lacks files[] list")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
        ):
            raise BundleVerificationError(f"malformed manifest entry: {entry!r}")
    return entries


def load_bundle_from_dir(bundle_dir: str) -> TemplateBundle:
    """Load a template bundle from a directory; verify every file vs manifest."""
    base = Path(bundle_dir)
    manifest_path = base / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleVerificationError(
            f"cannot read manifest.json in {bundle_dir}: {exc}"
        ) from exc
    entries = _check_manifest_entry_shapes(manifest)
    files: dict[str, bytes] = {}
    for entry in entries:
        rel = entry["path"]
        if os.path.isabs(rel) or ".." in Path(rel).parts:
            raise BundleVerificationError(f"unsafe manifest path: {rel!r}")
        data = (base / rel).read_bytes() if (base / rel).is_file() else None
        if data is None:
            raise BundleVerificationError(f"bundle file missing: {rel}")
        if _sha256_hex(data) != entry["sha256"].lower():
            raise BundleVerificationError(f"sha256 mismatch for bundle file: {rel}")
        files[rel] = data
    return TemplateBundle(version=manifest["template_version"], files=files,
                          manifest=manifest)


def load_bundle_from_zip(data: bytes, expected_sha256: str) -> TemplateBundle:
    """Load a template bundle from zip bytes; verify archive sha256 first."""
    if _sha256_hex(data) != expected_sha256.lower():
        raise BundleVerificationError(
            "template bundle sha256 mismatch: refusing to use the bundle"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleVerificationError(f"not a valid zip archive: {exc}") from exc
    names = set(archive.namelist())
    if "manifest.json" not in names:
        raise BundleVerificationError("zip bundle lacks manifest.json at root")
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError, KeyError) as exc:
        raise BundleVerificationError(f"cannot read manifest.json: {exc}") from exc
    entries = _check_manifest_entry_shapes(manifest)
    files: dict[str, bytes] = {}
    for entry in entries:
        rel = entry["path"]
        if os.path.isabs(rel) or ".." in Path(rel).parts or rel not in names:
            raise BundleVerificationError(f"unsafe or missing zip entry: {rel!r}")
        content = archive.read(rel)
        if _sha256_hex(content) != entry["sha256"].lower():
            raise BundleVerificationError(f"sha256 mismatch for bundle file: {rel}")
        files[rel] = content
    return TemplateBundle(version=manifest["template_version"], files=files,
                          manifest=manifest)
