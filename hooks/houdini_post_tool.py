#!/usr/bin/env python3
"""PostToolUse hook for Houdini MCP error tracking and docs detection.

Reads tool result from stdin. Tracks errors and docs references in
.agent/gate_state.json to enforce RULE 7 (docs reference after failures).

Shipped in the houdini-mcp repo (hooks/) and registered by the Claude Code
plugin manifest (hooks/hooks.json).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# mcp__houdini-mcp__<tool> or mcp__plugin_<plugin-name>_houdini-mcp__<tool>
TOOL_NAME_RE = re.compile(r"^mcp__(?:plugin_[A-Za-z0-9_-]+_)?houdini-mcp__(?P<tool>.+)$")

# Tools that count as "documentation referenced"
DOC_TOOLS = {
    "get_parameter_schema",
    "get_node_info",
    "query_houdini_docs",
    "get_houdini_help",
    "search_vex_function",
    "get_vex_details",
}


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_output = hook_input.get("tool_output", {})

    # Strip MCP prefix (direct or plugin-bundled server)
    short_name = tool_name
    match = TOOL_NAME_RE.match(tool_name)
    if match:
        short_name = match.group("tool")

    # Resolve project directory
    project_dir = hook_input.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    gate_path = Path(project_dir) / ".agent" / "gate_state.json"

    if not gate_path.exists():
        sys.exit(0)

    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        sys.exit(0)

    modified = False

    # Parse tool_output to check for errors
    result_data = tool_output
    if isinstance(result_data, str):
        try:
            result_data = json.loads(result_data)
        except (json.JSONDecodeError, Exception):
            result_data = {}

    # Track errors
    is_error = isinstance(result_data, dict) and result_data.get("status") == "error"
    if is_error:
        error_counts = gate.get("error_counts", {})
        error_counts[short_name] = error_counts.get(short_name, 0) + 1
        gate["error_counts"] = error_counts
        modified = True

    # Track successful doc tool calls
    if short_name in DOC_TOOLS and not is_error:
        gate["docs_referenced"] = True
        gate["error_counts"] = {}  # Reset all error counts
        modified = True

        # Also track schema_checked for get_parameter_schema
        if short_name == "get_parameter_schema":
            node_path = ""
            if isinstance(tool_input, dict):
                node_path = tool_input.get("node_path", "")
            if node_path:
                schema_checked = gate.get("schema_checked", {})
                schema_checked[node_path] = True
                gate["schema_checked"] = schema_checked

            # Also populate verified_params with parameter names
            if isinstance(result_data, dict):
                params = result_data.get("parameters", [])
                param_names = [p["name"] for p in params if isinstance(p, dict) and p.get("name")]
                if param_names and node_path:
                    verified_params = gate.get("verified_params", {})
                    existing_for_node = set(verified_params.get(node_path, []))
                    existing_for_node.update(param_names)
                    verified_params[node_path] = sorted(existing_for_node)
                    gate["verified_params"] = verified_params

    # Track verified node types from list_node_types
    if short_name == "list_node_types" and not is_error:
        node_types_list = result_data.get("node_types", []) if isinstance(result_data, dict) else []
        if node_types_list:
            existing = set(gate.get("verified_node_types", []))
            for nt in node_types_list:
                name = nt.get("name", "") if isinstance(nt, dict) else ""
                if name:
                    existing.add(name)
            gate["verified_node_types"] = sorted(existing)
            modified = True

    # Save if modified
    if modified:
        gate["last_updated"] = datetime.now(timezone.utc).isoformat()
        tmp_path = gate_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(gate, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(gate_path)
        except Exception:
            pass  # Best-effort write

    sys.exit(0)


if __name__ == "__main__":
    main()
