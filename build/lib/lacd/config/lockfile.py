"""Version lock file ``.lacd/lock.json``: model, validation, drift detection.

Canonical hashing rules (any writer of managed content, e.g. ``lacd init``,
must follow these or drift checks will disagree):

- managed (``replace``) files: SHA-256 over the raw file bytes.
- shared (``marked-section``) files: SHA-256 over the UTF-8 bytes of the
  section content, i.e. the exact text between the end of the
  ``<!-- LACD:MANAGED:BEGIN ... section="<name>" ... -->`` marker and the
  start of the next ``<!-- LACD:MANAGED:END -->`` marker. No trimming or
  newline normalisation. Files are decoded as UTF-8 with ``errors="replace"``.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import jsonschema

PathLike = Union[str, Path]

MERGE_MODES = ("replace", "marked-section")
SOURCE_TYPES = ("git-tag", "git-commit", "local-dir")

_BEGIN_RE = re.compile(
    r"<!--\s*LACD:MANAGED:BEGIN\b[^>]*?section=\"([^\"]+)\"[^>]*?-->"
)
_END_RE = re.compile(r"<!--\s*LACD:MANAGED:END\s*-->")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class LockCorruptError(ValueError):
    """The lock file exists but is not a valid lock."""

    def __init__(self, reason: str, errors: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.errors = list(errors or [])


def _lock_schema() -> dict:
    schema_path = importlib.resources.files("lacd.schemas") / "lock.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_lock_dict(d: dict) -> list[str]:
    """Validate a decoded lock mapping; return error strings (empty == valid)."""
    if not isinstance(d, dict):
        return ["<root>: lock file must be a JSON object"]
    schema = _lock_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(d), key=lambda e: list(e.absolute_path))
    out: list[str] = []
    for e in errors:
        location = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"{location}: {e.message}")
    return out


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file, read in binary mode (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_managed_section(text: str, section: str) -> str | None:
    """Return the raw content of a named managed section, or ``None``."""
    for m in _BEGIN_RE.finditer(text):
        if m.group(1) == section:
            end = _END_RE.search(text, m.end())
            if end is None:
                return None
            return text[m.end() : end.start()]
    return None


def hash_managed_section(text: str, section: str) -> str | None:
    """SHA-256 of a managed section's canonical content, or ``None``."""
    content = extract_managed_section(text, section)
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_unsafe_path(rel: str) -> bool:
    """Syntactic escape check: absolute paths or ``..`` segments."""
    if not isinstance(rel, str) or not rel:
        return True
    parts = Path(rel).parts
    return Path(rel).is_absolute() or ".." in parts


def _safe_join(project_dir: Path, rel: str) -> Path:
    """Join a lock-listed relative path, fail-closed on escape attempts."""
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"invalid lock path: {rel!r}")
    target = (project_dir / rel).resolve()
    base = project_dir.resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"lock path escapes project_dir: {rel!r}")
    return target


@dataclass
class LockSource:
    type: str  # "git-tag" | "git-commit" | "local-dir"
    repo: str
    ref: str | None
    commit: str | None  # full 40-hex SHA when known, else None (honest)

    @classmethod
    def from_dict(cls, d: dict) -> "LockSource":
        return cls(type=d["type"], repo=d["repo"], ref=d.get("ref"),
                   commit=d.get("commit"))

    @property
    def pin_strength(self) -> str:
        """Derived pin strength: ``"strong"`` | ``"tag-only"`` (D5/R13).

        ``"strong"`` only when ``commit`` is a full 40-hex SHA. Anything
        else (``None``, malformed) is ``"tag-only"`` — and must never be
        reported as strongly pinned. Derived, never stored.
        """
        if isinstance(self.commit, str) and _COMMIT_SHA_RE.fullmatch(self.commit):
            return "strong"
        return "tag-only"


@dataclass
class ManagedFileEntry:
    path: str
    sha256: str
    template: str
    template_version: str
    merge: str

    @classmethod
    def from_dict(cls, d: dict) -> "ManagedFileEntry":
        return cls(path=d["path"], sha256=d["sha256"], template=d["template"],
                   template_version=d["template_version"], merge=d["merge"])


@dataclass
class SharedFileEntry:
    path: str
    section: str
    sha256: str
    template: str
    template_version: str
    merge: str

    @classmethod
    def from_dict(cls, d: dict) -> "SharedFileEntry":
        return cls(path=d["path"], section=d["section"], sha256=d["sha256"],
                   template=d["template"], template_version=d["template_version"],
                   merge=d["merge"])


@dataclass
class LockFile:
    """In-memory representation of a validated ``.lacd/lock.json``."""

    lock_format_version: str
    lacd_cli_version: str
    source: LockSource
    schema_version: str
    policy_version: str
    template_version: str
    managed_files: list[ManagedFileEntry] = field(default_factory=list)
    shared_files: list[SharedFileEntry] = field(default_factory=list)
    generated_at: str = ""
    generated_by: str = ""
    required_checks: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "LockFile":
        return cls(
            lock_format_version=d["lock_format_version"],
            lacd_cli_version=d["lacd_cli_version"],
            source=LockSource.from_dict(d["source"]),
            schema_version=d["schema_version"],
            policy_version=d["policy_version"],
            template_version=d["template_version"],
            managed_files=[ManagedFileEntry.from_dict(e) for e in d.get("managed_files", [])],
            shared_files=[SharedFileEntry.from_dict(e) for e in d.get("shared_files", [])],
            generated_at=d.get("generated_at", ""),
            generated_by=d.get("generated_by", ""),
            required_checks=list(d.get("required_checks", [])),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def lock_path(project_dir: PathLike) -> Path:
        return Path(project_dir) / ".lacd" / "lock.json"

    def validate(self) -> list[str]:
        """Validate this lock; return error strings (empty == valid).

        Path safety is checked syntactically here (absolute or ``..``);
        :meth:`load` re-checks against the real ``project_dir``.
        """
        errors = validate_lock_dict(self.to_dict())
        for key, entries in (("managed_files", self.managed_files),
                             ("shared_files", self.shared_files)):
            for i, e in enumerate(entries):
                if _is_unsafe_path(e.path):
                    errors.append(
                        f"{key}[{i}].path: path escapes project_dir: {e.path!r}")
        return errors

    @classmethod
    def load(cls, project_dir: PathLike) -> "LockFile":
        """Load and validate the lock file.

        Raises:
            FileNotFoundError: no ``.lacd/lock.json``.
            LockCorruptError: present but invalid JSON / schema / unsafe paths.
            OSError: I/O failure while reading.
        """
        path = cls.lock_path(project_dir)
        if not path.is_file():
            raise FileNotFoundError(f"no lock file at {path}")
        raw = path.read_text(encoding="utf-8")  # OSError propagates intentionally
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LockCorruptError(f"lock file is not valid JSON: {exc}",
                                   [f"<root>: invalid JSON: {exc}"]) from exc
        errors = validate_lock_dict(data)
        lock: "LockFile | None" = None
        if not errors and isinstance(data, dict):
            try:
                lock = cls.from_dict(data)
            except (KeyError, TypeError, AttributeError) as exc:
                errors.append(f"<root>: malformed lock structure: {exc}")
        if lock is not None:
            base = Path(project_dir)
            for key, entries in (("managed_files", lock.managed_files),
                                 ("shared_files", lock.shared_files)):
                for i, e in enumerate(entries):
                    try:
                        _safe_join(base, e.path)
                    except ValueError:
                        errors.append(
                            f"{key}[{i}].path: path escapes project_dir: {e.path!r}")
        if errors or lock is None:
            raise LockCorruptError("lock file failed validation", errors)
        return lock

    def save(self, project_dir: PathLike) -> Path:
        """Write the lock file (validates first, fail-closed)."""
        errors = self.validate()
        if errors:
            raise ValueError(f"refusing to save invalid lock: {errors}")
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()
        path = self.lock_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    def check_drift(self, project_dir: PathLike) -> list[dict]:
        """Compare working-tree files against locked hashes.

        Returns a list of ``{"path", "expected_sha256", "actual_sha256",
        "kind"}`` dicts where ``kind`` is ``"modified"`` or ``"missing"``
        (``actual_sha256`` is ``None`` when the file/section is missing).
        Paths are confined to ``project_dir`` (fail-closed).
        """
        base = Path(project_dir)
        drift: list[dict] = []
        for entry in self.managed_files:
            target = _safe_join(base, entry.path)
            if not target.is_file():
                drift.append({"path": entry.path,
                              "expected_sha256": entry.sha256,
                              "actual_sha256": None, "kind": "missing"})
                continue
            actual = sha256_file(target)
            if actual != entry.sha256:
                drift.append({"path": entry.path,
                              "expected_sha256": entry.sha256,
                              "actual_sha256": actual, "kind": "modified"})
        for entry in self.shared_files:
            target = _safe_join(base, entry.path)
            if not target.is_file():
                drift.append({"path": entry.path,
                              "expected_sha256": entry.sha256,
                              "actual_sha256": None, "kind": "missing"})
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
            actual = hash_managed_section(text, entry.section)
            if actual != entry.sha256:
                drift.append({"path": entry.path,
                              "expected_sha256": entry.sha256,
                              "actual_sha256": actual, "kind": "modified"})
        return drift
