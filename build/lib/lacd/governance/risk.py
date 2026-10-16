"""Risk-tier classification for file changes.

Implements MODULE_CONTRACT §4 (governance/risk.py).

Rules are evaluated in contract order; the first match wins.
Unknown/illegal paths are T2 (fail-closed).

- T2: `.github/**`, `.lacd/**`, `**/workflows/**`, filenames containing
  secret|credential|token|private (case-insensitive), suffixes
  .pem/.key/.p12/.pfx, `SECURITY.md`, path escape / empty / absolute paths.
- T0: `docs/**`, `examples/**`, `README*`, `LICENSE*`, `NOTICE*`,
  `CHANGELOG*` (SECURITY.md already T2; AGENTS.md is T1).
- T1: `AGENTS.md`, `src/**`, `tests/**`, `schemas/**`, `pyproject.toml`,
  and every other regular file.

`classify_change` returns the highest tier (T2 > T1 > T2 > T0 -> T2 wins).
"""

from __future__ import annotations

import os

TIER_T0 = "T0"
TIER_T1 = "T1"
TIER_T2 = "T2"

_TIER_RANK = {TIER_T0: 0, TIER_T1: 1, TIER_T2: 2}

_SECRET_KEYWORDS = ("secret", "credential", "token", "private")
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

_T0_DIR_PREFIXES = ("docs/", "examples/")
_T0_NAME_PREFIXES = ("readme", "license", "notice", "changelog")
_T1_DIR_PREFIXES = ("src/", "tests/", "schemas/")


def _normalize(path: str | None) -> str | None:
    """Normalize to a clean relative posix path; return None if illegal.

    Illegal (fail-closed -> T2): None, empty/blank, absolute paths,
    `..` segments (escape), embedded NUL.
    """
    if path is None:
        return None
    p = str(path).strip()
    if not p:
        return None
    if "\x00" in p:
        return None
    if os.path.isabs(p):
        return None
    parts = p.replace("\\", "/").split("/")
    if any(seg == ".." for seg in parts):
        return None
    cleaned = [seg for seg in parts if seg not in ("", ".")]
    if not cleaned:
        return None
    return "/".join(cleaned)


def classify_path(path: str) -> str:
    """Classify a single path as "T0" | "T1" | "T2" (contract order)."""
    norm = _normalize(path)
    if norm is None:
        return TIER_T2
    lower = norm.lower()
    name = norm.rsplit("/", 1)[-1]
    name_lower = name.lower()
    segments = norm.split("/")

    # ---- T2 ----
    if lower == ".github" or lower.startswith(".github/"):
        return TIER_T2
    if lower == ".lacd" or lower.startswith(".lacd/"):
        return TIER_T2
    if "workflows" in [s.lower() for s in segments]:
        return TIER_T2
    if any(keyword in name_lower for keyword in _SECRET_KEYWORDS):
        return TIER_T2
    if any(name_lower.endswith(suffix) for suffix in _SECRET_SUFFIXES):
        return TIER_T2
    if name_lower == "security.md":
        return TIER_T2

    # ---- T0 ----  (SECURITY.md already returned T2 above)
    if any(lower.startswith(prefix) for prefix in _T0_DIR_PREFIXES):
        return TIER_T0
    if any(name_lower.startswith(prefix) for prefix in _T0_NAME_PREFIXES):
        return TIER_T0

    # ---- T1 ----
    # AGENTS.md is a shared managed file -> T1, never T0.
    # Everything else regular -> T1.
    return TIER_T1


def classify_change(paths: list[str]) -> str:
    """Classify a change set; return the highest tier (T2 > T1 > T0).

    Empty or non-list input is illegal -> T2 (fail-closed).
    """
    if not paths:
        return TIER_T2
    try:
        items = list(paths)
    except TypeError:
        return TIER_T2
    if not items:
        return TIER_T2
    best = TIER_T0
    for item in items:
        tier = classify_path(item)
        if _TIER_RANK[tier] > _TIER_RANK[best]:
            best = tier
            if best == TIER_T2:
                break
    return best
