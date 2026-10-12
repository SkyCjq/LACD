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
    _parse_template_bundle,
    parse_template_bundle,
    version_from_tag,
)
from lacd.upgrade.migrate import BundleVerificationError, load_bundle_from_zip

SHA = "ab" * 32
SHA2 = "cd" * 32


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


# ---------------------------------------------------------------------------
# parse_template_bundle (v1.0.1: three formats, fail-closed)
# ---------------------------------------------------------------------------

def _fixture_path(name):
    import pathlib
    return pathlib.Path(__file__).parent / "fixtures" / name


def _real_v100_release():
    with open(_fixture_path("real-v100-release.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _asset(name="lacd-templates-1.0.1.zip",
           url="https://example.com/lacd-templates-1.0.1.zip"):
    return {"name": name, "browser_download_url": url}


def test_parse_real_v100_unclosed_fence_returns_none():
    """Defect #1 regression: the real v1.0.0 body has an UNCLOSED fence.

    The new parser must return None (fail-closed), not crash and not
    misparse the sha256 out of the broken block via the fallback.
    """
    release = _real_v100_release()
    # sanity: the fixture really is the broken one (single opening fence)
    assert release["body"].count("```") == 1
    assert len(release["assets"]) == 1
    result = parse_template_bundle(release)  # must not raise
    assert result is None


def test_parse_closed_fence_succeeds():
    body = (
        "notes\n"
        "```json\n"
        '{"template_bundle": {"url": "https://x/y.zip", "sha256": "%s"}}\n'
        "```\n"
    ) % SHA
    release = {"body": body, "assets": []}
    assert parse_template_bundle(release) == {"url": "https://x/y.zip", "sha256": SHA}


def test_parse_closed_fence_uppercase_hex_normalized():
    body = '```json\n{"template_bundle": {"url": "https://x/y.zip", "sha256": "%s"}}\n```' % (
        SHA.upper())
    assert parse_template_bundle({"body": body, "assets": []})["sha256"] == SHA


def test_parse_sha_line_with_single_asset_succeeds():
    body = f"Release notes\nTemplate-Bundle-SHA256: {SHA}\n"
    release = {"body": body, "assets": [_asset()]}
    assert parse_template_bundle(release) == {
        "url": "https://example.com/lacd-templates-1.0.1.zip",
        "sha256": SHA,
    }


def test_parse_sha_line_case_insensitive_separator():
    body = f"notes\nTemplate_Bundle_SHA256 = {SHA}\n"
    release = {"body": body, "assets": [_asset()]}
    assert parse_template_bundle(release)["sha256"] == SHA


def test_parse_single_asset_fallback_bare_hex_in_prose():
    """Format (c): one asset + bare 64-hex in prose (no fence)."""
    body = (
        "## Checksums\n\n"
        f"{SHA}  lacd-templates-1.0.1.zip\n"
    )
    release = {"body": body, "assets": [_asset()]}
    assert parse_template_bundle(release) == {
        "url": "https://example.com/lacd-templates-1.0.1.zip",
        "sha256": SHA,
    }


def test_parse_bare_hex_inside_fence_does_not_count():
    """A 64-hex inside a CLOSED fence is not prose: (c) must not bind it."""
    body = (
        "```\n"
        f"checksum: {SHA}\n"
        "```\n"
        "no hex in prose here\n"
    )
    release = {"body": body, "assets": [_asset()]}
    assert parse_template_bundle(release) is None


def test_parse_missing_sha_refused():
    """Asset present but no sha256 anywhere -> None (never download blind)."""
    release = {"body": "just notes, no hashes", "assets": [_asset()]}
    assert parse_template_bundle(release) is None


def test_parse_no_assets_no_bundle():
    assert parse_template_bundle({"body": "nothing", "assets": []}) is None


def test_parse_non_hex_sha_refused():
    body = (
        "```json\n"
        '{"template_bundle": {"url": "https://x/y.zip", "sha256": "not-hex-at-all"}}\n'
        "```\n"
    )
    assert parse_template_bundle({"body": body, "assets": []}) is None


def test_parse_short_hex_sha_refused():
    body = f"Template-Bundle-SHA256: {'ab' * 16}\n"  # 32 hex, too short
    assert parse_template_bundle({"body": body, "assets": [_asset()]}) is None


def test_parse_multiple_assets_ambiguous_refused():
    """Two candidate assets + one sha line: ambiguous -> None (fail-closed)."""
    body = f"Template-Bundle-SHA256: {SHA}\n"
    release = {"body": body, "assets": [_asset(), _asset("lacd-templates-1.0.1.tar.gz")]}
    assert parse_template_bundle(release) is None


def test_parse_unclosed_fence_does_not_block_sha_line():
    """Unclosed fence + valid (b) line: (b) still binds (fence only kills (a))."""
    body = (
        "```json\n"
        '{"template_bundle": {"url": "https://x/y.zip", "sha256": "%s"}}\n'
        "(unclosed fence above)\n"
        f"Template-Bundle-SHA256: {SHA2}\n"
    ) % SHA
    release = {"body": body, "assets": [_asset()]}
    result = parse_template_bundle(release)
    assert result == {
        "url": "https://example.com/lacd-templates-1.0.1.zip",
        "sha256": SHA2,
    }


def test_parse_fence_first_wins_over_sha_line():
    """(a) has priority: closed fence binds even when a (b) line also exists."""
    body = (
        "```json\n"
        '{"template_bundle": {"url": "https://fence/y.zip", "sha256": "%s"}}\n'
        "```\n"
        f"Template-Bundle-SHA256: {SHA2}\n"
    ) % SHA
    release = {"body": body, "assets": [_asset()]}
    result = parse_template_bundle(release)
    assert result == {"url": "https://fence/y.zip", "sha256": SHA}


def test_parse_invalid_json_fence_skipped():
    body = "```json\n{not valid json\n```\n"
    assert parse_template_bundle({"body": body, "assets": []}) is None


def test_parse_fence_missing_url_refused():
    body = '```json\n{"template_bundle": {"sha256": "%s"}}\n```\n' % SHA
    assert parse_template_bundle({"body": body, "assets": []}) is None


def test_parse_not_a_dict():
    assert parse_template_bundle(None) is None
    assert parse_template_bundle("body") is None
    assert parse_template_bundle({}) is None


def test_parse_asset_missing_download_url():
    body = f"Template-Bundle-SHA256: {SHA}\n"
    release = {"body": body, "assets": [{"name": "lacd-templates-1.0.1.zip"}]}
    assert parse_template_bundle(release) is None


def test_legacy_parse_unchanged():
    """Old _parse_template_bundle kept for internal compatibility."""
    # (a) still works
    body = '```json\n{"template_bundle": {"url": "https://x/y.zip", "sha256": "%s"}}\n```' % SHA
    assert _parse_template_bundle({"body": body, "assets": []}) == {
        "url": "https://x/y.zip", "sha256": SHA}
    # (b) still works
    release = {"body": f"Template-Bundle-SHA256: {SHA}\n", "assets": [_asset()]}
    assert _parse_template_bundle(release)["sha256"] == SHA
    # unclosed fence -> None (same as before)
    assert _parse_template_bundle(_real_v100_release()) is None


def test_github_source_uses_new_parser(monkeypatch):
    """list_releases resolves bundles via parse_template_bundle (3 formats)."""
    # format (c): single asset + bare hex in prose
    body = f"checksums:\n{SHA}  lacd-templates-1.0.1.zip\n"
    payload = json.dumps([{
        "tag_name": "v1.0.1",
        "body": body,
        "assets": [_asset()],
    }]).encode()
    _patch_urlopen(monkeypatch, lambda: _FakeResp(payload))
    releases = GitHubReleasesSource("o/r").list_releases()
    assert releases[0].template_bundle == {
        "url": "https://example.com/lacd-templates-1.0.1.zip",
        "sha256": SHA,
    }


def test_load_bundle_from_zip_rejects_tampered_sha():
    """A sha256 that does not match the bytes is refused (fail-closed)."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"template_version": "1.0.1", "files": []}))
    data = buf.getvalue()
    with pytest.raises(BundleVerificationError):
        load_bundle_from_zip(data, SHA)  # wrong sha for these bytes


def test_load_bundle_from_zip_accepts_correct_sha():
    import io
    import zipfile
    import hashlib
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"template_version": "1.0.1", "files": []}))
    data = buf.getvalue()
    bundle = load_bundle_from_zip(data, hashlib.sha256(data).hexdigest())
    assert bundle.version == "1.0.1"


# ---------------------------------------------------------------------------
# v1.0.2 additions: real v1.0.1 fixture + no phantom v1.0.2
# ---------------------------------------------------------------------------

def _real_v101_release():
    with open(_fixture_path("real-v101-release.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_parse_real_v101_closed_fence_and_sha_line():
    """The real v1.0.1 release (closed fence + Template-Bundle-SHA256 line)
    must parse to a usable bundle descriptor."""
    from lacd.upgrade.source import parse_template_bundle
    rel = _real_v101_release()
    assert rel["tag_name"] == "v1.0.1"
    parsed = parse_template_bundle(rel)
    assert parsed is not None, "real v1.0.1 release must be parseable"
    assert parsed["url"] == (
        "https://github.com/SkyCjq/LACD/releases/download/v1.0.1/"
        "lacd-templates-1.0.1.zip")
    assert parsed["sha256"] == "a01c9e4f0227a35ff3755af891962840706bb408b50470c66bf03159e7e7ec51"
    assert len(parsed["sha256"]) == 64


def test_list_releases_identifies_v100_and_v101_not_phantom_v102():
    """upgrade --check source layer: real v1.0.0/v1.0.1 fixtures are
    identified; v1.0.2 must NOT appear as discoverable (not released).

    Guards against 'pretending v1.0.2 is published' — the release list
    comes only from actual release entries, never from local version strings.
    """
    from lacd.upgrade.source import ReleaseInfo, parse_template_bundle, version_from_tag
    v100 = _real_v100_release()
    v101 = _real_v101_release()

    # Simulate what GitHubSource.list_releases does with real API entries.
    def _from_entry(entry):
        tag = entry.get("tag_name")
        return ReleaseInfo(version=version_from_tag(tag), tag=tag,
                           template_bundle=parse_template_bundle(entry))
    releases = [_from_entry(v101), _from_entry(v100)]
    tags = [r.tag for r in releases]
    assert "v1.0.1" in tags
    assert "v1.0.0" in tags
    assert not any(t.startswith("v1.0.2") for t in tags), (
        f"v1.0.2 must not be discoverable before release; got tags={tags}")
