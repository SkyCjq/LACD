"""Read-only GitHub remote enforcement verification.

Implements MODULE_CONTRACT §6.1 and §9 (v1.0.1).

Two modes:

* ``policy=None`` (deprecated): v1.0.0 semantics — "PR requirement exists"
  is treated as sufficient for ENFORCED. Kept byte-for-byte for backward
  compatibility; it is NOT sufficient evidence (see ADR-0010) and callers
  should migrate to an explicit ``EnforcementPolicy``.
* ``policy=EnforcementPolicy(...)``: strict item-by-item verification.
  ENFORCED only when every policy item is positively confirmed. Any key
  item that cannot be read (403 / undecodable / missing field) ->
  UNVERIFIED + missing list. A definitively absent/unsatisfied item ->
  NOT_ENFORCED.

Iron rules honored here:
  - "unverified" is never reported as enforced (fail-closed).
  - This module only performs HTTP GET requests; it never creates or
    modifies any remote configuration.
  - Secrets (tokens) are never written to `details`, logs, or errors.
  - 404 on the classic protection endpoint definitively means "no classic
    protection configured" (GitHub semantics); 403 means "unreadable" and
    therefore UNVERIFIED. A ruleset counts only when it is
    ``enforcement: active`` AND its ref conditions match the target branch.
"""

from __future__ import annotations

import fnmatch
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API_BASE = "https://api.github.com"
_USER_AGENT = "lacd/1.0.1 (read-only enforcement check)"

# Machine-readable reason codes (see MODULE_CONTRACT §2.3).
# v1.0.0 codes:
REMOTE_PROTECTION_VERIFIED = "REMOTE_PROTECTION_VERIFIED"
REMOTE_PROTECTION_ABSENT = "REMOTE_PROTECTION_ABSENT"
REMOTE_PERMISSION_DENIED = "REMOTE_PERMISSION_DENIED"
REMOTE_NO_CREDENTIAL = "REMOTE_NO_CREDENTIAL"
REMOTE_NETWORK_ERROR = "REMOTE_NETWORK_ERROR"
# Additional code introduced by this module (documented per contract §2.3):
#   RULESET_PULL_REQUEST_VERIFIED - enforcement established via a ruleset
#   pull_request rule rather than classic branch protection.
RULESET_PULL_REQUEST_VERIFIED = "RULESET_PULL_REQUEST_VERIFIED"
# v1.0.1 policy codes:
POLICY_LEGACY = "POLICY_LEGACY"
POLICY_ITEM_UNVERIFIED = "POLICY_ITEM_UNVERIFIED"
POLICY_ITEM_FAILED = "POLICY_ITEM_FAILED"
BYPASS_ALLOWED = "BYPASS_ALLOWED"
REQUIRED_CHECK_MISSING = "REQUIRED_CHECK_MISSING"
HUMAN_MERGE_REQUIRED = "HUMAN_MERGE_REQUIRED"

_RULESET_DETAIL_LIMIT = 20


class _NetworkError(Exception):
    """Transport-level failure or undecodable response (never carries secrets)."""


@dataclass
class EnforcementReport:
    """Result of a read-only remote enforcement check.

    status: "enforced" | "not_enforced" | "unverified"
    reasons: machine-readable codes, e.g. REMOTE_PROTECTION_VERIFIED.
    details: key factual summary only; never contains secrets. With a
        policy, details["items"] lists every policy item with
        checked/satisfied/evidence, and details["missing"] lists what could
        not be read when status is unverified.
    """

    status: str
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class EnforcementPolicy:
    """Versioned policy for strict ENFORCED verification (contract §9)."""

    policy_version: str = "1"
    require_pull_request: bool = True
    required_checks: list[str] = field(default_factory=list)
    allow_bypass: bool = False
    human_merge_required: bool = True


# ---------------------------------------------------------------------------
# HTTP layer (unchanged from v1.0.0)
# ---------------------------------------------------------------------------

def _api_request(method: str, url: str, token: str,
                 payload: dict | None = None,
                 timeout: int = 15) -> tuple[int, object | None]:
    """Perform one GitHub API request. Returns (http_status, decoded_json|None).

    Raises _NetworkError on transport failure or invalid response body.
    The token is only ever sent in the Authorization header.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise _NetworkError(f"request failed: {type(exc).__name__}") from exc
    if not body:
        return status, None
    try:
        return status, json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _NetworkError("response body is not valid JSON") from exc


# ---------------------------------------------------------------------------
# v1.0.0 legacy path (policy=None) — semantics frozen, do not "improve"
# ---------------------------------------------------------------------------

def _ruleset_has_pull_request_rule(repo: str, token: str,
                                   timeout: int) -> tuple[bool, dict]:
    """Return (has_pull_request_rule, facts). Best-effort; transport failure
    raises _NetworkError so the caller can keep it unverified."""
    status, listing = _api_request(
        "GET", f"{API_BASE}/repos/{repo}/rulesets", token, timeout=timeout
    )
    facts: dict = {"rulesets_checked": 0, "ruleset_pull_request_rule": False,
                   "rulesets_http_status": status}
    if status == 404:
        # No rulesets feature / none configured.
        return False, facts
    if status == 403:
        raise _NetworkError("rulesets endpoint denied (403)")
    if status != 200 or not isinstance(listing, list):
        raise _NetworkError(f"unexpected rulesets response: HTTP {status}")
    for entry in listing[:_RULESET_DETAIL_LIMIT]:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        facts["rulesets_checked"] += 1
        detail_url = f"{API_BASE}/repos/{repo}/rulesets/{entry['id']}"
        d_status, detail = _api_request("GET", detail_url, token, timeout=timeout)
        if d_status != 200 or not isinstance(detail, dict):
            continue
        rules = detail.get("rules") or []
        if any(isinstance(r, dict) and r.get("type") == "pull_request"
               for r in rules):
            facts["ruleset_pull_request_rule"] = True
            return True, facts
    return False, facts


def _legacy_check(repo: str, branch: str, credential: str,
                  timeout: int) -> EnforcementReport:
    """v1.0.0 semantics, frozen. PR-requirement existence is (insufficiently)
    treated as ENFORCED. Do not change; migrate callers to a policy."""
    details: dict = {"repo": repo, "branch": branch,
                     "policy": None, "policy_note": POLICY_LEGACY}
    protection_url = (
        f"{API_BASE}/repos/{repo}/branches/"
        f"{urllib.parse.quote(branch, safe='')}/protection"
    )
    try:
        prot_status, protection = _api_request(
            "GET", protection_url, credential, timeout=timeout
        )
    except _NetworkError:
        return EnforcementReport(
            status="unverified",
            reasons=[REMOTE_NETWORK_ERROR, POLICY_LEGACY],
            details=details,
        )
    details["protection_http_status"] = prot_status

    if prot_status in (401, 403):
        return EnforcementReport(
            status="unverified",
            reasons=[REMOTE_PERMISSION_DENIED, POLICY_LEGACY],
            details=details,
        )
    if prot_status not in (200, 404):
        return EnforcementReport(
            status="unverified",
            reasons=[REMOTE_NETWORK_ERROR, POLICY_LEGACY],
            details=details,
        )

    pr_required_classic = False
    if prot_status == 200 and isinstance(protection, dict):
        pr_required_classic = (
            protection.get("required_pull_request_reviews") is not None
        )
        checks = protection.get("required_status_checks")
        details["required_pull_request_reviews"] = pr_required_classic
        details["required_status_checks"] = checks is not None
        admins = protection.get("enforce_admins")
        if isinstance(admins, dict):
            details["enforce_admins"] = bool(admins.get("enabled"))
        elif admins is not None:
            details["enforce_admins"] = bool(admins)

    ruleset_pr = False
    try:
        ruleset_pr, rs_facts = _ruleset_has_pull_request_rule(
            repo, credential, timeout
        )
        details.update(rs_facts)
    except _NetworkError:
        details["rulesets_checked"] = details.get("rulesets_checked", 0)
        details["rulesets_note"] = "rulesets could not be checked"

    if pr_required_classic or ruleset_pr:
        reasons = [REMOTE_PROTECTION_VERIFIED, POLICY_LEGACY]
        if ruleset_pr and not pr_required_classic:
            reasons.append(RULESET_PULL_REQUEST_VERIFIED)
        details["enforcement_basis"] = (
            "classic-protection" if pr_required_classic else "ruleset"
        )
        return EnforcementReport(
            status="enforced", reasons=reasons, details=details
        )

    return EnforcementReport(
        status="not_enforced",
        reasons=[REMOTE_PROTECTION_ABSENT, POLICY_LEGACY],
        details=details,
    )


# ---------------------------------------------------------------------------
# v1.0.1 policy path — strict item-by-item verification
# ---------------------------------------------------------------------------

@dataclass
class _ClassicEvidence:
    readable: bool
    present: bool
    data: dict | None = None
    note: str = ""


@dataclass
class _RulesetEvidence:
    id: object = None
    readable: bool = False
    active: bool = False
    ref_matches: bool = False
    has_pr_rule: bool = False
    pr_approvals_required: int | None = None
    required_checks: list = field(default_factory=list)
    bypass_actors: list = field(default_factory=list)
    note: str = ""


@dataclass
class _RulesetsEvidence:
    readable: bool
    items: list = field(default_factory=list)
    note: str = ""


def _ref_matches_branch(conditions, branch: str) -> bool:
    """Does the ruleset's ref conditions cover refs/heads/<branch>?

    Fail-closed: no include patterns -> no match. Exclude wins over include.
    Patterns use GitHub's `*` wildcard semantics (fnmatch).
    """
    ref = f"refs/heads/{branch}"
    ref_name = (conditions or {}).get("ref_name") or {}
    include = ref_name.get("include") or []
    exclude = ref_name.get("exclude") or []
    if not include:
        return False
    if not any(fnmatch.fnmatchcase(ref, str(p)) for p in include):
        return False
    if any(fnmatch.fnmatchcase(ref, str(p)) for p in exclude):
        return False
    return True


def _classic_required_checks(protection: dict) -> list[str]:
    out: list[str] = []
    rsc = protection.get("required_status_checks") or {}
    for check in rsc.get("checks") or []:
        ctx = check.get("context") if isinstance(check, dict) else None
        if ctx:
            out.append(str(ctx))
    for ctx in rsc.get("contexts") or []:  # legacy shape
        out.append(str(ctx))
    return out


def _ruleset_required_checks(detail: dict) -> list[str]:
    out: list[str] = []
    for rule in detail.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters") or {}
        for check in params.get("required_status_checks") or []:
            ctx = check.get("context") if isinstance(check, dict) else None
            if ctx:
                out.append(str(ctx))
    return out


def _ruleset_pr_info(detail: dict) -> tuple[bool, int | None]:
    for rule in detail.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("type") != "pull_request":
            continue
        params = rule.get("parameters") or {}
        count = params.get("required_approving_review_count")
        return True, count if isinstance(count, int) else None
    return False, None


def _fetch_classic(repo: str, branch: str, credential: str,
                   timeout: int) -> _ClassicEvidence:
    url = (
        f"{API_BASE}/repos/{repo}/branches/"
        f"{urllib.parse.quote(branch, safe='')}/protection"
    )
    status, protection = _api_request("GET", url, credential, timeout=timeout)
    if status == 200 and isinstance(protection, dict):
        return _ClassicEvidence(readable=True, present=True, data=protection)
    if status == 404:
        # Definitive: no classic protection configured on this branch.
        return _ClassicEvidence(readable=True, present=False,
                                note="no classic protection (404)")
    if status in (401, 403):
        return _ClassicEvidence(readable=False, present=False,
                                note=f"protection unreadable (HTTP {status})")
    raise _NetworkError(f"unexpected protection response: HTTP {status}")


def _fetch_rulesets(repo: str, branch: str, credential: str,
                    timeout: int) -> _RulesetsEvidence:
    status, listing = _api_request(
        "GET", f"{API_BASE}/repos/{repo}/rulesets", credential, timeout=timeout
    )
    if status == 404:
        return _RulesetsEvidence(readable=True, items=[],
                                 note="no rulesets (404)")
    if status in (401, 403):
        return _RulesetsEvidence(readable=False, items=[],
                                 note=f"rulesets list unreadable (HTTP {status})")
    if status != 200 or not isinstance(listing, list):
        raise _NetworkError(f"unexpected rulesets response: HTTP {status}")
    items: list[_RulesetEvidence] = []
    for entry in listing[:_RULESET_DETAIL_LIMIT]:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        rid = entry["id"]
        d_status, detail = _api_request(
            "GET", f"{API_BASE}/repos/{repo}/rulesets/{rid}",
            credential, timeout=timeout,
        )
        if d_status != 200 or not isinstance(detail, dict):
            items.append(_RulesetEvidence(
                id=rid, readable=False,
                note=f"ruleset detail unreadable (HTTP {d_status})"))
            continue
        has_pr, approvals = _ruleset_pr_info(detail)
        items.append(_RulesetEvidence(
            id=rid,
            readable=True,
            active=(detail.get("enforcement") == "active"),
            ref_matches=_ref_matches_branch(detail.get("conditions"), branch),
            has_pr_rule=has_pr,
            pr_approvals_required=approvals,
            required_checks=_ruleset_required_checks(detail),
            bypass_actors=list(detail.get("bypass_actors") or []),
        ))
    return _RulesetsEvidence(readable=True, items=items)


# Item verdicts: True = satisfied, False = definitively not satisfied,
# None = cannot be verified (fail-closed -> UNVERIFIED).

def _eval_pr_requirement(classic: _ClassicEvidence,
                         rulesets: _RulesetsEvidence):
    if classic.readable and classic.present:
        if (classic.data or {}).get("required_pull_request_reviews") is not None:
            return True, "classic: required_pull_request_reviews present", None
    if not rulesets.readable:
        if classic.readable:
            return (None, "",
                    "rulesets unreadable: cannot rule out PR-rule coverage")
        return None, "", "classic protection and rulesets both unreadable"
    for rs in rulesets.items:
        if not rs.readable:
            return None, "", f"ruleset {rs.id}: detail unreadable"
        if rs.active and rs.ref_matches and rs.has_pr_rule:
            return (True,
                    f"ruleset {rs.id}: active, ref matches, pull_request rule",
                    None)
    if not classic.readable:
        return None, "", "classic protection unreadable"
    return False, "no PR requirement in classic protection or any qualifying ruleset", None


def _eval_required_check(name: str, classic: _ClassicEvidence,
                         rulesets: _RulesetsEvidence):
    if classic.readable and classic.present:
        if name in _classic_required_checks(classic.data or {}):
            return True, f"classic: required_status_checks contains '{name}'", None
    if not rulesets.readable:
        return None, "", "rulesets unreadable: cannot rule out check coverage"
    for rs in rulesets.items:
        if not rs.readable:
            return None, "", f"ruleset {rs.id}: detail unreadable"
        if rs.active and rs.ref_matches and name in rs.required_checks:
            return (True,
                    f"ruleset {rs.id}: required_status_checks contains '{name}'",
                    None)
    if not classic.readable:
        return None, "", "classic protection unreadable"
    return False, f"check '{name}' not found in any readable source", None


def _eval_no_bypass(classic: _ClassicEvidence, rulesets: _RulesetsEvidence):
    if classic.readable and classic.present:
        admins = (classic.data or {}).get("enforce_admins")
        if isinstance(admins, dict):
            enabled = admins.get("enabled")
        elif isinstance(admins, bool):
            enabled = admins
        else:
            enabled = None
        if enabled is False:
            return (False,
                    "classic: enforce_admins.enabled=false (admins can bypass)",
                    None)
        if enabled is None:
            return None, "", "classic: enforce_admins field missing"
        # enabled True -> no classic bypass; continue to rulesets
    elif not classic.readable:
        return None, "", "classic protection unreadable (cannot verify admin enforcement)"
    # classic absent (404): nothing classic to bypass; check rulesets.
    if not rulesets.readable:
        return None, "", "rulesets unreadable (cannot verify bypass_actors)"
    for rs in rulesets.items:
        if not rs.readable:
            return None, "", f"ruleset {rs.id}: detail unreadable"
        if rs.active and rs.ref_matches and rs.bypass_actors:
            return False, f"ruleset {rs.id}: bypass_actors configured", None
    return True, "no bypass: classic admins enforced (or absent) and no ruleset bypass_actors", None


def _policy_check(repo: str, branch: str, policy: EnforcementPolicy,
                  credential: str, timeout: int) -> EnforcementReport:
    details: dict = {"repo": repo, "branch": branch,
                     "policy_version": policy.policy_version}
    items: dict = {}
    missing: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []

    try:
        classic = _fetch_classic(repo, branch, credential, timeout)
        rulesets = _fetch_rulesets(repo, branch, credential, timeout)
    except _NetworkError:
        return EnforcementReport(
            status="unverified", reasons=[REMOTE_NETWORK_ERROR],
            details={**details, "missing": ["remote evidence unreachable"]},
        )

    # --- item 1: PR requirement ---
    if policy.require_pull_request:
        verdict, evidence, miss = _eval_pr_requirement(classic, rulesets)
        items["require_pull_request"] = {
            "checked": verdict is not None, "satisfied": bool(verdict),
            "evidence": evidence}
        if verdict is None:
            missing.append(f"require_pull_request: {miss}")
        elif not verdict:
            failed.append("require_pull_request")

    # --- item 2: required checks ---
    if policy.required_checks:
        check_missing: list[str] = []
        check_evidence: list[str] = []
        for name in policy.required_checks:
            verdict, evidence, miss = _eval_required_check(
                name, classic, rulesets)
            if verdict is None:
                missing.append(f"required_checks[{name}]: {miss}")
            elif not verdict:
                check_missing.append(name)
                failed.append(f"required_checks[{name}]")
                reasons.append(REQUIRED_CHECK_MISSING)
            else:
                check_evidence.append(evidence)
        items["required_checks"] = {
            # checked is False if any individual check was unverifiable
            "checked": not any(
                m.startswith("required_checks[") for m in missing),
            "satisfied": not check_missing,
            "missing": check_missing,
            "evidence": "; ".join(check_evidence) if check_evidence else "",
        }

    # --- item 3: no bypass ---
    if not policy.allow_bypass:
        verdict, evidence, miss = _eval_no_bypass(classic, rulesets)
        items["allow_bypass=false"] = {
            "checked": verdict is not None, "satisfied": bool(verdict),
            "evidence": evidence}
        if verdict is None:
            missing.append(f"allow_bypass: {miss}")
        elif not verdict:
            failed.append("allow_bypass")
            reasons.append(BYPASS_ALLOWED)

    details["items"] = items

    if missing:
        details["missing"] = missing
        return EnforcementReport(
            status="unverified",
            reasons=[POLICY_ITEM_UNVERIFIED],
            details=details,
        )
    if failed:
        details["failed"] = failed
        return EnforcementReport(
            status="not_enforced",
            reasons=[POLICY_ITEM_FAILED] + reasons,
            details=details,
        )

    # --- all confirmed: report human-merge note for 0-approval setups ---
    pr_via_ruleset = False
    approvals: int | None = None
    if policy.require_pull_request:
        if classic.readable and classic.present and (
                (classic.data or {}).get("required_pull_request_reviews") is not None):
            prr = (classic.data or {}).get("required_pull_request_reviews") or {}
            approvals = prr.get("required_approving_review_count")
        else:
            for rs in rulesets.items:
                if rs.readable and rs.active and rs.ref_matches and rs.has_pr_rule:
                    pr_via_ruleset = True
                    approvals = rs.pr_approvals_required
                    break
    human_merge = bool(policy.human_merge_required and approvals == 0)
    details["human_merge_required"] = human_merge
    if human_merge:
        details["human_merge_note"] = (
            "PR required with 0 approving reviews: no independent review is "
            "enforced; a human must review and merge. This is allowed, not a "
            "failure — but it must not be presented as an approving review."
        )
        reasons.append(HUMAN_MERGE_REQUIRED)

    reasons.insert(0, REMOTE_PROTECTION_VERIFIED)
    if pr_via_ruleset:
        reasons.append(RULESET_PULL_REQUEST_VERIFIED)
    return EnforcementReport(status="enforced", reasons=reasons, details=details)


def check_remote_enforcement(repo: str, branch: str, *,
                             policy: EnforcementPolicy | None = None,
                             token: str | None = None,
                             timeout: int = 15) -> EnforcementReport:
    """Verify remote branch protection / rulesets (read-only).

    With ``policy=None`` (deprecated): v1.0.0 semantics — the mere existence
    of a PR requirement is reported as ENFORCED. This is kept for backward
    compatibility but is NOT sufficient evidence (see ADR-0010); new callers
    must pass an explicit ``EnforcementPolicy``.

    With a policy: every enabled item is verified independently against
    classic branch protection and rulesets (a ruleset counts only when
    ``enforcement`` is ``active`` and its ref conditions match the branch).
    Any key item that cannot be read (403 / undecodable / missing field)
    -> UNVERIFIED with a missing list. A definitively unsatisfied item ->
    NOT_ENFORCED. Only fully confirmed items -> ENFORCED.

    Never creates or modifies remote configuration. Never reports
    "enforced" on unverified evidence.
    """
    if not repo or "/" not in repo.strip("/"):
        raise ValueError("repo must look like 'owner/name'")
    if not branch:
        raise ValueError("branch must be non-empty")
    repo = repo.strip()
    branch = branch.strip()

    credential = token or os.environ.get("GITHUB_TOKEN")
    if not credential:
        details = {"repo": repo, "branch": branch}
        if policy is not None:
            details["policy_version"] = policy.policy_version
        return EnforcementReport(
            status="unverified",
            reasons=[REMOTE_NO_CREDENTIAL],
            details={**details,
                     "note": "no token argument and GITHUB_TOKEN is unset"},
        )

    if policy is None:
        return _legacy_check(repo, branch, credential, timeout)
    if not isinstance(policy, EnforcementPolicy):
        raise ValueError("policy must be an EnforcementPolicy or None")
    return _policy_check(repo, branch, policy, credential, timeout)
