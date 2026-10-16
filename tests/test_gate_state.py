"""Tests for GateState model and file I/O."""

from pathlib import Path

from houdini_mcp.tools.workflow.gate_state import GATE_STATE_FILENAME, GateState


class TestGateStateCreate:
    def test_create_initial_defaults(self):
        gate = GateState.create_initial()
        assert gate.workflow_initialized is False
        assert gate.ready_for_implementation is False
        assert gate.active_stage is None
        assert gate.active_stage_in_progress is False
        assert gate.pending_diff_id is None
        assert gate.diff_approved is False
        assert gate.schema_checked == {}
        assert gate.error_counts == {}
        assert gate.docs_referenced is False
        assert gate.last_updated != ""


class TestGateStateSerialize:
    def test_to_dict_round_trip(self):
        gate = GateState(
            workflow_initialized=True,
            ready_for_implementation=True,
            active_stage="S01",
            active_stage_in_progress=True,
            pending_diff_id="diff_123",
            diff_approved=True,
            schema_checked={"/obj/geo1": True},
            verified_node_types=["box", "sphere", "xform"],
            verified_params={"/obj/geo1/box1": ["sizex", "sizey", "sizez"]},
            error_counts={"create_node": 2},
            docs_referenced=True,
        )
        data = gate.to_dict()
        restored = GateState.from_dict(data)
        assert restored.workflow_initialized is True
        assert restored.ready_for_implementation is True
        assert restored.active_stage == "S01"
        assert restored.active_stage_in_progress is True
        assert restored.pending_diff_id == "diff_123"
        assert restored.diff_approved is True
        assert restored.schema_checked == {"/obj/geo1": True}
        assert restored.error_counts == {"create_node": 2}
        assert restored.docs_referenced is True

    def test_verified_fields_round_trip(self):
        gate = GateState(
            verified_node_types=["box", "sphere", "xform"],
            verified_params={
                "/obj/geo1/box1": ["sizex", "sizey", "sizez"],
                "/obj/geo1/xform1": ["tx", "ty", "tz"],
            },
        )
        data = gate.to_dict()
        restored = GateState.from_dict(data)
        assert restored.verified_node_types == ["box", "sphere", "xform"]
        assert restored.verified_params == {
            "/obj/geo1/box1": ["sizex", "sizey", "sizez"],
            "/obj/geo1/xform1": ["tx", "ty", "tz"],
        }

    def test_from_dict_missing_new_fields(self):
        """Old gate_state.json without verified fields restores with defaults."""
        gate = GateState.from_dict({
            "workflow_initialized": True,
            "schema_checked": {"/obj/geo1": True},
        })
        assert gate.workflow_initialized is True
        assert gate.verified_node_types == []
        assert gate.verified_params == {}

    def test_from_dict_with_missing_keys(self):
        gate = GateState.from_dict({})
        assert gate.workflow_initialized is False
        assert gate.active_stage is None
        assert gate.schema_checked == {}
        assert gate.verified_node_types == []
        assert gate.verified_params == {}

    def test_from_dict_with_extra_keys(self):
        gate = GateState.from_dict({"workflow_initialized": True, "unknown_key": 42})
        assert gate.workflow_initialized is True


class TestGateStateFileIO:
    def test_save_and_load(self, tmp_path: Path):
        gate = GateState(
            workflow_initialized=True,
            active_stage="S01",
            diff_approved=True,
        )
        gate_file = tmp_path / GATE_STATE_FILENAME
        gate.save(gate_file)
        assert gate_file.exists()

        loaded = GateState.load(gate_file)
        assert loaded.workflow_initialized is True
        assert loaded.active_stage == "S01"
        assert loaded.diff_approved is True

    def test_load_missing_file_returns_default(self, tmp_path: Path):
        gate = GateState.load(tmp_path / "nonexistent.json")
        assert gate.workflow_initialized is False
        assert gate.active_stage is None

    def test_load_corrupted_file_returns_default(self, tmp_path: Path):
        bad_file = tmp_path / GATE_STATE_FILENAME
        bad_file.write_text("not json", encoding="utf-8")
        gate = GateState.load(bad_file)
        assert gate.workflow_initialized is False

    def test_save_updates_last_updated(self, tmp_path: Path):
        gate = GateState()
        gate_file = tmp_path / GATE_STATE_FILENAME
        gate.save(gate_file)
        first_update = gate.last_updated

        import time
        time.sleep(0.01)  # Ensure timestamp differs

        gate.workflow_initialized = True
        gate.save(gate_file)
        assert gate.last_updated != first_update

    def test_atomic_write(self, tmp_path: Path):
        """Verify no .tmp file remains after save."""
        gate = GateState(workflow_initialized=True)
        gate_file = tmp_path / GATE_STATE_FILENAME
        gate.save(gate_file)
        assert gate_file.exists()
        assert not (tmp_path / "gate_state.tmp").exists()
