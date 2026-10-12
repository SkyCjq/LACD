"""Unit tests for upgrade/compat.py.

The parallel modules (core.versioning, config.lockfile) may not exist yet;
install contract-exact stubs only when the real ones are absent.
"""
import json
import re
import sys
import types
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

from lacd.upgrade.compat import CompatPolicy, CompatReport, check_compatibility
from lacd.upgrade.source import ReleaseInfo


def _releases(*versions):
    return [ReleaseInfo(version=v, tag=f"v{v}") for v in versions]


# ---------------------------------------------------------------------------
# bump classification
# ---------------------------------------------------------------------------

def test_patch_allowed():
    r = check_compatibility("1.0.0", "1.0.1", CompatPolicy(), [])
    assert r.allowed is True
    assert r.bump == "patch"
    assert r.requires_major_acceptance is False
    assert r.support_status == "unknown"  # no release data
    assert r.preconditions == []


def test_minor_allowed_with_preconditions():
    r = check_compatibility("1.0.0", "1.1.0", CompatPolicy(), [])
    assert r.allowed is True
    assert r.bump == "minor"
    assert r.requires_major_acceptance is False
    assert len(r.preconditions) > 0


def test_major_refused_by_default():
    r = check_compatibility("1.0.0", "2.0.0", CompatPolicy(), [])
    assert r.allowed is False
    assert r.bump == "major"
    assert r.requires_major_acceptance is True
    assert "migration-plan-required" in r.preconditions


def test_major_allowed_when_accepted():
    r = check_compatibility("1.0.0", "2.0.0", CompatPolicy(), [], accept_major=True)
    assert r.allowed is True
    assert r.bump == "major"
    assert r.requires_major_acceptance is True
    assert "migration-plan-required" in r.preconditions


def test_downgrade_refused():
    r = check_compatibility("1.1.0", "1.0.0", CompatPolicy(), [])
    assert r.allowed is False
    assert r.bump == "downgrade"
    assert r.requires_major_acceptance is False


def test_same_allowed_noop():
    r = check_compatibility("1.0.0", "1.0.0", CompatPolicy(), [])
    assert r.allowed is True
    assert r.bump == "same"


def test_illegal_version_raises():
    with pytest.raises(ValueError):
        check_compatibility("1.0.0", "not-a-version", CompatPolicy(), [])


# ---------------------------------------------------------------------------
# support window: newest N minors of the same major
# ---------------------------------------------------------------------------

def test_support_window_supported():
    releases = _releases("1.0.0", "1.1.0", "1.2.0")
    r = check_compatibility("1.0.0", "1.2.0", CompatPolicy(supported_minors=2), releases)
    assert r.support_status == "supported"


def test_support_window_boundary():
    # floor = 2 - 2 + 1 = 1 -> minor 1 is still supported
    releases = _releases("1.0.0", "1.1.0", "1.2.0")
    r = check_compatibility("1.0.0", "1.1.0", CompatPolicy(supported_minors=2), releases)
    assert r.support_status == "supported"


def test_support_window_unsupported_is_reported_not_forced():
    releases = _releases("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0")
    r = check_compatibility("1.0.0", "1.0.1", CompatPolicy(supported_minors=2), releases)
    assert r.support_status == "unsupported"
    assert r.allowed is True  # patch still allowed; reported, not enforced
    assert any("support window" in n for n in r.notes)


def test_support_window_per_major():
    # releases of another major do not affect this major's window
    releases = _releases("2.0.0", "2.1.0", "2.2.0")
    r = check_compatibility("1.0.0", "1.0.1", CompatPolicy(), releases)
    assert r.support_status == "unknown"


def test_support_window_custom_policy():
    releases = _releases("1.0.0", "1.1.0", "1.2.0")
    r = check_compatibility("1.0.0", "1.1.0", CompatPolicy(supported_minors=1), releases)
    assert r.support_status == "unsupported"
