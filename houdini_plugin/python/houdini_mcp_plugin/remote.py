"""Remote mode for Houdini MCP Plugin.

This module provides an RPyC listener inside Houdini, allowing external
MCP servers to connect and drive the `hou` module remotely.

By default the listener binds to 127.0.0.1 (localhost only). RPyC classic
mode is unauthenticated remote code execution -- never expose it beyond
localhost unless you fully trust the network. To allow LAN access, pass
host="0.0.0.0" explicitly (or set HOUDINI_MCP_HOST).
"""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("houdini_mcp_plugin.remote")

# Global state for remote mode.
# _server is an rpyc ThreadedServer instance, or the string "hrpyc" when
# the bundled hrpyc fallback was used.
_server: Optional[Any] = None
_server_thread: Optional[threading.Thread] = None
_port: int = 18811
_host: str = "127.0.0.1"


def start_hrpyc_server(port: int = 18811, host: str = "127.0.0.1") -> dict:
    """Start an RPyC listener so external MCP servers can connect.

    Args:
        port: Port to listen on (default: 18811)
        host: Interface to bind (default: "127.0.0.1" = localhost only).
              Use "0.0.0.0" to accept LAN connections. NOT recommended:
              RPyC classic mode is unauthenticated code execution.

    Returns:
        Dict with status and connection info
    """
    global _server, _server_thread, _port, _host

    if _server is not None:
        return {
            "status": "already_running",
            "port": _port,
            "host": _host,
            "message": f"RPyC server is already running on {_host}:{_port}",
        }

    # Preferred path: plain rpyc ThreadedServer, which lets us control the
    # bind address (hrpyc.start_server always binds all interfaces).
    rpyc_import_error = None
    try:
        from rpyc.core.service import SlaveService
        from rpyc.utils.server import ThreadedServer
    except ImportError as e:
        rpyc_import_error = e
        logger.warning(f"rpyc not importable directly ({e}); falling back to hrpyc")

    if rpyc_import_error is None:
        try:
            server = ThreadedServer(
                SlaveService,
                hostname=host,
                port=port,
                # Fail loudly if the port is taken. On Windows, reuse_addr=True
                # would silently double-bind against another listener.
                reuse_addr=False,
                auto_register=False,
                logger=logger,
            )
            thread = threading.Thread(
                target=server.start, name="houdini_mcp_rpyc", daemon=True
            )
            thread.start()

            _server = server
            _server_thread = thread
            _port = port
            _host = host

            logger.info(f"Started RPyC server on {host}:{port}")
            connect_host = "localhost" if host in ("127.0.0.1", "localhost") else host
            return {
                "status": "success",
                "port": port,
                "host": host,
                "message": (
                    f"RPyC server started on {host}:{port}. "
                    f"Connect with: HOUDINI_HOST={connect_host} HOUDINI_PORT={port}"
                ),
            }
        except OSError as e:
            logger.error(f"Failed to bind RPyC server on {host}:{port}: {e}")
            return {
                "status": "error",
                "message": (
                    f"Failed to bind {host}:{port}: {e}. "
                    "The port may already be in use (another Houdini instance?)."
                ),
            }
        except Exception as e:
            logger.error(f"Failed to start RPyC server: {e}")
            return {
                "status": "error",
                "message": f"Failed to start RPyC server: {e}",
            }

    # Fallback: bundled hrpyc (no bind-address control: binds all interfaces).
    try:
        import hrpyc

        hrpyc.start_server(port=port)
        _server = "hrpyc"
        _server_thread = None
        _port = port
        _host = "0.0.0.0"

        logger.info(f"Started hrpyc server on port {port} (all interfaces)")
        return {
            "status": "success",
            "port": port,
            "host": "0.0.0.0",
            "message": (
                f"hrpyc server started on port {port}. WARNING: hrpyc binds all "
                f"network interfaces; restrict access with a firewall."
            ),
        }
    except ImportError as e:
        logger.error(f"Neither rpyc nor hrpyc available: {e}")
        return {
            "status": "error",
            "message": (
                "Neither rpyc nor hrpyc is available. Make sure Houdini's "
                "Python environment includes hrpyc."
            ),
        }
    except Exception as e:
        logger.error(f"Failed to start hrpyc server: {e}")
        return {
            "status": "error",
            "message": f"Failed to start hrpyc server: {e}",
        }


def stop_hrpyc_server() -> dict:
    """Stop the RPyC listener.

    Returns:
        Dict with status
    """
    global _server, _server_thread

    if _server is None:
        return {
            "status": "not_running",
            "message": "RPyC server is not running",
        }

    try:
        if _server == "hrpyc":
            import hrpyc

            hrpyc.stop_server()
        else:
            _server.close()

        _server = None
        _server_thread = None

        logger.info("Stopped RPyC server")
        return {
            "status": "success",
            "message": "RPyC server stopped",
        }
    except Exception as e:
        logger.error(f"Failed to stop RPyC server: {e}")
        return {
            "status": "error",
            "message": f"Failed to stop RPyC server: {e}",
        }


def is_hrpyc_running() -> bool:
    """Check if the RPyC listener is running (in this process).

    Returns:
        True if running, False otherwise
    """
    return _server is not None


def get_hrpyc_status() -> dict:
    """Get RPyC listener status.

    Returns:
        Dict with status information
    """
    if not is_hrpyc_running():
        return {
            "running": False,
            "message": (
                "RPyC server is not running. Use start_hrpyc_server() to "
                "enable remote connections."
            ),
        }

    connect_host = "localhost" if _host in ("127.0.0.1", "localhost") else _host
    return {
        "running": True,
        "port": _port,
        "host": _host,
        "backend": "hrpyc" if _server == "hrpyc" else "rpyc.ThreadedServer",
        "connection_string": f"HOUDINI_HOST={connect_host} HOUDINI_PORT={_port}",
        "message": f"RPyC server is running on {_host}:{_port}.",
    }
