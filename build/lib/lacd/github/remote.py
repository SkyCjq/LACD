"""Read-only GitHub remote enforcement verification.

Implements MODULE_CONTRACT §6.1.

Iron rules honored here:
  - "unverified" is never reported as enforced (fail-closed).
  - This module only performs HTTP GET requests; it never creates or
    modifies any remote configuration.
  - Secrets (tokens) are never written to `details`, logs, or errors.

Deviation note vs contract §6.1: the contract mentions trying `gh` CLI
first; the assigned task narrowed this to the `GITHUB_TOKEN` environment
variable only, which this implementation follows.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API_BASE = "https://api.github.com"
_USER_AGENT = "lacd/1.0.0 (read-only enforcement check)"

# Machine-readable reason codes (see MODULE_CONTRACT §2.3).
# Additional code introduced by this module (documented per contract §2.3):
#   RULESET_PULL_REQUEST_VERIFIED - enforcement established via a ruleset
#   pull_request rule rather than classic branch protection.
REMOTE_PROTECTION_VERIFIED = "REMOTE_PROTECTION_VERIFIED"
REMOTE_PROTECTION_ABSENT = "REMOTE_PROTECTION_ABSENT"
REMOTE_PERMISSION_DENIED = "REMOTE_PERMISSION_DENIED"
REMOTE_NO_CREDENTIAL = "REMOTE_NO_CREDENTIAL"
REMOTE_NETWORK_ERROR = "REMOTE_NETWORK_ERROR"
RULESET_PULL_REQUEST_VERIFIED = "RULESET_PULL_REQUEST_VERIFIED"

_RULESET_DETAIL_LIMIT = 20


class _NetworkError(Exception):
    """Transport-level failure or undecodable response (never carries secrets)."""


@dataclass
class EnforcementReport:
    """Result of a read-only remote enforcement check.

    status: "enforced" | "not_enforced" | "unverified"
    reasons: machine-readable codes, e.g. REMOTE_PROTECTION_VERIFIED.
    details: key factual summary only; never contains secrets.
    """

    status: str
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


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


def check_remote_enforcement(repo: str, branch: str,
                             token: str | None = None,
                             timeout: int = 15) -> EnforcementReport:
    """Verify remote branch protection / rulesets (read-only).

    Minimum bar for "enforced": the branch requires pull requests
    (`required_pull_request_reviews` non-null in classic protection, or a
    `pull_request` rule in a ruleset). `required_status_checks` and
    `enforce_admins` are recorded in details as supporting facts.
    """
    if not repo or "/" not in repo.strip("/"):
        raise ValueError("repo must look like 'owner/name'")
    if not branch:
        raise ValueError("branch must be non-empty")
    repo = repo.strip()
    branch = branch.strip()

    credential = token or os.environ.get("GITHUB_TOKEN")
    details: dict = {"repo": repo, "branch": branch}
    if not credential:
        return EnforcementReport(
            status="unverified",
            reasons=[REMOTE_NO_CREDENTIAL],
            details={**details, "note": "no token argument and GITHUB_TOKEN is unset"},
        )

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
            status="unverified", reasons=[REMOTE_NETWORK_ERROR], details=details
        )
    details["protection_http_status"] = prot_status

    if prot_status in (401, 403):
        # Cannot establish facts -> unverified, never "not_enforced".
        return EnforcementReport(
            status="unverified",
            reasons=[REMOTE_PERMISSION_DENIED],
            details=details,
        )
    if prot_status not in (200, 404):
        return EnforcementReport(
            status="unverified", reasons=[REMOTE_NETWORK_ERROR], details=details
        )

    pr_required_classic = False
    if prot_status == 200 and isinstance(protection, dict):
        # Non-null required_pull_request_reviews == PR required (contract minimum bar).
        pr_required_classic = protection.get("required_pull_request_reviews") is not None
        checks = protection.get("required_status_checks")
        details["required_pull_request_reviews"] = pr_required_classic
        details["required_status_checks"] = checks is not None
        admins = protection.get("enforce_admins")
        if isinstance(admins, dict):
            details["enforce_admins"] = bool(admins.get("enabled"))
        elif admins is not None:
            details["enforce_admins"] = bool(admins)

    # Rulesets are an independent enforcement source; a 404 on classic
    # protection does not preclude them.
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
        reasons = [REMOTE_PROTECTION_VERIFIED]
        if ruleset_pr and not pr_required_classic:
            reasons.append("RULESET_PULL_REQUEST_VERIFIED")
        details["enforcement_basis"] = (
            "classic-protection" if pr_required_classic else "ruleset"
        )
        return EnforcementReport(
            status="enforced", reasons=reasons, details=details
        )

    return EnforcementReport(
        status="not_enforced",
        reasons=[REMOTE_PROTECTION_ABSENT],
        details=details,
    )
