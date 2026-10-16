"""Houdini MCP auto-start (runs on every scene load / new scene).

Found via HOUDINI_PATH/scripts when the houdini_mcp package is installed.
ensure_started() is idempotent, so repeated invocations are cheap no-ops.
"""

try:
    from houdini_mcp_plugin import autostart

    autostart.ensure_started()
except Exception as e:  # never break scene loads
    print("[houdini_mcp] autostart failed in 456.py: {0}".format(e))
