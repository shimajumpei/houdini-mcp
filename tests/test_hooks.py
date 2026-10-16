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
# Gate hook CLAUDE.md injection tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not GATE_SCRIPT.exists(), reason="Gate hook script not installed")
class TestGateHookClaudeMdInjection:
    """Tests for Check 0: CLAUDE.md auto-injection in the gate hook."""

    def test_copies_claude_md_when_missing(self, tmp_path: Path):
        """CLAUDE.md missing → copied from canonical, gate check continues."""
        # Set up canonical source
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        canonical_file = canonical_dir / "CLAUDE.md"
        canonical_file.write_text("# Canonical CLAUDE.md\nRules here.", encoding="utf-8")

        # Project dir has no CLAUDE.md but has gate_state that allows
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        agent_dir = project_dir / ".agent"
        agent_dir.mkdir()
        (agent_dir / "gate_state.json").write_text(
            json.dumps({
                "workflow_initialized": True,
                "ready_for_implementation": True,
                "active_stage_in_progress": True,
                "diff_approved": True,
                "error_counts": {},
            }, indent=2),
            encoding="utf-8",
        )

        stdin_data = json.dumps({
            "tool_name": "mcp__houdini-mcp__create_node",
            "tool_input": {"node_type": "geo"},
            "cwd": str(project_dir),
        })

        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "HOUDINI_CLAUDE_MD_CANONICAL": str(canonical_file)},
        )

        assert result.returncode == 0
        # CLAUDE.md should have been copied
        assert (project_dir / "CLAUDE.md").exists()
        assert (project_dir / "CLAUDE.md").read_text(encoding="utf-8") == "# Canonical CLAUDE.md\nRules here."
        # All gate checks pass → allow (no stdout)
        assert result.stdout.strip() == ""

    def test_does_not_overwrite_existing_claude_md(self, tmp_path: Path):
        """Existing CLAUDE.md is not overwritten."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        canonical_file = canonical_dir / "CLAUDE.md"
        canonical_file.write_text("# Canonical version", encoding="utf-8")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# Custom version", encoding="utf-8")

        stdin_data = json.dumps({
            "tool_name": "mcp__houdini-mcp__create_node",
            "tool_input": {"node_type": "geo"},
            "cwd": str(project_dir),
        })

        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "HOUDINI_CLAUDE_MD_CANONICAL": str(canonical_file)},
        )

        assert result.returncode == 0
        # Custom version preserved
        assert (project_dir / "CLAUDE.md").read_text(encoding="utf-8") == "# Custom version"
        # No gate_state → deny (fail-closed for modification tools)
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_skips_when_canonical_missing(self, tmp_path: Path):
        """No canonical file → no copy attempted, but still deny (no gate_state)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        stdin_data = json.dumps({
            "tool_name": "mcp__houdini-mcp__create_node",
            "tool_input": {"node_type": "geo"},
            "cwd": str(project_dir),
        })

        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "HOUDINI_CLAUDE_MD_CANONICAL": str(tmp_path / "nonexistent" / "CLAUDE.md")},
        )

        assert result.returncode == 0
        assert not (project_dir / "CLAUDE.md").exists()
        # No gate_state → deny (fail-closed for modification tools)
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "ensure_project" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_denies_on_copy_failure(self, tmp_path: Path):
        """Copy failure → deny with manual-copy instruction."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        canonical_file = canonical_dir / "CLAUDE.md"
        canonical_file.write_text("# Canonical", encoding="utf-8")

        # Use a non-writable directory as project_dir
        project_dir = tmp_path / "readonly_project"
        project_dir.mkdir()
        # Make project_dir read-only (on Windows, we simulate by using a file path as dir)
        # Instead, point to an invalid path that can't be written
        bad_project_dir = tmp_path / "no_such_dir" / "deep" / "path"
        # Don't create it — copy will fail because parent doesn't exist

        stdin_data = json.dumps({
            "tool_name": "mcp__houdini-mcp__create_node",
            "tool_input": {"node_type": "geo"},
            "cwd": str(bad_project_dir),
        })

        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "HOUDINI_CLAUDE_MD_CANONICAL": str(canonical_file)},
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "CLAUDE.md" in output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "auto-copy failed" in output["hookSpecificOutput"]["permissionDecisionReason"]


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
