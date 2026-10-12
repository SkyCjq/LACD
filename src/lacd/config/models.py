"""Typed model for ``lacd.yaml`` (LACD configuration).

Use :func:`lacd.config.schema.load_config` to build one from disk; the
dictionaries it consumes are expected to have passed JSON-Schema validation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

DEFAULT_UPSTREAM_REPO = "SkyCjq/LACD"


@dataclass
class LacdSection:
    enabled: bool
    mode: str
    policy_version: str
    upstream_repo: str = DEFAULT_UPSTREAM_REPO


@dataclass
class GovernanceSection:
    pull_request_required: bool
    human_merge_required: bool
    risk_tiers: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)


@dataclass
class VerificationSection:
    bind_to_commit_sha: bool
    fail_closed: bool
    github_enforcement_required: bool


@dataclass
class AgentsSection:
    protocol: str
    auto_detect: bool


@dataclass
class ProjectSection:
    extensions: str


@dataclass
class LacdConfig:
    """In-memory representation of a validated ``lacd.yaml``."""

    schema_version: str
    lacd: LacdSection
    governance: GovernanceSection
    verification: VerificationSection
    agents: AgentsSection
    project: ProjectSection

    @classmethod
    def from_dict(cls, d: dict) -> "LacdConfig":
        lacd = d["lacd"]
        return cls(
            schema_version=d["schema_version"],
            lacd=LacdSection(
                enabled=lacd["enabled"],
                mode=lacd["mode"],
                policy_version=lacd["policy_version"],
                upstream_repo=lacd.get("upstream_repo", DEFAULT_UPSTREAM_REPO),
            ),
            governance=GovernanceSection(
                pull_request_required=d["governance"]["pull_request_required"],
                human_merge_required=d["governance"]["human_merge_required"],
                risk_tiers=list(d["governance"]["risk_tiers"]),
                required_checks=list(d["governance"].get("required_checks", [])),
            ),
            verification=VerificationSection(
                bind_to_commit_sha=d["verification"]["bind_to_commit_sha"],
                fail_closed=d["verification"]["fail_closed"],
                github_enforcement_required=d["verification"][
                    "github_enforcement_required"
                ],
            ),
            agents=AgentsSection(
                protocol=d["agents"]["protocol"],
                auto_detect=d["agents"]["auto_detect"],
            ),
            project=ProjectSection(extensions=d["project"]["extensions"]),
        )

    def to_dict(self) -> dict:
        return asdict(self)
