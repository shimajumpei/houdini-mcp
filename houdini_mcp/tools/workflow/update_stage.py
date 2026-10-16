"""update_stage tool — Update stage status with transition rules."""

from __future__ import annotations

from typing import Any

from ._common import (
    _ensure_project,
    _load_gate_state,
    _record_event,
    _save_gate_state,
    _save_state,
    _write_log,
    handle_workflow_errors,
)
from .models import StageState, _now_iso, is_valid_transition


@handle_workflow_errors("update_stage")
def update_stage(
    stage_id: str,
    status: str,
    reason: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Update a stage's status with transition rule enforcement.

    Args:
        stage_id: Stage ID to update.
        status: Target status (pending, in_progress, validated, failed, completed).
        reason: Optional reason for the transition.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Status with updated stage information.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    state = result.state

    # Ensure stage exists
    if stage_id not in state.stages:
        state.stages[stage_id] = StageState()

    stage_state = state.stages[stage_id]
    current_status = stage_state.status

    # Validate transition
    if not is_valid_transition(current_status, status):
        from .models import ALLOWED_TRANSITIONS
        allowed = ALLOWED_TRANSITIONS.get(current_status, [])
        return {
            "status": "error",
            "message": (
                f"Invalid transition: '{current_status}' -> '{status}' for stage '{stage_id}'. "
                f"Allowed transitions from '{current_status}': {allowed}"
            ),
        }

    # Block pending → in_progress; redirect to plan_stage
    if current_status == "pending" and status == "in_progress":
        return {
            "status": "error",
            "message": (
                f"To start stage '{stage_id}', use plan_stage(stage_id, description) instead. "
                "plan_stage creates a plan file, resets gate enforcement, and properly "
                "transitions the stage to 'in_progress'."
            ),
        }

    # Extra rule: validated requires last validation pass
    if status == "validated" and stage_state.last_validation_passed is not True:
        return {
            "status": "error",
            "message": (
                f"Cannot set stage '{stage_id}' to 'validated': "
                "last validation did not pass. Run validate_stage first."
            ),
        }

    # Apply transition
    stage_state.status = status

    if status == "in_progress" and stage_state.started_at is None:
        stage_state.started_at = _now_iso()

    if status == "completed":
        stage_state.completed_at = _now_iso()

    # Update active stage
    if status in ("in_progress", "validated"):
        state.active_stage = stage_id
    elif status == "completed" and state.active_stage == stage_id:
        state.active_stage = None

    _save_state(agent_dir, state)

    # Update gate state for enforcement hooks
    gate = _load_gate_state(agent_dir)
    if status == "completed":
        gate.active_stage = None
        gate.active_stage_in_progress = False
        gate.diff_approved = False
        gate.pending_diff_id = None
        gate.schema_checked = {}
        gate.verified_params = {}
    elif status == "failed":
        gate.active_stage_in_progress = False
        gate.diff_approved = False
    elif status == "in_progress":
        gate.active_stage = stage_id
        gate.active_stage_in_progress = True
    elif status == "validated":
        gate.diff_approved = False
        gate.pending_diff_id = None
    _save_gate_state(agent_dir, gate)

    _write_log(agent_dir, "update_stage", {
        "event": "update_stage",
        "stage_id": stage_id,
        "from_status": current_status,
        "to_status": status,
        "reason": reason,
    })

    _record_event(
        agent_dir, "stage_status_changed",
        f"Stage '{stage_id}': {current_status} -> {status}",
        stage_id=stage_id,
        details={"from_status": current_status, "to_status": status, "reason": reason},
    )

    response: dict[str, Any] = {
        "status": "success",
        "stage_id": stage_id,
        "previous_status": current_status,
        "new_status": status,
        "reason": reason,
        "active_stage": state.active_stage,
    }

    # Recommend plan_stage for failed → in_progress restarts
    if current_status == "failed" and status == "in_progress":
        response["recommendation"] = (
            f"Consider running plan_stage('{stage_id}', description) to create "
            "a fresh plan file for the retry attempt."
        )

    return response
