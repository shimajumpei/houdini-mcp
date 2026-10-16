"""Tests for the plugin hook scripts (hooks/*.py in this repository).

Tests invoke hook scripts as subprocesses with mock stdin/env.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Hook scripts shipped with the Claude Code plugin in this repository
REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
GATE_SCRIPT = HOOKS_DIR / "houdini_gate.py"
POST_SCRIPT = HOOKS_DIR / "houdini_post_tool.py"


def _run_gate_hook(tool_name, tool_input, gate_state, tmp_path):
    """Run the gate hook script with given inputs."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gate_state.json").write_text(
        json.dumps(gate_state, indent=2), encoding="utf-8"
    )

    stdin_data = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(tmp_path),
    })

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result


def _run_post_hook(tool_name, tool_input, tool_output, gate_state, tmp_path):
    """Run the post-tool hook script with given inputs."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "gate_state.json").write_text(
        json.dumps(gate_state, indent=2), encoding="utf-8"
    )

    stdin_data = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "cwd": str(tmp_path),
    })

    result = subprocess.run(
        [sys.executable, str(POST_SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result


def _read_gate_state(tmp_path):
    """Read gate_state.json from tmp_path."""
    return json.loads(
        (tmp_path / ".agent" / "gate_state.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Gate hook tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not GATE_SCRIPT.exists(), reason="Gate hook script not installed")
class TestGateHook:
    def test_denies_when_not_initialized(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {"workflow_initialized": False},
            tmp_path,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        decision = output["hookSpecificOutput"]["permissionDecision"]
        assert decision == "deny"
        assert "ensure_project" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_denies_when_not_ready(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {"workflow_initialized": True, "ready_for_implementation": False},
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_when_no_active_stage(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": False,
            },
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "plan_stage" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_denies_when_diff_not_approved(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": False,
            },
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "propose_diff" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_allows_when_all_conditions_met(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "error_counts": {},
            },
            tmp_path,
        )
        assert result.returncode == 0
        # No output means allow
        assert result.stdout.strip() == ""

    def test_denies_set_parameter_without_schema(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__set_parameter",
            {"node_path": "/obj/geo1", "param_name": "tx", "value": 1.0},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "schema_checked": {},
            },
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "schema" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_allows_set_parameter_with_schema(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__set_parameter",
            {"node_path": "/obj/geo1", "param_name": "tx", "value": 1.0},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "schema_checked": {"/obj/geo1": True},
            },
            tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_denies_create_node_unverified_type(self, tmp_path: Path):
        """create_node with unverified node_type is denied when verified_types exist."""
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "faketype", "parent_path": "/obj/geo1"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "verified_node_types": ["box", "sphere", "xform"],
            },
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "faketype" in output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "list_node_types" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_allows_create_node_verified_type(self, tmp_path: Path):
        """create_node with a verified node_type is allowed."""
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "box", "parent_path": "/obj/geo1"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "verified_node_types": ["box", "sphere", "xform"],
            },
            tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_allows_create_node_no_verified_types(self, tmp_path: Path):
        """create_node with empty verified_types → fail-soft allow."""
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "anything", "parent_path": "/obj/geo1"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "verified_node_types": [],
            },
            tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_denies_set_parameter_unverified_param(self, tmp_path: Path):
        """set_parameter with unverified param_name is denied when verified_params exist."""
        result = _run_gate_hook(
            "mcp__houdini-mcp__set_parameter",
            {"node_path": "/obj/geo1", "param_name": "fakeparm", "value": 1.0},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "schema_checked": {"/obj/geo1": True},
                "verified_params": {"/obj/geo1": ["tx", "ty", "tz"]},
            },
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "fakeparm" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_allows_set_parameter_verified_param(self, tmp_path: Path):
        """set_parameter with verified param_name is allowed."""
        result = _run_gate_hook(
            "mcp__houdini-mcp__set_parameter",
            {"node_path": "/obj/geo1", "param_name": "tx", "value": 1.0},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "schema_checked": {"/obj/geo1": True},
                "verified_params": {"/obj/geo1": ["tx", "ty", "tz"]},
            },
            tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_denies_after_repeated_errors(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "error_counts": {"create_node": 3},
                "docs_referenced": False,
            },
            tmp_path,
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "documentation" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_allows_after_docs_referenced(self, tmp_path: Path):
        result = _run_gate_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "error_counts": {"create_node": 3},
                "docs_referenced": True,
            },
            tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_denies_when_no_gate_state_file(self, tmp_path: Path):
        """Modification tools without .agent/ should be denied (fail-closed)."""
        stdin_data = json.dumps({
            "tool_name": "mcp__houdini-mcp__create_node",
            "tool_input": {"node_type": "geo"},
            "cwd": str(tmp_path),
        })
        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "ensure_project" in output["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# Gate hook rules delivery tests (Check 0)
# ---------------------------------------------------------------------------

RULES_FILE = REPO_ROOT / "rules" / "HOUDINI_RULES.md"


def _rules_env(tmp_path: Path, **extra: str) -> dict:
    """Environment with an isolated ack store (LOCALAPPDATA, temp) under tmp_path."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("HOUDINI_CLAUDE_MD_CANONICAL", "CLAUDE_PLUGIN_ROOT")}
    env["LOCALAPPDATA"] = str(tmp_path / "localappdata")
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(tmp_path / "temp")
    (tmp_path / "temp").mkdir(exist_ok=True)
    env.update(extra)
    return env


def _run_gate_session(tool_name, project_dir: Path, env: dict, session_id="sess-1"):
    payload = {"tool_name": tool_name, "tool_input": {}, "cwd": str(project_dir)}
    if session_id is not None:
        payload["session_id"] = session_id
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(project_dir),
    )


def _ack_dir(tmp_path: Path) -> Path:
    return tmp_path / "localappdata" / "houdini-mcp" / "rules-ack"


@pytest.mark.skipif(not GATE_SCRIPT.exists(), reason="Gate hook script not installed")
class TestGateHookRulesDelivery:
    """Check 0: the first houdini-mcp call of a session must make Claude read the rules."""

    def _project(self, tmp_path: Path) -> Path:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        return project_dir

    def test_first_call_denied_with_rules_path(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        result = _run_gate_session(
            "mcp__houdini-mcp__get_scene_info", project_dir, _rules_env(tmp_path)
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert str(RULES_FILE.resolve()) in reason
        assert "Read tool" in reason
        assert "retry" in reason
        assert (_ack_dir(tmp_path) / "sess-1").exists()

    def test_second_call_same_session_passes_check0(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path)
        _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        result = _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_second_call_change_tool_reaches_existing_checks(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path)
        _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        result = _run_gate_session("mcp__houdini-mcp__create_node", project_dir, env)
        output = json.loads(result.stdout)
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Workflow not initialized" in reason
        assert "HOUDINI_RULES" not in reason

    def test_different_session_denied_again(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path)
        _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        result = _run_gate_session(
            "mcp__houdini-mcp__get_scene_info", project_dir, env, session_id="sess-2"
        )
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert str(RULES_FILE.resolve()) in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_nothing_written_to_project_dir(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path)
        for session_id in ("sess-1", "sess-1", "sess-2", None):
            _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env, session_id)
        assert list(project_dir.iterdir()) == []

    def test_missing_session_id_allows_and_writes_nothing(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        result = _run_gate_session(
            "mcp__houdini-mcp__get_scene_info", project_dir, _rules_env(tmp_path), session_id=None
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not (tmp_path / "localappdata").exists()
        assert list((tmp_path / "temp").iterdir()) == []
        assert list(project_dir.iterdir()) == []

    def test_override_path_used_in_reason(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        custom = tmp_path / "custom_rules.md"
        custom.write_text("# rules", encoding="utf-8")
        env = _rules_env(tmp_path, HOUDINI_CLAUDE_MD_CANONICAL=str(custom))
        result = _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        output = json.loads(result.stdout)
        assert str(custom.resolve()) in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_missing_rules_file_skips_check0(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path, HOUDINI_CLAUDE_MD_CANONICAL=str(tmp_path / "missing.md"))
        result = _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        assert result.stdout.strip() == ""
        assert not _ack_dir(tmp_path).exists()

    def test_falls_back_to_temp_dir_without_localappdata(self, tmp_path: Path):
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path)
        del env["LOCALAPPDATA"]
        result = _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (tmp_path / "temp" / "houdini-mcp" / "rules-ack" / "sess-1").exists()
        result = _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        assert result.stdout.strip() == ""

    def test_never_writes_ack_into_cwd_used_as_temp(self, tmp_path: Path):
        """If the temp dir resolves to the project dir, no ack is written there."""
        project_dir = self._project(tmp_path)
        env = _rules_env(tmp_path)
        del env["LOCALAPPDATA"]
        env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(project_dir)
        result = _run_gate_session("mcp__houdini-mcp__get_scene_info", project_dir, env)
        assert result.stdout.strip() == ""
        assert list(project_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Post-tool hook tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not POST_SCRIPT.exists(), reason="Post-tool hook script not installed")
class TestPostToolHook:
    def test_increments_error_count(self, tmp_path: Path):
        gate = {
            "workflow_initialized": True,
            "error_counts": {},
            "docs_referenced": False,
        }
        _run_post_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {"status": "error", "message": "Node type not found"},
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert updated["error_counts"]["create_node"] == 1

    def test_increments_error_count_multiple_times(self, tmp_path: Path):
        gate = {
            "workflow_initialized": True,
            "error_counts": {"create_node": 1},
            "docs_referenced": False,
        }
        _run_post_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {"status": "error", "message": "Failed again"},
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert updated["error_counts"]["create_node"] == 2

    def test_docs_reference_resets_errors(self, tmp_path: Path):
        gate = {
            "workflow_initialized": True,
            "error_counts": {"create_node": 3},
            "docs_referenced": False,
            "schema_checked": {},
        }
        _run_post_hook(
            "mcp__houdini-mcp__get_parameter_schema",
            {"node_path": "/obj/geo1"},
            {"status": "success", "parameters": []},
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert updated["docs_referenced"] is True
        assert updated["error_counts"] == {}

    def test_schema_check_updates_gate(self, tmp_path: Path):
        gate = {
            "workflow_initialized": True,
            "error_counts": {},
            "docs_referenced": False,
            "schema_checked": {},
        }
        _run_post_hook(
            "mcp__houdini-mcp__get_parameter_schema",
            {"node_path": "/obj/geo1/sphere1"},
            {"status": "success", "parameters": [{"name": "radx"}]},
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert updated["schema_checked"]["/obj/geo1/sphere1"] is True

    def test_success_does_not_increment_error(self, tmp_path: Path):
        gate = {
            "workflow_initialized": True,
            "error_counts": {},
            "docs_referenced": False,
        }
        _run_post_hook(
            "mcp__houdini-mcp__create_node",
            {"node_type": "geo"},
            {"status": "success", "node_path": "/obj/geo1"},
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert updated["error_counts"] == {}

    def test_list_node_types_populates_verified(self, tmp_path: Path):
        """Successful list_node_types populates verified_node_types."""
        gate = {
            "workflow_initialized": True,
            "error_counts": {},
            "docs_referenced": False,
            "verified_node_types": [],
        }
        _run_post_hook(
            "mcp__houdini-mcp__list_node_types",
            {"category": "Sop"},
            {
                "status": "success",
                "node_types": [
                    {"name": "box", "label": "Box"},
                    {"name": "sphere", "label": "Sphere"},
                ],
            },
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert "box" in updated["verified_node_types"]
        assert "sphere" in updated["verified_node_types"]

    def test_get_parameter_schema_populates_verified_params(self, tmp_path: Path):
        """Successful get_parameter_schema populates verified_params."""
        gate = {
            "workflow_initialized": True,
            "error_counts": {},
            "docs_referenced": False,
            "schema_checked": {},
            "verified_params": {},
        }
        _run_post_hook(
            "mcp__houdini-mcp__get_parameter_schema",
            {"node_path": "/obj/geo1/box1"},
            {
                "status": "success",
                "parameters": [
                    {"name": "sizex", "type": "float"},
                    {"name": "sizey", "type": "float"},
                    {"name": "sizez", "type": "float"},
                ],
            },
            gate,
            tmp_path,
        )
        updated = _read_gate_state(tmp_path)
        assert "/obj/geo1/box1" in updated["verified_params"]
        assert sorted(updated["verified_params"]["/obj/geo1/box1"]) == ["sizex", "sizey", "sizez"]

    def test_no_gate_state_does_not_crash(self, tmp_path: Path):
        """Post hook should not fail if gate_state.json is missing."""
        stdin_data = json.dumps({
            "tool_name": "mcp__houdini-mcp__create_node",
            "tool_input": {},
            "tool_output": {"status": "error"},
            "cwd": str(tmp_path),
        })
        result = subprocess.run(
            [sys.executable, str(POST_SCRIPT)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
