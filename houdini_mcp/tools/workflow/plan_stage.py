"""plan_stage tool — Create a stage plan and transition to in_progress."""

from __future__ import annotations

from typing import Any

from ._common import (
    _ensure_project,
    _load_gate_state,
    _readiness_to_dict,
    _record_event,
    _save_gate_state,
    _save_state,
    _write_log,
    handle_workflow_errors,
)
from .models import StageState, _now_iso


@handle_workflow_errors("plan_stage")
def plan_stage(
    stage_id: str,
    description: str,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Create a plan for a stage and transition it to in_progress.

    Args:
        stage_id: Stage ID from stages.yaml.
        description: Markdown description of what this stage will do.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Status with plan file path and updated state.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    state = result.state

    # Readiness gate
    if not result.readiness.ready:
        return {
            "status": "error",
            "message": "Project is not ready for implementation.",
            **_readiness_to_dict(result.readiness),
        }

    # Ensure stage exists in state (initialize if new)
    if stage_id not in state.stages:
        state.stages[stage_id] = StageState()

    stage_state = state.stages[stage_id]

    # Validate transition: must be pending or failed -> in_progress
    if stage_state.status not in ("pending", "failed"):
        return {
            "status": "error",
            "message": (
                f"Cannot plan stage '{stage_id}': current status is '{stage_state.status}'. "
                f"plan_stage requires status 'pending' or 'failed'."
            ),
        }

    # Write plan file
    plan_dir = agent_dir / "stage_plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plan_dir / f"{stage_id}.md"
    plan_file.write_text(description, encoding="utf-8")

    # Transition to in_progress
    stage_state.status = "in_progress"
    stage_state.plan_file = str(plan_file.relative_to(agent_dir))
    stage_state.started_at = _now_iso()

    # Update active stage
    state.active_stage = stage_id

    _save_state(agent_dir, state)

    # Update gate state for enforcement hooks
    gate = _load_gate_state(agent_dir)
    gate.active_stage = stage_id
    gate.active_stage_in_progress = True
    gate.diff_approved = False
    gate.pending_diff_id = None
    gate.schema_checked = {}
    gate.verified_params = {}  # Reset per stage; verified_node_types preserved (global)
    gate.error_counts = {}
    gate.docs_referenced = False
    _save_gate_state(agent_dir, gate)

    _write_log(agent_dir, "plan_stage", {
        "event": "plan_stage",
        "stage_id": stage_id,
        "plan_file": str(plan_file.relative_to(agent_dir)),
        "new_status": "in_progress",
    })

    _record_event(
        agent_dir, "stage_planned", f"Stage '{stage_id}' planned and started",
        stage_id=stage_id,
        details={"plan_file": str(plan_file.relative_to(agent_dir))},
    )

    return {
        "status": "success",
        "stage_id": stage_id,
        "plan_file": str(plan_file.relative_to(agent_dir)),
        "stage_status": "in_progress",
        "active_stage": state.active_stage,
    }
