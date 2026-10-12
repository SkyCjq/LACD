"""Unit tests for lacd.governance.risk (MODULE_CONTRACT §4)."""

import pytest

from lacd.governance.risk import classify_change, classify_path


# ---------- T2: governance / policy / secret surface ----------

@pytest.mark.parametrize("path", [
    ".github/workflows/lacd-verify.yml",   # .github/**
    ".github/dependabot.yml",
    ".github",                              # the dir itself
    ".lacd/lock.json",                      # .lacd/**
    ".lacd/backup/2026-01-01/x",
    "some/workflows/deploy.yml",            # **/workflows/** anywhere
    "docs/workflows/x.md",                  # T2 beats docs/** (contract order)
    "src/my_secret_config.py",              # filename contains "secret"
    "lib/CREDENTIALS.txt",                  # case-insensitive
    "tools/TokenHelper.py",                 # "token", case-insensitive
    "notes/private_notes.md",               # "private"
    "certs/server.pem",                     # secret suffixes
    "keys/deploy.key",
    "dist/app.p12",
    "dist/app.pfx",
    "KEYS/ID.PEM",                          # suffix case-insensitive
    "SECURITY.md",                          # explicit T2
    "sub/SECURITY.md",
])
def test_t2_rules(path):
    assert classify_path(path) == "T2"


@pytest.mark.parametrize("path", [
    None,
    "",
    "   ",
    "../escape.txt",        # path escape
    "a/../../b.txt",
    "/abs/path.txt",       # absolute path
    "x\x00y",              # NUL byte
])
def test_illegal_paths_are_t2(path):
    assert classify_path(path) == "T2"


# ---------- T0: documentation ----------

@pytest.mark.parametrize("path", [
    "docs/guide.md",
    "docs/specification/PROTOCOL.md",
    "examples/demo.py",
    "README.md",
    "readme.md",            # case-insensitive
    "README",
    "LICENSE",
    "LICENSE-MIT",
    "NOTICE.txt",
    "CHANGELOG.md",
])
def test_t0_rules(path):
    assert classify_path(path) == "T0"


def test_security_md_is_t2_not_t0():
    # Contract: SECURITY.md is T2 even though it looks documentation-ish.
    assert classify_path("SECURITY.md") == "T2"


def test_agents_md_is_t1_not_t0():
    # Contract: AGENTS.md is T1 (shared managed file), never T0.
    assert classify_path("AGENTS.md") == "T1"
    assert classify_path("sub/AGENTS.md") == "T1"
    # But a docs/** location still matches the T0 docs rule (order matters).
    assert classify_path("docs/AGENTS.md") == "T0"


# ---------- T1: implementation / tests / everything else ----------

@pytest.mark.parametrize("path", [
    "AGENTS.md",
    "src/lacd/risk.py",
    "tests/unit/test_risk.py",
    "schemas/lacd-config.schema.json",
    "pyproject.toml",
    "main.py",              # other regular files
    "data/sample.json",
    "weird/path.unknownext",
])
def test_t1_rules(path):
    assert classify_path(path) == "T1"


# ---------- classify_change: highest tier wins ----------

def test_classify_change_all_t0():
    assert classify_change(["docs/a.md", "README.md", "examples/x.py"]) == "T0"


def test_classify_change_t1_beats_t0():
    assert classify_change(["docs/a.md", "src/x.py"]) == "T1"


def test_classify_change_t2_beats_all():
    assert classify_change(["docs/a.md", "src/x.py", ".github/workflows/w.yml"]) == "T2"
    assert classify_change(["README.md", "notes/private.md"]) == "T2"


def test_classify_change_empty_is_t2_fail_closed():
    assert classify_change([]) == "T2"
    assert classify_change([""]) == "T2"


def test_classify_change_stops_at_t2():
    # T2 anywhere in the list dominates, regardless of position.
    assert classify_change([".lacd/lock.json"]) == "T2"
