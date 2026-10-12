"""Loading and validation of ``lacd.yaml`` (LACD configuration).

The JSON Schema is located via :mod:`importlib.resources` against the
installed ``lacd`` package, so schema lookup never depends on the current
working directory.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Union

import jsonschema
import yaml

from lacd.config.models import LacdConfig

PathLike = Union[str, Path]


class ConfigNotFound(FileNotFoundError):
    """Raised when ``project_dir`` contains no ``lacd.yaml``."""


class ConfigInvalid(ValueError):
    """Raised when ``lacd.yaml`` exists but is not a valid configuration."""

    def __init__(self, reason: str, errors: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.errors = list(errors or [])


def _config_schema() -> dict:
    schema_path = importlib.resources.files("lacd.schemas") / "lacd-config.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_config_dict(d: dict) -> list[str]:
    """Validate a decoded config mapping; return a list of error strings.

    An empty list means the mapping is valid. Never raises on bad input.
    """
    if not isinstance(d, dict):
        return ["<root>: configuration must be a mapping"]
    schema = _config_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(d), key=lambda e: list(e.absolute_path))
    out: list[str] = []
    for e in errors:
        location = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"{location}: {e.message}")
    return out


def load_config(project_dir: PathLike) -> LacdConfig:
    """Load and validate ``<project_dir>/lacd.yaml``.

    Raises:
        ConfigNotFound: no ``lacd.yaml`` in ``project_dir``.
        ConfigInvalid: unreadable-as-YAML or schema-invalid content.
        OSError: the file exists but cannot be read (I/O failure); callers
            map this to UNVERIFIED rather than "invalid config".
    """
    path = Path(project_dir) / "lacd.yaml"
    if not path.is_file():
        raise ConfigNotFound(f"no lacd.yaml in {Path(project_dir)}")
    raw = path.read_text(encoding="utf-8")  # OSError propagates intentionally
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigInvalid(f"lacd.yaml is not valid YAML: {exc}") from exc
    if data is None:
        raise ConfigInvalid("lacd.yaml is empty")
    errors = validate_config_dict(data)
    if errors:
        raise ConfigInvalid("lacd.yaml failed schema validation", errors)
    return LacdConfig.from_dict(data)
