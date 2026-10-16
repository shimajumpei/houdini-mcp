"""record_norm_check tool — Record a norm check (e.g. NotebookLM sanity check)."""

from __future__ import annotations

from typing import Any

from ._common import (
    _append_changelog,
    _ensure_project,
    _record_decision,
    _record_event,
    handle_workflow_errors,
)

_VALID_IMPACTS = ("adopt", "hold", "reject", "skipped")


@handle_workflow_errors("record_norm_check")
def record_norm_check(
    kind: str,
    summary: str,
    impact: str,
    reason: str | None = None,
    links: str | None = None,
    also_record_decision: bool = False,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Record a norm check result (consulted or skipped).

    Args:
        kind: Norm type, e.g. ``"notebooklm"``.
        summary: One-line summary of the check result.
        impact: ``"adopt"`` | ``"hold"`` | ``"reject"`` | ``"skipped"``.
        reason: Reason for skipping (when impact is ``"skipped"``).
        links: Optional memo (notebook ID, reference, etc.).
        also_record_decision: If True, also append an ADR to DECISIONS.md.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Status with event_id and list of files written to.
    """
    # Validate impact
    if impact not in _VALID_IMPACTS:
        return {
            "status": "error",
            "message": (
                f"impact must be one of {list(_VALID_IMPACTS)}, got '{impact}'."
            ),
        }

    # Resolve .agent/ — quiet exit if absent
    try:
        result = _ensure_project(project_root)
    except Exception:
        return {
            "status": "skipped",
            "message": "No .agent/ found; norm check not recorded.",
        }

    agent_dir = result.agent_dir

    # Build event details
    details: dict[str, Any] = {"kind": kind, "impact": impact}
    if reason:
        details["reason"] = reason
    if links:
        details["links"] = links

    # 1. Record event
    event_id = _record_event(
        agent_dir,
        "norm_check_recorded",
        summary,
        details=details,
    )

    # 2. Append to CHANGELOG
    if impact == "skipped":
        changelog_summary = f"NotebookLM: skipped — {reason or summary}"
    else:
        changelog_summary = f"NotebookLM: consulted — {summary} (impact: {impact})"

    _append_changelog(
        agent_dir,
        summary=changelog_summary,
        rationale=summary,
        scope=kind,
    )

    recorded_to = ["events", "changelog"]

    # 3. Optionally record decision (ADR)
    if also_record_decision:
        _record_decision(
            agent_dir,
            context=f"Norm check ({kind}): {summary}",
            decision=f"{impact}: {summary}",
            alternatives=reason or "N/A",
            consequences=f"Impact recorded as '{impact}'",
        )
        recorded_to.append("decisions")

    return {
        "status": "success",
        "event_id": event_id,
        "impact": impact,
        "recorded_to": recorded_to,
    }
