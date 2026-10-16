"""Integration tests for workflow management tools.

Uses tmp_path fixtures — no Houdini mock needed.
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest
import yaml

from houdini_mcp.tools.workflow import (
    ensure_project,
    read_project,
    plan_stage,
    record_scan,
    validate_stage,
    update_stage,
    update_spec_and_stages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_filled_spec(agent_dir: Path) -> None:
    """Write a spec.md with all required sections filled in."""
    spec = """\
# Project Specification

## Goal

Build a procedural terrain system.

## Requirements

- Generate heightfield terrain
- Apply erosion simulation

## Inputs

- Heightfield resolution parameter
- Erosion iterations parameter

## Outputs

- Final terrain geometry
- Exported heightfield map

## Acceptance Criteria

- Terrain generates without errors
- Resolution is configurable

## Definition of Done

- All stages completed and validated
- Output geometry exports successfully
"""
    (agent_dir / "spec.md").write_text(spec, encoding="utf-8")


def _write_filled_stages(agent_dir: Path) -> None:
    """Write a stages.yaml with valid stage definitions."""
    stages = {
        "stages": [
            {
                "id": "S01",
                "name": "Base Terrain",
                "purpose": "Create heightfield base",
                "depends_on": [],
                "produces": ["heightfield"],
                "consumes": [],
            },
            {
                "id": "S02",
                "name": "Erosion",
                "purpose": "Apply erosion to terrain",
                "depends_on": ["S01"],
                "produces": ["eroded_terrain"],
                "consumes": ["heightfield"],
            },
        ]
    }
    (agent_dir / "stages.yaml").write_text(
        yaml.dump(stages, default_flow_style=False), encoding="utf-8"
    )


def _make_ready_project(root: Path) -> Path:
    """Create a fully ready .agent/ directory."""
    result = ensure_project(str(root))
    agent_dir = root / ".agent"
    _write_filled_spec(agent_dir)
    _write_filled_stages(agent_dir)
    return agent_dir


# ---------------------------------------------------------------------------
# ensure_project tests
# ---------------------------------------------------------------------------


class TestEnsureProject:
    """Tests for ensure_project tool."""

    def test_bootstrap_creates_agent_dir(self, tmp_path: Path):
        result = ensure_project(str(tmp_path))
        assert result["status"] == "success"
        assert result["bootstrapped"] is True
        assert (tmp_path / ".agent").is_dir()
        assert (tmp_path / ".agent" / "spec.md").exists()
        assert (tmp_path / ".agent" / "stages.yaml").exists()
        assert (tmp_path / ".agent" / "contracts.yaml").exists()
        assert (tmp_path / ".agent" / "build_state.json").exists()
        assert (tmp_path / ".agent" / "stage_plans").is_dir()
        assert (tmp_path / ".agent" / "scene_scans").is_dir()
        assert (tmp_path / ".agent" / "logs").is_dir()

    def test_idempotent_second_call(self, tmp_path: Path):
        ensure_project(str(tmp_path))
        result = ensure_project(str(tmp_path))
        assert result["status"] == "success"
        assert result["bootstrapped"] is False
        assert result["repaired_files"] == []

    def test_repairs_missing_file(self, tmp_path: Path):
        ensure_project(str(tmp_path))
        # Delete one file
        (tmp_path / ".agent" / "spec.md").unlink()
        result = ensure_project(str(tmp_path))
        assert result["status"] == "success"
        assert result["bootstrapped"] is False
        assert "created file: spec.md" in result["repaired_files"]
        assert (tmp_path / ".agent" / "spec.md").exists()

    def test_repairs_missing_directory(self, tmp_path: Path):
        ensure_project(str(tmp_path))
        import shutil
        shutil.rmtree(tmp_path / ".agent" / "stage_plans")
        result = ensure_project(str(tmp_path))
        assert "created directory: stage_plans" in result["repaired_files"]

    def test_template_not_ready(self, tmp_path: Path):
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        assert len(result["missing_requirements"]) > 0
        assert result["next_action"] is not None

    def test_ready_after_filling_spec_and_stages(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True
        assert result["missing_requirements"] == []
        assert result["next_action"] is None


# ---------------------------------------------------------------------------
# read_project tests
# ---------------------------------------------------------------------------


class TestReadProject:
    """Tests for read_project tool."""

    def test_read_all(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = read_project("all", str(tmp_path))
        assert result["status"] == "success"
        assert "spec" in result
        assert "stages" in result
        assert "contracts" in result
        assert "build_state" in result

    def test_read_spec(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = read_project("spec", str(tmp_path))
        assert result["status"] == "success"
        assert "Goal" in result["spec"]

    def test_read_stages(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = read_project("stages", str(tmp_path))
        assert result["status"] == "success"
        assert len(result["stages"]["stages"]) == 2

    def test_read_build_state(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = read_project("build_state", str(tmp_path))
        assert result["status"] == "success"
        assert "version" in result["build_state"]

    def test_invalid_target(self, tmp_path: Path):
        ensure_project(str(tmp_path))
        result = read_project("invalid", str(tmp_path))
        assert result["status"] == "error"

    def test_readiness_always_included(self, tmp_path: Path):
        ensure_project(str(tmp_path))
        result = read_project("spec", str(tmp_path))
        assert "ready_for_implementation" in result
        assert "missing_requirements" in result


# ---------------------------------------------------------------------------
# plan_stage tests
# ---------------------------------------------------------------------------


class TestPlanStage:
    """Tests for plan_stage tool."""

    def test_plan_creates_file_and_transitions(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = plan_stage("S01", "## Plan\n- Create heightfield", str(tmp_path))
        assert result["status"] == "success"
        assert result["stage_status"] == "in_progress"
        assert result["active_stage"] == "S01"
        plan_file = tmp_path / ".agent" / "stage_plans" / "S01.md"
        assert plan_file.exists()
        assert "heightfield" in plan_file.read_text(encoding="utf-8")

    def test_plan_rejected_when_not_ready(self, tmp_path: Path):
        ensure_project(str(tmp_path))  # template only, not filled
        result = plan_stage("S01", "plan content", str(tmp_path))
        assert result["status"] == "error"
        assert "not ready" in result["message"]
        assert "missing_requirements" in result

    def test_plan_rejected_when_already_in_progress(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "first plan", str(tmp_path))
        result = plan_stage("S01", "second plan", str(tmp_path))
        assert result["status"] == "error"
        assert "in_progress" in result["message"]

    def test_plan_allowed_for_failed_stage(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "first plan", str(tmp_path))
        update_stage("S01", "failed", "broken", str(tmp_path))
        result = plan_stage("S01", "retry plan", str(tmp_path))
        assert result["status"] == "success"
        assert result["stage_status"] == "in_progress"


# ---------------------------------------------------------------------------
# record_scan tests
# ---------------------------------------------------------------------------


class TestRecordScan:
    """Tests for record_scan tool."""

    def test_record_scan_creates_file(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        scan = {"nodes": ["/obj/terrain"], "hip_file": "test.hip"}
        result = record_scan(scan, "S01", str(tmp_path))
        assert result["status"] == "success"
        assert result["stage_id"] == "S01"
        scan_file = tmp_path / ".agent" / result["scan_file"]
        assert scan_file.exists()
        saved = json.loads(scan_file.read_text(encoding="utf-8"))
        assert saved["hip_file"] == "test.hip"

    def test_record_scan_uses_active_stage(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        result = record_scan({"data": "test"}, project_root=str(tmp_path))
        assert result["stage_id"] == "S01"

    def test_record_scan_unassigned_when_no_stage(self, tmp_path: Path):
        ensure_project(str(tmp_path))
        result = record_scan({"data": "test"}, project_root=str(tmp_path))
        assert result["stage_id"] == "unassigned"


# ---------------------------------------------------------------------------
# validate_stage tests
# ---------------------------------------------------------------------------


class TestValidateStage:
    """Tests for validate_stage tool."""

    def test_validate_passes_with_plan_and_scan(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        record_scan({"data": "scan"}, "S01", str(tmp_path))
        result = validate_stage("S01", project_root=str(tmp_path))
        assert result["status"] == "success"
        assert result["passed"] is True

    def test_validate_fails_without_plan(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = validate_stage("S01", project_root=str(tmp_path))
        assert result["status"] == "success"
        assert result["passed"] is False
        check_names = [c["check"] for c in result["checks"]]
        assert "plan_exists" in check_names
        plan_check = next(c for c in result["checks"] if c["check"] == "plan_exists")
        assert plan_check["passed"] is False

    def test_validate_fails_without_scan(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        result = validate_stage("S01", project_root=str(tmp_path))
        scan_check = next(c for c in result["checks"] if c["check"] == "scan_recorded")
        assert scan_check["passed"] is False

    def test_validate_checks_dependencies(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        # S02 depends on S01
        plan_stage("S01", "plan S01", str(tmp_path))
        plan_stage("S02", "plan S02", str(tmp_path))
        record_scan({"data": "scan"}, "S02", str(tmp_path))
        result = validate_stage("S02", project_root=str(tmp_path))
        dep_check = next(c for c in result["checks"] if c["check"] == "dependencies_completed")
        assert dep_check["passed"] is False
        assert "S01" in dep_check["detail"]


# ---------------------------------------------------------------------------
# update_stage tests
# ---------------------------------------------------------------------------


class TestUpdateStage:
    """Tests for update_stage tool."""

    def test_invalid_transition_rejected(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        result = update_stage("S01", "completed", project_root=str(tmp_path))
        assert result["status"] == "error"
        assert "Invalid transition" in result["message"]

    def test_validated_requires_passing_validation(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        # Try to set validated without running validate_stage
        result = update_stage("S01", "validated", project_root=str(tmp_path))
        assert result["status"] == "error"
        assert "validation did not pass" in result["message"]

    def test_full_lifecycle(self, tmp_path: Path):
        """Test complete stage lifecycle: pending -> in_progress -> validated -> completed."""
        _make_ready_project(tmp_path)

        # pending -> in_progress (via plan_stage)
        r = plan_stage("S01", "plan", str(tmp_path))
        assert r["status"] == "success"

        # Record scan
        record_scan({"data": "scan"}, "S01", str(tmp_path))

        # Validate
        v = validate_stage("S01", project_root=str(tmp_path))
        assert v["passed"] is True

        # in_progress -> validated
        r = update_stage("S01", "validated", project_root=str(tmp_path))
        assert r["status"] == "success"
        assert r["new_status"] == "validated"

        # validated -> completed
        r = update_stage("S01", "completed", project_root=str(tmp_path))
        assert r["status"] == "success"
        assert r["new_status"] == "completed"

    def test_failed_to_in_progress(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        update_stage("S01", "failed", "broken", str(tmp_path))
        # failed -> in_progress
        result = update_stage("S01", "in_progress", project_root=str(tmp_path))
        assert result["status"] == "success"
        assert result["new_status"] == "in_progress"

    def test_completed_clears_active_stage(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        record_scan({"data": "scan"}, "S01", str(tmp_path))
        validate_stage("S01", project_root=str(tmp_path))
        update_stage("S01", "validated", project_root=str(tmp_path))
        result = update_stage("S01", "completed", project_root=str(tmp_path))
        assert result["active_stage"] is None

    def test_completed_has_no_outgoing_transitions(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        plan_stage("S01", "plan", str(tmp_path))
        record_scan({"data": "scan"}, "S01", str(tmp_path))
        validate_stage("S01", project_root=str(tmp_path))
        update_stage("S01", "validated", project_root=str(tmp_path))
        update_stage("S01", "completed", project_root=str(tmp_path))
        for target in ["pending", "in_progress", "validated", "failed"]:
            r = update_stage("S01", target, project_root=str(tmp_path))
            assert r["status"] == "error"


# ---------------------------------------------------------------------------
# Readiness check tests
# ---------------------------------------------------------------------------


class TestReadiness:
    """Tests for readiness check logic."""

    def test_template_spec_not_ready(self, tmp_path: Path):
        """Template spec.md should not pass readiness."""
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        spec_issues = [m for m in result["missing_requirements"] if m.startswith("spec.md:")]
        assert len(spec_issues) > 0

    def test_partial_spec_detected(self, tmp_path: Path):
        """Spec with only Goal filled is not sufficient."""
        ensure_project(str(tmp_path))
        agent_dir = tmp_path / ".agent"
        spec = """\
# Project Specification

## Goal

Build a terrain system.

## Requirements

(Describe the requirements)

## Inputs

(Describe the inputs)

## Outputs

(Describe the expected outputs)

## Acceptance Criteria

(Describe the acceptance criteria)
"""
        (agent_dir / "spec.md").write_text(spec, encoding="utf-8")
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        # Goal should pass but others should fail
        goal_issues = [m for m in result["missing_requirements"] if "'Goal'" in m]
        assert len(goal_issues) == 0

    def test_empty_stages_not_ready(self, tmp_path: Path):
        """Empty stages list should not pass readiness."""
        ensure_project(str(tmp_path))
        agent_dir = tmp_path / ".agent"
        _write_filled_spec(agent_dir)
        # stages.yaml still has empty list from template
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        stages_issues = [m for m in result["missing_requirements"] if m.startswith("stages.yaml:")]
        assert len(stages_issues) > 0

    def test_stage_missing_purpose(self, tmp_path: Path):
        """Stage without purpose field fails readiness."""
        ensure_project(str(tmp_path))
        agent_dir = tmp_path / ".agent"
        _write_filled_spec(agent_dir)
        stages = {
            "stages": [
                {
                    "id": "S01",
                    "name": "Test",
                    "purpose": "",  # empty
                    "depends_on": [],
                    "produces": [],
                    "consumes": [],
                }
            ]
        }
        (agent_dir / "stages.yaml").write_text(
            yaml.dump(stages, default_flow_style=False), encoding="utf-8"
        )
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        assert any("purpose" in m for m in result["missing_requirements"])

    def test_fully_defined_project_is_ready(self, tmp_path: Path):
        """Fully defined spec and stages should pass readiness."""
        _make_ready_project(tmp_path)
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True
        assert result["missing_requirements"] == []

    def test_must_have_alternative_to_requirements(self, tmp_path: Path):
        """'Must Have' can substitute for 'Requirements'."""
        ensure_project(str(tmp_path))
        agent_dir = tmp_path / ".agent"
        spec = """\
# Project Specification

## Goal

Build a terrain system.

## Must Have

- Heightfield generation
- Erosion simulation

## Inputs

- Resolution parameter

## Outputs

- Terrain geometry

## Acceptance Criteria

- Generates without errors

## Definition of Done

- All stages pass validation
"""
        (agent_dir / "spec.md").write_text(spec, encoding="utf-8")
        _write_filled_stages(agent_dir)
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True


# ---------------------------------------------------------------------------
# CLAUDE.md auto-injection tests
# ---------------------------------------------------------------------------


class TestEnsureProjectClaudeMd:
    """Tests for CLAUDE.md auto-injection via ensure_project."""

    def test_copies_claude_md_on_bootstrap(self, tmp_path: Path):
        """ensure_project copies CLAUDE.md from canonical source when missing."""
        # Prepare canonical source in a temp location
        canonical = tmp_path / "canonical" / "CLAUDE.md"
        canonical.parent.mkdir()
        canonical.write_text("# Canonical CLAUDE.md v3\nRules.", encoding="utf-8")

        with mock.patch.dict(os.environ, {"HOUDINI_CLAUDE_MD_CANONICAL": str(canonical)}):
            # Force re-evaluation of the module-level constant
            import houdini_mcp.tools.workflow._common as _common_mod
            original = _common_mod._CANONICAL_CLAUDE_MD
            _common_mod._CANONICAL_CLAUDE_MD = Path(str(canonical))
            try:
                project_dir = tmp_path / "project"
                project_dir.mkdir()
                result = ensure_project(str(project_dir))
                assert result["status"] == "success"
                assert (project_dir / "CLAUDE.md").exists()
                assert (project_dir / "CLAUDE.md").read_text(encoding="utf-8") == "# Canonical CLAUDE.md v3\nRules."
                assert "created CLAUDE.md from canonical source" in result["repaired_files"]
            finally:
                _common_mod._CANONICAL_CLAUDE_MD = original

    def test_does_not_overwrite_existing(self, tmp_path: Path):
        """ensure_project does not overwrite existing CLAUDE.md."""
        canonical = tmp_path / "canonical" / "CLAUDE.md"
        canonical.parent.mkdir()
        canonical.write_text("# Canonical version", encoding="utf-8")

        with mock.patch.dict(os.environ, {"HOUDINI_CLAUDE_MD_CANONICAL": str(canonical)}):
            import houdini_mcp.tools.workflow._common as _common_mod
            original = _common_mod._CANONICAL_CLAUDE_MD
            _common_mod._CANONICAL_CLAUDE_MD = Path(str(canonical))
            try:
                project_dir = tmp_path / "project"
                project_dir.mkdir()
                (project_dir / "CLAUDE.md").write_text("# My custom rules", encoding="utf-8")
                result = ensure_project(str(project_dir))
                assert result["status"] == "success"
                assert (project_dir / "CLAUDE.md").read_text(encoding="utf-8") == "# My custom rules"
                assert "created CLAUDE.md from canonical source" not in result.get("repaired_files", [])
            finally:
                _common_mod._CANONICAL_CLAUDE_MD = original


# ---------------------------------------------------------------------------
# Event recording tests (Phase 1)
# ---------------------------------------------------------------------------

from houdini_mcp.tools.workflow import (
    propose_diff,
    approve_diff,
)


def _read_events(agent_dir: Path) -> list[dict]:
    """Read all events from events.jsonl."""
    events_file = agent_dir / "history" / "events.jsonl"
    if not events_file.exists():
        return []
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class TestEventRecording:
    """Tests for _record_event and events.jsonl integration."""

    def test_new_dirs_bootstrapped(self, tmp_path: Path):
        """ensure_project creates history/, snapshots/, graphs/, reports/ dirs."""
        result = ensure_project(str(tmp_path))
        assert result["status"] == "success"
        for subdir in ("history", "snapshots", "graphs", "reports"):
            assert (tmp_path / ".agent" / subdir).is_dir(), f"{subdir} not created"

    def test_events_jsonl_created_on_bootstrap(self, tmp_path: Path):
        """ensure_project writes a project_bootstrap event to events.jsonl."""
        ensure_project(str(tmp_path))
        events = _read_events(tmp_path / ".agent")
        assert len(events) >= 1
        bootstrap_events = [e for e in events if e["event_type"] == "project_bootstrap"]
        assert len(bootstrap_events) == 1

    def test_event_fields_complete(self, tmp_path: Path):
        """Each event has mandatory fields: schema_version, event_id, ts, event_type, project_root, summary."""
        ensure_project(str(tmp_path))
        events = _read_events(tmp_path / ".agent")
        required_fields = {"schema_version", "event_id", "ts", "event_type", "project_root", "summary"}
        for event in events:
            for field in required_fields:
                assert field in event, f"Missing field '{field}' in event: {event}"

    def test_event_id_format(self, tmp_path: Path):
        """Event IDs follow evt_YYYYMMDD_HHMMSS_hex format."""
        ensure_project(str(tmp_path))
        events = _read_events(tmp_path / ".agent")
        for event in events:
            assert event["event_id"].startswith("evt_"), f"Bad event_id: {event['event_id']}"

    def test_full_lifecycle_records_events(self, tmp_path: Path):
        """A full lifecycle produces events for each major step."""
        agent_dir = _make_ready_project(tmp_path)

        # plan_stage -> stage_planned
        plan_stage("S01", "Test plan", str(tmp_path))

        # propose_diff -> diff_proposed
        propose_result = propose_diff("S01", {"rationale": "test"}, str(tmp_path))
        diff_id = propose_result["diff_id"]

        # approve_diff -> diff_approved
        approve_diff(diff_id, str(tmp_path))

        # record_scan -> scene_scanned
        record_scan({"nodes": []}, "S01", str(tmp_path))

        # validate_stage -> stage_validated
        validate_stage("S01", project_root=str(tmp_path))

        # update_stage -> stage_status_changed (validated)
        update_stage("S01", "validated", project_root=str(tmp_path))

        # update_stage -> stage_status_changed (completed)
        update_stage("S01", "completed", project_root=str(tmp_path))

        events = _read_events(agent_dir)
        event_types = [e["event_type"] for e in events]

        assert "project_bootstrap" in event_types
        assert "stage_planned" in event_types
        assert "diff_proposed" in event_types
        assert "diff_approved" in event_types
        assert "scene_scanned" in event_types
        assert "stage_validated" in event_types
        assert "stage_status_changed" in event_types

    def test_record_event_fail_soft(self, tmp_path: Path):
        """_record_event does not crash when write fails."""
        from houdini_mcp.tools.workflow._common import _record_event

        # Mock Path.open to raise OSError, which _record_event uses
        with mock.patch.object(Path, "open", side_effect=OSError("disk full")):
            agent_dir = tmp_path / ".agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            result = _record_event(agent_dir, "test_event", "test summary")
            assert result == ""  # Returns empty string on failure

    def test_no_bootstrap_event_on_second_call(self, tmp_path: Path):
        """Second ensure_project call does not produce another bootstrap event."""
        ensure_project(str(tmp_path))
        ensure_project(str(tmp_path))
        events = _read_events(tmp_path / ".agent")
        bootstrap_events = [e for e in events if e["event_type"] == "project_bootstrap"]
        assert len(bootstrap_events) == 1


# ---------------------------------------------------------------------------
# Phase 2: Agile Extensions Tests
# ---------------------------------------------------------------------------


class TestSpecTemplate:
    """Tests for extended spec.md template and readiness checks."""

    def test_spec_template_has_dod(self, tmp_path: Path):
        """spec.md template includes Definition of Done section."""
        ensure_project(str(tmp_path))
        spec_path = tmp_path / ".agent" / "spec.md"
        content = spec_path.read_text(encoding="utf-8")
        assert "## Definition of Done" in content

    def test_spec_template_has_assumptions(self, tmp_path: Path):
        """spec.md template includes Assumptions section."""
        ensure_project(str(tmp_path))
        spec_path = tmp_path / ".agent" / "spec.md"
        content = spec_path.read_text(encoding="utf-8")
        assert "## Assumptions" in content

    def test_spec_template_has_open_questions(self, tmp_path: Path):
        """spec.md template includes Open Questions section."""
        ensure_project(str(tmp_path))
        spec_path = tmp_path / ".agent" / "spec.md"
        content = spec_path.read_text(encoding="utf-8")
        assert "## Open Questions" in content

    def test_spec_template_has_change_log(self, tmp_path: Path):
        """spec.md template includes Change Log table."""
        ensure_project(str(tmp_path))
        spec_path = tmp_path / ".agent" / "spec.md"
        content = spec_path.read_text(encoding="utf-8")
        assert "## Change Log" in content
        assert "| Date | Change | Reason | Impact |" in content

    def test_readiness_requires_dod(self, tmp_path: Path):
        """Empty Definition of Done blocks readiness."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        # Write spec without DoD
        spec = """\
# Project Specification

## Goal

Build terrain.

## Requirements

- Generate heightfield

## Inputs

- Resolution

## Outputs

- Terrain geometry

## Acceptance Criteria

- Works correctly
"""
        (agent_dir / "spec.md").write_text(spec, encoding="utf-8")
        _write_filled_stages(agent_dir)

        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        missing = result["missing_requirements"]
        assert any("Definition of Done" in m for m in missing)

    def test_readiness_dod_filled_passes(self, tmp_path: Path):
        """Filled Definition of Done passes readiness."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        _write_filled_spec(agent_dir)  # includes DoD now
        _write_filled_stages(agent_dir)

        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True

    def test_readiness_ignores_optional_sections(self, tmp_path: Path):
        """Assumptions/Open Questions/Change Log are optional for readiness."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        # spec with DoD but no Assumptions/Open Questions/Change Log
        _write_filled_spec(agent_dir)
        _write_filled_stages(agent_dir)

        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True


class TestStagesOptionalFields:
    """Tests for optional stages.yaml fields (mode, notes, replaces, etc.)."""

    def test_stages_optional_fields_preserved(self, tmp_path: Path):
        """Optional fields in stages.yaml round-trip through read_project."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        _write_filled_spec(agent_dir)

        stages = {
            "stages": [
                {
                    "id": "S01",
                    "name": "Base Terrain",
                    "purpose": "Create heightfield base",
                    "depends_on": [],
                    "produces": ["heightfield"],
                    "consumes": [],
                    "mode": "spike",
                    "notes": "Experimental approach",
                    "replaces": [],
                    "parallel_with": [],
                    "acceptance": ["Heightfield generated"],
                },
            ]
        }
        (agent_dir / "stages.yaml").write_text(
            yaml.dump(stages, default_flow_style=False), encoding="utf-8"
        )

        result = read_project("stages", str(tmp_path))
        assert result["status"] == "success"
        stage = result["stages"]["stages"][0]
        assert stage["mode"] == "spike"
        assert stage["notes"] == "Experimental approach"
        assert stage["acceptance"] == ["Heightfield generated"]

    def test_stages_readiness_ignores_optional(self, tmp_path: Path):
        """Optional fields don't affect readiness check."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        _write_filled_spec(agent_dir)
        # stages without optional fields
        _write_filled_stages(agent_dir)

        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True

    def test_backward_compat_old_stages(self, tmp_path: Path):
        """stages.yaml without optional fields still works."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        _write_filled_spec(agent_dir)

        stages = {
            "stages": [
                {
                    "id": "S01",
                    "name": "Base Terrain",
                    "purpose": "Create base",
                    "depends_on": [],
                    "produces": ["heightfield"],
                    "consumes": [],
                },
            ]
        }
        (agent_dir / "stages.yaml").write_text(
            yaml.dump(stages, default_flow_style=False), encoding="utf-8"
        )

        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is True


class TestUpdateSpecAndStages:
    """Tests for update_spec_and_stages tool."""

    def _setup_project(self, tmp_path: Path) -> Path:
        """Set up a ready project and return agent_dir."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        _write_filled_spec(agent_dir)
        _write_filled_stages(agent_dir)
        ensure_project(str(tmp_path))
        return agent_dir

    def test_update_spec_changelog_entry(self, tmp_path: Path):
        """Changelog entry appended to spec.md Change Log table."""
        self._setup_project(tmp_path)

        # Add Change Log section to spec first
        spec_path = tmp_path / ".agent" / "spec.md"
        content = spec_path.read_text(encoding="utf-8")
        content += "\n## Change Log\n\n| Date | Change | Reason | Impact |\n|------|--------|--------|--------|\n"
        spec_path.write_text(content, encoding="utf-8")

        result = update_spec_and_stages(
            "Switch to OpenCL solver",
            spec_patch={
                "changelog_entry": {
                    "reason": "Performance bottleneck with VEX",
                    "impact": "S02 needs rebuild",
                },
            },
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert "spec.md" in result["files_updated"]

        updated = spec_path.read_text(encoding="utf-8")
        assert "Performance bottleneck with VEX" in updated

    def test_update_spec_section_update(self, tmp_path: Path):
        """Section content replaced in spec.md."""
        self._setup_project(tmp_path)

        result = update_spec_and_stages(
            "Revise requirements",
            spec_patch={
                "section_updates": {
                    "Requirements": "- Use OpenCL for simulation\n- Support GPU acceleration",
                },
            },
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        spec = (tmp_path / ".agent" / "spec.md").read_text(encoding="utf-8")
        assert "Use OpenCL for simulation" in spec

    def test_update_spec_dod_append(self, tmp_path: Path):
        """Line appended to Definition of Done."""
        self._setup_project(tmp_path)

        result = update_spec_and_stages(
            "Add DoD criterion",
            spec_patch={"dod_append": "Performance benchmark passes"},
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        spec = (tmp_path / ".agent" / "spec.md").read_text(encoding="utf-8")
        assert "Performance benchmark passes" in spec

    def test_update_stages_add_stage(self, tmp_path: Path):
        """New stage added to stages.yaml."""
        self._setup_project(tmp_path)

        result = update_spec_and_stages(
            "Add spike stage for OpenCL",
            stages_patch={
                "add_stage": {
                    "id": "SPIKE_01",
                    "name": "OpenCL Spike",
                    "purpose": "Test OpenCL solver feasibility",
                    "depends_on": ["S01"],
                    "produces": ["test_volume"],
                    "consumes": ["heightfield"],
                    "mode": "spike",
                    "notes": "Exploratory",
                },
            },
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert "stages.yaml" in result["files_updated"]

        stages = yaml.safe_load(
            (tmp_path / ".agent" / "stages.yaml").read_text(encoding="utf-8")
        )
        ids = [s["id"] for s in stages["stages"]]
        assert "SPIKE_01" in ids
        spike = [s for s in stages["stages"] if s["id"] == "SPIKE_01"][0]
        assert spike["mode"] == "spike"

    def test_update_stages_update_fields(self, tmp_path: Path):
        """Existing stage fields updated in stages.yaml."""
        self._setup_project(tmp_path)

        result = update_spec_and_stages(
            "Mark S01 as spike",
            stages_patch={
                "update_stage": {
                    "id": "S01",
                    "mode": "spike",
                    "notes": "Testing approach",
                },
            },
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert "stages.yaml" in result["files_updated"]

        stages = yaml.safe_load(
            (tmp_path / ".agent" / "stages.yaml").read_text(encoding="utf-8")
        )
        s01 = [s for s in stages["stages"] if s["id"] == "S01"][0]
        assert s01["mode"] == "spike"
        assert s01["notes"] == "Testing approach"

    def test_changelog_md_appended(self, tmp_path: Path):
        """CHANGELOG.md gets entry on update."""
        self._setup_project(tmp_path)

        update_spec_and_stages(
            "Direction change: VEX to OpenCL",
            spec_patch={
                "section_updates": {"Goal": "Build GPU-accelerated terrain"},
            },
            project_root=str(tmp_path),
        )

        changelog = tmp_path / ".agent" / "history" / "CHANGELOG.md"
        assert changelog.exists()
        content = changelog.read_text(encoding="utf-8")
        assert "Direction change: VEX to OpenCL" in content

    def test_decision_recorded(self, tmp_path: Path):
        """DECISIONS.md gets ADR entry."""
        self._setup_project(tmp_path)

        update_spec_and_stages(
            "Record solver decision",
            spec_patch={
                "decision": {
                    "context": "VEX solver too slow for 1024x1024",
                    "decision": "Switch to OpenCL",
                    "alternatives": "Optimize VEX, use C++ HDK",
                    "consequences": "GPU dependency, need OpenCL driver",
                },
            },
            project_root=str(tmp_path),
        )

        decisions = tmp_path / ".agent" / "history" / "DECISIONS.md"
        assert decisions.exists()
        content = decisions.read_text(encoding="utf-8")
        assert "ADR-001" in content
        assert "Switch to OpenCL" in content
        assert "Optimize VEX" in content

    def test_events_recorded_on_update(self, tmp_path: Path):
        """Events recorded when spec/stages updated."""
        self._setup_project(tmp_path)

        update_spec_and_stages(
            "Update both",
            spec_patch={"dod_append": "Test criterion"},
            stages_patch={
                "add_stage": {
                    "id": "S03",
                    "name": "New Stage",
                    "purpose": "Testing",
                    "depends_on": [],
                    "produces": [],
                    "consumes": [],
                },
            },
            project_root=str(tmp_path),
        )

        events = _read_events(tmp_path / ".agent")
        event_types = [e["event_type"] for e in events]
        assert "spec_updated" in event_types
        assert "stages_updated" in event_types


# ---------------------------------------------------------------------------
# Phase 3: Validation Modes + Snapshots + Mermaid Tests
# ---------------------------------------------------------------------------


class TestValidateMode:
    """Tests for validate_stage mode parameter."""

    def _setup_validated_stage(self, tmp_path: Path) -> Path:
        """Set up a project with a stage ready for validation."""
        agent_dir = _make_ready_project(tmp_path)
        plan_stage("S01", "Test plan", str(tmp_path))
        record_scan({"nodes": [], "node_count": 0}, stage_id="S01", project_root=str(tmp_path))
        return agent_dir

    def test_validate_light_mode(self, tmp_path: Path):
        """Light mode runs 4 checks."""
        self._setup_validated_stage(tmp_path)
        result = validate_stage("S01", mode="light", project_root=str(tmp_path))
        assert result["status"] == "success"
        assert result["mode"] == "light"
        check_names = [c["check"] for c in result["checks"]]
        assert len(check_names) == 4
        assert "plan_exists" in check_names
        assert "scan_recorded" in check_names
        assert "artifacts_registered" in check_names
        assert "dependencies_completed" in check_names

    def test_validate_strict_mode(self, tmp_path: Path):
        """Strict mode runs 6 checks."""
        self._setup_validated_stage(tmp_path)
        result = validate_stage("S01", mode="strict", project_root=str(tmp_path))
        assert result["status"] == "success"
        assert result["mode"] == "strict"
        check_names = [c["check"] for c in result["checks"]]
        assert len(check_names) == 6
        assert "acceptance_criteria_met" in check_names
        assert "dod_compliance" in check_names

    def test_validate_strict_fails_no_acceptance(self, tmp_path: Path):
        """Strict fails if no acceptance criteria on stage."""
        self._setup_validated_stage(tmp_path)
        # Default stages don't have acceptance field
        result = validate_stage("S01", mode="strict", project_root=str(tmp_path))
        acc_check = [c for c in result["checks"] if c["check"] == "acceptance_criteria_met"][0]
        assert acc_check["passed"] is False

    def test_validate_strict_passes_with_acceptance(self, tmp_path: Path):
        """Strict passes acceptance check when criteria defined."""
        agent_dir = self._setup_validated_stage(tmp_path)

        # Add acceptance criteria to S01
        stages = yaml.safe_load(
            (agent_dir / "stages.yaml").read_text(encoding="utf-8")
        )
        for s in stages["stages"]:
            if s["id"] == "S01":
                s["acceptance"] = ["Heightfield generated", "No errors"]
        (agent_dir / "stages.yaml").write_text(
            yaml.dump(stages, default_flow_style=False), encoding="utf-8"
        )

        result = validate_stage("S01", mode="strict", project_root=str(tmp_path))
        acc_check = [c for c in result["checks"] if c["check"] == "acceptance_criteria_met"][0]
        assert acc_check["passed"] is True

    def test_validate_strict_fails_no_dod(self, tmp_path: Path):
        """Strict fails if DoD is placeholder."""
        agent_dir = self._setup_validated_stage(tmp_path)
        # Overwrite spec to have placeholder DoD
        spec = (agent_dir / "spec.md").read_text(encoding="utf-8")
        spec = spec.replace(
            "- All stages completed and validated\n- Output geometry exports successfully",
            "(Define what \"done\" means for this project)",
        )
        (agent_dir / "spec.md").write_text(spec, encoding="utf-8")

        result = validate_stage("S01", mode="strict", project_root=str(tmp_path))
        dod_check = [c for c in result["checks"] if c["check"] == "dod_compliance"][0]
        assert dod_check["passed"] is False

    def test_validate_default_is_light(self, tmp_path: Path):
        """Default mode is light."""
        self._setup_validated_stage(tmp_path)
        result = validate_stage("S01", project_root=str(tmp_path))
        assert result["mode"] == "light"
        assert len(result["checks"]) == 4

    def test_validate_invalid_mode(self, tmp_path: Path):
        """Invalid mode returns error."""
        self._setup_validated_stage(tmp_path)
        result = validate_stage("S01", mode="ultra", project_root=str(tmp_path))
        assert result["status"] == "error"
        assert "Invalid mode" in result["message"]


class TestSnapshot:
    """Tests for snapshot generation."""

    def test_snapshot_saved_on_record_scan(self, tmp_path: Path):
        """Snapshot file created when recording scan."""
        _make_ready_project(tmp_path)
        plan_stage("S01", "Test", str(tmp_path))

        scan_data = {"nodes": [{"name": "sphere1", "type": "Sop/sphere"}], "node_count": 1}
        result = record_scan(scan_data, stage_id="S01", project_root=str(tmp_path))

        assert result["status"] == "success"
        # Check snapshot was created
        snapshots_dir = tmp_path / ".agent" / "snapshots"
        snapshots = list(snapshots_dir.glob("snapshot_*.json"))
        assert len(snapshots) >= 1

        # Verify snapshot content
        snapshot = json.loads(snapshots[0].read_text(encoding="utf-8"))
        assert "event_id" in snapshot
        assert snapshot["snapshot_source"] == "scan_data"
        assert snapshot["node_count"] == 1
        assert "scene_data" in snapshot

    def test_snapshot_uses_scan_data(self, tmp_path: Path):
        """Snapshot content matches original scan_data."""
        _make_ready_project(tmp_path)
        plan_stage("S01", "Test", str(tmp_path))

        scan_data = {
            "nodes": [
                {"name": "sphere1", "type": "Sop/sphere"},
                {"name": "box1", "type": "Sop/box"},
            ],
            "node_count": 2,
        }
        record_scan(scan_data, stage_id="S01", project_root=str(tmp_path))

        snapshots_dir = tmp_path / ".agent" / "snapshots"
        snapshots = list(snapshots_dir.glob("snapshot_*.json"))
        snapshot = json.loads(snapshots[0].read_text(encoding="utf-8"))
        assert snapshot["scene_data"]["node_count"] == 2
        assert len(snapshot["scene_data"]["nodes"]) == 2

    def test_record_scan_returns_artifacts(self, tmp_path: Path):
        """record_scan returns artifact paths when snapshot created."""
        _make_ready_project(tmp_path)
        plan_stage("S01", "Test", str(tmp_path))

        scan_data = {
            "nodes": [{"name": "sphere1", "type": "Sop/sphere"}],
            "connections": [{"src": "sphere1", "dst": "transform1"}],
            "node_count": 1,
        }
        result = record_scan(scan_data, stage_id="S01", project_root=str(tmp_path))
        assert "artifacts" in result
        assert any("snapshot" in a for a in result["artifacts"])


class TestMermaidGraph:
    """Tests for Mermaid graph generation."""

    def test_stage_graph_generated_on_validate(self, tmp_path: Path):
        """Stage graph .mmd file created during validate_stage."""
        _make_ready_project(tmp_path)
        plan_stage("S01", "Test", str(tmp_path))
        record_scan({"nodes": [], "node_count": 0}, stage_id="S01", project_root=str(tmp_path))
        validate_stage("S01", project_root=str(tmp_path))

        graphs_dir = tmp_path / ".agent" / "graphs"
        graphs = list(graphs_dir.glob("stages_*.mmd"))
        assert len(graphs) >= 1

        content = graphs[0].read_text(encoding="utf-8")
        assert "flowchart TD" in content
        assert "S01" in content

    def test_network_graph_generated_on_scan(self, tmp_path: Path):
        """Network graph .mmd file created from scan_data with connections."""
        _make_ready_project(tmp_path)
        plan_stage("S01", "Test", str(tmp_path))

        scan_data = {
            "nodes": [
                {"name": "sphere1", "type": "Sop/sphere"},
                {"name": "transform1", "type": "Sop/xform"},
            ],
            "connections": [
                {"src": "sphere1", "dst": "transform1"},
            ],
            "node_count": 2,
        }
        record_scan(scan_data, stage_id="S01", project_root=str(tmp_path))

        graphs_dir = tmp_path / ".agent" / "graphs"
        network_graphs = list(graphs_dir.glob("network_*.mmd"))
        assert len(network_graphs) >= 1

        content = network_graphs[0].read_text(encoding="utf-8")
        assert "flowchart TD" in content
        assert "sphere1" in content
        assert "transform1" in content

    def test_stage_graph_includes_dependencies(self, tmp_path: Path):
        """Stage graph shows dependency arrows."""
        _make_ready_project(tmp_path)
        plan_stage("S01", "Test", str(tmp_path))
        record_scan({"nodes": [], "node_count": 0}, stage_id="S01", project_root=str(tmp_path))
        validate_stage("S01", project_root=str(tmp_path))

        graphs_dir = tmp_path / ".agent" / "graphs"
        graphs = list(graphs_dir.glob("stages_*.mmd"))
        content = graphs[0].read_text(encoding="utf-8")
        # S02 depends on S01
        assert "S01 --> S02" in content


# ---------------------------------------------------------------------------
# Feature B: next_action improvement + external change detection
# ---------------------------------------------------------------------------


class TestNextActionImprovement:
    """Tests for improved next_action messages."""

    def test_next_action_suggests_update_tool(self, tmp_path: Path):
        """next_action mentions update_spec_and_stages when spec is incomplete."""
        result = ensure_project(str(tmp_path))
        assert result["ready_for_implementation"] is False
        assert result["next_action"] is not None
        assert "update_spec_and_stages" in result["next_action"]


class TestExternalChangeDetection:
    """Tests for spec/stages external change detection."""

    def test_external_spec_change_detected(self, tmp_path: Path):
        """External spec.md modification is detected on next ensure_project."""
        _make_ready_project(tmp_path)
        # First call to set hashes
        ensure_project(str(tmp_path))

        # Modify spec.md externally (not via update_spec_and_stages)
        spec_path = tmp_path / ".agent" / "spec.md"
        content = spec_path.read_text(encoding="utf-8")
        spec_path.write_text(content + "\n## Extra Section\n\nSome content\n", encoding="utf-8")

        # Second call should detect the change
        result = ensure_project(str(tmp_path))
        assert "external_changes_detected" in result
        assert "spec.md" in result["external_changes_detected"]

        # Event should be recorded
        events = _read_events(tmp_path / ".agent")
        event_types = [e["event_type"] for e in events]
        assert "spec_updated_external" in event_types

    def test_external_stages_change_detected(self, tmp_path: Path):
        """External stages.yaml modification is detected on next ensure_project."""
        _make_ready_project(tmp_path)
        ensure_project(str(tmp_path))

        # Modify stages.yaml externally
        stages_path = tmp_path / ".agent" / "stages.yaml"
        content = stages_path.read_text(encoding="utf-8")
        stages_path.write_text(content + "  # external edit\n", encoding="utf-8")

        result = ensure_project(str(tmp_path))
        assert "external_changes_detected" in result
        assert "stages.yaml" in result["external_changes_detected"]

        events = _read_events(tmp_path / ".agent")
        event_types = [e["event_type"] for e in events]
        assert "stages_updated_external" in event_types

    def test_update_spec_and_stages_updates_hash(self, tmp_path: Path):
        """update_spec_and_stages updates hashes so no external change flagged."""
        _make_ready_project(tmp_path)
        ensure_project(str(tmp_path))

        # Update via the proper tool
        update_spec_and_stages(
            "Add DoD criterion",
            spec_patch={"dod_append": "All tests pass"},
            project_root=str(tmp_path),
        )

        # Next ensure_project should NOT flag external change
        result = ensure_project(str(tmp_path))
        assert "external_changes_detected" not in result


# ---------------------------------------------------------------------------
# Feature A: plan_stage forced redirect in update_stage
# ---------------------------------------------------------------------------


class TestUpdateStageRedirect:
    """Tests for plan_stage forced redirect."""

    def test_update_stage_pending_to_in_progress_redirects(self, tmp_path: Path):
        """pending→in_progress via update_stage is blocked; plan_stage suggested."""
        _make_ready_project(tmp_path)
        ensure_project(str(tmp_path))
        result = update_stage("S01", "in_progress", project_root=str(tmp_path))
        assert result["status"] == "error"
        assert "plan_stage" in result["message"]

    def test_update_stage_failed_to_in_progress_suggests_plan(self, tmp_path: Path):
        """failed→in_progress is allowed but suggests plan_stage."""
        _make_ready_project(tmp_path)
        # Move S01 to in_progress then failed
        plan_stage("S01", "Test plan", str(tmp_path))
        update_stage("S01", "failed", reason="test failure", project_root=str(tmp_path))

        result = update_stage("S01", "in_progress", project_root=str(tmp_path))
        assert result["status"] == "success"
        assert "plan_stage" in result.get("recommendation", "")
