"""Shared utilities for workflow management tools.

Provides auto-bootstrap, readiness checking, and error handling
common to all workflow tool functions.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .gate_state import GATE_STATE_FILENAME, GateState
from .models import BuildState, _now_iso
from .templates import CONTRACTS_YAML_TEMPLATE, SPEC_MD_TEMPLATE, STAGES_YAML_TEMPLATE

logger = logging.getLogger(__name__)

# Directories created during bootstrap
_AGENT_SUBDIRS = [
    "stage_plans", "scene_scans", "logs", "diffs",
    "history", "snapshots", "graphs", "reports", "checkpoints",
]

# Files and their templates
_TEMPLATE_FILES: dict[str, str] = {
    "spec.md": SPEC_MD_TEMPLATE,
    "stages.yaml": STAGES_YAML_TEMPLATE,
    "contracts.yaml": CONTRACTS_YAML_TEMPLATE,
}

# Required sections in spec.md (Requirements and Must Have are OR)
SPEC_REQUIRED_SECTIONS = [
    "Goal",
    "Requirements",
    "Must Have",
    "Inputs",
    "Outputs",
    "Acceptance Criteria",
    "Definition of Done",
]

# Placeholder patterns that indicate a section is not filled in
_PLACEHOLDER_PATTERNS = [
    re.compile(r"^\s*\(Describe.*\)\s*$", re.MULTILINE),
    re.compile(r"^\s*\(Define.*\)\s*$", re.MULTILINE),
    re.compile(r"^\s*\(List.*\)\s*$", re.MULTILINE),
    re.compile(r"^\s*\(Specify.*\)\s*$", re.MULTILINE),
]


@dataclass
class ReadinessResult:
    """Result of readiness check for implementation."""

    ready: bool
    missing: list[str]
    next_action: str | None


@dataclass
class EnsureProjectResult:
    """Result of _ensure_project."""

    agent_dir: Path
    state: BuildState
    bootstrapped: bool
    repaired: list[str]
    readiness: ReadinessResult


def handle_workflow_errors(operation_name: str) -> Callable:
    """Decorator for consistent error handling in workflow tools."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> dict[str, Any]:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error during {operation_name}: {e}")
                return {
                    "status": "error",
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                }

        return wrapper

    return decorator


def _resolve_project_root(project_root: str | None) -> Path:
    """Resolve project root directory. Defaults to cwd if not specified."""
    if project_root:
        return Path(project_root).resolve()
    return Path.cwd().resolve()


# ---------------------------------------------------------------------------
# CLAUDE.md auto-injection
# ---------------------------------------------------------------------------

_CANONICAL_CLAUDE_MD = Path(
    os.environ.get(
        "HOUDINI_CLAUDE_MD_CANONICAL",
        str(Path.home() / ".claude" / "houdini" / "CLAUDE.md"),
    )
)


def _ensure_claude_md(project_root: Path) -> str | None:
    """Copy canonical CLAUDE.md to project root if missing.

    Returns:
        Description of action taken, or None if no action needed.
    """
    target = project_root / "CLAUDE.md"
    if target.exists():
        return None
    if not _CANONICAL_CLAUDE_MD.exists():
        return None
    shutil.copy2(str(_CANONICAL_CLAUDE_MD), str(target))
    return "created CLAUDE.md from canonical source"


def _ensure_project(project_root: str | None = None) -> EnsureProjectResult:
    """Ensure .agent/ directory exists with all required files.

    Auto-bootstraps if missing. Repairs partial setups.
    Idempotent: existing files are never overwritten.
    """
    root = _resolve_project_root(project_root)
    agent_dir = root / ".agent"
    bootstrapped = False
    repaired: list[str] = []

    # Create .agent/ if missing
    if not agent_dir.exists():
        agent_dir.mkdir(parents=True, exist_ok=True)
        bootstrapped = True

    # Create subdirectories
    for subdir in _AGENT_SUBDIRS:
        subdir_path = agent_dir / subdir
        if not subdir_path.exists():
            subdir_path.mkdir(parents=True, exist_ok=True)
            if not bootstrapped:
                repaired.append(f"created directory: {subdir}")

    # Create template files (skip existing)
    for filename, template in _TEMPLATE_FILES.items():
        filepath = agent_dir / filename
        if not filepath.exists():
            filepath.write_text(template, encoding="utf-8")
            if not bootstrapped:
                repaired.append(f"created file: {filename}")

    # Load or create build_state.json
    state_path = agent_dir / "build_state.json"
    if state_path.exists():
        state = BuildState.load(state_path)
    else:
        state = BuildState.create_initial(str(root))
        state.save(state_path)
        if not bootstrapped:
            repaired.append("created file: build_state.json")

    # Log bootstrap event
    if bootstrapped:
        _write_log(agent_dir, "bootstrap", {
            "event": "bootstrap",
            "project_root": str(root),
            "created_dirs": _AGENT_SUBDIRS,
            "created_files": list(_TEMPLATE_FILES.keys()) + ["build_state.json"],
        })

    # Run readiness check
    readiness = _check_readiness(agent_dir)

    return EnsureProjectResult(
        agent_dir=agent_dir,
        state=state,
        bootstrapped=bootstrapped,
        repaired=repaired,
        readiness=readiness,
    )


def _check_readiness(agent_dir: Path) -> ReadinessResult:
    """Check if spec.md and stages.yaml are sufficiently defined."""
    missing: list[str] = []

    # Check spec.md
    spec_path = agent_dir / "spec.md"
    if spec_path.exists():
        spec_missing = _check_spec_readiness(spec_path)
        missing.extend(spec_missing)
    else:
        missing.append("spec.md: file is missing")

    # Check stages.yaml
    stages_path = agent_dir / "stages.yaml"
    if stages_path.exists():
        stages_missing = _check_stages_readiness(stages_path)
        missing.extend(stages_missing)
    else:
        missing.append("stages.yaml: file is missing")

    # Determine next action
    next_action: str | None = None
    if missing:
        spec_issues = [m for m in missing if m.startswith("spec.md:")]
        if spec_issues:
            next_action = (
                "Define project specification using "
                "update_spec_and_stages(change_summary='...', spec_patch={\"section_updates\": {...}})"
            )
        else:
            next_action = (
                "Define workflow stages using "
                "update_spec_and_stages(change_summary='...', stages_patch={\"add_stage\": {...}})"
            )

    return ReadinessResult(
        ready=len(missing) == 0,
        missing=missing,
        next_action=next_action,
    )


def _check_spec_readiness(spec_path: Path) -> list[str]:
    """Check spec.md for required sections with actual content."""
    missing: list[str] = []
    content = spec_path.read_text(encoding="utf-8")

    # Parse sections: find ## headers and their content
    sections = _parse_markdown_sections(content)

    # Check required sections
    # "Requirements" and "Must Have" are OR condition
    has_requirements = False
    for section_name in SPEC_REQUIRED_SECTIONS:
        if section_name in ("Requirements", "Must Have"):
            # OR condition — check later
            continue

        section_content = sections.get(section_name)
        if section_content is None:
            missing.append(f"spec.md: '{section_name}' section is missing")
        elif _is_placeholder_content(section_content):
            missing.append(
                f"spec.md: '{section_name}' section is empty or contains only placeholder text"
            )

    # OR check for Requirements / Must Have
    req_content = sections.get("Requirements")
    must_content = sections.get("Must Have")
    if req_content is not None and not _is_placeholder_content(req_content):
        has_requirements = True
    if must_content is not None and not _is_placeholder_content(must_content):
        has_requirements = True
    if not has_requirements:
        missing.append(
            "spec.md: 'Requirements' or 'Must Have' section is missing or empty"
        )

    return missing


def _check_stages_readiness(stages_path: Path) -> list[str]:
    """Check stages.yaml for valid stage definitions."""
    missing: list[str] = []

    try:
        data = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        missing.append(f"stages.yaml: YAML parse error: {e}")
        return missing

    if not isinstance(data, dict) or "stages" not in data:
        missing.append("stages.yaml: 'stages' key is missing")
        return missing

    stages = data["stages"]
    if not isinstance(stages, list) or len(stages) == 0:
        missing.append("stages.yaml: 'stages' must be a non-empty list")
        return missing

    required_fields = ["id", "name", "purpose"]
    list_fields = ["depends_on", "produces", "consumes"]

    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            missing.append(f"stages.yaml: stage at index {i} is not a mapping")
            continue

        stage_id = stage.get("id", f"index_{i}")

        for fld in required_fields:
            val = stage.get(fld)
            if not val or (isinstance(val, str) and not val.strip()):
                missing.append(f"stages.yaml: stage '{stage_id}' is missing '{fld}' field")

        for fld in list_fields:
            val = stage.get(fld)
            if val is None:
                missing.append(f"stages.yaml: stage '{stage_id}' is missing '{fld}' field")
            elif not isinstance(val, list):
                missing.append(
                    f"stages.yaml: stage '{stage_id}' field '{fld}' must be a list"
                )

    return missing


def _parse_markdown_sections(content: str) -> dict[str, str]:
    """Parse markdown into {section_name: section_content} for ## headers."""
    sections: dict[str, str] = {}
    header_pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)

    matches = list(header_pattern.finditer(content))
    for i, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        sections[name] = body

    return sections


def _is_placeholder_content(text: str) -> bool:
    """Check if section content is empty or only placeholder text."""
    if not text or not text.strip():
        return True

    stripped = text.strip()
    # Check if the entire content matches a placeholder pattern
    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern.fullmatch(stripped):
            return True

    # Check if it's just a single bullet prefix with nothing useful
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    return bool(all(ln == "-" or ln == "- " for ln in lines))


def _save_state(agent_dir: Path, state: BuildState) -> None:
    """Save build state to .agent/build_state.json."""
    state_path = agent_dir / "build_state.json"
    state.save(state_path)


def _write_log(agent_dir: Path, event_type: str, data: dict[str, Any]) -> None:
    """Write a log entry to .agent/logs/."""
    logs_dir = agent_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{event_type}_{timestamp}.json"
    log_entry = {
        "timestamp": _now_iso(),
        **data,
    }
    log_file.write_text(json.dumps(log_entry, indent=2, ensure_ascii=False), encoding="utf-8")


# Schema version for events.jsonl format evolution
_EVENTS_SCHEMA_VERSION = "1"


def _record_event(
    agent_dir: Path,
    event_type: str,
    summary: str,
    *,
    stage_id: str | None = None,
    diff_id: str | None = None,
    details: dict[str, Any] | None = None,
    artifacts: list[str] | None = None,
) -> str:
    """Append an event to history/events.jsonl.

    **Fail-soft**: never raises — if the write fails, a warning is logged
    and an empty string is returned.  Workflow tool callers must not
    depend on the event_id for correctness.

    Returns:
        The generated event_id, or ``""`` on write failure.
    """
    try:
        history_dir = agent_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        event_id = f"evt_{ts_str}_{uuid.uuid4().hex[:8]}"

        # Mandatory fields (schema guarantee)
        event: dict[str, Any] = {
            "schema_version": _EVENTS_SCHEMA_VERSION,
            "event_id": event_id,
            "ts": now.isoformat(),
            "event_type": event_type,
            "project_root": str(agent_dir.parent),
            "summary": summary,
        }

        # Optional fields — only written when provided
        if stage_id is not None:
            event["stage_id"] = stage_id
        if diff_id is not None:
            event["diff_id"] = diff_id
        if details:
            event["details"] = details
        if artifacts:
            event["artifacts"] = artifacts

        events_file = history_dir / "events.jsonl"
        with events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        return event_id
    except Exception:
        logger.warning("Failed to record event to events.jsonl", exc_info=True)
        return ""


def _readiness_to_dict(readiness: ReadinessResult) -> dict[str, Any]:
    """Convert ReadinessResult to dict for API responses."""
    return {
        "ready_for_implementation": readiness.ready,
        "missing_requirements": readiness.missing,
        "next_action": readiness.next_action,
    }


def _stage_summary(state: BuildState) -> dict[str, str]:
    """Return a summary of all stage statuses."""
    return {sid: ss.status for sid, ss in state.stages.items()}


# ---------------------------------------------------------------------------
# Gate state helpers
# ---------------------------------------------------------------------------


def _load_gate_state(agent_dir: Path) -> GateState:
    """Load gate state from .agent/gate_state.json."""
    return GateState.load(agent_dir / GATE_STATE_FILENAME)


def _save_gate_state(agent_dir: Path, gate: GateState) -> None:
    """Save gate state to .agent/gate_state.json."""
    gate.save(agent_dir / GATE_STATE_FILENAME)


def _update_gate_state(agent_dir: Path, **updates: Any) -> GateState:
    """Load gate state, apply updates, save, and return."""
    gate = _load_gate_state(agent_dir)
    for key, value in updates.items():
        if hasattr(gate, key):
            setattr(gate, key, value)
    _save_gate_state(agent_dir, gate)
    return gate


# ---------------------------------------------------------------------------
# CHANGELOG.md / DECISIONS.md helpers
# ---------------------------------------------------------------------------


def _append_changelog(
    agent_dir: Path,
    summary: str,
    rationale: str,
    scope: str,
    result: str = "",
) -> None:
    """Append entry to .agent/history/CHANGELOG.md.

    Fail-soft: logs warning on error, never raises.
    """
    try:
        history_dir = agent_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        changelog = history_dir / "CHANGELOG.md"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Build entry
        lines = [
            f"\n## {today}\n",
            f"\n### {summary}\n",
            f"- **Rationale:** {rationale}\n",
            f"- **Scope:** {scope}\n",
        ]
        if result:
            lines.append(f"- **Result:** {result}\n")

        # Create file with header if new
        if not changelog.exists():
            changelog.write_text("# Change Log\n", encoding="utf-8")

        with changelog.open("a", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        logger.warning("Failed to append to CHANGELOG.md", exc_info=True)


def _record_decision(
    agent_dir: Path,
    context: str,
    decision: str,
    alternatives: str,
    consequences: str,
) -> None:
    """Append ADR entry to .agent/history/DECISIONS.md.

    Fail-soft: logs warning on error, never raises.
    """
    try:
        history_dir = agent_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        decisions_file = history_dir / "DECISIONS.md"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Determine next ADR number
        adr_num = 1
        if decisions_file.exists():
            content = decisions_file.read_text(encoding="utf-8")
            # Count existing ADR headers
            import re as _re
            existing = _re.findall(r"## ADR-(\d+):", content)
            if existing:
                adr_num = max(int(n) for n in existing) + 1

        lines = [
            f"\n## ADR-{adr_num:03d}: {decision}\n",
            f"- **Date:** {today}\n",
            f"- **Context:** {context}\n",
            f"- **Decision:** {decision}\n",
            f"- **Alternatives:** {alternatives}\n",
            f"- **Consequences:** {consequences}\n",
        ]

        if not decisions_file.exists():
            decisions_file.write_text("# Architecture Decision Records\n", encoding="utf-8")

        with decisions_file.open("a", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        logger.warning("Failed to record decision to DECISIONS.md", exc_info=True)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def _take_snapshot(
    agent_dir: Path,
    event_id: str,
    *,
    scan_data: dict[str, Any] | None = None,
    scope: str | None = None,
) -> str | None:
    """Save lightweight snapshot to .agent/snapshots/.

    Fail-soft: returns None on error, never raises.

    Returns:
        Relative path to snapshot file, or None on failure.
    """
    try:
        snapshots_dir = agent_dir / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        snapshot: dict[str, Any] = {
            "event_id": event_id,
            "ts": now.isoformat(),
            "scope": scope,
            "snapshot_source": "scan_data" if scan_data else "none",
        }

        if scan_data:
            # Extract summary info from scan_data
            snapshot["node_count"] = scan_data.get("node_count", len(scan_data.get("nodes", [])))
            snapshot["scene_data"] = scan_data

        snapshot_file = snapshots_dir / f"snapshot_{event_id}.json"
        snapshot_file.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return str(snapshot_file.relative_to(agent_dir))
    except Exception:
        logger.warning("Failed to save snapshot", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Mermaid graph helpers
# ---------------------------------------------------------------------------


def _generate_stage_graph(agent_dir: Path, event_id: str) -> str | None:
    """Generate Mermaid graph of stages from stages.yaml + build_state.

    Fail-soft: returns None on error, never raises.

    Returns:
        Relative path to .mmd file, or None on failure.
    """
    try:
        stages_path = agent_dir / "stages.yaml"
        if not stages_path.exists():
            return None

        data = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "stages" not in data:
            return None

        stages_list = data["stages"]
        if not isinstance(stages_list, list) or not stages_list:
            return None

        # Load build state for status info
        state_path = agent_dir / "build_state.json"
        state = BuildState.load(state_path) if state_path.exists() else BuildState.create_initial("")

        lines = ["flowchart TD"]
        for stage in stages_list:
            if not isinstance(stage, dict):
                continue
            sid = stage.get("id", "unknown")
            name = stage.get("name", sid)
            mode = stage.get("mode", "normal")
            stage_state = state.stages.get(sid)
            status = stage_state.status if stage_state else "pending"
            lines.append(f'    {sid}["{sid}: {name}<br/>{mode} | {status}"]')

        # Add dependency edges
        for stage in stages_list:
            if not isinstance(stage, dict):
                continue
            sid = stage.get("id", "unknown")
            for dep in stage.get("depends_on", []):
                lines.append(f"    {dep} --> {sid}")

        graphs_dir = agent_dir / "graphs"
        graphs_dir.mkdir(parents=True, exist_ok=True)
        graph_file = graphs_dir / f"stages_{event_id}.mmd"
        graph_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(graph_file.relative_to(agent_dir))
    except Exception:
        logger.warning("Failed to generate stage graph", exc_info=True)
        return None


def _generate_network_graph(
    agent_dir: Path, event_id: str, scan_data: dict[str, Any]
) -> str | None:
    """Generate Mermaid graph of node connections from scan_data.

    Fail-soft: returns None on error, never raises.

    Returns:
        Relative path to .mmd file, or None on failure.
    """
    try:
        nodes = scan_data.get("nodes", [])
        connections = scan_data.get("connections", [])
        if not nodes and not connections:
            return None

        lines = ["flowchart TD"]

        # Add nodes
        for node in nodes:
            if isinstance(node, dict):
                name = node.get("name", "unknown")
                node_type = node.get("type", "unknown")
                lines.append(f'    {name}["{name}<br/>{node_type}"]')

        # Add connections
        for conn in connections:
            if isinstance(conn, dict):
                src = conn.get("src", conn.get("from", ""))
                dst = conn.get("dst", conn.get("to", ""))
                if src and dst:
                    # Use simple node names (last part of path)
                    src_name = src.rsplit("/", 1)[-1] if "/" in str(src) else str(src)
                    dst_name = dst.rsplit("/", 1)[-1] if "/" in str(dst) else str(dst)
                    lines.append(f"    {src_name} --> {dst_name}")

        if len(lines) <= 1:
            return None

        graphs_dir = agent_dir / "graphs"
        graphs_dir.mkdir(parents=True, exist_ok=True)
        graph_file = graphs_dir / f"network_{event_id}.mmd"
        graph_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(graph_file.relative_to(agent_dir))
    except Exception:
        logger.warning("Failed to generate network graph", exc_info=True)
        return None
