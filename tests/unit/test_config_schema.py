"""Unit tests: config/schema.py and config/models.py."""

import pytest
import yaml

from lacd.config.models import DEFAULT_UPSTREAM_REPO, LacdConfig
from lacd.config.schema import (
    ConfigInvalid,
    ConfigNotFound,
    load_config,
    validate_config_dict,
)
from .conftest import valid_config_dict, write_project


def test_load_config_missing_raises_not_found(tmp_path):
    with pytest.raises(ConfigNotFound):
        load_config(tmp_path)


def test_load_config_invalid_yaml(tmp_path):
    (tmp_path / "lacd.yaml").write_text("lacd: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigInvalid):
        load_config(tmp_path)


def test_load_config_empty_file(tmp_path):
    (tmp_path / "lacd.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigInvalid):
        load_config(tmp_path)


def test_load_config_schema_violation_missing_required(tmp_path):
    cfg = valid_config_dict()
    del cfg["governance"]
    (tmp_path / "lacd.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ConfigInvalid) as ei:
        load_config(tmp_path)
    assert ei.value.errors, "ConfigInvalid must carry the schema errors"


def test_load_config_schema_violation_bad_enum(tmp_path):
    cfg = valid_config_dict()
    cfg["lacd"]["mode"] = "something-else"
    (tmp_path / "lacd.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ConfigInvalid):
        load_config(tmp_path)


def test_load_config_schema_violation_wrong_type(tmp_path):
    cfg = valid_config_dict()
    cfg["governance"]["pull_request_required"] = "yes-please"
    (tmp_path / "lacd.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ConfigInvalid):
        load_config(tmp_path)


def test_validate_config_dict_returns_errors_not_raise():
    bad = valid_config_dict()
    bad["verification"]["fail_closed"] = 42
    errors = validate_config_dict(bad)
    assert len(errors) >= 1
    assert any("fail_closed" in e for e in errors)


def test_validate_config_dict_valid_is_empty():
    assert validate_config_dict(valid_config_dict()) == []


def test_validate_config_dict_non_dict():
    assert validate_config_dict(["not", "a", "dict"]) != []


def test_load_config_ok_and_defaults(tmp_path):
    write_project(tmp_path)  # default config omits upstream_repo
    cfg = load_config(tmp_path)
    assert isinstance(cfg, LacdConfig)
    assert cfg.schema_version == "1"
    assert cfg.lacd.enabled is True
    assert cfg.lacd.mode == "lacd"
    assert cfg.lacd.upstream_repo == DEFAULT_UPSTREAM_REPO == "SkyOneBranch/LACD"
    assert cfg.governance.risk_tiers == ["T0", "T1", "T2"]
    assert cfg.verification.github_enforcement_required is True
    assert cfg.agents.protocol == "lacd"
    assert cfg.project.extensions == ".lacd/project-rules.yaml"


def test_config_round_trip_to_dict():
    cfg = LacdConfig.from_dict(valid_config_dict())
    d = cfg.to_dict()
    assert d["lacd"]["upstream_repo"] == "SkyOneBranch/LACD"
    cfg2 = LacdConfig.from_dict(d)
    assert cfg2.to_dict() == d


def test_explicit_upstream_repo_kept():
    cfg = valid_config_dict()
    cfg["lacd"]["upstream_repo"] = "Example/Other"
    loaded = LacdConfig.from_dict(cfg)
    assert loaded.lacd.upstream_repo == "Example/Other"
