"""In-process MCP server for Houdini.

This module provides an MCP server that runs inside Houdini's Python environment,
using stdio transport for communication with MCP clients.
"""

import logging
import sys
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("houdini_mcp_plugin.server")

# Global server state
_server_thread: Optional[threading.Thread] = None
_server_running = False
_mcp_instance: Optional[Any] = None


def _create_mcp_server():
    """Create the MCP server instance with all tools registered.

    This creates a FastMCP instance configured for stdio transport,
    with tools that use the local Houdini connection.
    """
    from fastmcp import FastMCP
    from .connection import get_connection

    mcp = FastMCP("Houdini MCP (Local)")
    conn = get_connection()

    # Register tools that use the local connection
    # These are simplified versions that don't need host/port parameters

    @mcp.tool()
    def get_scene_info() -> Dict[str, Any]:
        """Get current Houdini scene information."""
        hou = conn.hou
        try:
            hip_file = hou.hipFile.path()
            version = hou.applicationVersionString()

            obj_node = hou.node("/obj")
            obj_children = []
            if obj_node:
                obj_children = [
                    {"name": n.name(), "type": n.type().name()} for n in obj_node.children()
                ]

            return {
                "status": "success",
                "hip_file": hip_file,
                "houdini_version": version,
                "obj_nodes": obj_children,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def create_node(
        node_type: str, parent_path: str = "/obj", name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new node in the Houdini scene."""
        hou = conn.hou
        try:
            parent = hou.node(parent_path)
            if parent is None:
                return {"status": "error", "message": f"Parent node not found: {parent_path}"}

            new_node = parent.createNode(node_type, name)
            return {
                "status": "success",
                "node_path": new_node.path(),
                "node_type": new_node.type().name(),
                "node_name": new_node.name(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def get_node_info(node_path: str, include_params: bool = True) -> Dict[str, Any]:
        """Get detailed information about a node."""
        hou = conn.hou
        try:
            node = hou.node(node_path)
            if node is None:
                return {"status": "error", "message": f"Node not found: {node_path}"}

            info = {
                "status": "success",
                "path": node.path(),
                "name": node.name(),
                "type": node.type().name(),
                "category": node.type().category().name(),
                "children_count": len(node.children()),
                "inputs": [i.path() if i else None for i in node.inputs()],
                "outputs": [o.path() for o in node.outputs()],
            }

            if include_params:
                params = {}
                for parm in node.parms():
                    try:
                        params[parm.name()] = parm.eval()
                    except Exception:
                        params[parm.name()] = str(parm.rawValue())
                info["parameters"] = params

            return info
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def set_parameter(node_path: str, param_name: str, value: Any) -> Dict[str, Any]:
        """Set a parameter value on a node."""
        hou = conn.hou
        try:
            node = hou.node(node_path)
            if node is None:
                return {"status": "error", "message": f"Node not found: {node_path}"}

            parm = node.parm(param_name)
            if parm is None:
                # Try as parm tuple
                parm_tuple = node.parmTuple(param_name)
                if parm_tuple is None:
                    return {"status": "error", "message": f"Parameter not found: {param_name}"}
                parm_tuple.set(value)
                return {
                    "status": "success",
                    "node_path": node_path,
                    "param_name": param_name,
                    "new_value": list(parm_tuple.eval()),
                }

            parm.set(value)
            return {
                "status": "success",
                "node_path": node_path,
                "param_name": param_name,
                "new_value": parm.eval(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def delete_node(node_path: str) -> Dict[str, Any]:
        """Delete a node from the scene."""
        hou = conn.hou
        try:
            node = hou.node(node_path)
            if node is None:
                return {"status": "error", "message": f"Node not found: {node_path}"}

            name = node.name()
            node.destroy()
            return {
                "status": "success",
                "deleted_node": node_path,
                "node_name": name,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def execute_code(
        code: str, capture_diff: bool = False, allow_dangerous: bool = False
    ) -> Dict[str, Any]:
        """Execute Python code in Houdini with safety rails."""
        import re

        hou = conn.hou
        import io
        import contextlib
        import builtins as _builtins_mod

        # Handle empty code
        if not code or not code.strip():
            return {
                "status": "success",
                "stdout": "",
                "stderr": "",
                "message": "Empty code - nothing to execute",
            }

        # Scan for dangerous patterns before execution
        _DANGEROUS_PATTERNS = [
            (r"\bos\.system\b", "os.system - shell command execution"),
            (r"\bsubprocess\b", "subprocess - process spawning"),
            (r"\bshutil\.rmtree\b", "shutil.rmtree - recursive deletion"),
            (r"\bos\.remove\b", "os.remove - file deletion"),
            (r"\bos\.unlink\b", "os.unlink - file deletion"),
            (r"\b__import__\b", "__import__ - dynamic import"),
        ]
        dangerous_found = []
        for pattern, desc in _DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                dangerous_found.append(desc)

        if dangerous_found and not allow_dangerous:
            return {
                "status": "error",
                "message": "Dangerous operations detected in code",
                "dangerous_patterns": dangerous_found,
                "hint": "Set allow_dangerous=True to proceed",
            }

        # Restricted builtins (same as main execute_code tool)
        _SAFE_BUILTINS = {
            "bool": _builtins_mod.bool, "int": _builtins_mod.int,
            "float": _builtins_mod.float, "str": _builtins_mod.str,
            "bytes": _builtins_mod.bytes, "list": _builtins_mod.list,
            "tuple": _builtins_mod.tuple, "dict": _builtins_mod.dict,
            "set": _builtins_mod.set, "frozenset": _builtins_mod.frozenset,
            "type": _builtins_mod.type, "object": _builtins_mod.object,
            "range": _builtins_mod.range, "enumerate": _builtins_mod.enumerate,
            "zip": _builtins_mod.zip, "map": _builtins_mod.map,
            "filter": _builtins_mod.filter, "reversed": _builtins_mod.reversed,
            "sorted": _builtins_mod.sorted, "iter": _builtins_mod.iter,
            "next": _builtins_mod.next,
            "abs": _builtins_mod.abs, "min": _builtins_mod.min,
            "max": _builtins_mod.max, "sum": _builtins_mod.sum,
            "round": _builtins_mod.round, "pow": _builtins_mod.pow,
            "repr": _builtins_mod.repr, "format": _builtins_mod.format,
            "chr": _builtins_mod.chr, "ord": _builtins_mod.ord,
            "len": _builtins_mod.len, "isinstance": _builtins_mod.isinstance,
            "hasattr": _builtins_mod.hasattr, "getattr": _builtins_mod.getattr,
            "setattr": _builtins_mod.setattr, "callable": _builtins_mod.callable,
            "any": _builtins_mod.any, "all": _builtins_mod.all,
            "print": _builtins_mod.print,
            "Exception": _builtins_mod.Exception,
            "ValueError": _builtins_mod.ValueError,
            "TypeError": _builtins_mod.TypeError,
            "KeyError": _builtins_mod.KeyError,
            "IndexError": _builtins_mod.IndexError,
            "AttributeError": _builtins_mod.AttributeError,
            "RuntimeError": _builtins_mod.RuntimeError,
            "StopIteration": _builtins_mod.StopIteration,
            "None": None, "True": True, "False": False,
            "__import__": _builtins_mod.__import__,
        }

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        exec_globals = {"hou": hou, "__builtins__": _SAFE_BUILTINS}

        try:
            with (
                contextlib.redirect_stdout(stdout_capture),
                contextlib.redirect_stderr(stderr_capture),
            ):
                exec(code, exec_globals)

            result = {
                "status": "success",
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
            }
            if dangerous_found and allow_dangerous:
                result["dangerous_patterns_executed"] = dangerous_found
            return result
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
            }

    @mcp.tool()
    def list_children(node_path: str, recursive: bool = False) -> Dict[str, Any]:
        """List child nodes."""
        hou = conn.hou
        try:
            node = hou.node(node_path)
            if node is None:
                return {"status": "error", "message": f"Node not found: {node_path}"}

            def get_child_info(n, depth=0, max_depth=10):
                if depth > max_depth:
                    return []

                result = []
                for child in n.children():
                    info = {
                        "path": child.path(),
                        "name": child.name(),
                        "type": child.type().name(),
                    }
                    if recursive and child.children():
                        info["children"] = get_child_info(child, depth + 1, max_depth)
                    result.append(info)
                return result

            children = get_child_info(node)
            return {
                "status": "success",
                "node_path": node_path,
                "children": children,
                "count": len(children),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def save_scene(file_path: Optional[str] = None) -> Dict[str, Any]:
        """Save the current Houdini scene."""
        hou = conn.hou
        try:
            if file_path:
                hou.hipFile.save(file_path)
            else:
                hou.hipFile.save()

            return {
                "status": "success",
                "saved_to": file_path or hou.hipFile.path(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    def check_connection() -> Dict[str, Any]:
        """Check connection status."""
        return conn.get_info()

    return mcp


def start_server(use_thread: bool = True) -> bool:
    """Start the MCP server.

    Args:
        use_thread: If True, run in a background thread. If False, block.

    Returns:
        True if server started successfully, False otherwise.
    """
    global _server_thread, _server_running, _mcp_instance

    if _server_running:
        logger.warning("MCP server is already running")
        return False

    try:
        _mcp_instance = _create_mcp_server()

        if use_thread:

            def run_server():
                global _server_running
                _server_running = True
                logger.info("Starting MCP server (stdio transport) in background thread")
                try:
                    _mcp_instance.run(transport="stdio")
                except Exception as e:
                    logger.error(f"MCP server error: {e}")
                finally:
                    _server_running = False

            _server_thread = threading.Thread(target=run_server, daemon=True)
            _server_thread.start()
            logger.info("MCP server thread started")
        else:
            _server_running = True
            logger.info("Starting MCP server (stdio transport) in main thread")
            _mcp_instance.run(transport="stdio")
            _server_running = False

        return True
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        return False


def stop_server() -> bool:
    """Stop the MCP server.

    Returns:
        True if server was stopped, False if it wasn't running.
    """
    global _server_running, _server_thread, _mcp_instance

    if not _server_running:
        logger.warning("MCP server is not running")
        return False

    # Signal the server to stop
    # Note: FastMCP doesn't have a clean shutdown mechanism for stdio,
    # so we mainly just mark it as not running
    _server_running = False
    _mcp_instance = None
    _server_thread = None

    logger.info("MCP server stopped")
    return True


def is_server_running() -> bool:
    """Check if the MCP server is running."""
    return _server_running
