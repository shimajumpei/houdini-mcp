"""record_scan tool — Record a Houdini scene scan."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ._common import (
    _ensure_project,
    _generate_network_graph,
    _record_event,
    _save_state,
    _take_snapshot,
    _write_log,
    handle_workflow_errors,
)


@handle_workflow_errors("record_scan")
def record_scan(
    scan_data: dict[str, Any],
    stage_id: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Record a Houdini scene scan result.

    Args:
        scan_data: Output from get_scene_info / serialize_scene etc.
        stage_id: Optional stage to associate the scan with.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Status with scan file path.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    state = result.state

    # Determine effective stage_id
    effective_stage = stage_id or state.active_stage or "unassigned"

    # Write scan file
    scans_dir = agent_dir / "scene_scans"
    scans_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    scan_file = scans_dir / f"{effective_stage}_{timestamp}.json"
    scan_file.write_text(
        json.dumps(scan_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Update state
    relative_path = str(scan_file.relative_to(agent_dir))
    state.last_scan = relative_path

    if effective_stage in state.stages:
        state.stages[effective_stage].last_scan_file = relative_path

    _save_state(agent_dir, state)
    _write_log(agent_dir, "record_scan", {
        "event": "record_scan",
        "stage_id": effective_stage,
        "scan_file": relative_path,
    })

    event_id = _record_event(
        agent_dir, "scene_scanned", f"Scene scan recorded for stage '{effective_stage}'",
        stage_id=effective_stage,
        details={"scan_file": relative_path},
    )

    # Save snapshot from scan_data
    artifacts: list[str] = []
    if event_id:
        snapshot_path = _take_snapshot(
            agent_dir, event_id, scan_data=scan_data, scope=effective_stage,
        )
        if snapshot_path:
            artifacts.append(snapshot_path)

        # Generate network graph from scan_data
        network_path = _generate_network_graph(agent_dir, event_id, scan_data)
        if network_path:
            artifacts.append(network_path)

    result_data: dict[str, Any] = {
        "status": "success",
        "stage_id": effective_stage,
        "scan_file": relative_path,
    }
    if artifacts:
        result_data["artifacts"] = artifacts

    return result_data
