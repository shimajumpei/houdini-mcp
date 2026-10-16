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

Check 0: Rules delivery. The first houdini-mcp tool call of a Claude Code
session is denied with an instruction to read the rules file
(rules/HOUDINI_RULES.md) with the Read tool and retry. Delivery is recorded
per session_id under %LOCALAPPDATA%/houdini-mcp/rules-ack (fallback: the temp
dir), never in the project directory. The rules file is never copied.

Shipped in the houdini-mcp repo (hooks/) and registered by the Claude Code
plugin manifest (hooks/hooks.json). Tool names arrive either as
mcp__houdini-mcp__<tool> (server registered directly) or as
mcp__plugin_<plugin>_houdini-mcp__<tool> (server bundled in the plugin).
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# mcp__houdini-mcp__<tool> or mcp__plugin_<plugin-name>_houdini-mcp__<tool>
TOOL_NAME_RE = re.compile(r"^mcp__(?:plugin_[A-Za-z0-9_-]+_)?houdini-mcp__(?P<tool>.+)$")


def short_tool_name(tool_name):
    """Return the bare houdini-mcp tool name, or None for any other tool."""
    match = TOOL_NAME_RE.match(tool_name or "")
    return match.group("tool") if match else None


RULES_FILENAME = "HOUDINI_RULES.md"


def canonical_rules_path():
    """Locate the canonical Houdini rules file that Claude must read.

    Order: HOUDINI_CLAUDE_MD_CANONICAL env override (used as-is), then
    <plugin root>/rules/HOUDINI_RULES.md via CLAUDE_PLUGIN_ROOT, then rules/
    next to this script's parent directory, then the legacy
    ~/.claude/houdini/CLAUDE.md. Returns the first existing candidate, or the
    first candidate if none exist.
    """
    override = os.environ.get("HOUDINI_CLAUDE_MD_CANONICAL")
    if override:
        return Path(override)
    candidates = []
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        candidates.append(Path(plugin_root) / "rules" / RULES_FILENAME)
    candidates.append(Path(__file__).resolve().parent.parent / "rules" / RULES_FILENAME)
    candidates.append(Path.home() / ".claude" / "houdini" / "CLAUDE.md")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _same_path(a, b):
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, ValueError):
        return False


def rules_ack_dirs(forbidden_dirs=()):
    """Directories that may hold per-session rules acknowledgements.

    %LOCALAPPDATA%/houdini-mcp/rules-ack first, then <temp>/houdini-mcp/rules-ack.
    A base directory equal to one of forbidden_dirs (the cwd / project dir)
    is skipped, so nothing is ever written there.
    """
    bases = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        bases.append(Path(local))
    bases.append(Path(tempfile.gettempdir()))
    dirs = []
    for base in bases:
        if any(_same_path(base, f) for f in forbidden_dirs if f):
            continue
        dirs.append(base / "houdini-mcp" / "rules-ack")
    return dirs


def rules_ack_name(session_id):
    """File name for a session's acknowledgement (session ids are UUIDs)."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        return session_id
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def rules_acknowledged(session_id, ack_dirs):
    name = rules_ack_name(session_id)
    return any((d / name).exists() for d in ack_dirs)


def record_rules_ack(session_id, rules_path, ack_dirs):
    """Record that the rules were delivered in this session. Returns success."""
    payload = json.dumps({
        "session_id": session_id,
        "rules_path": str(rules_path),
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    })
    name = rules_ack_name(session_id)
    for ack_dir in ack_dirs:
        try:
            ack_dir.mkdir(parents=True, exist_ok=True)
            (ack_dir / name).write_text(payload, encoding="utf-8")
            return True
        except OSError:
            continue
    return False


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
    # Check 0: Rules delivery, once per Claude Code session
    # (runs for ALL houdini tools, including read-only ones). The first call
    # of a session is denied with an instruction to Read the rules file; the
    # delivery is recorded per session_id outside the project directory.
    # Fail-open (no deny, nothing written) when session_id is missing, the
    # rules file cannot be found, or the acknowledgement cannot be recorded.
    # ------------------------------------------------------------------
    session_id = hook_input.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        ack_dirs = rules_ack_dirs(forbidden_dirs=(project_dir, os.getcwd()))
        if not rules_acknowledged(session_id, ack_dirs):
            rules_path = canonical_rules_path()
            if rules_path.exists():
                rules_path = rules_path.resolve()
                if record_rules_ack(session_id, rules_path, ack_dirs):
                    deny(
                        "Houdini rules are not loaded in this session yet. "
                        f"Read the rules file with the Read tool: {rules_path} . "
                        "Follow those rules for all Houdini work in this session, "
                        f"then retry this {short_name} call."
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
