#!/usr/bin/env python3
"""PreToolUse hook for Houdini MCP architectural enforcement.

Reads tool invocation from stdin (JSON with tool_name, tool_input).
Checks .agent/gate_state.json for preconditions.
Outputs structured JSON to deny, or exits 0 silently to allow.

DENY-BY-DEFAULT: the hook matcher sends ALL houdini-mcp tool calls here.
Read-only and workflow tools pass via an explicit allowlist;
everything else (including newly added server tools) is treated as a
change tool and must satisfy the gate checks. New mutating tools are
therefore gated automatically without touching the hook configuration.

Check 0: Ensures CLAUDE.md exists in project directory (auto-copy from canonical).

Shipped in the houdini-mcp repo (hooks/) and registered by the Claude Code
plugin manifest (hooks/hooks.json). Tool names arrive either as
mcp__houdini-mcp__<tool> (server registered directly) or as
mcp__plugin_<plugin>_houdini-mcp__<tool> (server bundled in the plugin).
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

# mcp__houdini-mcp__<tool> or mcp__plugin_<plugin-name>_houdini-mcp__<tool>
TOOL_NAME_RE = re.compile(r"^mcp__(?:plugin_[A-Za-z0-9_-]+_)?houdini-mcp__(?P<tool>.+)$")


def short_tool_name(tool_name):
    """Return the bare houdini-mcp tool name, or None for any other tool."""
    match = TOOL_NAME_RE.match(tool_name or "")
    return match.group("tool") if match else None


def canonical_rules_path():
    """Locate the canonical rules file (CLAUDE.md) to copy into projects.

    Order: HOUDINI_CLAUDE_MD_CANONICAL env override (used as-is), then
    <plugin root>/rules/CLAUDE.md via CLAUDE_PLUGIN_ROOT, then rules/ next to
    this script's parent directory, then the legacy ~/.claude/houdini/CLAUDE.md.
    Returns the first existing candidate, or the first candidate if none exist.
    """
    override = os.environ.get("HOUDINI_CLAUDE_MD_CANONICAL")
    if override:
        return Path(override)
    candidates = []
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        candidates.append(Path(plugin_root) / "rules" / "CLAUDE.md")
    candidates.append(Path(__file__).resolve().parent.parent / "rules" / "CLAUDE.md")
    candidates.append(Path.home() / ".claude" / "houdini" / "CLAUDE.md")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]

# Read-only tools: inspection, rendering, help. Never modify the scene.
READ_ONLY_TOOLS = {
    "get_scene_info",
    "serialize_scene",
    "get_node_info",
    "list_children",
    "find_nodes",
    "get_geo_summary",
    "get_parameter_schema",
    "list_node_types",
    "ping_houdini",
    "check_connection",
    "doctor",
    "render_viewport",
    "render_quad_view",
    "capture_pane_screenshot",
    "list_visible_panes",
    "capture_multiple_panes",
    "render_node_network",
    "find_error_nodes",
    "get_houdini_help",
    "manage_cache",
    "get_summarization_status",
    "get_render_settings",
    "list_render_nodes",
    "get_material_info",
    "get_crowd_info",
    "validate_crowd_setup",
    "get_last_scene_diff",
}

# Workflow tools: they ARE the gate mechanism, so they must stay callable.
WORKFLOW_TOOLS = {
    "ensure_project",
    "read_project",
    "plan_stage",
    "propose_diff",
    "approve_diff",
    "record_scan",
    "validate_stage",
    "update_stage",
    "update_spec_and_stages",
    "record_norm_check",
}

ALLOWED_TOOLS = READ_ONLY_TOOLS | WORKFLOW_TOOLS


def main():
    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        # Can't parse input -- fail-open (allow)
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only houdini-mcp tools are subject to this gate
    short_name = short_tool_name(tool_name)
    if short_name is None:
        sys.exit(0)

    # Resolve project directory from hook input or environment
    project_dir = hook_input.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    # ------------------------------------------------------------------
    # Check 0: Ensure CLAUDE.md exists in project directory
    # (runs for ALL houdini tools, including read-only ones, so the rules
    # land in the project as soon as Houdini work starts)
    # ------------------------------------------------------------------
    canonical = canonical_rules_path()
    target = Path(project_dir) / "CLAUDE.md"

    if not target.exists() and canonical.exists():
        try:
            shutil.copy2(str(canonical), str(target))
        except OSError:
            if short_name not in ALLOWED_TOOLS:
                deny(
                    f"CLAUDE.md is missing and auto-copy failed. "
                    f"Manually copy {canonical} to {target}.",
                    "ensure_project",
                )
                return

    # ------------------------------------------------------------------
    # Allowlist: read-only and workflow tools pass without gate checks
    # ------------------------------------------------------------------
    if short_name in ALLOWED_TOOLS:
        sys.exit(0)

    # ------------------------------------------------------------------
    # Everything below is treated as a CHANGE tool (deny-by-default)
    # ------------------------------------------------------------------
    gate_path = Path(project_dir) / ".agent" / "gate_state.json"

    # If no gate state file exists, deny modification tools and require ensure_project
    if not gate_path.exists():
        deny(
            "Workflow not initialized (no .agent/gate_state.json). "
            "Call ensure_project() first, then read_project(target='all').",
            "ensure_project",
        )
        return

    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        # Can't read gate state -- fail-open
        sys.exit(0)

    # Check 1: workflow_initialized
    if not gate.get("workflow_initialized", False):
        deny(
            "Workflow not initialized. "
            "Call ensure_project() first, then read_project(target='all').",
            "ensure_project",
        )
        return

    # Check 2: ready_for_implementation
    if not gate.get("ready_for_implementation", False):
        deny(
            "Project not ready for implementation. "
            "Fill in spec.md and stages.yaml, then call ensure_project() to re-check.",
            "ensure_project",
        )
        return

    # Check 3: active_stage_in_progress
    if not gate.get("active_stage_in_progress", False):
        deny(
            "No active stage in progress. "
            "Call plan_stage(stage_id, description) to start a stage.",
            "plan_stage",
        )
        return

    # Check 4: diff_approved
    if not gate.get("diff_approved", False):
        deny(
            "No approved diff. "
            "Call propose_diff(stage_id, diff_spec) to create a diff proposal, "
            "present it to the user, then call approve_diff(diff_id) after approval.",
            "propose_diff",
        )
        return

    # Check 5: create_node requires verified node_type (fail-soft)
    if short_name == "create_node":
        node_type = tool_input.get("node_type", "")
        verified_types = gate.get("verified_node_types", [])
        if node_type and verified_types and node_type not in verified_types:
            deny(
                f"Node type '{node_type}' is not verified. "
                f"Call list_node_types(category='Sop', name_filter='{node_type}') first.",
                "list_node_types",
            )
            return

    # Check 6: set_parameter requires schema check + param-level verification
    # (safe_set_parameter does its own schema validation, so only raw
    # set_parameter is checked here)
    if short_name == "set_parameter":
        node_path = tool_input.get("node_path", "")
        param_name = tool_input.get("param_name", "")

        # 6a: node-level schema check (existing behavior preserved)
        schema_checked = gate.get("schema_checked", {})
        if not schema_checked.get(node_path, False):
            deny(
                f"Schema not checked for node '{node_path}'. "
                "Use safe_set_parameter() instead, or call "
                "get_parameter_schema(node_path, include_description_ja=True) first.",
                "safe_set_parameter",
            )
            return

        # 6b: param-level verification (fail-soft)
        verified_params = gate.get("verified_params", {})
        node_params = verified_params.get(node_path, [])
        if param_name and node_params and param_name not in node_params:
            deny(
                f"Parameter '{param_name}' is not verified on node '{node_path}'. "
                f"Call get_parameter_schema(node_path='{node_path}') and choose a valid param_name. "
                f"Verified: {node_params[:15]}{'...' if len(node_params) > 15 else ''}",
                "get_parameter_schema",
            )
            return

    # Check 7: Error count / docs reference enforcement
    error_counts = gate.get("error_counts", {})
    docs_referenced = gate.get("docs_referenced", False)
    tool_error_count = error_counts.get(short_name, 0)
    if tool_error_count >= 2 and not docs_referenced:
        deny(
            f"Tool '{short_name}' has failed {tool_error_count} times consecutively. "
            "Reference documentation first: call get_parameter_schema(), "
            "get_node_info(), or query_houdini_docs() before retrying.",
            "get_parameter_schema",
        )
        return

    # All checks passed -- allow
    sys.exit(0)


def deny(reason: str, next_tool: str = ""):
    """Output structured deny decision and exit."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    # Also include next_tool suggestion in the reason for clarity
    if next_tool:
        output["hookSpecificOutput"]["permissionDecisionReason"] += (
            f" [Next: call {next_tool}()]"
        )
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
