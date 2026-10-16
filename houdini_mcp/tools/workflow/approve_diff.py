"""approve_diff tool — Approve a pending diff proposal."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._common import (
    _ensure_project,
    _load_gate_state,
    _record_event,
    _save_gate_state,
    _write_log,
    handle_workflow_errors,
)


@handle_workflow_errors("approve_diff")
def approve_diff(
    diff_id: str,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Approve a pending diff proposal, unlocking change tools.

    Args:
        diff_id: The diff ID returned by propose_diff.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with approval status.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    gate = _load_gate_state(agent_dir)

    # Validate diff_id matches pending
    if gate.pending_diff_id != diff_id:
        return {
            "status": "error",
            "message": (
                f"Diff ID mismatch. Pending: {gate.pending_diff_id}, "
                f"Provided: {diff_id}"
            ),
        }

    # Best-effort scene checkpoint BEFORE change tools are unlocked,
    # so the approved diff can be rolled back
    checkpoint = _save_checkpoint(agent_dir, diff_id)

    # Mark as approved in gate state
    gate.diff_approved = True
    _save_gate_state(agent_dir, gate)

    # Update the diff file too
    diff_file = agent_dir / "diffs" / f"{diff_id}.json"
    if diff_file.exists():
        diff_data = json.loads(diff_file.read_text(encoding="utf-8"))
        diff_data["approved"] = True
        diff_data["approved_at"] = datetime.now(timezone.utc).isoformat()
        diff_file.write_text(
            json.dumps(diff_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    _write_log(agent_dir, "approve_diff", {
        "event": "approve_diff",
        "diff_id": diff_id,
        "checkpoint": checkpoint,
    })

    _record_event(
        agent_dir, "diff_approved", f"Diff '{diff_id}' approved",
        diff_id=diff_id,
        stage_id=gate.active_stage,
    )

    message = "Diff approved. Change tools are now unlocked for this diff."
    if checkpoint.get("saved"):
        message += f" Scene checkpoint saved: {checkpoint['path']}"
    else:
        message += f" (No scene checkpoint: {checkpoint.get('reason', 'unknown')})"

    return {
        "status": "success",
        "diff_id": diff_id,
        "approved": True,
        "checkpoint": checkpoint,
        "message": message,
    }


def _save_checkpoint(agent_dir: Path, diff_id: str) -> dict[str, Any]:
    """Save a .hip copy of the current scene to .agent/checkpoints/.

    Taken at approval time (before change tools run) so an approved diff
    can be rolled back by loading the checkpoint file.

    Fail-soft: approval must succeed even when the checkpoint cannot be
    saved (e.g. Houdini not connected), so this never raises.
    """
    try:
        from ...connection import ensure_connected, is_connected

        if not is_connected():
            return {
                "saved": False,
                "reason": "not connected to Houdini (checkpoint skipped)",
            }

        hou = ensure_connected()
        if not hasattr(hou.hipFile, "saveCopy"):
            return {"saved": False, "reason": "hou.hipFile.saveCopy not available"}

        checkpoints_dir = agent_dir / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = checkpoints_dir / f"{diff_id}_{ts}.hip"

        hou.hipFile.saveCopy(str(path))
        return {"saved": True, "path": str(path)}
    except Exception as e:
        return {"saved": False, "reason": f"{type(e).__name__}: {e}"}
