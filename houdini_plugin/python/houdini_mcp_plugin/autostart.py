"""Auto-start hook for the Houdini MCP RPyC listener.

Called from scripts/123.py and scripts/456.py (found via HOUDINI_PATH when
the houdini_mcp package is installed). Idempotent and fail-soft: it must
never break Houdini startup or scene loads.

Environment variables:
    HOUDINI_MCP_AUTOSTART  "0" / "false" / "off" disables auto-start (default: on)
    HOUDINI_MCP_HOST       bind address (default: 127.0.0.1, localhost only)
    HOUDINI_MCP_PORT       listen port (default: 18811)
"""

import os

DEFAULT_PORT = 18811

_announced = False


def ensure_started() -> dict:
    """Start the RPyC listener if appropriate. Safe to call repeatedly."""
    global _announced

    if os.environ.get("HOUDINI_MCP_AUTOSTART", "1").strip().lower() in (
        "0",
        "false",
        "off",
    ):
        return {"status": "disabled", "message": "HOUDINI_MCP_AUTOSTART is off"}

    # Only run inside a live Houdini session, and only with UI (skip hython
    # batch jobs so render farms don't open listeners on random machines).
    try:
        import hou
    except ImportError:
        return {"status": "skipped", "message": "hou module not available"}

    try:
        if not hou.isUIAvailable():
            return {"status": "skipped", "message": "non-interactive session"}
    except AttributeError:
        pass  # very old builds: assume interactive

    from .remote import is_hrpyc_running, start_hrpyc_server

    if is_hrpyc_running():
        return {"status": "already_running"}

    host = os.environ.get("HOUDINI_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("HOUDINI_MCP_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT

    status = start_hrpyc_server(port=port, host=host)

    if not _announced:
        _announced = True
        print(
            "[houdini_mcp] autostart: {0} -- {1}".format(
                status.get("status", "?"), status.get("message", "")
            )
        )

    return status
