"""Semantic version parsing, comparison and bump classification (LACD core).

Strict ``MAJOR.MINOR.PATCH`` with no leading zeros, per SemVer 2.0.0.
Pre-release / build metadata are not supported in v1.0 and are rejected.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(s: str) -> tuple[int, int, int]:
    """Parse ``"1.2.3"`` into ``(1, 2, 3)``.

    Raises:
        ValueError: if ``s`` is not a strict ``MAJOR.MINOR.PATCH`` string.
    """
    if not isinstance(s, str):
        raise ValueError(f"version must be a string, got {type(s).__name__}")
    m = _VERSION_RE.match(s.strip())
    if not m:
        raise ValueError(
            f"invalid semantic version: {s!r} (expected MAJOR.MINOR.PATCH, "
            "no leading zeros, no pre-release/build metadata)"
        )
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def compare(a: str, b: str) -> int:
    """Compare two versions: ``-1`` if ``a < b``, ``0`` if equal, ``1`` if ``a > b``."""
    ta, tb = parse_version(a), parse_version(b)
    return (ta > tb) - (ta < tb)


def classify_bump(from_v: str, to_v: str) -> str:
    """Classify the change from ``from_v`` to ``to_v``.

    Returns one of ``"patch"``, ``"minor"``, ``"major"``, ``"same"``,
    ``"downgrade"``. Raises :exc:`ValueError` on invalid input.
    """
    f, t = parse_version(from_v), parse_version(to_v)
    if f == t:
        return "same"
    if t < f:
        return "downgrade"
    if t[0] != f[0]:
        return "major"
    if t[1] != f[1]:
        return "minor"
    return "patch"


def is_supported(version: str, policy) -> bool:
    """Best-effort support-window check for a version against a compat policy.

    The policy object is duck-typed (owned by ``upgrade/compat.py``); the
    attributes honoured here are:

    - ``latest_version`` (or ``latest``): newest known release of the line.
      Within the same major, a version is supported when
      ``minor >= latest.minor - supported_minors + 1``.
    - ``minimum_version``: hard floor; anything below is unsupported.
    - ``supported_minors``: window width, default ``2``.

    With no usable policy information any well-formed version is reported as
    supported. Raises :exc:`ValueError` on an invalid version string.
    """
    v = parse_version(version)
    latest = getattr(policy, "latest_version", None) or getattr(policy, "latest", None)
    if latest is not None:
        lv = parse_version(str(latest))
        window = int(getattr(policy, "supported_minors", 2))
        if v[0] != lv[0]:
            return False
        return v[1] >= lv[1] - window + 1
    minimum = getattr(policy, "minimum_version", None)
    if minimum is not None:
        return compare(version, str(minimum)) >= 0
    return True
