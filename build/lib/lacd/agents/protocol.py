"""Agent collaboration protocol helpers.

Implements MODULE_CONTRACT §7 and docs/specification/PROTOCOL.md §3.

AT-24: `agent_checklist` really calls the live `core` state computation
(`core.state.compute_state` and `core.report.build_status_dict`); it never
reads a static status file. The core modules are imported lazily so this
module loads even while they are being implemented in parallel.
"""

from __future__ import annotations

from pathlib import Path

MANAGED_BEGIN = (
    '<!-- LACD:MANAGED:BEGIN section="lacd-agent-protocol" version="1.0.0" -->'
)
MANAGED_END = "<!-- LACD:MANAGED:END -->"

READY_STATES = ("VALIDATED", "ENFORCED")


def read_project_instructions(project_dir: str | Path) -> dict:
    """Locate and summarize the project instructions entrypoint (AGENTS.md).

    Returns {"found": bool, "path": str|None, "managed_section_present": bool,
             "chars": int}.
    """
    candidate = Path(project_dir) / "AGENTS.md"
    empty = {"found": False, "path": None,
             "managed_section_present": False, "chars": 0}
    if not candidate.is_file():
        return empty
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return empty
    return {
        "found": True,
        "path": str(candidate.resolve()),
        "managed_section_present": MANAGED_BEGIN in text,
        "chars": len(text),
    }


def _state_name(state) -> str:
    """Best-effort string name of an AdoptionState (str Enum or plain str)."""
    return str(getattr(state, "value", state))


def agent_checklist(project_dir: str | Path,
                    intended_paths: list[str] | None = None) -> dict:
    """Build the agent start-up checklist from live state (contract §7).

    Really calls `core.state.compute_state` and
    `core.report.build_status_dict`; `AGENTS.md` presence alone never
    implies readiness (PROTOCOL §4).

    Returns {"instructions", "status", "risk_tier", "rules", "ready"}.
    """
    # Lazy imports: core modules are delivered by parallel agents.
    from lacd.core.report import build_status_dict
    from lacd.core.state import compute_state
    from lacd.governance.risk import classify_change

    instructions = read_project_instructions(project_dir)
    status = build_status_dict(project_dir)
    state_report = compute_state(project_dir)

    state = status.get("state") if isinstance(status, dict) else None
    if not state:
        state = _state_name(state_report.state)
    else:
        state = _state_name(state)

    risk_tier = classify_change(list(intended_paths)) if intended_paths else None

    rules: list[str] = [
        f"Current adoption state is {state}. Treat `lacd status --json` as the "
        "only trusted status source; AGENTS.md presence alone proves nothing.",
    ]
    if instructions["found"]:
        if instructions["managed_section_present"]:
            rules.append(
                "Project instructions found (AGENTS.md with LACD managed "
                "section); follow them before starting work."
            )
        else:
            rules.append(
                "AGENTS.md found but the LACD managed section is missing; "
                "run `lacd init` (or refresh) to install the protocol section."
            )
    else:
        rules.append(
            "No AGENTS.md found: read the LACD protocol docs and run "
            "`lacd init` before starting agent work."
        )

    if risk_tier:
        rules.append(f"Intended paths classify as risk tier {risk_tier}.")
        if risk_tier == "T2":
            rules.append(
                "T2 paths touch governance/policy/secret surface: get human "
                "authorization first, work on a branch, and submit via PR; "
                "never push directly to a protected branch."
            )
        elif risk_tier == "T1":
            rules.append(
                "T1 paths change implementation/tests: run `lacd verify` "
                "before committing and submit via PR."
            )
        else:
            rules.append(
                "T0 paths are documentation-only: still run `lacd verify` "
                "before finishing."
            )

    if state == "NOT_INSTALLED":
        rules.append("LACD is not installed here: run `lacd init` first.")
    elif state in ("BLOCKED", "DRIFTED"):
        rules.append(
            f"State is {state}: resolve blockers/drift (`lacd verify`) "
            "before starting new work."
        )
    elif state == "UNVERIFIED":
        rules.append(
            "Key facts cannot be established (UNVERIFIED): do not claim any "
            "enforcement guarantees; fix the underlying cause first."
        )
    rules.append("Human merges every PR; LACD never auto-merges.")

    ready = state in READY_STATES and instructions["found"]
    return {
        "instructions": instructions,
        "status": status,
        "risk_tier": risk_tier,
        "rules": rules,
        "ready": ready,
    }
