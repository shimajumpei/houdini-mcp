"""Tests for propose_diff and approve_diff tools."""

import json
from pathlib import Path

from houdini_mcp.tools.workflow import (
    approve_diff,
    ensure_project,
    plan_stage,
    propose_diff,
    update_stage,
)
from houdini_mcp.tools.workflow.gate_state import GATE_STATE_FILENAME, GateState

# ---------------------------------------------------------------------------
# Helpers (reuse pattern from test_workflow.py)
# ---------------------------------------------------------------------------

def _write_filled_spec(agent_dir: Path) -> None:
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

- Rendered terrain image
- Heightfield geometry

## Acceptance Criteria

- Terrain generates without errors
- Erosion is visually correct

## Definition of Done

- All stages completed and validated
- Output renders match expectations
"""
    (agent_dir / "spec.md").write_text(spec, encoding="utf-8")


def _write_filled_stages(agent_dir: Path) -> None:
    import yaml
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
        ]
    }
    (agent_dir / "stages.yaml").write_text(
        yaml.dump(stages, default_flow_style=False), encoding="utf-8"
    )


def _make_ready_project(root: Path) -> Path:
    ensure_project(str(root))
    agent_dir = root / ".agent"
    _write_filled_spec(agent_dir)
    _write_filled_stages(agent_dir)
    return agent_dir


def _setup_active_stage(root: Path, stage_id: str = "S01") -> None:
    """Set up a ready project with an active stage."""
    _make_ready_project(root)
    plan_stage(stage_id, "Test plan", str(root))


# ---------------------------------------------------------------------------
# propose_diff tests
# ---------------------------------------------------------------------------

class TestProposeDiff:
    def test_propose_creates_diff_file(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = propose_diff("S01", {
            "nodes_to_add": [{"type": "sphere", "name": "s1", "parent_path": "/obj/geo1"}],
            "scope": "/obj/geo1",
            "rationale": "Test sphere",
        }, str(tmp_path))

        assert result["status"] == "success"
        assert "diff_id" in result
        diff_file = tmp_path / ".agent" / "diffs" / f"{result['diff_id']}.json"
        assert diff_file.exists()

        diff_data = json.loads(diff_file.read_text(encoding="utf-8"))
        assert diff_data["approved"] is False
        assert diff_data["stage_id"] == "S01"

    def test_propose_updates_gate_state(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = propose_diff("S01", {"scope": "test"}, str(tmp_path))

        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        assert gate.pending_diff_id == result["diff_id"]
        assert gate.diff_approved is False

    def test_propose_requires_active_stage(self, tmp_path: Path):
        _make_ready_project(tmp_path)
        # Don't call plan_stage — no active stage
        # Need to re-run ensure_project to get gate state
        ensure_project(str(tmp_path))
        result = propose_diff("S01", {"scope": "test"}, str(tmp_path))
        assert result["status"] == "error"
        assert "not the active" in result["message"]

    def test_propose_rejects_wrong_stage(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = propose_diff("S99", {"scope": "test"}, str(tmp_path))
        assert result["status"] == "error"

    def test_propose_rejects_list_diff_spec(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = propose_diff("S01", [{"scope": "test"}], str(tmp_path))
        assert result["status"] == "error"
        assert "dict" in result["message"]
        assert "list" in result["message"]

    def test_propose_rejects_string_diff_spec(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = propose_diff("S01", "just a string", str(tmp_path))
        assert result["status"] == "error"
        assert "dict" in result["message"]
        assert "str" in result["message"]

    def test_propose_error_includes_example(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = propose_diff("S01", 42, str(tmp_path))
        assert result["status"] == "error"
        assert "example" in result
        assert "valid_keys" in result
        assert "params_to_change" in result["valid_keys"]

    def test_propose_replaces_previous_pending(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result1 = propose_diff("S01", {"scope": "first"}, str(tmp_path))
        result2 = propose_diff("S01", {"scope": "second"}, str(tmp_path))

        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        assert gate.pending_diff_id == result2["diff_id"]
        assert gate.pending_diff_id != result1["diff_id"]

    def test_propose_rejects_unverified_node_type(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        # Manually set verified_node_types in gate
        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        gate.verified_node_types = ["box", "sphere"]
        gate.save(tmp_path / ".agent" / GATE_STATE_FILENAME)

        result = propose_diff("S01", {
            "nodes_to_add": [
                {"type": "box", "name": "b1", "parent_path": "/obj/geo1"},
                {"type": "faketype", "name": "f1", "parent_path": "/obj/geo1"},
            ],
            "scope": "/obj/geo1",
            "rationale": "test",
        }, str(tmp_path))
        assert result["status"] == "error"
        assert "faketype" in result["message"]
        assert "unverified_node_types" in result

    def test_propose_rejects_unverified_param(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        gate.verified_params = {"/obj/geo1/box1": ["sizex", "sizey"]}
        gate.save(tmp_path / ".agent" / GATE_STATE_FILENAME)

        result = propose_diff("S01", {
            "params_to_change": [
                {"node_path": "/obj/geo1/box1", "param_name": "sizex", "value": 2.0},
                {"node_path": "/obj/geo1/box1", "param_name": "fakeparm", "value": 1.0},
            ],
            "scope": "/obj/geo1",
            "rationale": "test",
        }, str(tmp_path))
        assert result["status"] == "error"
        assert "fakeparm" in result["message"]
        assert "unverified_params" in result

    def test_propose_allows_when_no_verified_data(self, tmp_path: Path):
        """With empty verified lists, fail-soft allows any types/params."""
        _setup_active_stage(tmp_path)
        result = propose_diff("S01", {
            "nodes_to_add": [{"type": "anything", "name": "a1", "parent_path": "/obj"}],
            "params_to_change": [{"node_path": "/obj/x", "param_name": "y", "value": 1}],
            "scope": "/obj",
            "rationale": "test",
        }, str(tmp_path))
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# approve_diff tests
# ---------------------------------------------------------------------------

class TestApproveDiff:
    def test_approve_sets_flag(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        prop = propose_diff("S01", {"scope": "test"}, str(tmp_path))
        diff_id = prop["diff_id"]

        result = approve_diff(diff_id, str(tmp_path))
        assert result["status"] == "success"
        assert result["approved"] is True

        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        assert gate.diff_approved is True

    def test_approve_updates_diff_file(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        prop = propose_diff("S01", {"scope": "test"}, str(tmp_path))
        approve_diff(prop["diff_id"], str(tmp_path))

        diff_file = tmp_path / ".agent" / "diffs" / f"{prop['diff_id']}.json"
        diff_data = json.loads(diff_file.read_text(encoding="utf-8"))
        assert diff_data["approved"] is True
        assert "approved_at" in diff_data

    def test_approve_rejects_wrong_diff_id(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        propose_diff("S01", {"scope": "test"}, str(tmp_path))
        result = approve_diff("wrong_id", str(tmp_path))
        assert result["status"] == "error"
        assert "mismatch" in result["message"].lower()

    def test_approve_rejects_when_no_pending(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        result = approve_diff("some_id", str(tmp_path))
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Integration: diff reset on stage completion
# ---------------------------------------------------------------------------

class TestDiffResetOnStageComplete:
    def test_diff_approved_resets_on_validated(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        prop = propose_diff("S01", {"scope": "test"}, str(tmp_path))
        approve_diff(prop["diff_id"], str(tmp_path))

        # Record scan + validate to move to validated
        from houdini_mcp.tools.workflow import record_scan, validate_stage
        record_scan({"test": True}, "S01", str(tmp_path))
        validate_stage("S01", project_root=str(tmp_path))
        update_stage("S01", "validated", project_root=str(tmp_path))

        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        assert gate.diff_approved is False
        assert gate.pending_diff_id is None

    def test_gate_resets_on_stage_completed(self, tmp_path: Path):
        _setup_active_stage(tmp_path)
        prop = propose_diff("S01", {"scope": "test"}, str(tmp_path))
        approve_diff(prop["diff_id"], str(tmp_path))

        from houdini_mcp.tools.workflow import record_scan, validate_stage
        record_scan({"test": True}, "S01", str(tmp_path))
        validate_stage("S01", project_root=str(tmp_path))
        update_stage("S01", "validated", project_root=str(tmp_path))
        update_stage("S01", "completed", project_root=str(tmp_path))

        gate = GateState.load(tmp_path / ".agent" / GATE_STATE_FILENAME)
        assert gate.active_stage is None
        assert gate.active_stage_in_progress is False
        assert gate.diff_approved is False
        assert gate.verified_params == {}  # Reset on completed
