"""Tests for PM watch hook (hooks/pm_watch.py).

Tests invoke pm_watch.py as a subprocess with mock .agent/ directory
structures and JSON stdin payloads.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Use the repo copy directly for testing
REPO_ROOT = Path(__file__).resolve().parent.parent
PM_WATCH_SCRIPT = REPO_ROOT / "hooks" / "pm_watch.py"


def _run_pm_watch(payload: dict, cwd_path: Path, *, timeout: int = 10):
    """Run pm_watch.py with given stdin payload."""
    stdin_data = json.dumps(payload)
    result = subprocess.run(
        [sys.executable, str(PM_WATCH_SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd_path),
    )
    return result


def _make_agent_dir(tmp_path: Path) -> Path:
    """Create minimal .agent/ structure for pm_watch."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "history").mkdir(exist_ok=True)
    (agent_dir / "reports").mkdir(exist_ok=True)
    return agent_dir


def _write_events(agent_dir: Path, events: list[dict]) -> None:
    """Write events to .agent/history/events.jsonl."""
    lines = [json.dumps(e) for e in events]
    (agent_dir / "history" / "events.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_build_state(agent_dir: Path, state: dict) -> None:
    (agent_dir / "build_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


def _write_gate_state(agent_dir: Path, state: dict) -> None:
    (agent_dir / "gate_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


# -----------------------------------------------------------------------
# Test 1: No .agent/ → exit 0, nothing created
# -----------------------------------------------------------------------

class TestNoAgentDir:
    def test_no_agent_dir_exits_cleanly(self, tmp_path):
        """pm_watch should exit 0 and create nothing when .agent/ is absent."""
        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        # No reports directory should be created
        assert not (tmp_path / ".agent").exists()


# -----------------------------------------------------------------------
# Test 2: Basic PM_STATUS generation
# -----------------------------------------------------------------------

class TestPmStatusGeneration:
    def test_stop_generates_pm_status(self, tmp_path):
        """Stop event with valid .agent/ should create PM_STATUS.md."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {
            "active_stage": "S01",
            "stages": {"S01": {"status": "in_progress"}},
        })
        _write_gate_state(agent_dir, {"approved_diff_id": "diff_abc"})
        _write_events(agent_dir, [
            {"event_type": "scene_scanned", "ts": "2026-03-01T10:00:00Z"},
            {"event_type": "stage_validated", "ts": "2026-03-01T10:05:00Z"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        status_path = agent_dir / "reports" / "PM_STATUS.md"
        assert status_path.exists()
        content = status_path.read_text(encoding="utf-8")
        assert "# PM Status" in content
        assert "S01" in content
        assert "in_progress" in content
        assert "diff_abc" in content

    def test_stop_no_stdout(self, tmp_path):
        """Stop event should NOT produce stdout (async, file-only)."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# -----------------------------------------------------------------------
# Test 3: Stagnation detection (in_progress + no scan/validate)
# -----------------------------------------------------------------------

class TestStagnationDetection:
    def test_stagnation_next_actions(self, tmp_path):
        """If stage is in_progress but no scan/validate events → stagnation."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {
            "active_stage": "S02",
            "stages": {"S02": {"status": "in_progress"}},
        })
        # No scan or validate events
        _write_events(agent_dir, [
            {"event_type": "stage_status_changed", "ts": "2026-03-01T09:00:00Z"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        content = (agent_dir / "reports" / "PM_STATUS.md").read_text(encoding="utf-8")
        assert "record_scan" in content
        assert "validate_stage" in content
        assert "S02" in content


# -----------------------------------------------------------------------
# Test 4: External change detection → PM_ALERTS
# -----------------------------------------------------------------------

class TestExternalChangeAlerts:
    def test_external_spec_change_alert(self, tmp_path):
        """spec_updated_external in events → alert in PM_ALERTS.md."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})
        _write_events(agent_dir, [
            {"event_type": "spec_updated_external", "ts": "2026-03-01T11:00:00Z"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        # Check PM_STATUS has the alert
        status_content = (agent_dir / "reports" / "PM_STATUS.md").read_text(encoding="utf-8")
        assert "spec.md" in status_content
        assert "ensure_project" in status_content

        # Check PM_ALERTS has the alert entry
        alerts_path = agent_dir / "reports" / "PM_ALERTS.md"
        assert alerts_path.exists()
        alerts_content = alerts_path.read_text(encoding="utf-8")
        assert "External Spec Change" in alerts_content
        assert "spec.md" in alerts_content


# -----------------------------------------------------------------------
# Test 5: PostToolUseFailure → PM_ALERTS append
# -----------------------------------------------------------------------

class TestPostToolUseFailure:
    def test_failure_appends_alert(self, tmp_path):
        """PostToolUseFailure event should append a failure alert."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})
        _write_events(agent_dir, [])

        result = _run_pm_watch(
            {
                "hook_event_name": "PostToolUseFailure",
                "cwd": str(tmp_path),
                "tool_name": "mcp__houdini-mcp__set_parameter",
                "error": "Parameter 'nonexistent' not found on node",
            },
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        alerts_path = agent_dir / "reports" / "PM_ALERTS.md"
        assert alerts_path.exists()
        content = alerts_path.read_text(encoding="utf-8")
        assert "PostToolUseFailure" in content
        assert "set_parameter" in content
        assert "nonexistent" in content


# -----------------------------------------------------------------------
# Test 6: TaskCompleted → stdout summary
# -----------------------------------------------------------------------

class TestTaskCompletedStdout:
    def test_task_completed_stdout(self, tmp_path):
        """TaskCompleted should produce 1-3 line stdout summary."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {
            "active_stage": "S01",
            "stages": {"S01": {"status": "in_progress"}},
        })
        _write_events(agent_dir, [
            {"event_type": "scene_scanned", "ts": "2026-03-01T12:00:00Z"},
            {"event_type": "stage_validated", "ts": "2026-03-01T12:05:00Z"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "TaskCompleted", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        lines = result.stdout.strip().split("\n")
        assert 1 <= len(lines) <= 3
        assert "PM:" in lines[0]
        assert "S01" in lines[0]
        assert "Next:" in lines[1]


# -----------------------------------------------------------------------
# Test 7: Failure streak detection
# -----------------------------------------------------------------------

class TestFailureStreakDetection:
    def test_three_failures_triggers_alert(self, tmp_path):
        """3+ consecutive failures of same tool → streak alert."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})
        _write_events(agent_dir, [
            {"event_type": "tool_failed", "details": {"tool_name": "execute_code"}, "ts": "T1"},
            {"event_type": "tool_failed", "details": {"tool_name": "execute_code"}, "ts": "T2"},
            {"event_type": "tool_failed", "details": {"tool_name": "execute_code"}, "ts": "T3"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        status_content = (agent_dir / "reports" / "PM_STATUS.md").read_text(encoding="utf-8")
        assert "execute_code" in status_content
        assert "3" in status_content

        alerts_path = agent_dir / "reports" / "PM_ALERTS.md"
        assert alerts_path.exists()
        alerts_content = alerts_path.read_text(encoding="utf-8")
        assert "Tool Failure Streak" in alerts_content
        assert "execute_code" in alerts_content


# -----------------------------------------------------------------------
# Test 8: Empty/corrupt input → fail-soft
# -----------------------------------------------------------------------

# -----------------------------------------------------------------------
# Test 9: Optional NotebookLM line — shown on external change
# -----------------------------------------------------------------------

class TestOptionalNotebookLM:
    def test_optional_nbml_shown_on_external_change(self, tmp_path):
        """External change in events → Optional NotebookLM line in PM_STATUS."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})
        _write_events(agent_dir, [
            {"event_type": "spec_updated_external", "ts": "2026-03-01T11:00:00Z"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        content = (agent_dir / "reports" / "PM_STATUS.md").read_text(encoding="utf-8")
        assert "Optional: NotebookLM sanity check" in content
        assert "record_norm_check" in content

    def test_optional_nbml_not_shown_when_clean(self, tmp_path):
        """Clean state (scan + validate, no errors) → no Optional line."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {
            "active_stage": "S01",
            "stages": {"S01": {"status": "in_progress"}},
        })
        # Both scan and validate present → no stagnation
        # No external changes, no failures, no norm/decision events
        _write_events(agent_dir, [
            {"event_type": "scene_scanned", "ts": "2026-03-01T12:00:00Z"},
            {"event_type": "stage_validated", "ts": "2026-03-01T12:05:00Z"},
        ])

        result = _run_pm_watch(
            {"hook_event_name": "Stop", "cwd": str(tmp_path)},
            cwd_path=tmp_path,
        )
        assert result.returncode == 0

        content = (agent_dir / "reports" / "PM_STATUS.md").read_text(encoding="utf-8")
        assert "Optional" not in content


# -----------------------------------------------------------------------
# Test 10: Empty/corrupt input → fail-soft
# -----------------------------------------------------------------------

class TestFailSoft:
    def test_empty_stdin_exits_cleanly(self, tmp_path):
        """Empty stdin should not crash — just exit 0."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})

        result = subprocess.run(
            [sys.executable, str(PM_WATCH_SCRIPT)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0

    def test_corrupt_json_stdin_exits_cleanly(self, tmp_path):
        """Corrupt JSON on stdin should not crash."""
        agent_dir = _make_agent_dir(tmp_path)
        _write_build_state(agent_dir, {"active_stage": None, "stages": {}})

        result = subprocess.run(
            [sys.executable, str(PM_WATCH_SCRIPT)],
            input="{{{invalid json",
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
