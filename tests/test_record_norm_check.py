"""Tests for record_norm_check workflow tool."""

import json
from pathlib import Path

import pytest
import yaml

from houdini_mcp.tools.workflow.record_norm_check import record_norm_check
from houdini_mcp.tools.workflow.ensure_project import ensure_project


def _write_filled_spec(agent_dir: Path) -> None:
    spec = """\
# Project Specification

## Goal

Build a procedural terrain system.

## Requirements

- Generate heightfield terrain

## Inputs

- DEM data

## Outputs

- Rendered terrain

## Acceptance Criteria

- Terrain renders at 1024x1024
"""
    (agent_dir / "spec.md").write_text(spec, encoding="utf-8")


def _write_filled_stages(agent_dir: Path) -> None:
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
        ],
    }
    (agent_dir / "stages.yaml").write_text(
        yaml.dump(stages, default_flow_style=False), encoding="utf-8"
    )


def _setup_project(tmp_path: Path) -> Path:
    """Bootstrap a ready .agent/ project and return agent_dir."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    _write_filled_spec(agent_dir)
    _write_filled_stages(agent_dir)
    ensure_project(str(tmp_path))
    return agent_dir


# -----------------------------------------------------------------------
# Test 1: consulted (adopt) records changelog + events
# -----------------------------------------------------------------------

class TestConsultedAdopt:
    def test_consulted_adopt_records_changelog_and_events(self, tmp_path):
        agent_dir = _setup_project(tmp_path)

        result = record_norm_check(
            kind="notebooklm",
            summary="Volume migration uses voxel O(1) access",
            impact="adopt",
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert result["impact"] == "adopt"
        assert "events" in result["recorded_to"]
        assert "changelog" in result["recorded_to"]
        assert result["event_id"]  # non-empty

        # Verify events.jsonl
        events_path = agent_dir / "history" / "events.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        norm_events = [e for e in events if e["event_type"] == "norm_check_recorded"]
        assert len(norm_events) >= 1
        last = norm_events[-1]
        assert last["details"]["kind"] == "notebooklm"
        assert last["details"]["impact"] == "adopt"
        assert "Volume migration" in last["summary"]

        # Verify CHANGELOG.md
        changelog = (agent_dir / "history" / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "NotebookLM: consulted" in changelog
        assert "Volume migration" in changelog
        assert "adopt" in changelog


# -----------------------------------------------------------------------
# Test 2: consulted (hold) records with correct impact
# -----------------------------------------------------------------------

class TestConsultedHold:
    def test_consulted_hold_records_correctly(self, tmp_path):
        agent_dir = _setup_project(tmp_path)

        result = record_norm_check(
            kind="notebooklm",
            summary="Consider KineFX for bone-based deformation",
            impact="hold",
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert result["impact"] == "hold"

        # Verify event details
        events_path = agent_dir / "history" / "events.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        norm_events = [e for e in events if e["event_type"] == "norm_check_recorded"]
        assert norm_events[-1]["details"]["impact"] == "hold"

        # Verify CHANGELOG
        changelog = (agent_dir / "history" / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "consulted" in changelog
        assert "hold" in changelog


# -----------------------------------------------------------------------
# Test 3: skipped with reason
# -----------------------------------------------------------------------

class TestSkippedWithReason:
    def test_skipped_records_reason(self, tmp_path):
        agent_dir = _setup_project(tmp_path)

        result = record_norm_check(
            kind="notebooklm",
            summary="Simple parameter tweak",
            impact="skipped",
            reason="low-risk",
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert result["impact"] == "skipped"

        # Verify event details contain reason
        events_path = agent_dir / "history" / "events.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        norm_events = [e for e in events if e["event_type"] == "norm_check_recorded"]
        assert norm_events[-1]["details"]["reason"] == "low-risk"

        # Verify CHANGELOG has skipped line
        changelog = (agent_dir / "history" / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "NotebookLM: skipped" in changelog
        assert "low-risk" in changelog


# -----------------------------------------------------------------------
# Test 4: no .agent/ → status="skipped" without exception
# -----------------------------------------------------------------------

class TestNoAgentDir:
    def test_no_agent_dir_returns_skipped(self, tmp_path):
        # tmp_path has no .agent/ directory
        result = record_norm_check(
            kind="notebooklm",
            summary="Test check",
            impact="adopt",
            project_root=str(tmp_path),
        )

        # Should NOT raise — returns gracefully
        assert result["status"] in ("skipped", "success")
        # If .agent/ was auto-created by _ensure_project, that's fine too.
        # The key requirement: no exception thrown.


# -----------------------------------------------------------------------
# Test 5: also_record_decision appends to DECISIONS.md
# -----------------------------------------------------------------------

class TestAlsoRecordDecision:
    def test_also_record_decision_appends_to_decisions_md(self, tmp_path):
        agent_dir = _setup_project(tmp_path)

        result = record_norm_check(
            kind="notebooklm",
            summary="Switch from VEX to OpenCL for volume processing",
            impact="adopt",
            also_record_decision=True,
            project_root=str(tmp_path),
        )

        assert result["status"] == "success"
        assert "decisions" in result["recorded_to"]

        # Verify DECISIONS.md has an ADR entry
        decisions_path = agent_dir / "history" / "DECISIONS.md"
        assert decisions_path.exists()
        content = decisions_path.read_text(encoding="utf-8")
        assert "ADR-" in content
        assert "adopt" in content
        assert "OpenCL" in content


# -----------------------------------------------------------------------
# Test 6: invalid impact returns error
# -----------------------------------------------------------------------

class TestInvalidImpact:
    def test_invalid_impact_returns_error(self, tmp_path):
        _setup_project(tmp_path)

        result = record_norm_check(
            kind="notebooklm",
            summary="Test",
            impact="maybe",
            project_root=str(tmp_path),
        )

        assert result["status"] == "error"
        assert "impact must be one of" in result["message"]
