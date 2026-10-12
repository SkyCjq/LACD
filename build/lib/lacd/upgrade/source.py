"""Trusted release sources for LACD upgrades (upgrade/source.py).

Contract: docs/architecture/MODULE_CONTRACT.md §5.1,
          docs/specification/UPGRADE_CONTRACT.md §4.

A source lists available releases; it never downloads or executes code.
Template bundles referenced by a release are hash-verified later by
upgrade.migrate before any byte is trusted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:  # avoid a hard runtime dependency on config/models
    from lacd.config.models import LacdConfig

log = logging.getLogger(__name__)

DEFAULT_UPSTREAM_REPO = "SkyCjq/LACD"
DEFAULT_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 15

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# Convention line in release notes, e.g. "Template-Bundle-SHA256: <64 hex>".
_BUNDLE_SHA_LINE_RE = re.compile(
    r"(?im)^\s*template[-_ ]bundle[-_ ]sha256\s*[:=]\s*([0-9a-fA-F]{64})\s*$"
)
# Fenced JSON block in release notes carrying {"template_bundle": {...}}.
_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class SourceUnavailable(Exception):
    """The release source could not be reached.

    Covers network failures, timeouts, 5xx responses and missing local files.
    Callers must surface an explicit error and must never report
    "already latest" on this path (AT-12).
    """


class SourceInvalid(Exception):
    """The release source answered but its metadata is unusable.

    Covers non-JSON payloads, wrong shapes, 4xx responses (misconfigured or
    unauthorized source), etc.
    """


@dataclass
class ReleaseInfo:
    version: str            # "1.0.1" (normalized, no leading "v")
    tag: str                # "v1.0.1" (original tag as published)
    commit: str | None = None      # commit the tag points at (may be None)
    published_at: str | None = None
    prerelease: bool = False
    template_bundle: dict | None = None  # {"url": ..., "sha256": ...} | None
    notes: str = ""


def version_from_tag(tag: Any) -> str | None:
    """Normalize a release tag to "X.Y.Z"; return None for illegal tags."""
    if not isinstance(tag, str):
        return None
    name = tag.strip()
    if name[:1].lower() == "v":
        name = name[1:]
    return name if _VERSION_RE.match(name) else None


class UpgradeSource(ABC):
    """Abstract trusted source of LACD releases."""

    #: Tags skipped during the last list_releases() call (illegal tags).
    skipped: list[str]

    @abstractmethod
    def list_releases(self) -> list[ReleaseInfo]:
        """Return known releases. Raise SourceUnavailable / SourceInvalid."""

    @staticmethod
    def from_config(config: "LacdConfig", template_dir: str | None = None) -> "UpgradeSource":
        """Build the effective source from config + environment overrides.

        - repo: $LACD_UPSTREAM_REPO overrides config.lacd.upstream_repo,
          which defaults to "SkyCjq/LACD".
        - If template_dir (or $LACD_TEMPLATE_DIR) points at a directory
          containing releases.json, a FileSource on it is returned for
          fully-offline / test use. Otherwise a GitHubReleasesSource.
        """
        lacd_section = getattr(config, "lacd", None)
        repo = (
            os.environ.get("LACD_UPSTREAM_REPO")
            or getattr(lacd_section, "upstream_repo", None)
            or DEFAULT_UPSTREAM_REPO
        )
        tdir = template_dir or os.environ.get("LACD_TEMPLATE_DIR")
        if tdir:
            candidate = os.path.join(tdir, "releases.json")
            if os.path.isfile(candidate):
                log.info("using local releases file: %s", candidate)
                return FileSource(candidate)
        return GitHubReleasesSource(repo)


def _parse_template_bundle(entry: dict) -> dict | None:
    """Resolve {"url", "sha256"} for a GitHub release entry.

    Convention (documented, fail-closed):
    1. A fenced ```json block in the body may carry
       {"template_bundle": {"url": ..., "sha256": <64hex>}}.
    2. Otherwise, exactly one asset named lacd-template*.(zip|tar.gz)
       pairs with a "Template-Bundle-SHA256: <64hex>" line in the body.
    If a URL or a sha256 cannot be determined, return None honestly —
    never fabricate. A URL without a sha256 is unusable because bundles
    must be hash-verified before use.
    """
    body = entry.get("body") or ""

    def _ok_sha(value: Any) -> str | None:
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
            return value.strip().lower()
        return None

    # 1. Explicit JSON block wins.
    for match in _JSON_FENCE_RE.finditer(body):
        try:
            payload = json.loads(match.group(1))
        except (ValueError, json.JSONDecodeError):
            continue
        tb = payload.get("template_bundle") if isinstance(payload, dict) else None
        if isinstance(tb, dict):
            sha = _ok_sha(tb.get("sha256"))
            url = tb.get("url")
            if sha and isinstance(url, str) and url:
                return {"url": url, "sha256": sha}
    # 2. Asset + sha line convention.
    sha_match = _BUNDLE_SHA_LINE_RE.search(body)
    sha = sha_match.group(1).lower() if sha_match else None
    assets = entry.get("assets") or []
    candidates = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name") or ""
        if name.startswith("lacd-template") and name.endswith((".zip", ".tar.gz")):
            candidates.append(asset)
    if sha and len(candidates) == 1:
        url = candidates[0].get("browser_download_url")
        if isinstance(url, str) and url:
            return {"url": url, "sha256": sha}
    return None


class GitHubReleasesSource(UpgradeSource):
    """Read releases from the GitHub Releases API (stdlib urllib only)."""

    def __init__(
        self,
        repo: str,
        api_base: str = DEFAULT_API_BASE,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.repo = repo
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.skipped: list[str] = []

    def list_releases(self) -> list[ReleaseInfo]:
        url = f"{self.api_base}/repos/{self.repo}/releases"
        request = Request(
            url,
            headers={
                "User-Agent": "lacd-upgrade/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            # 5xx: transient server side -> unavailable. 4xx: the request or
            # the source itself is wrong/unauthorized -> invalid.
            if exc.code >= 500:
                raise SourceUnavailable(
                    f"github api {url} returned HTTP {exc.code}"
                ) from exc
            raise SourceInvalid(
                f"github api {url} returned HTTP {exc.code}: check repo name / access"
            ) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise SourceUnavailable(f"cannot reach {url}: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceInvalid(f"github api {url} returned non-JSON data") from exc
        if not isinstance(payload, list):
            raise SourceInvalid(
                f"github api {url} returned unexpected shape (expected a JSON list)"
            )

        releases: list[ReleaseInfo] = []
        skipped: list[str] = []
        for entry in payload:
            if not isinstance(entry, dict):
                skipped.append("<non-object entry>")
                continue
            tag = entry.get("tag_name")
            version = version_from_tag(tag)
            if version is None:
                skipped.append(str(tag))
                continue
            releases.append(
                ReleaseInfo(
                    version=version,
                    tag=tag,
                    commit=None,  # the releases list API carries no tag commit
                    published_at=entry.get("published_at"),
                    prerelease=bool(entry.get("prerelease", False)),
                    template_bundle=_parse_template_bundle(entry),
                    notes=entry.get("body") or "",
                )
            )
        self.skipped = skipped
        if skipped:
            log.warning("skipped %d release(s) with illegal tags: %s", len(skipped), skipped)
        return releases


class FileSource(UpgradeSource):
    """Read releases from a local JSON file: {"releases": [ReleaseInfo-dict]}.

    Intended for tests and offline use. Entries with illegal versions are
    skipped (recorded on self.skipped), matching GitHubReleasesSource.
    """

    def __init__(self, path: str):
        self.path = path
        self.skipped: list[str] = []

    def list_releases(self) -> list[ReleaseInfo]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError as exc:
            raise SourceUnavailable(f"releases file not found: {self.path}") from exc
        except OSError as exc:
            raise SourceUnavailable(f"cannot read releases file {self.path}: {exc}") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise SourceInvalid(f"releases file {self.path} is not valid JSON") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("releases"), list):
            raise SourceInvalid(
                f"releases file {self.path} must be {{\"releases\": [...]}}"
            )

        releases: list[ReleaseInfo] = []
        skipped: list[str] = []
        for item in payload["releases"]:
            if not isinstance(item, dict):
                skipped.append("<non-object entry>")
                continue
            version = item.get("version")
            if not isinstance(version, str) or not _VERSION_RE.match(version):
                skipped.append(str(version))
                continue
            bundle = item.get("template_bundle")
            releases.append(
                ReleaseInfo(
                    version=version,
                    tag=item.get("tag") or f"v{version}",
                    commit=item.get("commit"),
                    published_at=item.get("published_at"),
                    prerelease=bool(item.get("prerelease", False)),
                    template_bundle=bundle if isinstance(bundle, dict) else None,
                    notes=item.get("notes") or "",
                )
            )
        self.skipped = skipped
        return releases
