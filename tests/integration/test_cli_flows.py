"""End-to-end CLI flows: init -> status -> verify in a real tmp project.

Contract test doubles (below) stand in for Agent-A's `lacd.core` /
`lacd.config` modules, which are not implemented yet.  They implement the
FROZEN contract formats faithfully against real on-disk state:

* lacd.yaml defaults      -> MODULE_CONTRACT.md section 2.1
* .lacd/lock.json format  -> section 2.2
* state priority list     -> section 2.3
* status JSON fields      -> section 2.4

The CLI under test calls the contract *signatures* (`compute_state`,
`build_status_dict`, `validate_status_dict`, `load_config`,
`DimensionResult`), so swapping in the real modules later needs no CLI
change; these doubles are then deleted.

The remote dimension uses the CLI's real degraded path
(`lacd.github.remote` not implemented -> unverified/REMOTE_NOT_CHECKED).
"""

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lacd import __version__
from lacd.cli import main as cli_main

main = cli_main.main

_AGENTS_BEGIN_RE = re.compile(
    r'<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol"[^>]*-->'
    r".*?"
    r"<!-- LACD:MANAGED:END -->",
    re.DOTALL,
)

_VALID_STATES = {"NOT_INSTALLED", "DECLARED", "VALIDATED", "ENFORCED",
                 "DRIFTED", "BLOCKED", "UNVERIFIED"}


# ------------------------------------------------------- contract doubles ---


class ConfigNotFound(Exception):
    pass


class ConfigInvalid(Exception):
    pass


def _ns(**kw):
    return SimpleNamespace(**kw)


def _dim(status, reasons=()):
    return _ns(status=status, reasons=list(reasons))


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def double_load_config(project_dir):
    p = Path(project_dir) / "lacd.yaml"
    if not p.exists():
        raise ConfigNotFound(f"no lacd.yaml in {project_dir}")
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigInvalid(f"yaml error: {exc}") from exc
    if not isinstance(d, dict):
        raise ConfigInvalid("top-level mapping required")
    required = ["schema_version", "lacd", "governance", "verification",
                "agents", "project"]
    missing = [k for k in required if k not in d]
    if missing:
        raise ConfigInvalid(f"missing keys: {missing}")
    if d.get("schema_version") != "1":
        raise ConfigInvalid("schema_version must be '1'")
    return _ns(schema_version=d["schema_version"],
               lacd=_ns(**d["lacd"]),
               governance=_ns(**d["governance"]),
               verification=_ns(**d["verification"]),
               agents=_ns(**d["agents"]),
               project=_ns(**d["project"]))


def _git_info(project_dir):
    def run(*a):
        try:
            r = subprocess.run(["git", "-C", str(project_dir), *a],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    return run("rev-parse", "--abbrev-ref", "HEAD"), run("rev-parse", "HEAD")


def _section_inner(agents_path):
    # Exact text between the markers, NO trimming (mirrors the real
    # lacd.config.lockfile.check_drift semantics).
    text = agents_path.read_text(encoding="utf-8")
    m = _AGENTS_BEGIN_RE.search(text)
    if not m:
        return None
    block = m.group(0)
    begin_end = block.index("-->") + 3
    return block[begin_end:block.rindex("<!-- LACD:MANAGED:END -->")]


def double_compute_state(project_dir, remote=None):
    pd = Path(project_dir)
    remote_dim = remote if remote is not None else _dim("unverified",
                                                        ["REMOTE_NOT_CHECKED"])

    def report(state, local, drift, blocking, vc, steps):
        return _ns(state=state, local_validation=local,
                   remote_enforcement=remote_dim, drift=drift,
                   blocking=blocking, version_consistency=vc,
                   next_steps=steps)

    try:
        double_load_config(str(pd))
    except ConfigNotFound:
        return report("NOT_INSTALLED", _dim("fail", ["CONFIG_MISSING"]),
                      _dim("clean", []), _dim("none", []),
                      {"status": "unknown", "detail": "no lacd.yaml found"},
                      ["run `lacd init` to adopt LACD"])
    except ConfigInvalid as exc:
        return report("BLOCKED", _dim("fail", ["CONFIG_INVALID"]),
                      _dim("clean", []), _dim("none", ["CONFIG_INVALID"]),
                      {"status": "unknown", "detail": str(exc)},
                      ["fix lacd.yaml"])

    lock_p = pd / ".lacd" / "lock.json"
    if not lock_p.exists():
        return report("DECLARED", _dim("fail", ["LOCK_MISSING"]),
                      _dim("clean", []), _dim("none", []),
                      {"status": "unknown", "detail": "no lock file"},
                      ["run `lacd init` to create the version lock"])
    try:
        lock = json.loads(lock_p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return report("BLOCKED", _dim("fail", ["LOCK_CORRUPT"]),
                      _dim("clean", []), _dim("none", ["LOCK_CORRUPT"]),
                      {"status": "unknown", "detail": "lock.json unreadable"},
                      ["re-run `lacd init --force` (backs up first)"])

    drifted = []
    for mf in lock.get("managed_files", []):
        fp = pd / mf["path"]
        actual = hashlib.sha256(fp.read_bytes()).hexdigest() if fp.exists() else None
        if actual != mf.get("sha256"):
            drifted.append(mf["path"])
    for sf in lock.get("shared_files", []):
        ap = pd / sf["path"]
        inner = _section_inner(ap) if ap.exists() else None
        # Same rule as the real lacd.config.lockfile.check_drift: sha256 of
        # the exact text between the markers, no trimming.
        if inner is None or hashlib.sha256(
                inner.encode("utf-8")).hexdigest() != sf.get("sha256"):
            drifted.append(sf["path"])
    drift = _dim("drifted", ["DRIFT_DETECTED"]) if drifted else _dim("clean", [])

    locked_cli = lock.get("lacd_cli_version")
    if not locked_cli:
        vc = {"status": "unknown", "detail": "lock has no lacd_cli_version"}
    elif locked_cli == __version__:
        vc = {"status": "consistent",
              "detail": f"running CLI {__version__} matches locked {locked_cli}"}
    else:
        vc = {"status": "mismatch",
              "detail": f"running CLI {__version__} != locked {locked_cli}; "
                        "run the locked version in an isolated venv"}

    if drifted:
        state, steps = "DRIFTED", ["inspect drifted files; re-run init or upgrade"]
    elif remote_dim.status == "enforced":
        state, steps = "ENFORCED", []
    else:
        state, steps = "VALIDATED", []
    if vc["status"] == "mismatch":
        steps = steps + ["version mismatch: use the locked CLI version"]
    return report(state, _dim("pass", []), drift, _dim("none", []), vc, steps)


def double_build_status_dict(project_dir, remote=None):
    rep = double_compute_state(project_dir, remote=remote)
    pd = Path(project_dir)
    branch, head = _git_info(pd)
    lock_p = pd / ".lacd" / "lock.json"
    locked_cli = template_v = None
    if lock_p.exists():
        try:
            lock = json.loads(lock_p.read_text(encoding="utf-8"))
            locked_cli = lock.get("lacd_cli_version")
            template_v = lock.get("template_version")
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "schema": "lacd-status/1",
        "lacd_cli_version": __version__,
        "generated_at": _utc_now_iso(),
        "project_dir": str(pd.resolve()),
        "branch": branch,
        "head_sha": head,
        "mode": "lacd",
        "versions": {"config_schema": "1", "policy": "1", "lock_format": "1",
                     "locked_cli": locked_cli, "template": template_v},
        "state": rep.state,
        "dimensions": {
            "local_validation": {"status": rep.local_validation.status,
                                 "reasons": rep.local_validation.reasons},
            "remote_enforcement": {"status": rep.remote_enforcement.status,
                                   "reasons": rep.remote_enforcement.reasons},
            "drift": {"status": rep.drift.status,
                      "reasons": rep.drift.reasons},
            "blocking": {"status": rep.blocking.status,
                         "reasons": rep.blocking.reasons},
        },
        "version_consistency": rep.version_consistency,
        "next_steps": rep.next_steps,
    }


_DIM_STATUSES = {
    "local_validation": {"pass", "fail", "unverified", "not-run"},
    "remote_enforcement": {"enforced", "not_enforced", "unverified"},
    "drift": {"clean", "drifted"},
    "blocking": {"none", "blocked"},
}


def double_validate_status_dict(d):
    errors = []
    if not isinstance(d, dict):
        return ["status must be a mapping"]
    for key in ("schema", "lacd_cli_version", "generated_at", "project_dir",
                "branch", "head_sha", "mode", "versions", "state",
                "dimensions", "version_consistency", "next_steps"):
        if key not in d:
            errors.append(f"missing required field: {key}")
    if d.get("schema") != "lacd-status/1":
        errors.append("schema must be 'lacd-status/1'")
    if d.get("state") not in _VALID_STATES:
        errors.append(f"unknown state: {d.get('state')}")
    dims = d.get("dimensions")
    if isinstance(dims, dict):
        for name, allowed in _DIM_STATUSES.items():
            dim = dims.get(name)
            if not isinstance(dim, dict):
                errors.append(f"dimensions.{name}: missing")
                continue
            if dim.get("status") not in allowed:
                errors.append(f"dimensions.{name}.status invalid: {dim.get('status')}")
            if not isinstance(dim.get("reasons"), list):
                errors.append(f"dimensions.{name}.reasons must be a list")
    v = d.get("versions")
    if isinstance(v, dict):
        for key in ("config_schema", "policy", "lock_format",
                    "locked_cli", "template"):
            if key not in v:
                errors.append(f"versions.{key}: missing")
    vc = d.get("version_consistency")
    if isinstance(vc, dict) and vc.get("status") not in (
            "consistent", "mismatch", "unknown"):
        errors.append(f"version_consistency.status invalid: {vc.get('status')}")
    if not isinstance(d.get("next_steps"), list):
        errors.append("next_steps must be a list")
    return errors


def _install_doubles(monkeypatch):
    fake = _ns(
        compute_state=double_compute_state,
        build_status_dict=double_build_status_dict,
        validate_status_dict=double_validate_status_dict,
        load_config=double_load_config,
        DimensionResult=lambda status, reasons: _dim(status, reasons),
    )
    monkeypatch.setattr(cli_main, "_contract", lambda: fake)

    def no_remote_module(*a, **k):
        raise ImportError("no module named 'lacd.github.remote'")

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        no_remote_module)


# ------------------------------------------------------------------ flows ---


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True, timeout=30)


@pytest.fixture
def git_project(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "init")
    # Fake origin so repo detection works without network access.
    _git(tmp_path, "remote", "add", "origin",
         "https://github.com/example/repo.git")
    _install_doubles(monkeypatch)
    return tmp_path


def test_flow_init_status_verify_all_green(git_project, capsys):
    p = git_project
    assert main(["init", "--path", str(p)]) == 0
    capsys.readouterr()

    assert main(["status", "--path", str(p)]) == 0
    assert "state: VALIDATED" in capsys.readouterr().out

    assert main(["status", "--path", str(p), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["schema"] == "lacd-status/1"
    assert d["state"] == "VALIDATED"
    assert d["dimensions"]["local_validation"]["status"] == "pass"
    assert d["dimensions"]["drift"]["status"] == "clean"
    assert d["dimensions"]["remote_enforcement"]["status"] == "unverified"
    assert d["version_consistency"]["status"] == "consistent"
    assert len(d["head_sha"]) == 40

    # Non-strict verify: report only, exit 0 despite unverified remote.
    assert main(["verify", "--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "REMOTE_NOT_CHECKED" in out

    # Strict verify: enforcement required by default -> exit 2.
    assert main(["verify", "--path", str(p), "--strict"]) == 2

    # Evidence binding: matching SHA ok, tampered SHA -> exit 2.
    ev_ok = p / "evidence-ok.json"
    ev_ok.write_text(json.dumps({"head_sha": d["head_sha"]}), encoding="utf-8")
    assert main(["verify", "--path", str(p), "--evidence", str(ev_ok)]) == 0
    ev_bad = p / "evidence-bad.json"
    ev_bad.write_text(json.dumps({"head_sha": "0" * 40}), encoding="utf-8")
    assert main(["verify", "--path", str(p), "--evidence", str(ev_bad)]) == 2
    assert "EVIDENCE_SHA_MISMATCH" in capsys.readouterr().err


def test_flow_drift_detected_after_workflow_edit(git_project, capsys):
    p = git_project
    assert main(["init", "--path", str(p)]) == 0
    capsys.readouterr()

    wf = p / ".github" / "workflows" / "lacd-verify.yml"
    wf.write_text(wf.read_text(encoding="utf-8") + "\n# local tweak\n",
                  encoding="utf-8")

    assert main(["status", "--path", str(p), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["state"] == "DRIFTED"
    assert d["dimensions"]["drift"]["status"] == "drifted"

    assert main(["verify", "--path", str(p), "--strict"]) == 2


def test_flow_not_installed(tmp_path, monkeypatch, capsys):
    _install_doubles(monkeypatch)
    assert main(["status", "--path", str(tmp_path), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["state"] == "NOT_INSTALLED"


def test_flow_strict_passes_when_remote_enforced(git_project, monkeypatch, capsys):
    p = git_project
    assert main(["init", "--path", str(p)]) == 0
    capsys.readouterr()

    def fake_check(repo, branch, token=None, timeout=15):
        return _ns(status="enforced", reasons=["REMOTE_PROTECTION_VERIFIED"])

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    assert main(["verify", "--path", str(p), "--strict"]) == 0
    out = capsys.readouterr().out
    assert "remote_enforcement: enforced" in out
    assert "state: ENFORCED" in out
