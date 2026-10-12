"""Shared fixtures for LACD unit tests: synthetic adopter projects."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

VALID_CONFIG = {
    "schema_version": "1",
    "lacd": {"enabled": True, "mode": "lacd", "policy_version": "1"},
    "governance": {
        "pull_request_required": True,
        "human_merge_required": True,
        "risk_tiers": ["T0", "T1", "T2"],
    },
    "verification": {
        "bind_to_commit_sha": True,
        "fail_closed": True,
        "github_enforcement_required": True,
    },
    "agents": {"protocol": "lacd", "auto_detect": True},
    "project": {"extensions": ".lacd/project-rules.yaml"},
}


def valid_config_dict() -> dict:
    return copy.deepcopy(VALID_CONFIG)


def make_lock_dict(cli_version="1.0.0", managed=(), shared=()) -> dict:
    return {
        "lock_format_version": "1",
        "lacd_cli_version": cli_version,
        "source": {
            "type": "git-tag",
            "repo": "SkyCjq/LACD",
            "ref": "v1.0.0",
            "commit": "a" * 40,
        },
        "schema_version": "1",
        "policy_version": "1",
        "template_version": "1.0.0",
        "managed_files": list(managed),
        "shared_files": list(shared),
        "generated_at": "2026-10-12T06:00:00+00:00",
        "generated_by": "lacd 1.0.0",
    }


def write_project(
    tmp_path: Path,
    *,
    config=None,
    lock: bool = True,
    lock_cli_version: str = "1.0.0",
    managed_files=(),
    shared_files=(),
    extensions: bool = True,
) -> Path:
    """Create a synthetic adopter project under ``tmp_path``.

    ``managed_files``: iterable of ``(relpath, content)``; entries are hashed
    into the generated lock. ``shared_files``: lock entries (dicts) used
    verbatim, e.g. for AGENTS.md section tests.
    """
    cfg = valid_config_dict() if config is None else config
    (tmp_path / "lacd.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    lacd_dir = tmp_path / ".lacd"
    lacd_dir.mkdir(exist_ok=True)
    if extensions:
        (lacd_dir / "project-rules.yaml").write_text(
            "# project rules\n", encoding="utf-8"
        )
    managed_entries = []
    for rel, content in managed_files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content if isinstance(content, bytes) else str(content).encode()
        p.write_bytes(data)
        managed_entries.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "template": f"templates/{rel}",
                "template_version": "1.0.0",
                "merge": "replace",
            }
        )
    if lock:
        (lacd_dir / "lock.json").write_text(
            json.dumps(make_lock_dict(lock_cli_version, managed_entries,
                                      list(shared_files))),
            encoding="utf-8",
        )
    return tmp_path


@pytest.fixture
def project_factory(tmp_path):
    def _make(**kwargs) -> Path:
        target = tmp_path / f"proj-{_make.counter}"
        _make.counter += 1
        target.mkdir()
        return write_project(target, **kwargs)

    _make.counter = 0
    return _make
