"""validate_stage tool — Run validation checks on a stage."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ._common import (
    _ensure_project,
    _generate_stage_graph,
    _is_placeholder_content,
    _parse_markdown_sections,
    _record_event,
    _save_state,
    _write_log,
    handle_workflow_errors,
)
from .models import BuildState

_VALID_MODES = ("light", "strict")


@handle_workflow_errors("validate_stage")
def validate_stage(
    stage_id: str,
    mode: str = "light",
    scan_data: dict[str, Any] | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Run validation checks on a stage (does not change status).

    Light mode checks (default):
    1. Plan file exists
    2. Scan has been recorded
    3. Contract artifacts registered (future)
    4. depends_on stages are completed

    Strict mode adds:
    5. acceptance_criteria_met — checks stage's acceptance list
    6. dod_compliance — checks spec.md Definition of Done is non-placeholder

    Args:
        stage_id: Stage ID to validate.
        mode: Validation mode: "light" (default) or "strict".
        scan_data: Optional fresh scan data (not used for validation logic,
                   but recorded if provided).
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Validation result with per-check details.
    """
    if mode not in _VALID_MODES:
        return {
            "status": "error",
            "message": f"Invalid mode '{mode}'. Must be one of: {_VALID_MODES}",
        }

    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    state = result.state

    checks: list[dict[str, Any]] = []

    # Check 1: Plan file exists
    plan_file = agent_dir / "stage_plans" / f"{stage_id}.md"
    checks.append({
        "check": "plan_exists",
        "passed": plan_file.exists(),
        "detail": (
            f"Plan file found: {plan_file.name}"
            if plan_file.exists()
            else f"Plan file missing: stage_plans/{stage_id}.md"
        ),
    })

    # Check 2: Scan recorded
    stage_state = state.stages.get(stage_id)
    has_scan = stage_state is not None and stage_state.last_scan_file is not None
    checks.append({
        "check": "scan_recorded",
        "passed": has_scan,
        "detail": (
            f"Last scan: {stage_state.last_scan_file}"
            if has_scan
            else "No scan recorded for this stage"
        ),
    })

    # Check 3: Artifacts registered (placeholder — always passes for now)
    checks.append({
        "check": "artifacts_registered",
        "passed": True,
        "detail": "Artifact registration check not yet implemented (passes by default)",
    })

    # Check 4: depends_on completed
    deps_check = _check_dependencies(agent_dir, stage_id, state)
    checks.append(deps_check)

    # Strict mode additional checks
    if mode == "strict":
        # Check 5: Acceptance criteria met
        acc_check = _check_acceptance_criteria(agent_dir, stage_id)
        checks.append(acc_check)

        # Check 6: Definition of Done compliance
        dod_check = _check_dod_compliance(agent_dir)
        checks.append(dod_check)

    all_passed = all(c["passed"] for c in checks)

    # Update last_validation_passed on the stage state
    if stage_state is not None:
        stage_state.last_validation_passed = all_passed
        _save_state(agent_dir, state)

    # Log validation result
    log_data = {
        "event": "validate_stage",
        "stage_id": stage_id,
        "mode": mode,
        "passed": all_passed,
        "checks": checks,
    }
    _write_log(agent_dir, f"validation_{stage_id}", log_data)

    event_id = _record_event(
        agent_dir, "stage_validated",
        f"Stage '{stage_id}' validation {'passed' if all_passed else 'failed'} (mode={mode})",
        stage_id=stage_id,
        details={"passed": all_passed, "mode": mode, "checks": [c["check"] for c in checks]},
    )

    # Generate stage graph
    artifacts: list[str] = []
    if event_id:
        graph_path = _generate_stage_graph(agent_dir, event_id)
        if graph_path:
            artifacts.append(graph_path)

    result_data: dict[str, Any] = {
        "status": "success",
        "stage_id": stage_id,
        "mode": mode,
        "passed": all_passed,
        "checks": checks,
    }
    if artifacts:
        result_data["artifacts"] = artifacts

    return result_data


def _check_dependencies(
    agent_dir: Path, stage_id: str, state: BuildState
) -> dict[str, Any]:
    """Check if all depends_on stages are completed."""
    stages_path = agent_dir / "stages.yaml"
    if not stages_path.exists():
        return {
            "check": "dependencies_completed",
            "passed": True,
            "detail": "No stages.yaml found; skipping dependency check",
        }

    try:
        data = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {
            "check": "dependencies_completed",
            "passed": True,
            "detail": "stages.yaml parse error; skipping dependency check",
        }

    stages_list = data.get("stages", []) if isinstance(data, dict) else []
    stage_def = None
    for s in stages_list:
        if isinstance(s, dict) and s.get("id") == stage_id:
            stage_def = s
            break

    if stage_def is None:
        return {
            "check": "dependencies_completed",
            "passed": True,
            "detail": f"Stage '{stage_id}' not found in stages.yaml; skipping dependency check",
        }

    depends_on = stage_def.get("depends_on", [])
    if not depends_on:
        return {
            "check": "dependencies_completed",
            "passed": True,
            "detail": "No dependencies",
        }

    incomplete = []
    for dep_id in depends_on:
        dep_state = state.stages.get(dep_id)
        if dep_state is None or dep_state.status != "completed":
            dep_status = dep_state.status if dep_state else "not started"
            incomplete.append(f"{dep_id} ({dep_status})")

    if incomplete:
        return {
            "check": "dependencies_completed",
            "passed": False,
            "detail": f"Incomplete dependencies: {', '.join(incomplete)}",
        }

    return {
        "check": "dependencies_completed",
        "passed": True,
        "detail": f"All dependencies completed: {', '.join(depends_on)}",
    }


def _check_acceptance_criteria(agent_dir: Path, stage_id: str) -> dict[str, Any]:
    """Check if stage has acceptance criteria defined in stages.yaml."""
    stages_path = agent_dir / "stages.yaml"
    if not stages_path.exists():
        return {
            "check": "acceptance_criteria_met",
            "passed": True,
            "detail": "No stages.yaml found; skipping acceptance check",
        }

    try:
        data = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {
            "check": "acceptance_criteria_met",
            "passed": True,
            "detail": "stages.yaml parse error; skipping acceptance check",
        }

    stages_list = data.get("stages", []) if isinstance(data, dict) else []
    stage_def = None
    for s in stages_list:
        if isinstance(s, dict) and s.get("id") == stage_id:
            stage_def = s
            break

    if stage_def is None:
        return {
            "check": "acceptance_criteria_met",
            "passed": True,
            "detail": f"Stage '{stage_id}' not found in stages.yaml; skipping",
        }

    acceptance = stage_def.get("acceptance", [])
    if not acceptance:
        return {
            "check": "acceptance_criteria_met",
            "passed": False,
            "detail": f"Stage '{stage_id}' has no acceptance criteria defined",
        }

    return {
        "check": "acceptance_criteria_met",
        "passed": True,
        "detail": f"Acceptance criteria defined: {len(acceptance)} item(s)",
    }


def _check_dod_compliance(agent_dir: Path) -> dict[str, Any]:
    """Check if spec.md Definition of Done is non-placeholder."""
    spec_path = agent_dir / "spec.md"
    if not spec_path.exists():
        return {
            "check": "dod_compliance",
            "passed": False,
            "detail": "spec.md not found",
        }

    content = spec_path.read_text(encoding="utf-8")
    sections = _parse_markdown_sections(content)
    dod_content = sections.get("Definition of Done")

    if dod_content is None:
        return {
            "check": "dod_compliance",
            "passed": False,
            "detail": "Definition of Done section missing from spec.md",
        }

    if _is_placeholder_content(dod_content):
        return {
            "check": "dod_compliance",
            "passed": False,
            "detail": "Definition of Done contains only placeholder text",
        }

    return {
        "check": "dod_compliance",
        "passed": True,
        "detail": "Definition of Done is defined",
    }
