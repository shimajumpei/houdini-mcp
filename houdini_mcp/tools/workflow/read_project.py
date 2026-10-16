"""read_project tool — Read project configuration files."""

from __future__ import annotations

import json
from typing import Any

import yaml

from ._common import (
    _ensure_project,
    _load_gate_state,
    _readiness_to_dict,
    _save_gate_state,
    handle_workflow_errors,
)


@handle_workflow_errors("read_project")
def read_project(
    target: str = "all",
    project_root: str | None = None,
) -> dict[str, Any]:
    """Read project configuration files as structured data.

    Args:
        target: What to read — "spec", "stages", "contracts", "build_state", or "all".
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Structured data from the requested file(s), plus readiness info.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir

    # Update gate state
    gate = _load_gate_state(agent_dir)
    gate.workflow_initialized = True
    gate.ready_for_implementation = result.readiness.ready
    _save_gate_state(agent_dir, gate)

    valid_targets = {"spec", "stages", "contracts", "build_state", "all"}
    if target not in valid_targets:
        return {
            "status": "error",
            "message": f"Invalid target: '{target}'. Valid targets: {', '.join(sorted(valid_targets))}",
        }

    data: dict[str, Any] = {}

    if target in ("spec", "all"):
        spec_path = agent_dir / "spec.md"
        data["spec"] = spec_path.read_text(encoding="utf-8") if spec_path.exists() else None

    if target in ("stages", "all"):
        stages_path = agent_dir / "stages.yaml"
        if stages_path.exists():
            data["stages"] = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
        else:
            data["stages"] = None

    if target in ("contracts", "all"):
        contracts_path = agent_dir / "contracts.yaml"
        if contracts_path.exists():
            data["contracts"] = yaml.safe_load(contracts_path.read_text(encoding="utf-8"))
        else:
            data["contracts"] = None

    if target in ("build_state", "all"):
        state_path = agent_dir / "build_state.json"
        if state_path.exists():
            data["build_state"] = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            data["build_state"] = None

    return {
        "status": "success",
        "target": target,
        **data,
        **_readiness_to_dict(result.readiness),
    }
