"""LACD command-line interface."""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def templates_dir() -> Path:
    """Return the directory holding the bundled managed templates.

    Resolved via :mod:`importlib.resources` so it works from a source tree,
    an editable install, and a wheel alike. This is one of the two frozen
    resource entry points (MODULE_CONTRACT §9 / ADR-0008); do not locate
    templates via ``Path(__file__)`` anywhere else.
    """
    return Path(str(importlib.resources.files("lacd.templates")))
