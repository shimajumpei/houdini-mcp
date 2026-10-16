"""Unit tests for workflow data models and transition rules."""

import json
from pathlib import Path

import pytest

from houdini_mcp.tools.workflow.models import (
    ALLOWED_TRANSITIONS,
    ArtifactRecord,
    BuildState,
    StageState,
    StageStatus,
    is_valid_transition,
)


class TestStageStatus:
    """Tests for StageStatus enum."""

    def test_enum_values(self):
        assert StageStatus.PENDING == "pending"
        assert StageStatus.IN_PROGRESS == "in_progress"
        assert StageStatus.VALIDATED == "validated"
        assert StageStatus.COMPLETED == "completed"
        assert StageStatus.FAILED == "failed"

    def test_enum_count(self):
        assert len(StageStatus) == 5


class TestTransitionRules:
    """Tests for state transition validation."""

    def test_valid_transitions(self):
        assert is_valid_transition("pending", "in_progress") is True
        assert is_valid_transition("in_progress", "validated") is True
        assert is_valid_transition("in_progress", "failed") is True
        assert is_valid_transition("validated", "completed") is True
        assert is_valid_transition("failed", "in_progress") is True

    def test_invalid_transitions(self):
        assert is_valid_transition("pending", "completed") is False
        assert is_valid_transition("pending", "validated") is False
        assert is_valid_transition("pending", "failed") is False
        assert is_valid_transition("completed", "pending") is False
        assert is_valid_transition("completed", "in_progress") is False
        assert is_valid_transition("validated", "in_progress") is False
        assert is_valid_transition("validated", "failed") is False

    def test_completed_has_no_transitions(self):
        assert ALLOWED_TRANSITIONS["completed"] == []

    def test_unknown_status_returns_false(self):
        assert is_valid_transition("nonexistent", "pending") is False


class TestStageState:
    """Tests for StageState dataclass."""

    def test_default_values(self):
        state = StageState()
        assert state.status == "pending"
        assert state.plan_file is None
        assert state.last_validation_passed is None
        assert state.last_scan_file is None
        assert state.started_at is None
        assert state.completed_at is None

    def test_to_dict(self):
        state = StageState(status="in_progress", plan_file="stage_plans/S01.md")
        d = state.to_dict()
        assert d["status"] == "in_progress"
        assert d["plan_file"] == "stage_plans/S01.md"
        assert d["last_validation_passed"] is None

    def test_round_trip(self):
        original = StageState(
            status="validated",
            plan_file="stage_plans/S01.md",
            last_validation_passed=True,
            last_scan_file="scene_scans/S01_20260101.json",
            started_at="2026-01-01T00:00:00+00:00",
        )
        restored = StageState.from_dict(original.to_dict())
        assert restored.status == original.status
        assert restored.plan_file == original.plan_file
        assert restored.last_validation_passed == original.last_validation_passed
        assert restored.last_scan_file == original.last_scan_file
        assert restored.started_at == original.started_at


class TestArtifactRecord:
    """Tests for ArtifactRecord dataclass."""

    def test_round_trip(self):
        original = ArtifactRecord(
            name="terrain_heightfield",
            stage_id="S01",
            artifact_type="node",
            path="/obj/terrain/heightfield1",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        restored = ArtifactRecord.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.stage_id == original.stage_id
        assert restored.artifact_type == original.artifact_type


class TestBuildState:
    """Tests for BuildState dataclass."""

    def test_create_initial(self):
        state = BuildState.create_initial("/path/to/project")
        assert state.version == "1.0.0"
        assert state.project_root == "/path/to/project"
        assert state.active_stage is None
        assert state.stages == {}
        assert state.artifacts == {}
        assert state.last_scan is None
        assert state.created_at != ""
        assert state.last_updated != ""

    def test_to_dict_structure(self):
        state = BuildState.create_initial("/proj")
        state.stages["S01"] = StageState(status="pending")
        d = state.to_dict()
        assert "version" in d
        assert "project" in d
        assert d["project"]["root"] == "/proj"
        assert "stages" in d
        assert d["stages"]["S01"]["status"] == "pending"

    def test_round_trip(self):
        original = BuildState.create_initial("/proj")
        original.stages["S01"] = StageState(status="in_progress", plan_file="p.md")
        original.stages["S02"] = StageState(status="pending")
        original.active_stage = "S01"

        restored = BuildState.from_dict(original.to_dict())
        assert restored.version == original.version
        assert restored.project_root == original.project_root
        assert restored.active_stage == "S01"
        assert restored.stages["S01"].status == "in_progress"
        assert restored.stages["S02"].status == "pending"

    def test_save_and_load(self, tmp_path: Path):
        state = BuildState.create_initial(str(tmp_path))
        state.stages["S01"] = StageState(status="pending")
        state_file = tmp_path / "build_state.json"

        state.save(state_file)
        assert state_file.exists()

        loaded = BuildState.load(state_file)
        assert loaded.project_root == str(tmp_path)
        assert "S01" in loaded.stages
        assert loaded.last_updated != ""

    def test_save_updates_last_updated(self, tmp_path: Path):
        state = BuildState.create_initial(str(tmp_path))
        old_updated = state.last_updated
        state_file = tmp_path / "build_state.json"

        # Save should update last_updated
        import time
        time.sleep(0.01)
        state.save(state_file)
        assert state.last_updated != old_updated
