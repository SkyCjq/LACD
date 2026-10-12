"""Unit tests for `lacd init` (real filesystem, tmp dirs; no core/config needed)."""

import hashlib
import json
import subprocess

import pytest
import yaml

from lacd import __version__
from lacd.cli import main as cli_main
from lacd.cli import templates_dir

main = cli_main.main


def _read(p):
    return p.read_text(encoding="utf-8")


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"lacd {__version__}" in capsys.readouterr().out


def test_no_subcommand_prints_help_and_exits_1(capsys):
    assert main([]) == 1
    assert "init" in capsys.readouterr().out


def test_init_dry_run_writes_nothing(tmp_path, capsys):
    rc = main(["init", "--path", str(tmp_path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "lacd.yaml" in out
    assert ".lacd/lock.json" in out
    assert ".github/workflows/lacd-verify.yml" in out
    assert "AGENTS.md" in out
    # Nothing was written.
    assert not (tmp_path / "lacd.yaml").exists()
    assert not (tmp_path / ".lacd").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_init_creates_all_files(tmp_path, capsys):
    rc = main(["init", "--path", str(tmp_path)])
    assert rc == 0
    assert "initialized" in capsys.readouterr().out

    # lacd.yaml: frozen defaults (contract section 2.1).
    cfg = yaml.safe_load(_read(tmp_path / "lacd.yaml"))
    assert cfg["schema_version"] == "1"
    assert cfg["lacd"] == {
        "enabled": True, "mode": "lacd",
        "policy_version": "1", "upstream_repo": "SkyCjq/LACD",
    }
    assert cfg["governance"]["risk_tiers"] == ["T0", "T1", "T2"]
    assert cfg["verification"]["github_enforcement_required"] is True
    assert cfg["agents"]["protocol"] == "lacd"
    assert cfg["project"]["extensions"] == ".lacd/project-rules.yaml"

    # lock.json: frozen format (contract section 2.2).
    lock = json.loads(_read(tmp_path / ".lacd" / "lock.json"))
    assert lock["lock_format_version"] == "1"
    assert lock["lacd_cli_version"] == __version__
    assert lock["source"]["type"] == "git-tag"
    assert lock["source"]["repo"] == "SkyCjq/LACD"
    assert lock["source"]["ref"] == "v" + __version__
    assert lock["source"]["commit"] is None  # unknown: honest null, never invented
    assert lock["template_version"] == __version__
    mf = lock["managed_files"][0]
    assert mf["path"] == ".github/workflows/lacd-verify.yml"
    assert mf["merge"] == "replace"
    assert mf["sha256"] == hashlib.sha256(
        (tmp_path / ".github" / "workflows" / "lacd-verify.yml").read_bytes()
    ).hexdigest()
    sf = lock["shared_files"][0]
    assert sf["path"] == "AGENTS.md"
    assert sf["section"] == "lacd-agent-protocol"
    assert sf["merge"] == "marked-section"
    # Shared-file sha256 = hash of the exact embedded section content
    # (Agent-A drift-check semantics: text between the markers, untrimmed).
    assert sf["sha256"] == hashlib.sha256(
        cli_main._agents_section_inner().encode("utf-8")
    ).hexdigest()

    # Templates installed byte-identical.
    tdir = templates_dir()
    assert (tmp_path / ".github" / "workflows" / "lacd-verify.yml").read_bytes() == (
        tdir.joinpath("workflows", "lacd-verify.yml")
    ).read_bytes()

    # project-rules.yaml created from template.
    assert (tmp_path / ".lacd" / "project-rules.yaml").read_bytes() == (
        tdir / "project-rules.template.yaml"
    ).read_bytes()

    # AGENTS.md managed section present with markers.
    agents = _read(tmp_path / "AGENTS.md")
    assert '<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol"' in agents
    assert "<!-- LACD:MANAGED:END -->" in agents
    assert "lacd status --json" in agents


def test_init_idempotent(tmp_path, capsys):
    assert main(["init", "--path", str(tmp_path)]) == 0
    capsys.readouterr()
    rc = main(["init", "--path", str(tmp_path)])
    assert rc == 0
    assert "already-initialized" in capsys.readouterr().out


def test_init_refuses_existing_different_config(tmp_path, capsys):
    (tmp_path / "lacd.yaml").write_text("schema_version: '1'\n# user file\n",
                                        encoding="utf-8")
    rc = main(["init", "--path", str(tmp_path)])
    assert rc == 1
    assert "use --force" in capsys.readouterr().err
    # Not overwritten, no lock written.
    assert "user file" in _read(tmp_path / "lacd.yaml")
    assert not (tmp_path / ".lacd" / "lock.json").exists()


def test_init_force_backs_up_and_overwrites(tmp_path):
    old_cfg = "schema_version: '1'\n# precious user config\n"
    (tmp_path / "lacd.yaml").write_text(old_cfg, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# My project\nUser docs here.\n",
                                        encoding="utf-8")

    rc = main(["init", "--path", str(tmp_path), "--force"])
    assert rc == 0

    backups = list((tmp_path / ".lacd" / "backup").glob("init-*"))
    assert len(backups) == 1
    assert _read(backups[0] / "lacd.yaml") == old_cfg
    assert "User docs here." in _read(backups[0] / "AGENTS.md")

    # New canonical config installed; user AGENTS.md content preserved.
    assert "precious user config" not in _read(tmp_path / "lacd.yaml")
    agents = _read(tmp_path / "AGENTS.md")
    assert "User docs here." in agents
    assert '<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol"' in agents


def test_agents_md_user_content_preserved_on_append(tmp_path):
    user_text = "# My project\n\nSome user guidance that must survive.\n"
    (tmp_path / "AGENTS.md").write_text(user_text, encoding="utf-8")
    assert main(["init", "--path", str(tmp_path)]) == 0
    agents = _read(tmp_path / "AGENTS.md")
    assert "Some user guidance that must survive." in agents
    assert agents.index("# My project") < agents.index("LACD:MANAGED:BEGIN")


def test_agents_md_stale_section_refreshed_in_place(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# User docs\n\n"
        '<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol" version="0.9.0" -->\n'
        "stale managed content\n"
        "<!-- LACD:MANAGED:END -->\n",
        encoding="utf-8",
    )
    assert main(["init", "--path", str(tmp_path)]) == 0
    agents = _read(tmp_path / "AGENTS.md")
    assert "# User docs" in agents  # user content untouched
    assert "stale managed content" not in agents
    assert f'version="{__version__}"' in agents
    assert agents.count("LACD:MANAGED:BEGIN") == 1  # no duplicated sections


def test_project_rules_never_overwritten(tmp_path):
    assert main(["init", "--path", str(tmp_path)]) == 0
    rules = tmp_path / ".lacd" / "project-rules.yaml"
    rules.write_text("# user rules\nrules:\n  - id: custom\n", encoding="utf-8")
    assert main(["init", "--path", str(tmp_path), "--force"]) == 0
    assert "id: custom" in _read(rules)


def test_manifest_hashes_are_real(tmp_path):
    manifest = json.loads(
        (templates_dir().joinpath("manifest.json")).read_text(encoding="utf-8")
    )
    assert manifest["template_version"] == __version__
    assert len(manifest["files"]) == 3
    for entry in manifest["files"]:
        real = hashlib.sha256(
            (templates_dir().joinpath(*entry["path"].split("/"))).read_bytes()
        ).hexdigest()
        assert entry["sha256"] == real, f"stale hash for {entry['path']}"
        assert len(entry["sha256"]) == 64


def test_init_never_runs_git_commands(tmp_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=str(tmp_path),
                   check=True, capture_output=True)
    assert main(["init", "--path", str(tmp_path)]) == 0
    # lacd must not create commits (or touch git state at all).
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
                       capture_output=True)
    assert r.returncode != 0
