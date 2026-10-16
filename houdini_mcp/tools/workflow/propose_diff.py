"""propose_diff tool — Create a diff proposal for stage changes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ._common import (
    _ensure_project,
    _load_gate_state,
    _record_event,
    _save_gate_state,
    _write_log,
    handle_workflow_errors,
)


@handle_workflow_errors("propose_diff")
def propose_diff(
    stage_id: str,
    diff_spec: dict[str, Any],
    project_root: str | None = None,
) -> dict[str, Any]:
    """Propose a set of changes for approval before execution.

    Args:
        stage_id: Stage ID this diff belongs to.
        diff_spec: Dict describing planned changes:
            - nodes_to_add: list of {type, name, parent_path}
            - nodes_to_delete: list of node_paths
            - connections_to_add: list of {src_path, dst_path, ...}
            - connections_to_remove: list of {node_path, input_index}
            - params_to_change: list of {node_path, param_name, value}
            - scope: affected network paths
            - rationale: why this change is needed
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with diff_id and status.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    gate = _load_gate_state(agent_dir)

    # Validate diff_spec type
    if not isinstance(diff_spec, dict):
        return {
            "status": "error",
            "message": f"diff_spec must be a dict, got {type(diff_spec).__name__}.",
            "example": {
                "params_to_change": [{"node_path": "...", "param_name": "...", "value": "..."}],
                "scope": "...", "rationale": "...",
            },
            "valid_keys": ["nodes_to_add", "nodes_to_delete", "connections_to_add",
                           "connections_to_remove", "params_to_change", "scope", "rationale"],
        }

    # Validate active stage
    if gate.active_stage != stage_id or not gate.active_stage_in_progress:
        return {
            "status": "error",
            "message": (
                f"Stage '{stage_id}' is not the active in-progress stage. "
                f"Active stage: {gate.active_stage}, "
                f"in_progress: {gate.active_stage_in_progress}"
            ),
        }

    # Validate node types in diff_spec (fail-soft: skip if no verified types)
    if gate.verified_node_types:
        unverified_types = []
        for node_spec in diff_spec.get("nodes_to_add", []):
            if isinstance(node_spec, dict):
                nt = node_spec.get("type", "")
                if nt and nt not in gate.verified_node_types:
                    unverified_types.append(nt)
        if unverified_types:
            return {
                "status": "error",
                "message": (
                    f"diff_spec contains unverified node types: {unverified_types}. "
                    "Call list_node_types(name_filter=...) first."
                ),
                "unverified_node_types": unverified_types,
            }

    # Validate parameters in diff_spec (fail-soft: skip if no verified params)
    if gate.verified_params:
        unverified_params = []
        for param_spec in diff_spec.get("params_to_change", []):
            if isinstance(param_spec, dict):
                np = param_spec.get("node_path", "")
                pn = param_spec.get("param_name", "")
                if np and pn:
                    node_verified = gate.verified_params.get(np, [])
                    if node_verified and pn not in node_verified:
                        unverified_params.append(f"{np}/{pn}")
        if unverified_params:
            return {
                "status": "error",
                "message": (
                    f"diff_spec contains unverified parameters: {unverified_params}. "
                    "Call get_parameter_schema(node_path=...) first."
                ),
                "unverified_params": unverified_params,
            }

    # Generate diff_id
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    diff_id = f"{stage_id}_{timestamp}_{uuid.uuid4().hex[:8]}"

    # Save diff spec
    diffs_dir = agent_dir / "diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)
    diff_file = diffs_dir / f"{diff_id}.json"
    diff_record = {
        "diff_id": diff_id,
        "stage_id": stage_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved": False,
        "spec": diff_spec,
    }
    diff_file.write_text(
        json.dumps(diff_record, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Update gate state
    gate.pending_diff_id = diff_id
    gate.diff_approved = False
    _save_gate_state(agent_dir, gate)

    _write_log(agent_dir, "propose_diff", {
        "event": "propose_diff",
        "diff_id": diff_id,
        "stage_id": stage_id,
    })

    _record_event(
        agent_dir, "diff_proposed", f"Diff proposed for stage '{stage_id}'",
        stage_id=stage_id, diff_id=diff_id,
        details={"diff_file": str(diff_file.relative_to(agent_dir))},
    )

    return {
        "status": "success",
        "diff_id": diff_id,
        "stage_id": stage_id,
        "diff_file": str(diff_file.relative_to(agent_dir)),
        "message": "Diff proposed. Call approve_diff(diff_id) after user approval.",
    }
