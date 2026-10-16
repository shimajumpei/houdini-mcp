"""update_spec_and_stages tool — Atomic spec/stages change management."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import yaml

from ._common import (
    _append_changelog,
    _ensure_project,
    _record_decision,
    _record_event,
    _save_state,
    _write_log,
    handle_workflow_errors,
)


@handle_workflow_errors("update_spec_and_stages")
def update_spec_and_stages(
    change_summary: str,
    spec_patch: dict[str, Any] | None = None,
    stages_patch: dict[str, Any] | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Update spec.md and/or stages.yaml with change tracking.

    Supports atomic updates with automatic changelog, event recording,
    and optional ADR (Architecture Decision Record) creation.

    Args:
        change_summary: Human-readable summary of the change.
        spec_patch: Optional spec.md modifications:
            - changelog_entry: {reason: str, impact: str} — appends to Change Log table
            - section_updates: {section_name: new_content} — replaces section content
            - dod_append: str — appends line to Definition of Done
            - decision: {context, decision, alternatives, consequences} — records ADR
        stages_patch: Optional stages.yaml modifications:
            - add_stage: {id, name, purpose, ...} — appends new stage
            - update_stage: {id, ...fields_to_update} — updates existing stage fields
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Status with files_updated list and change_summary.
    """
    result = _ensure_project(project_root)
    agent_dir = result.agent_dir
    state = result.state

    files_updated: list[str] = []
    event_details: dict[str, Any] = {"change_summary": change_summary}

    # Apply spec_patch
    if spec_patch:
        spec_result = _apply_spec_patch(agent_dir, spec_patch)
        if spec_result:
            files_updated.append("spec.md")
            event_details["spec_changes"] = spec_result

        # Handle decision recording
        decision = spec_patch.get("decision")
        if isinstance(decision, dict):
            _record_decision(
                agent_dir,
                context=decision.get("context", ""),
                decision=decision.get("decision", ""),
                alternatives=decision.get("alternatives", ""),
                consequences=decision.get("consequences", ""),
            )
            event_details["decision_recorded"] = True

    # Apply stages_patch
    if stages_patch:
        stages_result = _apply_stages_patch(agent_dir, stages_patch)
        if stages_result:
            files_updated.append("stages.yaml")
            event_details["stages_changes"] = stages_result

    # Update build state timestamp and file hashes
    if files_updated:
        state.last_updated = datetime.now(timezone.utc).isoformat()
        if "spec.md" in files_updated:
            try:
                state.spec_hash = hashlib.md5(
                    (agent_dir / "spec.md").read_bytes()
                ).hexdigest()
            except OSError:
                pass
        if "stages.yaml" in files_updated:
            try:
                state.stages_hash = hashlib.md5(
                    (agent_dir / "stages.yaml").read_bytes()
                ).hexdigest()
            except OSError:
                pass
        _save_state(agent_dir, state)

    # Write log
    _write_log(agent_dir, "update_spec_and_stages", {
        "event": "update_spec_and_stages",
        "change_summary": change_summary,
        "files_updated": files_updated,
    })

    # Record events
    if "spec.md" in files_updated:
        _record_event(
            agent_dir, "spec_updated", f"Spec updated: {change_summary}",
            details=event_details.get("spec_changes"),
        )
    if "stages.yaml" in files_updated:
        _record_event(
            agent_dir, "stages_updated", f"Stages updated: {change_summary}",
            details=event_details.get("stages_changes"),
        )

    # Append to CHANGELOG.md
    if files_updated:
        _append_changelog(
            agent_dir,
            summary=change_summary,
            rationale=spec_patch.get("changelog_entry", {}).get("reason", change_summary)
            if spec_patch else change_summary,
            scope=", ".join(files_updated),
        )

    return {
        "status": "success",
        "files_updated": files_updated,
        "change_summary": change_summary,
        "details": event_details,
    }


def _apply_spec_patch(agent_dir: Any, spec_patch: dict[str, Any]) -> dict[str, Any] | None:
    """Apply modifications to spec.md. Returns change details or None."""
    spec_path = agent_dir / "spec.md"
    if not spec_path.exists():
        return None

    content = spec_path.read_text(encoding="utf-8")
    changes: dict[str, Any] = {}

    # Handle changelog_entry
    changelog_entry = spec_patch.get("changelog_entry")
    if isinstance(changelog_entry, dict):
        reason = changelog_entry.get("reason", "")
        impact = changelog_entry.get("impact", "")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_row = f"| {today} | {reason} | {reason} | {impact} |\n"

        # Find the Change Log table and append row
        # Look for the table header pattern, then append after the last |...| line
        table_pattern = re.compile(
            r"(\| Date \| Change \| Reason \| Impact \|\n\|[-|]+\|\n)((?:\|.*\|\n)*)",
            re.MULTILINE,
        )
        match = table_pattern.search(content)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + new_row + content[insert_pos:]
            changes["changelog_entry_added"] = True
        else:
            # No table found — try appending to Change Log section
            cl_header = re.search(r"^## Change Log\s*$", content, re.MULTILINE)
            if cl_header:
                # Append table + row after the header
                table = (
                    "\n| Date | Change | Reason | Impact |\n"
                    "|------|--------|--------|--------|\n"
                    + new_row
                )
                insert_pos = cl_header.end()
                content = content[:insert_pos] + table + content[insert_pos:]
                changes["changelog_table_created"] = True

    # Handle section_updates
    section_updates = spec_patch.get("section_updates")
    if isinstance(section_updates, dict):
        for section_name, new_content in section_updates.items():
            content = _replace_section(content, section_name, new_content)
            changes.setdefault("sections_updated", []).append(section_name)

    # Handle dod_append
    dod_append = spec_patch.get("dod_append")
    if isinstance(dod_append, str) and dod_append.strip():
        content = _append_to_section(content, "Definition of Done", dod_append.strip())
        changes["dod_appended"] = True

    if changes:
        spec_path.write_text(content, encoding="utf-8")
        return changes

    return None


def _apply_stages_patch(agent_dir: Any, stages_patch: dict[str, Any]) -> dict[str, Any] | None:
    """Apply modifications to stages.yaml. Returns change details or None."""
    stages_path = agent_dir / "stages.yaml"
    if not stages_path.exists():
        return None

    try:
        data = yaml.safe_load(stages_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None

    if not isinstance(data, dict):
        data = {"stages": []}
    if "stages" not in data or not isinstance(data["stages"], list):
        data["stages"] = []

    changes: dict[str, Any] = {}

    # Handle add_stage
    add_stage = stages_patch.get("add_stage")
    if isinstance(add_stage, dict) and "id" in add_stage:
        # Ensure required fields have defaults
        new_stage = {
            "id": add_stage["id"],
            "name": add_stage.get("name", add_stage["id"]),
            "purpose": add_stage.get("purpose", ""),
            "depends_on": add_stage.get("depends_on", []),
            "produces": add_stage.get("produces", []),
            "consumes": add_stage.get("consumes", []),
        }
        # Copy optional fields
        for opt_field in ("mode", "notes", "replaces", "parallel_with", "acceptance"):
            if opt_field in add_stage:
                new_stage[opt_field] = add_stage[opt_field]
        data["stages"].append(new_stage)
        changes["stage_added"] = add_stage["id"]

    # Handle update_stage
    update_stage = stages_patch.get("update_stage")
    if isinstance(update_stage, dict) and "id" in update_stage:
        stage_id = update_stage["id"]
        updated = False
        for stage in data["stages"]:
            if isinstance(stage, dict) and stage.get("id") == stage_id:
                for key, value in update_stage.items():
                    if key != "id":
                        stage[key] = value
                updated = True
                break
        if updated:
            changes["stage_updated"] = stage_id
        else:
            changes["stage_not_found"] = stage_id

    if changes and "stage_not_found" not in changes:
        stages_path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return changes

    return changes if changes else None


def _replace_section(content: str, section_name: str, new_content: str) -> str:
    """Replace the content of a ## section in markdown."""
    header_pattern = re.compile(
        rf"^(## {re.escape(section_name)}\s*\n)(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = header_pattern.search(content)
    if match:
        replacement = match.group(1) + "\n" + new_content.strip() + "\n\n"
        content = content[:match.start()] + replacement + content[match.end():]
    return content


def _append_to_section(content: str, section_name: str, text: str) -> str:
    """Append text to a ## section, replacing placeholder content if present."""
    header_pattern = re.compile(
        rf"^(## {re.escape(section_name)}\s*\n)(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = header_pattern.search(content)
    if match:
        body = match.group(2).strip()
        # Check if body is placeholder
        placeholder = re.compile(r"^\s*\(.*\)\s*$", re.DOTALL)
        if placeholder.match(body):
            new_body = f"\n- {text}\n\n"
        else:
            new_body = match.group(2).rstrip() + f"\n- {text}\n\n"
        replacement = match.group(1) + new_body
        content = content[:match.start()] + replacement + content[match.end():]
    return content
