"""ensure_project tool — Initialize or verify .agent/ directory."""

from __future__ import annotations

import hashlib
from typing import Any

from ._common import (
    _append_changelog,
    _ensure_claude_md,
    _ensure_project,
    _load_gate_state,
    _readiness_to_dict,
    _record_event,
    _save_gate_state,
    _save_state,
    _stage_summary,
    handle_workflow_errors,
)


def _file_md5(path) -> str | None:
    """Return hex MD5 digest of a file, or None if it doesn't exist."""
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except (OSError, FileNotFoundError):
        return None


@handle_workflow_errors("ensure_project")
def ensure_project(project_root: str | None = None) -> dict[str, Any]:
    """Initialize or verify the .agent/ workflow directory.

    Auto-bootstraps if .agent/ does not exist. Repairs partial setups.
    Includes readiness check to determine if spec/stages are defined.
    """
    result = _ensure_project(project_root)

    # Ensure CLAUDE.md exists in project root
    claude_md_action = _ensure_claude_md(result.agent_dir.parent)
    if claude_md_action:
        result.repaired.append(claude_md_action)

    # Detect external spec/stages changes by comparing file hashes
    agent_dir = result.agent_dir
    state = result.state

    current_spec_hash = _file_md5(agent_dir / "spec.md")
    current_stages_hash = _file_md5(agent_dir / "stages.yaml")

    external_changes: list[str] = []
    if state.spec_hash is not None and current_spec_hash != state.spec_hash:
        external_changes.append("spec.md")
        _record_event(
            agent_dir, "spec_updated_external",
            "spec.md was modified outside update_spec_and_stages",
        )
        _append_changelog(
            agent_dir,
            summary="External spec.md change detected",
            rationale="File was modified outside update_spec_and_stages tool",
            scope="spec.md",
        )
    if state.stages_hash is not None and current_stages_hash != state.stages_hash:
        external_changes.append("stages.yaml")
        _record_event(
            agent_dir, "stages_updated_external",
            "stages.yaml was modified outside update_spec_and_stages",
        )
        _append_changelog(
            agent_dir,
            summary="External stages.yaml change detected",
            rationale="File was modified outside update_spec_and_stages tool",
            scope="stages.yaml",
        )

    # Always update stored hashes to current values
    if current_spec_hash is not None:
        state.spec_hash = current_spec_hash
    if current_stages_hash is not None:
        state.stages_hash = current_stages_hash
    _save_state(agent_dir, state)

    # Update gate state
    gate = _load_gate_state(result.agent_dir)
    gate.workflow_initialized = True
    gate.ready_for_implementation = result.readiness.ready
    _save_gate_state(result.agent_dir, gate)

    # Record event
    if result.bootstrapped:
        _record_event(
            result.agent_dir,
            "project_bootstrap",
            "Project workflow initialized",
            details={"repaired": result.repaired},
        )

    response: dict[str, Any] = {
        "status": "success",
        "project_root": str(result.agent_dir.parent),
        "bootstrapped": result.bootstrapped,
        "repaired_files": result.repaired,
        **_readiness_to_dict(result.readiness),
        "active_stage": state.active_stage,
        "stage_summary": _stage_summary(state),
    }
    if external_changes:
        response["external_changes_detected"] = external_changes
    return response
