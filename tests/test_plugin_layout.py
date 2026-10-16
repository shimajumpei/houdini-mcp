"""Tests for the Claude Code plugin wiring in this repository.

Covers the plugin-bundled tool name prefix (mcp__plugin_<plugin>_<server>__<tool>)
in the hooks and the agreement between plugin.json, hooks/hooks.json, and the
canonical rules file.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
GATE_SCRIPT = REPO_ROOT / "hooks" / "houdini_gate.py"
POST_SCRIPT = REPO_ROOT / "hooks" / "houdini_post_tool.py"
RULES_FILE = REPO_ROOT / "rules" / "CLAUDE.md"
SERVER_NAME = "houdini-mcp"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _plugin_prefix() -> str:
    plugin_name = _load(PLUGIN_JSON)["name"]
    return f"mcp__plugin_{plugin_name}_{SERVER_NAME}__"


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("HOUDINI_CLAUDE_MD_CANONICAL", "CLAUDE_PLUGIN_ROOT")}
    env.update(extra)
    return env


def _run(script: Path, payload: dict, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=_env(**env),
    )


class TestManifests:
    def test_marketplace_lists_repo_root_as_plugin(self):
        marketplace = _load(MARKETPLACE_JSON)
        entries = {p["name"]: p for p in marketplace["plugins"]}
        plugin_name = _load(PLUGIN_JSON)["name"]
        assert entries[plugin_name]["source"] == "./"

    def test_mcp_server_uses_repo_venv(self):
        server = _load(PLUGIN_JSON)["mcpServers"][SERVER_NAME]
        assert server["command"] == "${CLAUDE_PLUGIN_ROOT}/.venv/Scripts/python.exe"
        assert server["args"] == ["-m", "houdini_mcp"]
        assert server["env"] == {
            "MCP_TRANSPORT": "stdio",
            "HOUDINI_HOST": "localhost",
            "HOUDINI_PORT": "18811",
            "FASTMCP_LOG_LEVEL": "ERROR",
        }

    def test_hook_matchers_match_plugin_tool_names(self):
        hooks = _load(HOOKS_JSON)["hooks"]
        prefix = _plugin_prefix()
        for event in ("PreToolUse", "PostToolUse"):
            entries = hooks[event]
            assert len(entries) == 1
            matcher = re.compile(entries[0]["matcher"])
            assert matcher.search(prefix + "create_node")
            assert matcher.search(prefix + "get_scene_info")
            assert not matcher.search("mcp__other-server__create_node")
            handler = entries[0]["hooks"][0]
            assert handler["command"] == "${CLAUDE_PLUGIN_ROOT}/.venv/Scripts/python.exe"
            script = handler["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", str(REPO_ROOT))
            assert Path(script).exists()

    def test_rules_file_present(self):
        assert RULES_FILE.exists()


class TestPluginPrefixedToolNames:
    def test_gate_denies_change_tool_with_plugin_prefix(self, tmp_path: Path):
        result = _run(GATE_SCRIPT, {
            "tool_name": _plugin_prefix() + "create_node",
            "tool_input": {"node_type": "geo"},
            "cwd": str(tmp_path),
        }, CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_gate_allows_read_only_tool_and_copies_rules_from_plugin_root(self, tmp_path: Path):
        result = _run(GATE_SCRIPT, {
            "tool_name": _plugin_prefix() + "get_scene_info",
            "tool_input": {},
            "cwd": str(tmp_path),
        }, CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert (tmp_path / "CLAUDE.md").read_bytes() == RULES_FILE.read_bytes()

    def test_gate_copies_rules_without_plugin_root(self, tmp_path: Path):
        result = _run(GATE_SCRIPT, {
            "tool_name": _plugin_prefix() + "get_scene_info",
            "tool_input": {},
            "cwd": str(tmp_path),
        })
        assert result.returncode == 0
        assert (tmp_path / "CLAUDE.md").read_bytes() == RULES_FILE.read_bytes()

    def test_gate_ignores_other_servers(self, tmp_path: Path):
        result = _run(GATE_SCRIPT, {
            "tool_name": "mcp__plugin_other_other-server__create_node",
            "tool_input": {},
            "cwd": str(tmp_path),
        }, CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_post_hook_records_short_tool_name(self, tmp_path: Path):
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "gate_state.json").write_text(
            json.dumps({"workflow_initialized": True, "error_counts": {}}), encoding="utf-8"
        )
        result = _run(POST_SCRIPT, {
            "tool_name": _plugin_prefix() + "create_node",
            "tool_input": {"node_type": "geo"},
            "tool_output": {"status": "error", "message": "boom"},
            "cwd": str(tmp_path),
        })
        assert result.returncode == 0
        state = json.loads((agent_dir / "gate_state.json").read_text(encoding="utf-8"))
        assert state["error_counts"] == {"create_node": 1}
