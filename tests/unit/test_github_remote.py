"""Unit tests for lacd.github.remote (MODULE_CONTRACT §6.1).

All HTTP traffic is faked by monkeypatching urllib.request.urlopen;
no real GitHub API call is made.
"""

import json
import urllib.error
import urllib.request

import pytest

from lacd.github import remote
from lacd.github.remote import (
    REMOTE_NETWORK_ERROR,
    REMOTE_NO_CREDENTIAL,
    REMOTE_PERMISSION_DENIED,
    REMOTE_PROTECTION_ABSENT,
    REMOTE_PROTECTION_VERIFIED,
    RULESET_PULL_REQUEST_VERIFIED,
    check_remote_enforcement,
)

TOKEN = "tok-test-123"
REPO = "octo/hello"
BRANCH = "main"


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _protection_payload(pr_reviews=True):
    return {
        "required_pull_request_reviews": (
            {"dismiss_stale_reviews": True} if pr_reviews else None
        ),
        "required_status_checks": {"checks": [{"context": "ci"}]},
        "enforce_admins": {"enabled": True},
    }


def _make_urlopen(mode):
    """mode: ok | nopreq | no404 | deny403 | network | ruleset"""

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        if mode == "network":
            raise urllib.error.URLError("connection refused")
        if url.endswith("/protection"):
            if mode == "ok":
                return _FakeResp(200, _protection_payload(True))
            if mode == "nopreq":
                return _FakeResp(200, _protection_payload(False))
            if mode == "deny403":
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            # no404 / ruleset: classic protection absent
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if url.endswith("/rulesets"):
            if mode == "ruleset":
                return _FakeResp(200, [{"id": 7, "name": "protect-main"}])
            return _FakeResp(200, [])
        if url.endswith("/rulesets/7"):
            return _FakeResp(
                200, {"id": 7, "rules": [{"type": "pull_request"}]}
            )
        raise AssertionError(f"unexpected URL in test: {url}")

    return fake_urlopen


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)


def _patch(monkeypatch, mode):
    monkeypatch.setattr(urllib.request, "urlopen", _make_urlopen(mode))


def test_200_with_pr_reviews_is_enforced(monkeypatch):
    _patch(monkeypatch, "ok")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    assert report.status == "enforced"
    assert REMOTE_PROTECTION_VERIFIED in report.reasons
    assert report.details["required_pull_request_reviews"] is True
    assert report.details["required_status_checks"] is True


def test_404_without_ruleset_is_not_enforced(monkeypatch):
    _patch(monkeypatch, "no404")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    assert report.status == "not_enforced"
    assert REMOTE_PROTECTION_ABSENT in report.reasons


def test_404_with_ruleset_pull_request_is_enforced(monkeypatch):
    _patch(monkeypatch, "ruleset")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    assert report.status == "enforced"
    assert RULESET_PULL_REQUEST_VERIFIED in report.reasons
    assert report.details["ruleset_pull_request_rule"] is True


def test_200_without_pr_requirement_is_not_enforced(monkeypatch):
    _patch(monkeypatch, "nopreq")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    assert report.status == "not_enforced"
    assert REMOTE_PROTECTION_ABSENT in report.reasons


def test_403_is_unverified_never_enforced(monkeypatch):
    _patch(monkeypatch, "deny403")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    assert report.status == "unverified"
    assert REMOTE_PERMISSION_DENIED in report.reasons
    assert report.status != "enforced"


def test_no_token_is_unverified_and_makes_no_request(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def _boom(req, timeout=None):
        raise AssertionError("no HTTP request should be attempted without a token")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    report = check_remote_enforcement(REPO, BRANCH, token=None)
    assert report.status == "unverified"
    assert REMOTE_NO_CREDENTIAL in report.reasons


def test_env_token_is_used(monkeypatch):
    _patch(monkeypatch, "ok")
    report = check_remote_enforcement(REPO, BRANCH, token=None)
    assert report.status == "enforced"


def test_network_error_is_unverified(monkeypatch):
    _patch(monkeypatch, "network")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    assert report.status == "unverified"
    assert REMOTE_NETWORK_ERROR in report.reasons
    assert report.status != "enforced"


def test_details_never_contain_the_token(monkeypatch):
    _patch(monkeypatch, "ok")
    report = check_remote_enforcement(REPO, BRANCH, token=TOKEN)
    blob = json.dumps(report.details) + json.dumps(report.reasons)
    assert TOKEN not in blob
    assert "Authorization" not in blob


def test_bad_repo_raises():
    with pytest.raises(ValueError):
        check_remote_enforcement("not-a-repo", BRANCH, token=TOKEN)
    with pytest.raises(ValueError):
        check_remote_enforcement(REPO, "", token=TOKEN)
