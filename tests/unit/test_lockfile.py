"""Unit tests: config/lockfile.py."""

import hashlib
import json

import pytest

from lacd.config.lockfile import (
    LockCorruptError,
    LockFile,
    extract_managed_section,
    hash_managed_section,
    sha256_file,
)
from .conftest import make_lock_dict, write_project

BEGIN = '<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol" version="1.0.0" -->'
END = "<!-- LACD:MANAGED:END -->"


def test_sha256_file_binary(tmp_path):
    p = tmp_path / "blob.bin"
    data = bytes(range(256)) * 4
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_save_load_round_trip(tmp_path):
    write_project(tmp_path, managed_files=[("wf/lacd-verify.yml", "workflow: v1\n")])
    lock = LockFile.load(tmp_path)
    assert lock.lock_format_version == "1"
    assert lock.lacd_cli_version == "1.0.0"
    assert lock.source.type == "git-tag"
    assert lock.source.commit == "a" * 40
    assert len(lock.managed_files) == 1
    assert lock.managed_files[0].path == "wf/lacd-verify.yml"
    assert lock.managed_files[0].merge == "replace"
    # save to another dir and reload
    other = tmp_path / "other"
    lock.save(other)
    reloaded = LockFile.load(other)
    assert reloaded.to_dict() == lock.to_dict()


def test_load_missing_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        LockFile.load(tmp_path)


def test_load_invalid_json_is_corrupt(tmp_path):
    (tmp_path / ".lacd").mkdir()
    (tmp_path / ".lacd" / "lock.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(LockCorruptError) as ei:
        LockFile.load(tmp_path)
    assert ei.value.errors


def test_load_schema_violation_is_corrupt(tmp_path):
    (tmp_path / ".lacd").mkdir()
    d = make_lock_dict()
    d["source"]["type"] = "floating-tag"  # not in enum
    (tmp_path / ".lacd" / "lock.json").write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(LockCorruptError):
        LockFile.load(tmp_path)


def test_validate_catches_bad_hash_and_merge():
    from lacd.config.lockfile import LockFile as LF
    d = make_lock_dict()
    d["managed_files"] = [
        {"path": "x.yml", "sha256": "zzz", "template": "t",
         "template_version": "1.0.0", "merge": "overwrite"},
    ]
    bad = LF.from_dict(d)
    errors = bad.validate()
    assert any("sha256" in e for e in errors)
    assert any("merge" in e for e in errors)
    good = LF.from_dict(make_lock_dict())
    assert good.validate() == []


def test_validate_rejects_escaping_paths():
    from lacd.config.lockfile import LockFile as LF, ManagedFileEntry
    lock = LF.from_dict(make_lock_dict())
    lock.managed_files.append(
        ManagedFileEntry(path="../evil.sh", sha256="a" * 64, template="t",
                         template_version="1.0.0", merge="replace")
    )
    errors = lock.validate()
    assert any("escapes" in e for e in errors)


def test_load_rejects_escaping_paths(tmp_path):
    (tmp_path / ".lacd").mkdir()
    d = make_lock_dict()
    d["managed_files"] = [
        {"path": "/abs/evil.sh", "sha256": "a" * 64, "template": "t",
         "template_version": "1.0.0", "merge": "replace"},
    ]
    (tmp_path / ".lacd" / "lock.json").write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(LockCorruptError):
        LockFile.load(tmp_path)


def test_check_drift_clean(tmp_path):
    write_project(tmp_path, managed_files=[("a.txt", "hello\n")])
    assert LockFile.load(tmp_path).check_drift(tmp_path) == []


def test_check_drift_modified(tmp_path):
    write_project(tmp_path, managed_files=[("a.txt", "hello\n")])
    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
    drift = LockFile.load(tmp_path).check_drift(tmp_path)
    assert len(drift) == 1
    entry = drift[0]
    assert entry["kind"] == "modified"
    assert entry["path"] == "a.txt"
    assert entry["expected_sha256"] == hashlib.sha256(b"hello\n").hexdigest()
    assert entry["actual_sha256"] == hashlib.sha256(b"changed\n").hexdigest()


def test_check_drift_missing(tmp_path):
    write_project(tmp_path, managed_files=[("a.txt", "hello\n")])
    (tmp_path / "a.txt").unlink()
    drift = LockFile.load(tmp_path).check_drift(tmp_path)
    assert len(drift) == 1
    assert drift[0]["kind"] == "missing"
    assert drift[0]["actual_sha256"] is None
    assert drift[0]["expected_sha256"] == hashlib.sha256(b"hello\n").hexdigest()


def _agents_md(section_body: str) -> str:
    return f"# Project\n\nuser stuff\n\n{BEGIN}\n{section_body}\n{END}\n\nmore user stuff\n"


def test_shared_section_drift(tmp_path):
    body = "protocol content v1"
    section_hash = hashlib.sha256(f"\n{body}\n".encode()).hexdigest()
    shared = [{
        "path": "AGENTS.md", "section": "lacd-agent-protocol",
        "sha256": section_hash, "template": "managed/agents-md.section.md",
        "template_version": "1.0.0", "merge": "marked-section",
    }]
    write_project(tmp_path, shared_files=shared)
    (tmp_path / "AGENTS.md").write_text(_agents_md(body), encoding="utf-8")
    assert LockFile.load(tmp_path).check_drift(tmp_path) == []
    # user edits outside the section: no drift
    (tmp_path / "AGENTS.md").write_text(
        _agents_md(body).replace("more user stuff", "edited by user"),
        encoding="utf-8",
    )
    assert LockFile.load(tmp_path).check_drift(tmp_path) == []
    # managed section edited: drift
    (tmp_path / "AGENTS.md").write_text(_agents_md("tampered"), encoding="utf-8")
    drift = LockFile.load(tmp_path).check_drift(tmp_path)
    assert len(drift) == 1 and drift[0]["kind"] == "modified"


def test_extract_managed_section_helpers():
    text = _agents_md("abc")
    assert extract_managed_section(text, "lacd-agent-protocol") == "\nabc\n"
    assert extract_managed_section(text, "nope") is None
    assert hash_managed_section(text, "lacd-agent-protocol") == hashlib.sha256(
        "\nabc\n".encode()
    ).hexdigest()
    assert hash_managed_section(text, "nope") is None


def test_save_refuses_invalid_lock(tmp_path):
    from lacd.config.lockfile import LockFile as LF
    lock = LF.from_dict(make_lock_dict())
    lock.lock_format_version = "999"  # violates const "1"
    with pytest.raises(ValueError):
        lock.save(tmp_path)
