"""Unit tests for upgrade/source.py."""
import json
import os

import pytest

from urllib.error import HTTPError, URLError

from lacd.upgrade.source import (
    FileSource,
    GitHubReleasesSource,
    ReleaseInfo,
    SourceInvalid,
    SourceUnavailable,
    UpgradeSource,
    version_from_tag,
)

SHA = "ab" * 32


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# tag normalization
# ---------------------------------------------------------------------------

def test_version_from_tag():
    assert version_from_tag("v1.0.1") == "1.0.1"
    assert version_from_tag("V2.3.4") == "2.3.4"
    assert version_from_tag("1.0.0") == "1.0.0"
    assert version_from_tag("latest") is None
    assert version_from_tag("v1.0") is None
    assert version_from_tag("v1.0.0-rc.1") is None
    assert version_from_tag(None) is None
    assert version_from_tag(123) is None


# ---------------------------------------------------------------------------
# FileSource
# ---------------------------------------------------------------------------

def _releases_file(tmp_path):
    payload = {
        "releases": [
            {
                "version": "1.0.1",
                "tag": "v1.0.1",
                "commit": "a" * 40,
                "published_at": "2026-10-01T00:00:00Z",
                "prerelease": False,
                "template_bundle": {"url": "https://example.com/b.zip", "sha256": SHA},
                "notes": "patch notes",
            },
            {"version": "1.1.0"},  # minimal entry: defaults apply
            {"version": "not-a-version", "tag": "latest"},  # illegal -> skipped
        ]
    }
    p = tmp_path / "releases.json"
    _write_json(p, payload)
    return p


def test_filesource_parses_releases(tmp_path):
    src = FileSource(str(_releases_file(tmp_path)))
    releases = src.list_releases()
    assert len(releases) == 2
    r = releases[0]
    assert isinstance(r, ReleaseInfo)
    assert r.version == "1.0.1"
    assert r.tag == "v1.0.1"
    assert r.commit == "a" * 40
    assert r.published_at == "2026-10-01T00:00:00Z"
    assert r.prerelease is False
    assert r.template_bundle == {"url": "https://example.com/b.zip", "sha256": SHA}
    assert r.notes == "patch notes"
    # minimal entry gets sane defaults
    assert releases[1].version == "1.1.0"
    assert releases[1].tag == "v1.1.0"
    assert releases[1].template_bundle is None  # honest None, not fabricated


def test_filesource_skips_illegal_versions(tmp_path):
    src = FileSource(str(_releases_file(tmp_path)))
    src.list_releases()
    assert "not-a-version" in src.skipped


def test_filesource_missing_file_raises_unavailable(tmp_path):
    with pytest.raises(SourceUnavailable):
        FileSource(str(tmp_path / "nope.json")).list_releases()


def test_filesource_malformed_json_raises_invalid(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(SourceInvalid):
        FileSource(str(p)).list_releases()


def test_filesource_wrong_shape_raises_invalid(tmp_path):
    p = tmp_path / "shape.json"
    _write_json(p, {"releases": {"1.0.0": "x"}})
    with pytest.raises(SourceInvalid):
        FileSource(str(p)).list_releases()


# ---------------------------------------------------------------------------
# GitHubReleasesSource (urlopen monkeypatched)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _github_payload():
    return json.dumps([
        {
            "tag_name": "v1.0.1",
            "prerelease": False,
            "published_at": "2026-10-01T00:00:00Z",
            "body": f"Release notes\nTemplate-Bundle-SHA256: {SHA}\n",
            "assets": [
                {"name": "lacd-templates-1.0.1.zip",
                 "browser_download_url": "https://example.com/lacd-templates-1.0.1.zip"},
                {"name": "checksums.txt",
                 "browser_download_url": "https://example.com/checksums.txt"},
            ],
        },
        {
            "tag_name": "v1.1.0",
            "prerelease": True,
            "published_at": "2026-10-05T00:00:00Z",
            "body": "beta, no bundle attached",
            "assets": [],
        },
        {"tag_name": "latest", "body": "floating tag, illegal"},
    ]).encode("utf-8")


def _patch_urlopen(monkeypatch, behavior):
    import lacd.upgrade.source as mod

    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return behavior()

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    return seen


def test_github_parses_releases_and_bundle(monkeypatch):
    seen = _patch_urlopen(monkeypatch, lambda: _FakeResp(_github_payload()))
    src = GitHubReleasesSource("SkyCjq/LACD")
    releases = src.list_releases()
    assert seen["url"] == "https://api.github.com/repos/SkyCjq/LACD/releases"
    assert seen["timeout"] == 15
    assert [r.version for r in releases] == ["1.0.1", "1.1.0"]
    assert releases[0].tag == "v1.0.1"
    assert releases[0].prerelease is False
    assert releases[1].prerelease is True
    # bundle resolved from asset + body sha convention
    assert releases[0].template_bundle == {
        "url": "https://example.com/lacd-templates-1.0.1.zip",
        "sha256": SHA,
    }
    # no sha line -> honest None, even though a zip asset exists
    assert releases[1].template_bundle is None
    # illegal tag skipped and recorded
    assert "latest" in src.skipped


def test_github_json_block_bundle_convention(monkeypatch):
    body = 'notes\n```json\n{"template_bundle": {"url": "https://x/y.zip", "sha256": "%s"}}\n```\n' % SHA
    payload = json.dumps([{"tag_name": "v2.0.0", "body": body, "assets": []}]).encode()
    _patch_urlopen(monkeypatch, lambda: _FakeResp(payload))
    releases = GitHubReleasesSource("o/r").list_releases()
    assert releases[0].template_bundle == {"url": "https://x/y.zip", "sha256": SHA}


def test_github_network_error_raises_unavailable(monkeypatch):
    def boom():
        raise URLError("connection refused")

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(SourceUnavailable):
        GitHubReleasesSource("o/r").list_releases()


def test_github_500_raises_unavailable(monkeypatch):
    def boom():
        raise HTTPError("http://x", 500, "server error", {}, None)

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(SourceUnavailable):
        GitHubReleasesSource("o/r").list_releases()


def test_github_404_raises_invalid(monkeypatch):
    def boom():
        raise HTTPError("http://x", 404, "not found", {}, None)

    _patch_urlopen(monkeypatch, boom)
    with pytest.raises(SourceInvalid):
        GitHubReleasesSource("o/r").list_releases()


def test_github_non_list_raises_invalid(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeResp(b'{"not": "a list"}'))
    with pytest.raises(SourceInvalid):
        GitHubReleasesSource("o/r").list_releases()


def test_github_non_json_raises_invalid(monkeypatch):
    _patch_urlopen(monkeypatch, lambda: _FakeResp(b"<html>nope</html>"))
    with pytest.raises(SourceInvalid):
        GitHubReleasesSource("o/r").list_releases()


# ---------------------------------------------------------------------------
# from_config factory
# ---------------------------------------------------------------------------

class _FakeLacdSection:
    def __init__(self, upstream_repo=None):
        self.upstream_repo = upstream_repo


class _FakeConfig:
    def __init__(self, upstream_repo=None):
        self.lacd = _FakeLacdSection(upstream_repo)


def test_from_config_defaults_to_documented_repo():
    src = UpgradeSource.from_config(_FakeConfig())
    assert isinstance(src, GitHubReleasesSource)
    assert src.repo == "SkyCjq/LACD"


def test_from_config_env_override(monkeypatch):
    monkeypatch.setenv("LACD_UPSTREAM_REPO", "Other/Repo")
    src = UpgradeSource.from_config(_FakeConfig("SkyCjq/LACD"))
    assert isinstance(src, GitHubReleasesSource)
    assert src.repo == "Other/Repo"


def test_from_config_template_dir_with_releases_json(tmp_path, monkeypatch):
    monkeypatch.delenv("LACD_UPSTREAM_REPO", raising=False)
    _write_json(tmp_path / "releases.json", {"releases": [{"version": "1.0.0"}]})
    src = UpgradeSource.from_config(_FakeConfig(), template_dir=str(tmp_path))
    assert isinstance(src, FileSource)
    assert src.list_releases()[0].version == "1.0.0"


def test_from_config_env_template_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("LACD_UPSTREAM_REPO", raising=False)
    monkeypatch.setenv("LACD_TEMPLATE_DIR", str(tmp_path))
    _write_json(tmp_path / "releases.json", {"releases": [{"version": "1.0.0"}]})
    src = UpgradeSource.from_config(_FakeConfig())
    assert isinstance(src, FileSource)


def test_from_config_template_dir_without_releases_json_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("LACD_UPSTREAM_REPO", raising=False)
    monkeypatch.delenv("LACD_TEMPLATE_DIR", raising=False)
    src = UpgradeSource.from_config(_FakeConfig(), template_dir=str(tmp_path))
    assert isinstance(src, GitHubReleasesSource)
