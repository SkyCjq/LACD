"""SemVer compatibility policy for LACD upgrades (upgrade/compat.py).

Contract: docs/architecture/MODULE_CONTRACT.md §5.2,
          docs/specification/UPGRADE_CONTRACT.md §5.

Rules:
- patch: allowed.
- minor: allowed; preconditions list the required reviews.
- major: refused unless the caller explicitly accepts (accept_major=True);
  preconditions always include "migration-plan-required".
- downgrade: refused on the upgrade path (use the controlled rollback flow).
- same: allowed (no-op).
- support_status describes the *target* version against the support window
  "newest N minors of the same major" (N = policy.supported_minors);
  "unknown" when no release data is available. An unsupported target is
  reported, never forced: it does not change `allowed`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lacd.core.versioning import classify_bump, parse_version


@dataclass
class CompatPolicy:
    supported_minors: int = 2  # newest N minors of a major count as supported


@dataclass
class CompatReport:
    allowed: bool
    bump: str  # "patch" | "minor" | "major" | "same" | "downgrade"
    requires_major_acceptance: bool
    support_status: str  # "supported" | "unsupported" | "unknown"
    preconditions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _support_status(
    to_v: str, policy: CompatPolicy, releases: list
) -> str:
    """Support window for the target: newest N minors of its major."""
    if not releases:
        return "unknown"
    try:
        major, minor, _ = parse_version(to_v)
    except ValueError:
        raise
    minors: list[int] = []
    for release in releases:
        version = getattr(release, "version", None)
        try:
            parsed = parse_version(version) if isinstance(version, str) else None
        except ValueError:
            continue
        if parsed is not None and parsed[0] == major:
            minors.append(parsed[1])
    if not minors:
        return "unknown"
    floor = max(minors) - policy.supported_minors + 1
    return "supported" if minor >= floor else "unsupported"


def check_compatibility(
    from_v: str,
    to_v: str,
    policy: CompatPolicy,
    releases: list,
    accept_major: bool = False,
) -> CompatReport:
    """Judge whether an upgrade from `from_v` to `to_v` may proceed.

    Raises ValueError for illegal version strings (fail-closed: the caller
    turns it into an explicit error, never a silent skip).
    """
    bump = classify_bump(from_v, to_v)  # raises ValueError on illegal input
    support_status = _support_status(to_v, policy, releases)

    notes: list[str] = []
    if support_status == "unsupported":
        notes.append(
            f"target {to_v} is outside the support window "
            f"(supported_minors={policy.supported_minors}); reported, not enforced"
        )

    if bump == "same":
        return CompatReport(
            allowed=True,
            bump=bump,
            requires_major_acceptance=False,
            support_status=support_status,
            preconditions=[],
            notes=notes + ["already at target version"],
        )
    if bump == "downgrade":
        return CompatReport(
            allowed=False,
            bump=bump,
            requires_major_acceptance=False,
            support_status=support_status,
            preconditions=[],
            notes=notes
            + [
                "downgrades are refused on the upgrade path; "
                "use the controlled rollback flow (rollback_last / downgrade migration)"
            ],
        )
    if bump == "patch":
        return CompatReport(
            allowed=True,
            bump=bump,
            requires_major_acceptance=False,
            support_status=support_status,
            preconditions=[],
            notes=notes,
        )
    if bump == "minor":
        return CompatReport(
            allowed=True,
            bump=bump,
            requires_major_acceptance=False,
            support_status=support_status,
            preconditions=["minor-new-fields-review", "governance-impact-check"],
            notes=notes,
        )
    # major
    preconditions = ["migration-plan-required", "human-review-required"]
    if accept_major:
        return CompatReport(
            allowed=True,
            bump=bump,
            requires_major_acceptance=True,
            support_status=support_status,
            preconditions=preconditions,
            notes=notes + ["major upgrade explicitly accepted by caller"],
        )
    return CompatReport(
        allowed=False,
        bump=bump,
        requires_major_acceptance=True,
        support_status=support_status,
        preconditions=preconditions,
        notes=notes + ["major upgrade requires explicit acceptance (accept_major=True)"],
    )
