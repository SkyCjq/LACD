"""LACD configuration: lacd.yaml schema/models and .lacd/lock.json lockfile."""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def schemas_dir() -> Path:
    """Return the directory holding the bundled JSON schemas.

    Resolved via :mod:`importlib.resources` so it works from a source tree,
    an editable install, and a wheel alike. This is one of the two frozen
    resource entry points (MODULE_CONTRACT §9 / ADR-0008); do not locate
    schemas via ``Path(__file__)`` anywhere else.
    """
    return Path(str(importlib.resources.files("lacd.schemas")))
