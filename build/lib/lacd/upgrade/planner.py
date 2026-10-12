"""Upgrade planning orchestration (upgrade/planner.py).

Contract: docs/architecture/MODULE_CONTRACT.md §5.4.

Pipeline: read lock -> find target release in source -> compatibility check
(refusals raise UpgradeRefused with a machine-readable reason) ->
plan_migration -> attach human_checkpoints.

Downgrades are refused here (UpgradeRefused); version rollback goes through
the dedicated rollback flow (migrate.rollback_last), never through migration.
"""
from __future__ import annotations

from lacd.config.lockfile import LockFile
from lacd.core.versioning import parse_version

from lacd.upgrade.compat import CompatPolicy, check_compatibility
from lacd.upgrade.migrate import MigrationPlan, plan_migration


class UpgradeRefused(Exception):
    """The upgrade was refused. `reason` is a machine-readable code."""

    def __init__(self, reason: str, detail: str = ""):
        message = f"upgrade refused: {reason}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.reason = reason
        self.detail = detail


def build_upgrade_plan(
    project_dir,
    to_version: str,
    *,
    source,
    bundle,
    accept_major: bool = False,
) -> MigrationPlan:
    """Assemble the full upgrade plan for `to_version` (pure; no disk writes)."""
    try:
        parse_version(to_version)
    except ValueError as exc:
        raise UpgradeRefused("invalid-target-version", to_version) from exc

    lock = LockFile.load(project_dir)
    releases = source.list_releases()  # SourceUnavailable/Invalid propagate

    target = next((r for r in releases if r.version == to_version), None)
    if target is None:
        raise UpgradeRefused(
            "unknown-target-version",
            f"{to_version} is not listed by the configured source",
        )

    from_version = lock.template_version
    policy = CompatPolicy()
    compat = check_compatibility(
        from_version, to_version, policy, releases, accept_major=accept_major
    )
    if not compat.allowed:
        raise UpgradeRefused(
            f"incompatible-{compat.bump}",
            "; ".join(compat.preconditions) or compat.bump,
        )

    if bundle is None:
        plan = MigrationPlan(from_version=from_version, to_version=to_version)
        plan.human_checkpoints.append(
            f"bundle-missing: no template bundle available for {to_version}; "
            "obtain and sha256-verify the bundle before applying"
        )
    else:
        if bundle.version != to_version:
            raise UpgradeRefused(
                "bundle-version-mismatch",
                f"bundle is {bundle.version}, target is {to_version}",
            )
        plan = plan_migration(project_dir, lock, bundle)

    # -- human checkpoints -------------------------------------------------
    for change in plan.changes:
        if change.action in ("add", "update") and change.risk == "T2":
            plan.human_checkpoints.append(
                f"review T2 change before applying: {change.path} ({change.action})"
            )
    if compat.requires_major_acceptance:
        plan.human_checkpoints.append(
            "migration-plan-required: major upgrade needs an explicit migration "
            "plan and human review before applying"
        )
    for precondition in compat.preconditions:
        if precondition != "migration-plan-required":
            plan.human_checkpoints.append(f"precondition: {precondition}")
    if plan.conflicts:
        plan.human_checkpoints.append(
            f"resolve {len(plan.conflicts)} conflict(s) before applying: "
            + ", ".join(c.path for c in plan.conflicts)
        )
    if compat.support_status == "unsupported":
        plan.human_checkpoints.append(
            f"note: target {to_version} is outside the support window "
            f"(supported_minors={policy.supported_minors}); reported, not blocking"
        )
    if getattr(target, "prerelease", False):
        plan.human_checkpoints.append(
            f"note: target {to_version} is a prerelease release"
        )
    return plan
