"""Houdini MCP auto-start (runs once when Houdini starts with no scene).

Found via HOUDINI_PATH/scripts when the houdini_mcp package is installed.
Note: Houdini runs only the FIRST 123.py found on the path; 456.py (which
runs on every scene load/new) is the backup entry point.
"""

try:
    from houdini_mcp_plugin import autostart

    autostart.ensure_started()
except Exception as e:  # never break Houdini startup
    print("[houdini_mcp] autostart failed in 123.py: {0}".format(e))
