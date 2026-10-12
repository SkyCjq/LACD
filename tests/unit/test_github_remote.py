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


# ---------------------------------------------------------------------------
# v1.0.1 policy path (R09/R10): strict item-by-item verification
# ---------------------------------------------------------------------------

from lacd.github.remote import (  # noqa: E402
    BYPASS_ALLOWED,
    HUMAN_MERGE_REQUIRED,
    POLICY_ITEM_FAILED,
    POLICY_ITEM_UNVERIFIED,
    POLICY_LEGACY,
    REQUIRED_CHECK_MISSING,
    EnforcementPolicy,
)


def _classic(pr_reviews=True, checks=(), admins_enabled=True, approvals=None):
    prr = None
    if pr_reviews:
        prr = {"dismiss_stale_reviews": False}
        if approvals is not None:
            prr["required_approving_review_count"] = approvals
    return {
        "required_pull_request_reviews": prr,
        "required_status_checks": {"checks": [{"context": c} for c in checks]},
        "enforce_admins": {"enabled": admins_enabled},
    }


def _ruleset_detail(rid, enforcement="active", include=("refs/heads/main",),
                    exclude=(), pr_rule=True, approvals=None,
                    checks=(), bypass=()):
    rules = []
    if pr_rule:
        params = {}
        if approvals is not None:
            params["required_approving_review_count"] = approvals
        rules.append({"type": "pull_request", "parameters": params})
    if checks:
        rules.append({"type": "required_status_checks", "parameters": {
            "required_status_checks": [{"context": c} for c in checks]}})
    return {
        "id": rid, "enforcement": enforcement,
        "conditions": {"ref_name": {"include": list(include),
                                    "exclude": list(exclude)}},
        "rules": rules,
        "bypass_actors": list(bypass),
    }


class _PolicyUrlopen:
    """Configurable fake: protection may be dict/404/403; rulesets likewise."""

    def __init__(self, protection="ok", rulesets="empty", details=None):
        # protection: "ok" | "nopreq" | 404 | 403 | "network"
        # rulesets: "empty" | 403 | "network" | list-of-detail-dicts | "detail404"
        self.protection = protection
        self.rulesets = rulesets
        self.details = details or {}

    def __call__(self, req, timeout=None):
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        if url.endswith("/protection"):
            p = self.protection
            if p == "network":
                raise urllib.error.URLError("down")
            if p == 403:
                raise urllib.error.HTTPError(url, 403, "F", {}, None)
            if p == 404:
                raise urllib.error.HTTPError(url, 404, "NF", {}, None)
            if p == "nopreq":
                return _FakeResp(200, _classic(pr_reviews=False))
            return _FakeResp(200, p if isinstance(p, dict) else _classic())
        if url.endswith("/rulesets"):
            r = self.rulesets
            if r == "network":
                raise urllib.error.URLError("down")
            if r == 403:
                raise urllib.error.HTTPError(url, 403, "F", {}, None)
            if r == 404:
                raise urllib.error.HTTPError(url, 404, "NF", {}, None)
            if isinstance(r, list):
                return _FakeResp(200, [{"id": d["id"]} for d in r])
            return _FakeResp(200, [])
        for rid, detail in self.details.items():
            if url.endswith(f"/rulesets/{rid}"):
                if detail == 404:
                    raise urllib.error.HTTPError(url, 404, "NF", {}, None)
                return _FakeResp(200, detail)
        raise AssertionError(f"unexpected URL: {url}")


def _ppolicy(**kw):
    return EnforcementPolicy(**kw)


def _run(monkeypatch, policy, protection="ok", rulesets="empty", details=None):
    fake = _PolicyUrlopen(protection, rulesets, details)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return check_remote_enforcement(REPO, BRANCH, policy=policy, token=TOKEN)


def test_policy_all_confirmed_classic_is_enforced(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(required_checks=["ci"]),
               protection=_classic(checks=["ci"]))
    assert rep.status == "enforced"
    assert REMOTE_PROTECTION_VERIFIED in rep.reasons
    assert rep.details["items"]["require_pull_request"]["checked"] is True
    assert rep.details["items"]["require_pull_request"]["satisfied"] is True
    assert rep.details["items"]["required_checks"]["satisfied"] is True
    assert rep.details["items"]["allow_bypass=false"]["satisfied"] is True
    assert rep.details["policy_version"] == "1"


def test_policy_ruleset_enforced_with_active_matching(monkeypatch):
    d = _ruleset_detail(9, checks=["ci"])
    rep = _run(monkeypatch, _ppolicy(required_checks=["ci"]),
               protection=404, rulesets=[d], details={9: d})
    assert rep.status == "enforced"
    assert RULESET_PULL_REQUEST_VERIFIED in rep.reasons


def test_policy_disabled_ruleset_is_not_enforced(monkeypatch):
    d = _ruleset_detail(9, enforcement="disabled")
    rep = _run(monkeypatch, _ppolicy(), protection=404,
               rulesets=[d], details={9: d})
    assert rep.status == "not_enforced"
    assert rep.status != "enforced"
    assert POLICY_ITEM_FAILED in rep.reasons
    assert "require_pull_request" in rep.details["failed"]


def test_policy_nonmatching_ref_is_not_enforced(monkeypatch):
    d = _ruleset_detail(9, include=("refs/heads/other",))
    rep = _run(monkeypatch, _ppolicy(), protection=404,
               rulesets=[d], details={9: d})
    assert rep.status == "not_enforced"
    assert rep.status != "enforced"


def test_policy_excluded_ref_is_not_enforced(monkeypatch):
    d = _ruleset_detail(9, include=("refs/heads/*",),
                        exclude=("refs/heads/main",))
    rep = _run(monkeypatch, _ppolicy(), protection=404,
               rulesets=[d], details={9: d})
    assert rep.status == "not_enforced"


def test_policy_bypass_admin_not_enforced(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(allow_bypass=False),
               protection=_classic(admins_enabled=False))
    assert rep.status == "not_enforced"
    assert BYPASS_ALLOWED in rep.reasons


def test_policy_bypass_actors_not_enforced(monkeypatch):
    d = _ruleset_detail(9, bypass=[{"actor_type": "Team"}])
    rep = _run(monkeypatch, _ppolicy(allow_bypass=False), protection=404,
               rulesets=[d], details={9: d})
    assert rep.status == "not_enforced"
    assert BYPASS_ALLOWED in rep.reasons


def test_policy_bypass_allowed_when_permitted(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(allow_bypass=True),
               protection=_classic(admins_enabled=False))
    assert rep.status == "enforced"


def test_policy_403_is_unverified_with_missing_list(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(), protection=403)
    assert rep.status == "unverified"
    assert POLICY_ITEM_UNVERIFIED in rep.reasons
    assert rep.details["missing"]  # non-empty missing list
    assert rep.status != "enforced"


def test_policy_403_rulesets_unverifiable(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(), protection=404, rulesets=403)
    assert rep.status == "unverified"
    assert POLICY_ITEM_UNVERIFIED in rep.reasons


def test_policy_404_classic_no_rulesets_is_not_enforced(monkeypatch):
    # 404 on classic = definitive "absent" (GitHub semantics), not unreadable.
    rep = _run(monkeypatch, _ppolicy(), protection=404, rulesets="empty")
    assert rep.status == "not_enforced"
    assert POLICY_ITEM_FAILED in rep.reasons
    assert "require_pull_request" in rep.details["failed"]


def test_policy_missing_required_check_not_enforced(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(required_checks=["ci", "security"]),
               protection=_classic(checks=["ci"]))
    assert rep.status == "not_enforced"
    assert REQUIRED_CHECK_MISSING in rep.reasons
    assert rep.details["items"]["required_checks"]["missing"] == ["security"]


def test_policy_check_unverifiable_when_sources_unreadable(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(required_checks=["ci"]),
               protection=404, rulesets=403)
    assert rep.status == "unverified"
    assert POLICY_ITEM_UNVERIFIED in rep.reasons


def test_policy_ruleset_detail_unreadable_is_unverified(monkeypatch):
    d = _ruleset_detail(9)
    rep = _run(monkeypatch, _ppolicy(), protection=404,
               rulesets=[d], details={9: 404})
    assert rep.status == "unverified"
    assert POLICY_ITEM_UNVERIFIED in rep.reasons


def test_policy_no_stale_verdict_old_sha(monkeypatch):
    # The check reflects CURRENT remote state: no caching of a past ENFORCED.
    fake = _PolicyUrlopen(protection=_classic())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r1 = check_remote_enforcement(REPO, BRANCH, policy=_ppolicy(), token=TOKEN)
    assert r1.status == "enforced"
    fake.protection = "nopreq"  # remote changed: PR requirement removed
    r2 = check_remote_enforcement(REPO, BRANCH, policy=_ppolicy(), token=TOKEN)
    assert r2.status == "not_enforced"


def test_policy_zero_approvals_reports_human_merge(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(human_merge_required=True),
               protection=_classic(approvals=0))
    assert rep.status == "enforced"
    assert HUMAN_MERGE_REQUIRED in rep.reasons
    assert rep.details["human_merge_required"] is True
    assert "human_merge_note" in rep.details


def test_policy_none_keeps_legacy_semantics_and_marks_deprecated(monkeypatch):
    _patch(monkeypatch, "ok")
    rep = check_remote_enforcement(REPO, BRANCH, token=TOKEN)  # policy=None
    assert rep.status == "enforced"
    assert REMOTE_PROTECTION_VERIFIED in rep.reasons
    assert POLICY_LEGACY in rep.reasons


def test_policy_invalid_type_rejected(monkeypatch):
    _patch(monkeypatch, "ok")
    with pytest.raises(ValueError):
        check_remote_enforcement(REPO, BRANCH, policy="nope", token=TOKEN)


def test_policy_network_error_is_unverified(monkeypatch):
    rep = _run(monkeypatch, _ppolicy(), protection="network")
    assert rep.status == "unverified"
    assert REMOTE_NETWORK_ERROR in rep.reasons


# ---------------------------------------------------------------------------
# v1.0.2 additions: policy-mandatory path coverage (D2)
# ---------------------------------------------------------------------------

def test_policy_no_token_is_unverified_with_policy_version(monkeypatch):
    """No token + policy: unverified, no HTTP request, policy_version recorded.

    The v1.0.1 no-token test only covered the legacy (policy=None) path.
    The strict path must also fail closed without a credential.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def _boom(req, timeout=None):
        raise AssertionError("no HTTP request should be attempted without a token")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    policy = EnforcementPolicy(required_checks=["ci"])
    report = check_remote_enforcement(REPO, BRANCH, policy=policy, token=None)
    assert report.status == "unverified"
    assert report.status != "enforced"
    assert REMOTE_NO_CREDENTIAL in report.reasons
    assert report.details.get("policy_version") == "1"


def test_policy_empty_required_checks_list_skips_check_item(monkeypatch):
    """required_checks=[] means the check item is not evaluated at all.

    An empty list must not produce a vacuous 'satisfied' required_checks item
    nor a missing entry — the item is simply absent from details.
    """
    rep = _run(monkeypatch, _ppolicy(required_checks=[]))
    assert rep.status == "enforced"
    assert "required_checks" not in rep.details["items"]


def test_policy_pr_not_required_skips_pr_item(monkeypatch):
    """require_pull_request=False: PR item absent; checks alone can enforce."""
    rep = _run(monkeypatch,
               _ppolicy(require_pull_request=False, required_checks=["ci"]),
               protection=_classic(pr_reviews=False, checks=["ci"]))
    assert rep.status == "enforced"
    assert "require_pull_request" not in rep.details["items"]
    assert rep.details["items"]["required_checks"]["satisfied"] is True


def test_remote_dimension_never_calls_with_policy_none(monkeypatch, tmp_path):
    """D2 (freeze §10): _remote_dimension must construct an EnforcementPolicy
    and pass it to check_remote_enforcement — never policy=None.

    This is the CLI main-path wiring assertion. The legacy branch stays in
    remote.py for backward compatibility but is banned from the CLI path.
    """
    import lacd.cli.main as cli_main

    seen = {}

    def fake_check(repo, branch, *, policy=None, token=None, timeout=15):
        seen["policy"] = policy
        return remote.EnforcementReport(status="unverified",
                                        reasons=["TEST_STUB"], details={})

    monkeypatch.setattr(cli_main, "_get_check_remote_enforcement",
                        lambda: fake_check)
    monkeypatch.setattr(cli_main, "_detect_repo_branch",
                        lambda p: ("octo/hello", "main"))
    monkeypatch.setattr(cli_main, "_check_config_trust",
                        lambda p: ("a" * 40, [], None))
    monkeypatch.setattr(cli_main, "_get_trusted_required_checks",
                        lambda p, sha: (["ci"], []))

    class _Contract:
        @staticmethod
        def DimensionResult(status, reasons):
            return (status, list(reasons))

    cli_main._remote_dimension(_Contract(), tmp_path)
    assert isinstance(seen.get("policy"), remote.EnforcementPolicy), (
        "D2 violation: _remote_dimension called check_remote_enforcement "
        f"with policy={seen.get('policy')!r}; an EnforcementPolicy is required")
    assert seen["policy"].policy_version == "1"
