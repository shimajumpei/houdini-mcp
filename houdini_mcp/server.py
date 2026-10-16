"""Houdini MCP Server - Main server entry point."""

import os
import logging
from typing import Any, Dict, List, Optional, Literal, Union

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .connection import ensure_connected, is_connected, disconnect, get_connection_info, ping
from . import tools

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("houdini_mcp.server")

# Environment configuration
HOUDINI_HOST = os.getenv("HOUDINI_HOST", "localhost")
HOUDINI_PORT = int(os.getenv("HOUDINI_PORT", "18811"))

# Create FastMCP instance
mcp = FastMCP("Houdini MCP")


# Health check endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for container orchestration."""
    return JSONResponse({"status": "healthy", "service": "houdini-mcp"})


@mcp.tool()
def get_scene_info() -> Dict[str, Any]:
    """
    Get current Houdini scene information.

    Returns information about the currently open scene including:
    - Hip file path
    - Houdini version
    - List of nodes in /obj
    """
    return tools.get_scene_info(HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def create_node(
    node_type: str, parent_path: str = "/obj", name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new node in the Houdini scene.

    Args:
        node_type: The type of node to create (e.g., "geo", "null", "cam")
        parent_path: The parent node path (default: "/obj")
        name: Optional name for the new node

    Examples:
        - create_node("geo") -> Creates a geometry container at /obj
        - create_node("sphere", "/obj/geo1") -> Creates a sphere SOP inside geo1
        - create_node("cam", name="render_cam") -> Creates a camera named render_cam
    """
    return tools.create_node(node_type, parent_path, name, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def execute_code(
    code: str,
    capture_diff: bool = False,
    max_stdout_size: int = 100000,
    max_stderr_size: int = 100000,
    max_diff_nodes: int = 1000,
    timeout: int = 30,
    allow_dangerous: bool = False,
    allow_heavy: bool = False,
) -> Dict[str, Any]:
    """
    Execute Python code in Houdini with scene change tracking and safety rails.

    The 'hou' module is available in the execution context.
    Use this for complex operations that aren't covered by other tools.

    SAFETY FEATURES:
    - Dangerous operation detection: Scans for patterns like hou.exit(), os.remove(), subprocess, etc.
    - Heavy pattern detection: Scans for patterns that cause timeout/hang on large geometry
      (e.g. geo.points(), list(geo.prims()), .allVoxels(), attribValue in loop).
      Blocked by default; set allow_heavy=True to override.
    - Output size caps: Prevents massive output from overwhelming the response
    - Execution timeout: Prevents runaway code from blocking indefinitely
    - Scene diff limits: Caps the number of nodes returned in scene changes

    Args:
        code: Python code to execute
        capture_diff: If True, captures before/after scene state and returns changes (default: False)
        max_stdout_size: Maximum stdout size in bytes (default: 100000 = 100KB)
        max_stderr_size: Maximum stderr size in bytes (default: 100000 = 100KB)
        max_diff_nodes: Maximum nodes in scene diff added_nodes list (default: 1000)
        timeout: Execution timeout in seconds (default: 30)
        allow_dangerous: If True, allows code with dangerous patterns to execute (default: False)
        allow_heavy: If True, allows code with heavy patterns (geo.points() etc.) to execute (default: False).
            When blocked, the response includes specific lightweight alternatives.

    Dangerous patterns detected:
        - hou.exit() - closes Houdini
        - os.remove(), os.unlink() - file deletion
        - shutil.rmtree() - directory deletion
        - subprocess, os.system() - shell execution
        - open() with write modes - file writing
        - hou.hipFile.clear() - scene wipe

    Heavy patterns detected (blocked unless allow_heavy=True):
        - geo.points() / geo.prims() — full list fetch, may hang on large geo
        - .allVoxels() — fetches entire voxel grid
        - attribValue() in loop — O(N) RPyC calls
        Use get_geo_summary(max_sample_points=N) or itertools.islice() instead.

    Example:
        execute_code('''
            obj = hou.node("/obj")
            geo = obj.createNode("geo", "my_geometry")
            sphere = geo.createNode("sphere")
            sphere.parm("radx").set(2.0)
            sphere.setDisplayFlag(True)
            sphere.setRenderFlag(True)
        ''')

    Returns:
        Dict with status, stdout, stderr, and scene_changes (if capture_diff=True).
        May include truncation flags (stdout_truncated, stderr_truncated, diff_truncated)
        if output exceeded size limits.
        If dangerous/heavy patterns detected and not allowed, returns error with detected patterns
        and suggested alternatives.
    """
    return tools.execute_code(
        code=code,
        capture_diff=capture_diff,
        max_stdout_size=max_stdout_size,
        max_stderr_size=max_stderr_size,
        max_diff_nodes=max_diff_nodes,
        timeout=timeout,
        allow_dangerous=allow_dangerous,
        allow_heavy=allow_heavy,
        host=HOUDINI_HOST,
        port=HOUDINI_PORT,
    )


@mcp.tool()
def set_parameter(
    node_path: str,
    param_name: str,
    value: Union[float, int, str, bool, List[float], List[int], List[str]],
) -> Dict[str, Any]:
    """
    Set a parameter value on a node.

    Args:
        node_path: Full path to the node (e.g., "/obj/geo1/sphere1")
        param_name: Name of the parameter (e.g., "radx", "tx", "scale")
        value: Value to set - can be:
               - float/int for numeric parameters
               - str for string/menu parameters
               - bool for toggle parameters
               - List[float]/List[int] for vector parameters (e.g., translate, rotate, scale)

    Examples:
        - set_parameter("/obj/geo1/sphere1", "radx", 2.5)
        - set_parameter("/obj/cam1", "tx", 10.0)
        - set_parameter("/obj/geo1", "t", [1.0, 2.0, 3.0])  # Vector param
    """
    return tools.set_parameter(node_path, param_name, value, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def get_node_info(
    node_path: str,
    include_params: bool = True,
    include_input_details: bool = True,
    include_errors: bool = False,
    force_cook: bool = False,
    compact: bool = False,
    include_description_ja: bool = False,
) -> Dict[str, Any]:
    """
    Get detailed information about a node.

    Args:
        node_path: Full path to the node
        include_params: Whether to include parameter values (default: True)
        include_input_details: When True, expand input connections to show source node,
                              output index, and connection index details (default: True)
        include_errors: When True, include cook state and error/warning information (default: False)
        force_cook: When True, force cook the node before checking errors (default: False)
        compact: When True, return minimal info (path, type, counts only) for reduced payload (default: False)
        include_description_ja: When True, include Japanese descriptions from local help files (default: False)

    Returns:
        Node information including type, children, connections, flags, and parameters.
        When include_input_details=True, also includes detailed input_connections array
        showing source nodes and output indices for each connection.
        When include_errors=True, also includes cook_info with cook_state, errors, and warnings.
        When compact=True, returns only path, type, and child/input/output counts.
        When include_description_ja=True, also includes description_ja (node summary) and
        parameter_descriptions_ja dict mapping parameter names to Japanese descriptions.

    Example:
        # Get node info with cook state and errors
        get_node_info("/obj/geo1/sphere1", include_errors=True)

        # Force cook and check for errors
        get_node_info("/obj/geo1/sphere1", include_errors=True, force_cook=True)

        # Get minimal info for reduced payload
        get_node_info("/obj/geo1/sphere1", compact=True)

        # Get node info with Japanese descriptions
        get_node_info("/obj/geo1/box1", include_description_ja=True)
    """
    return tools.get_node_info(
        node_path=node_path,
        include_params=include_params,
        max_params=50,
        include_input_details=include_input_details,
        include_errors=include_errors,
        force_cook=force_cook,
        compact=compact,
        include_description_ja=include_description_ja,
        host=HOUDINI_HOST,
        port=HOUDINI_PORT,
    )


@mcp.tool()
def delete_node(node_path: str) -> Dict[str, Any]:
    """
    Delete a node from the scene.

    Args:
        node_path: Full path to the node to delete

    Warning: This operation cannot be undone via this API.
    """
    return tools.delete_node(node_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def save_scene(file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Save the current Houdini scene.

    Args:
        file_path: Optional path to save to. If not provided, saves to current file.

    Example:
        - save_scene() -> Saves to current file
        - save_scene("/path/to/scene.hip") -> Saves to specified path
    """
    return tools.save_scene(file_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def load_scene(file_path: str) -> Dict[str, Any]:
    """
    Load a Houdini scene file.

    Args:
        file_path: Path to the .hip file to load
    """
    return tools.load_scene(file_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def new_scene() -> Dict[str, Any]:
    """
    Create a new empty Houdini scene.

    Warning: This will clear the current scene. Make sure to save first if needed.
    """
    return tools.new_scene(HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
async def serialize_scene(
    root_path: str = "/obj",
    include_params: bool = False,
    summarize: bool = False,
) -> Dict[str, Any]:
    """
    Serialize the scene structure to a dictionary.

    Useful for comparing scene states before and after operations,
    or for understanding the scene hierarchy.

    Args:
        root_path: Root node path to serialize from (default: "/obj")
        include_params: Include parameter values (can be verbose, default: False)
        summarize: If True, use AI to analyze scene structure (default: False).
                  Identifies complexity hotspots and optimization opportunities.

    Returns:
        Dict with scene structure, optionally with ai_summary field containing
        insights about organization, complexity, and optimization opportunities.
    """
    result = tools.serialize_scene(root_path, include_params, 10, HOUDINI_HOST, HOUDINI_PORT)

    # Apply AI summarization if requested or if scene is very large
    if summarize or tools.should_summarize(result):
        result = await tools.summarize_scene(result)

    return result


@mcp.tool()
def get_last_scene_diff() -> Dict[str, Any]:
    """
    Get the scene diff from the last execute_code call.

    Shows what nodes were added, removed, or modified during the last
    code execution. Useful for understanding what changes were made.
    """
    return tools.get_last_scene_diff()


@mcp.tool()
def list_node_types(
    category: Optional[str] = None,
    max_results: int = 100,
    name_filter: Optional[str] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List available Houdini node types.

    Args:
        category: Optional category filter (e.g., "Object", "Sop", "Cop2", "Vop")
        max_results: Maximum number of results to return (default: 100, max: 500)
        name_filter: Optional substring filter for node type names (case-insensitive)
        offset: Number of results to skip for pagination (default: 0)

    Returns:
        List of node types with their categories and descriptions.
        Includes pagination info (has_more, next_offset) when applicable.

    Note:
        Large categories like "Sop" have thousands of node types.
        Use name_filter to narrow results (e.g., name_filter="noise" for noise-related SOPs).

    Examples:
        list_node_types(category="Object")  # List Object-level nodes
        list_node_types(category="Sop", name_filter="noise")  # Find noise SOPs
        list_node_types(category="Sop", name_filter="vdb", max_results=50)  # VDB SOPs
        list_node_types(category="Sop", offset=100)  # Get next page of SOPs
    """
    return tools.list_node_types(
        category, max_results, name_filter, offset, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def list_children(
    node_path: str,
    recursive: bool = False,
    max_depth: int = 10,
    max_nodes: int = 1000,
    compact: bool = False,
) -> Dict[str, Any]:
    """
    List child nodes with paths, types, and current input connections.

    This tool is essential for understanding node networks and helps agents
    insert nodes without breaking existing connections. Each child includes
    detailed input connection information showing which nodes are connected
    and at which indices.

    Args:
        node_path: Path to the parent node (e.g., "/obj/geo1")
        recursive: If True, recursively traverse all descendants (default: False)
        max_depth: Maximum recursion depth to prevent infinite loops (default: 10)
        max_nodes: Maximum number of nodes to return as safety limit (default: 1000)
        compact: When True, return only path/name/type without connection details (default: False)

    Returns:
        Dict with children array containing node info including:
        - path: Full node path
        - name: Node name
        - type: Node type
        - inputs: Array of input connections with source_node and output_index (omitted if compact=True)
        - outputs: Array of output node paths (omitted if compact=True)

    Example:
        list_children("/obj/geo1", recursive=True, max_depth=3)
        list_children("/obj/geo1", compact=True)  # Minimal payload
    """
    return tools.list_children(
        node_path, recursive, max_depth, max_nodes, compact, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def find_nodes(
    root_path: str = "/obj",
    pattern: str = "*",
    node_type: Optional[str] = None,
    max_results: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Find nodes by name pattern or type using glob/substring matching.

    Supports wildcard patterns (* and ?) for flexible searching. When pattern
    contains no wildcards, also performs substring matching.

    Args:
        root_path: Root path to start search from (default: "/obj")
        pattern: Glob pattern or substring to match node names (default: "*")
                Examples: "noise*", "*grid*", "sphere"
        node_type: Optional node type filter (e.g., "sphere", "noise", "geo")
        max_results: Maximum number of results to return (default: 100)
        offset: Number of results to skip for pagination (default: 0)

    Returns:
        Dict with matches array containing matching nodes with path, name, and type.
        Includes pagination info (has_more, next_offset) when applicable.

    Examples:
        find_nodes("/obj", "noise*") - Find all nodes starting with "noise"
        find_nodes("/obj/geo1", "*", node_type="sphere") - Find all sphere nodes
        find_nodes("/obj", "grid") - Substring match for "grid"
        find_nodes("/obj", "*", offset=100) - Get next page of results
    """
    return tools.find_nodes(
        root_path, pattern, node_type, max_results, offset, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def render_viewport(
    camera_position: Optional[List[float]] = None,
    camera_rotation: Optional[List[float]] = None,
    look_at: Optional[str] = None,
    resolution: Optional[List[int]] = None,
    renderer: str = "opengl",
    output_format: str = "png",
    auto_frame: bool = True,
    orthographic: bool = False,
    karma_engine: str = "cpu",
) -> Dict[str, Any]:
    """
    Render the viewport and return the image as base64.

    Creates a temporary camera, positions it to frame the scene geometry,
    renders the scene, and returns the rendered image encoded as base64.
    Useful for AI vision analysis of the current scene state.

    Args:
        camera_position: [x, y, z] world position for camera (default: auto-calculated)
        camera_rotation: [rx, ry, rz] rotation in degrees (default: [-30, 45, 0] isometric)
        look_at: Node path to look at (centers camera on this node's geometry)
        resolution: [width, height] in pixels (default: [640, 480])
        renderer: Render engine - "opengl" (fast) or "karma" (quality)
        output_format: Image format - "png", "jpg", or "exr"
        auto_frame: If True, automatically frame all visible geometry (default: True)
        orthographic: If True, use orthographic projection (default: False)
        karma_engine: Karma render engine - "cpu" (quality) or "gpu" (fast XPU). Only used when renderer="karma"

    Note:
        render_viewport does not accept a save_path parameter.
        To save a rendered image to a file, use capture_pane_screenshot(save_path=...)
        which writes directly to the Houdini host filesystem.
        Check the tool's parameter schema for the full list of accepted arguments.

    Returns:
        Dict with:
        - image_base64: Base64-encoded image data
        - format: Image format used
        - resolution: [width, height]
        - camera_path: Path to the temporary camera used
        - bounding_box: Scene bounding box if auto_frame was used

    Examples:
        render_viewport()  # Auto-frame scene with isometric view
        render_viewport(camera_rotation=[0, 0, 0])  # Front view
        render_viewport(camera_rotation=[-90, 0, 0])  # Top view
        render_viewport(look_at="/obj/geo1", orthographic=True)
        render_viewport(renderer="karma", karma_engine="gpu")  # Fast GPU render
    """
    return tools.render_viewport(
        camera_position,
        camera_rotation,
        look_at,
        resolution,
        renderer,
        output_format,
        auto_frame,
        orthographic,
        karma_engine,
        HOUDINI_HOST,
        HOUDINI_PORT,
    )


@mcp.tool()
def render_quad_view(
    resolution: Optional[List[int]] = None,
    renderer: str = "opengl",
    output_format: str = "png",
    orthographic: bool = True,
    include_perspective: bool = True,
    karma_engine: str = "cpu",
) -> Dict[str, Any]:
    """
    Render 4 canonical views (Front, Left, Top, Perspective) in one call.

    Creates a camera rig and renders standardized views for spatial understanding.
    Returns all 4 images as base64-encoded strings. This is more efficient than
    calling render_viewport 4 times as it reuses the camera rig and calculates
    the bounding box only once.

    Args:
        resolution: [width, height] in pixels (default: [512, 512])
        renderer: Render engine - "opengl" (fast) or "karma" (quality)
        output_format: Image format - "png", "jpg", or "exr"
        orthographic: If True, use orthographic projection for Front/Left/Top views (default: True)
        include_perspective: If True, include perspective view; if False, only orthographic views (default: True)
        karma_engine: Karma render engine - "cpu" (quality) or "gpu" (fast XPU). Only used when renderer="karma"

    Returns:
        Dict with:
        - status: "success" or "error"
        - views: List of view results, each containing:
            - name: View name (front, left, top, perspective)
            - rotation: [rx, ry, rz] camera rotation used
            - image_base64: Base64-encoded image data
            - format: Image format used
            - resolution: [width, height]
            - orthographic: Whether orthographic projection was used
        - bounding_box: Scene bounding box info
        - renderer: Render engine used
        - total_render_time_ms: Total time for all renders

    Examples:
        render_quad_view()  # All 4 views with orthographic projection
        render_quad_view(orthographic=False)  # All views with perspective
        render_quad_view(resolution=[1024, 1024], renderer="karma")  # Higher quality
        render_quad_view(include_perspective=False)  # Only 3 orthographic views
        render_quad_view(renderer="karma", karma_engine="gpu")  # Fast GPU renders
    """
    return tools.render_quad_view(
        resolution,
        renderer,
        output_format,
        orthographic,
        include_perspective,
        karma_engine,
        HOUDINI_HOST,
        HOUDINI_PORT,
    )


@mcp.tool()
def list_render_nodes() -> Dict[str, Any]:
    """
    List all render nodes (ROPs) in the /out context.

    Returns information about each render node including type, path,
    and basic configuration like camera and output path.

    Returns:
        Dict with:
        - status: "success" or "error"
        - count: Number of render nodes found
        - render_nodes: List of render node info with:
            - path: Full node path
            - name: Node name
            - type: ROP type (opengl, karma, ifd, etc.)
            - camera: Camera path if set
            - output: Output image path if set
            - bypassed: Whether the node is bypassed

    Examples:
        list_render_nodes()  # List all ROPs in /out
    """
    return tools.list_render_nodes(HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def get_render_settings(rop_path: str) -> Dict[str, Any]:
    """
    Get current render configuration for a ROP node.

    Returns all relevant render settings based on the ROP type (Karma, Mantra, OpenGL).

    Args:
        rop_path: Full path to the ROP node (e.g., "/out/karma1")

    Returns:
        Dict with:
        - status: "success" or "error"
        - rop_path: Path to the ROP
        - rop_type: Type of ROP (karma, ifd, opengl)
        - settings: Dict of parameter names to current values
        - schema: Dict describing available settings for this ROP type

    Examples:
        get_render_settings("/out/karma1")
        get_render_settings("/out/mantra1")
    """
    return tools.get_render_settings(rop_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def set_render_settings(
    rop_path: str,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Modify render settings on a ROP node.

    Args:
        rop_path: Full path to the ROP node (e.g., "/out/karma1")
        settings: Dict of parameter names to values to set

    Returns:
        Dict with:
        - status: "success", "partial", or "error"
        - rop_path: Path to the ROP
        - updated: List of parameters that were updated
        - failed: List of parameters that failed to update

    Examples:
        set_render_settings("/out/karma1", {"samplesperpixel": 64, "engine": "xpu"})
        set_render_settings("/out/mantra1", {"vm_samplesx": 6, "vm_samplesy": 6})
    """
    return tools.set_render_settings(rop_path, settings, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def create_render_node(
    rop_type: str,
    name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a new render node (ROP) with optional settings.

    Args:
        rop_type: Type of ROP to create. Common types:
            - "opengl": Fast viewport render (recommended for previews)
            - "karma": Karma renderer (CPU or GPU)
            - "ifd": Mantra renderer
        name: Optional name for the node (auto-generated if not provided)
        settings: Optional dict of parameter values to set on creation

    Returns:
        Dict with:
        - status: "success" or "error"
        - rop_path: Path to the created ROP
        - rop_type: Type of ROP created
        - settings_applied: List of settings that were applied

    Examples:
        create_render_node("karma", "hero_render", {"engine": "xpu", "samplesperpixel": 64})
        create_render_node("opengl", settings={"antialias": 8})
        create_render_node("ifd", "final_render")
    """
    return tools.create_render_node(rop_type, name, settings, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
async def find_error_nodes(
    root_path: str = "/",
    include_warnings: bool = True,
    max_results: int = 100,
    summarize: bool = False,
) -> Dict[str, Any]:
    """
    Find all nodes with cook errors or warnings in the scene.

    Scans the entire node hierarchy starting from root_path and returns
    all nodes that have errors or warnings. Essential for debugging
    complex scenes where error locations are unknown.

    Args:
        root_path: Root path to start search from (default: "/" for entire scene)
        include_warnings: Whether to include nodes with warnings (default: True)
        max_results: Maximum number of results to return (default: 100)
        summarize: If True, use AI to triage and prioritize errors (default: False).
                  Provides actionable fix order and identifies root causes.

    Returns:
        Dict with error/warning nodes including:
        - error_nodes: List of nodes with errors (path, name, type, errors)
        - warning_nodes: List of nodes with warnings (if include_warnings=True)
        - error_count: Number of error nodes found
        - warning_count: Number of warning nodes found
        - total_scanned: Number of nodes scanned
        - ai_summary: (if summarize=True) AI-generated triage with prioritized fixes

    Examples:
        find_error_nodes()  # Find all errors in scene
        find_error_nodes("/obj/geo1")  # Find errors within a specific network
        find_error_nodes(include_warnings=False)  # Only errors, no warnings
        find_error_nodes(summarize=True)  # Get AI triage of errors
    """
    result = tools.find_error_nodes(
        root_path, include_warnings, max_results, HOUDINI_HOST, HOUDINI_PORT
    )

    # Apply AI summarization if requested or if many errors found
    if summarize or (result.get("error_count", 0) > 5):
        result = await tools.summarize_errors(result)

    return result


@mcp.tool()
def check_connection() -> Dict[str, Any]:
    """
    Check Houdini connection status with detailed info.

    Returns connection status, Houdini version, build info, and current hip file.
    Attempts to connect if not already connected.
    """
    try:
        # First try a quick ping
        if not is_connected():
            # Try to connect
            ensure_connected(HOUDINI_HOST, HOUDINI_PORT)

        # Get detailed connection info
        info = get_connection_info(HOUDINI_HOST, HOUDINI_PORT)
        info["status"] = "connected" if info["connected"] else "disconnected"
        return info

    except Exception as e:
        return {
            "status": "disconnected",
            "host": HOUDINI_HOST,
            "port": HOUDINI_PORT,
            "error": str(e),
        }


@mcp.tool()
def ping_houdini() -> Dict[str, Any]:
    """
    Quick connectivity test to Houdini.

    Returns True if Houdini RPC server is reachable.
    Does not maintain a persistent connection.
    """
    reachable = ping(HOUDINI_HOST, HOUDINI_PORT)
    return {
        "status": "success" if reachable else "error",
        "reachable": reachable,
        "host": HOUDINI_HOST,
        "port": HOUDINI_PORT,
    }


@mcp.tool()
def connect_nodes(
    src_path: str, dst_path: str, dst_input_index: int = 0, src_output_index: int = 0
) -> Dict[str, Any]:
    """
    Wire output of source node to input of destination node.

    Validates that node types are compatible (e.g., SOP→SOP, OBJ→OBJ) before connecting.
    Automatically disconnects existing connection if the destination input is already wired.

    Args:
        src_path: Path to source node (e.g., "/obj/geo1/grid1")
        dst_path: Path to destination node (e.g., "/obj/geo1/noise1")
        dst_input_index: Input index on destination node (default: 0)
        src_output_index: Output index on source node (default: 0)

    Returns:
        Dict with connection result including validation and connection details.

    Examples:
        connect_nodes("/obj/geo1/grid1", "/obj/geo1/noise1")
        connect_nodes("/obj/geo1/grid1", "/obj/geo1/merge1", dst_input_index=1)
    """
    return tools.connect_nodes(
        src_path, dst_path, dst_input_index, src_output_index, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def disconnect_node_input(node_path: str, input_index: int = 0) -> Dict[str, Any]:
    """
    Break/disconnect an input connection on a node.

    Args:
        node_path: Path to the node (e.g., "/obj/geo1/noise1")
        input_index: Input index to disconnect (default: 0)

    Returns:
        Dict with disconnection result including previous connection info if applicable.

    Examples:
        disconnect_node_input("/obj/geo1/noise1")  # Disconnect first input
        disconnect_node_input("/obj/geo1/merge1", input_index=1)  # Disconnect second input
    """
    return tools.disconnect_node_input(node_path, input_index, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def set_node_flags(
    node_path: str,
    display: Optional[bool] = None,
    render: Optional[bool] = None,
    bypass: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Set display, render, and bypass flags on a node.

    Only non-None values are set, allowing partial flag updates.
    Checks for flag availability using hasattr() before setting.

    Args:
        node_path: Path to the node (e.g., "/obj/geo1/sphere1")
        display: Display flag value (True/False) or None to skip
        render: Render flag value (True/False) or None to skip
        bypass: Bypass flag value (True/False) or None to skip

    Returns:
        Dict with result and flags that were set.

    Examples:
        set_node_flags("/obj/geo1/sphere1", display=True, render=True)
        set_node_flags("/obj/geo1/noise1", bypass=True)
        set_node_flags("/obj/geo1/mountain1", display=True)  # Only set display
    """
    return tools.set_node_flags(node_path, display, render, bypass, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def reorder_inputs(node_path: str, new_order: List[int]) -> Dict[str, Any]:
    """
    Reorder inputs on a node (useful for merge nodes).

    Stores existing connections, disconnects all, then reconnects in new order.
    new_order specifies the new position for each input: [1, 0, 2] swaps first two inputs.

    Args:
        node_path: Path to the node (e.g., "/obj/geo1/merge1")
        new_order: List specifying new input order (e.g., [1, 0, 2] to swap first two)

    Returns:
        Dict with reordering result including reconnection details.

    Examples:
        reorder_inputs("/obj/geo1/merge1", [1, 0, 2, 3])  # Swap first two inputs
        reorder_inputs("/obj/geo1/merge1", [2, 1, 0])  # Reverse three inputs
    """
    return tools.reorder_inputs(node_path, new_order, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def get_parameter_schema(
    node_path: str,
    parm_name: Optional[str] = None,
    max_parms: int = 100,
    include_description_ja: bool = False,
) -> Dict[str, Any]:
    """
    Get parameter metadata/schema for intelligent parameter setting.

    Returns detailed parameter information including types, defaults, ranges,
    menu items, tuple sizes, and current values. Essential for understanding
    what parameters are available on a node and how to set them correctly.

    Args:
        node_path: Full path to the node (e.g., "/obj/geo1/sphere1")
        parm_name: Optional specific parameter name. If provided, returns only that parameter's schema.
                  If None, returns schema for all parameters (up to max_parms)
        max_parms: Maximum number of parameters to return when parm_name is None (default: 100)
        include_description_ja: When True, include Japanese descriptions from local help files (default: False)

    Returns:
        Dict containing parameter schemas with type information, defaults, ranges, and current values.

    Schema includes:
        - name: Parameter name
        - label: UI label
        - type: Parameter type (float, int, string, menu, toggle, vector, ramp, etc.)
        - default: Default value(s)
        - min/max: Numeric ranges (if applicable)
        - menu_items: List of {label, value} for menu parameters
        - tuple_size: Size for vector parameters
        - is_animatable: Whether parameter can be keyframed
        - current_value: Current parameter value
        - description_ja: Japanese description (when include_description_ja=True)
        - menu_descriptions_ja: Japanese menu item descriptions (when include_description_ja=True)

    Examples:
        # Get all parameters on a sphere
        get_parameter_schema("/obj/geo1/sphere1")

        # Get specific parameter info
        get_parameter_schema("/obj/geo1/sphere1", parm_name="radx")

        # Get info for translate parameter (vector)
        get_parameter_schema("/obj/geo1", parm_name="t")

        # Get parameters with Japanese descriptions
        get_parameter_schema("/obj/geo1/box1", include_description_ja=True)
    """
    return tools.get_parameter_schema(
        node_path, parm_name, max_parms, include_description_ja, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
async def get_geo_summary(
    node_path: str,
    max_sample_points: int = 100,
    include_attributes: bool = True,
    include_groups: bool = True,
    summarize: bool = False,
) -> Dict[str, Any]:
    """
    Get geometry statistics and metadata for verification.

    Returns comprehensive geometry information including point/primitive counts,
    bounding box, attributes, groups, and optionally sample points. This tool is
    essential for agents to verify results after geometry operations.

    Args:
        node_path: Full path to the SOP node (e.g., "/obj/geo1/sphere1")
        max_sample_points: Maximum number of sample points to return (default: 100, max: 10000).
                          Set to 0 to skip point sampling.
        include_attributes: Whether to include attribute metadata (default: True)
        include_groups: Whether to include group information (default: True)
        summarize: If True, use AI to generate a concise summary of the geometry (default: False).
                  Useful for large/complex geometry to reduce token usage.

    Returns:
        Dict with comprehensive geometry summary including:
        - point_count, primitive_count, vertex_count: Geometry topology stats
        - bounding_box: {min, max, size, center} vectors in world space
        - cook_state: Current cook state ("cooked", "dirty", "uncooked", "error")
        - attributes: Metadata for point/primitive/vertex/detail attributes
        - groups: Names of point and primitive groups
        - sample_points: Optional array of first N points with their attribute values
        - ai_summary: (if summarize=True) AI-generated insights about the geometry

    Edge Cases:
        - Uncooked geometry: Tool will attempt to cook the node first
        - Empty geometry: Returns zeros for counts, not an error
        - Massive geometry (>1M points): Automatically caps sampling with warning
        - No geometry/Not a SOP: Returns error status

    Examples:
        # Basic geometry summary with 50 sample points
        get_geo_summary("/obj/geo1/sphere1", max_sample_points=50)

        # Just get counts and bbox, skip attributes/groups/samples
        get_geo_summary("/obj/geo1/grid1", max_sample_points=0,
                       include_attributes=False, include_groups=False)

        # Full detail for verification with AI summary
        get_geo_summary("/obj/geo1/noise1", max_sample_points=200, summarize=True)
    """
    result = tools.get_geo_summary(
        node_path, max_sample_points, include_attributes, include_groups, HOUDINI_HOST, HOUDINI_PORT
    )

    # Apply AI summarization if requested or if response is very large
    if summarize or tools.should_summarize(result):
        result = await tools.summarize_geometry(result)

    return result


@mcp.tool()
def get_houdini_help(
    help_type: str,
    item_name: str,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Fetch Houdini documentation from SideFX website.

    Retrieves and parses help documentation for nodes, VEX functions,
    and Python API. Helps AI understand Houdini concepts without
    hallucinating parameter names or functionality.

    NOTE: This tool does NOT require a Houdini connection - it fetches
    documentation directly from the SideFX website.

    Args:
        help_type: Type of documentation to fetch. Supported types:
            - "sop": SOP nodes (e.g., "box", "scatter", "vdbfrompolygons")
            - "obj": Object nodes (e.g., "geo", "cam", "light")
            - "dop": DOP nodes (e.g., "pyrosolver", "rbdpackedobject")
            - "cop2": COP nodes (e.g., "mosaic", "blur")
            - "chop": CHOP nodes (e.g., "math", "wave")
            - "vop": VOP nodes (e.g., "bind", "noise")
            - "lop": LOP/Solaris nodes (e.g., "usdimport", "materiallibrary")
            - "top": TOP/PDG nodes (e.g., "pythonscript", "wedge")
            - "rop": ROP nodes (e.g., "geometry", "karma")
            - "vex_function": VEX functions (e.g., "noise", "lerp", "chramp")
            - "python_hou": Python hou module classes (e.g., "Node", "Geometry")
        item_name: Name of the node or function (e.g., "box", "noise", "Node")
        timeout: Request timeout in seconds (default: 10)

    Returns:
        Dict with:
        - status: "success" or "error"
        - title: Documentation title
        - url: Source URL
        - description: Summary description
        - parameters: List of parameters with names, descriptions, and options
        - inputs: List of input connections (for nodes)
        - outputs: List of output connections (for nodes)
        - vex_info: Signature and return type (for VEX functions)
        - methods: Class methods (for Python hou module)

    Examples:
        get_houdini_help("sop", "box")  # Get box SOP documentation
        get_houdini_help("vex_function", "noise")  # Get VEX noise function docs
        get_houdini_help("python_hou", "Node")  # Get hou.Node class docs
        get_houdini_help("obj", "cam")  # Get camera object docs
    """
    return tools.get_houdini_help(help_type, item_name, timeout)


@mcp.tool()
def create_material(
    material_type: str = "principledshader",
    name: Optional[str] = None,
    parent_path: str = "/mat",
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a new material/shader node.

    Creates a material node in the specified context (typically /mat or /shop).
    Supports common material types like Principled Shader, MaterialX, and classic shaders.

    Args:
        material_type: Type of material to create. Common types:
            - "principledshader": Houdini's standard PBR shader (recommended)
            - "mtlxstandard_surface": MaterialX Standard Surface
            - "classicshader": Classic Mantra shader
        name: Optional name for the material. Auto-generated if not provided.
        parent_path: Parent context path (default: "/mat", alternative: "/shop")
        parameters: Optional dict of parameter values to set on the material.
            Common principledshader parameters:
            - basecolor: [r, g, b] base color
            - rough: float roughness (0-1)
            - metallic: float metallic (0-1)

    Returns:
        Dict with material_path, material_name, material_type, parameters_set

    Examples:
        create_material()  # Create default principled shader
        create_material("principledshader", "red_metal",
                       parameters={"basecolor": [1, 0, 0], "metallic": 1.0})
    """
    return tools.create_material(
        material_type, name, parent_path, parameters, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def assign_material(
    geometry_path: str,
    material_path: str,
    group: str = "",
) -> Dict[str, Any]:
    """
    Assign a material to geometry.

    Creates a Material SOP inside the geometry node to apply the material,
    or sets the shop_materialpath parameter directly.

    Args:
        geometry_path: Path to the geometry OBJ node (e.g., "/obj/geo1")
        material_path: Path to the material node (e.g., "/mat/principledshader1")
        group: Optional primitive group to apply material to (empty = all primitives)

    Returns:
        Dict with geometry_path, material_path, material_sop_path, method

    Examples:
        assign_material("/obj/geo1", "/mat/red_metal")
        assign_material("/obj/geo1", "/mat/gold", group="top_faces")
    """
    return tools.assign_material(geometry_path, material_path, group, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def get_material_info(material_path: str) -> Dict[str, Any]:
    """
    Get detailed information about a material node.

    Returns material type, parameters, and texture references.

    Args:
        material_path: Path to the material node (e.g., "/mat/principledshader1")

    Returns:
        Dict with:
        - material_path: Path to the material
        - material_name: Name of the material
        - material_type: Type of material (e.g., "principledshader")
        - parameters: Dict of parameter names to current values
        - textures: List of texture file references found

    Examples:
        get_material_info("/mat/principledshader1")
    """
    return tools.get_material_info(material_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def layout_children(
    node_path: str,
    horizontal_spacing: float = 2.0,
    vertical_spacing: float = 1.0,
) -> Dict[str, Any]:
    """
    Auto-layout child nodes in a network.

    Calls Houdini's built-in layoutChildren() to automatically arrange
    child nodes in a clean, organized layout.

    Args:
        node_path: Path to the parent node (e.g., "/obj/geo1")
        horizontal_spacing: Horizontal spacing between nodes (default: 2.0)
        vertical_spacing: Vertical spacing between nodes (default: 1.0)

    Returns:
        Dict with node_path and child_count

    Examples:
        layout_children("/obj/geo1")
        layout_children("/obj/geo1", horizontal_spacing=3.0, vertical_spacing=2.0)
    """
    return tools.layout_children(
        node_path, horizontal_spacing, vertical_spacing, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def set_node_color(node_path: str, color: List[float]) -> Dict[str, Any]:
    """
    Set the display color of a node in the network editor.

    Args:
        node_path: Path to the node (e.g., "/obj/geo1/sphere1")
        color: RGB color values as [r, g, b] where each value is 0.0-1.0

    Returns:
        Dict with node_path and color

    Examples:
        set_node_color("/obj/geo1/sphere1", [1, 0, 0])  # Red
        set_node_color("/obj/geo1/important", [1, 1, 0])  # Yellow
    """
    return tools.set_node_color(node_path, color, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def set_node_position(node_path: str, x: float, y: float) -> Dict[str, Any]:
    """
    Set the position of a node in the network editor.

    Args:
        node_path: Path to the node (e.g., "/obj/geo1/sphere1")
        x: X position in network editor units
        y: Y position in network editor units

    Returns:
        Dict with node_path and position

    Examples:
        set_node_position("/obj/geo1/sphere1", 0, 0)
        set_node_position("/obj/geo1/sphere1", 5.0, -3.0)
    """
    return tools.set_node_position(node_path, x, y, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def create_network_box(
    parent_path: str,
    node_paths: List[str],
    label: str = "",
    color: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Create a network box around a group of nodes.

    Network boxes help organize and visually group related nodes.

    Args:
        parent_path: Path to the parent network (e.g., "/obj/geo1")
        node_paths: List of node paths to include in the box
        label: Optional label text for the network box
        color: Optional RGB color [r, g, b] for the box (0.0-1.0 each)

    Returns:
        Dict with network_box_name, nodes_contained, label

    Examples:
        create_network_box("/obj/geo1", ["/obj/geo1/sphere1", "/obj/geo1/noise1"], "Deform")
    """
    return tools.create_network_box(
        parent_path, node_paths, label, color, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def manage_cache(action: str = "stats") -> Dict[str, Any]:
    """
    Manage the Houdini MCP cache system.

    The cache stores frequently-accessed data like node types and parameter
    schemas to improve performance. Node types are cached on first access
    and subsequent calls are instant.

    Args:
        action: Cache action to perform:
            - "stats": Get cache statistics (hits, misses, entry counts)
            - "invalidate": Clear all caches (forces refresh on next access)
            - "warmup": Pre-populate caches (may take a few seconds)

    Returns:
        Dict with cache information including:
        - action: The action that was performed
        - For "stats": Cache statistics for node_types and parameter_schemas
        - For "invalidate": Confirmation message
        - For "warmup": Warmup timing and entry counts

    Examples:
        manage_cache()  # Get cache stats (default)
        manage_cache("stats")  # Same as above
        manage_cache("invalidate")  # Clear caches
        manage_cache("warmup")  # Pre-populate caches
    """
    import time

    if action == "stats":
        return {
            "status": "success",
            "action": "stats",
            "caches": tools.get_cache_stats(),
        }

    elif action == "invalidate":
        tools.invalidate_all_caches()
        return {
            "status": "success",
            "action": "invalidate",
            "message": "All caches invalidated. Data will be refreshed on next access.",
        }

    elif action == "warmup":
        start = time.time()
        try:
            hou = ensure_connected(HOUDINI_HOST, HOUDINI_PORT)
            # Populate node type cache
            tools.node_type_cache.get_all_types(hou, HOUDINI_HOST, HOUDINI_PORT)
            elapsed_ms = (time.time() - start) * 1000
            return {
                "status": "success",
                "action": "warmup",
                "message": "Caches warmed up successfully",
                "warmup_time_ms": round(elapsed_ms, 1),
                "caches": tools.get_cache_stats(),
            }
        except Exception as e:
            return {
                "status": "error",
                "action": "warmup",
                "message": f"Failed to warm up caches: {str(e)}",
            }

    else:
        return {
            "status": "error",
            "action": action,
            "message": f"Unknown action: {action}. Valid actions: stats, invalidate, warmup",
        }


@mcp.tool()
def get_summarization_status() -> Dict[str, Any]:
    """
    Get AI summarization configuration and status.

    Returns current summarization settings including:
    - Whether summarization is enabled
    - Which model is being used
    - Proxy URL
    - Token thresholds for automatic summarization

    Use this to verify AI summarization is properly configured
    before using summarize=True on other tools.

    Returns:
        Dict with summarization configuration including:
        - enabled: Whether AI summarization is active
        - model: Claude model used for summarization
        - proxy_url: URL of the Claude API proxy
        - auto_threshold_tokens: Token count that triggers auto-summarization
        - target_summary_tokens: Target size for generated summaries
    """
    return tools.get_summarization_status()


@mcp.tool()
def capture_pane_screenshot(
    pane_type_name: str = "NetworkEditor",
    save_path: Optional[str] = None,
    fit_contents: bool = False,
) -> Dict[str, Any]:
    """
    Capture a screenshot of a Houdini pane tab.

    Uses Qt screen grab to capture the specified pane type. Returns
    base64-encoded PNG or saves to disk if save_path provided.

    Args:
        pane_type_name: Pane type to capture (default: "NetworkEditor")
            Common types: NetworkEditor, SceneViewer, Parm, IPRViewer,
            CompositorViewer, ChannelEditor, PythonShell, Textport
        save_path: Optional path to save PNG file (returns base64 if not provided)
        fit_contents: If True, fit/frame all contents before capture (default: False).
            When False, captures the current view (what the user is looking at).
            Supported for: NetworkEditor, SceneViewer, CompositorViewer, ChannelEditor.

    Returns:
        Dict with status, geometry, and either image_base64 or file_path

    Note:
        Panes on inactive desktops cannot be captured (invalid geometry).
        Use list_visible_panes() to find capturable panes.
    """
    return tools.capture_pane_screenshot(
        pane_type_name, save_path, fit_contents, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def list_visible_panes() -> Dict[str, Any]:
    """
    List all visible pane tabs in the current Houdini layout.

    Returns information about all panes across all desktops, including
    which ones are capturable (visible on current desktop with valid geometry).

    Returns:
        Dict with:
        - current_desktop: Name of active desktop
        - panes: List of pane info (name, type, desktop, is_visible, capturable)
        - capturable_count: Number of panes that can be captured
        - total_count: Total number of panes
        - available_types: List of all valid pane type names
    """
    return tools.list_visible_panes(HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def capture_multiple_panes(
    pane_types: List[str],
    save_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Capture screenshots of multiple pane types in one call.

    More efficient than calling capture_pane_screenshot multiple times
    as it reuses Qt module references.

    Args:
        pane_types: List of pane type names to capture
            e.g., ["NetworkEditor", "SceneViewer", "Parm"]
        save_dir: Optional directory to save PNGs (returns base64 if not provided)

    Returns:
        Dict with:
        - status: "success" if any captures succeeded
        - success_count: Number of successful captures
        - total_requested: Number of pane types requested
        - results: Per-pane results with image_base64 or file_path
    """
    return tools.capture_multiple_panes(pane_types, save_dir, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def render_node_network(
    node_path: str = "/obj",
    fit_contents: bool = True,
) -> Dict[str, Any]:
    """
    Capture a screenshot of a node's network showing its children.

    Navigates the NetworkEditor to the specified node and captures a screenshot.
    Useful for visualizing the network structure of any node context.

    Args:
        node_path: Path to the node whose network to capture (default: "/obj").
            The NetworkEditor will navigate into this node to show its children.
            Examples: "/obj", "/obj/geo1", "/stage", "/mat"
        fit_contents: If True, frame all children in view before capture (default: True).
            Set to False to capture the current zoom/pan state.

    Returns:
        Dict with:
        - status: "success" or "error"
        - node_path: The node path that was navigated to
        - child_count: Number of children in the network
        - geometry: x, y, width, height of the captured region
        - image_base64: Base64-encoded PNG image data

    Example:
        render_node_network("/obj")  # Capture /obj network
        render_node_network("/obj/geo1")  # Capture SOPs inside geo1
        render_node_network("/stage")  # Capture LOPs/Solaris network
    """
    return tools.render_node_network(node_path, fit_contents, HOUDINI_HOST, HOUDINI_PORT)


# =============================================================================
# Crowd Simulation Tools
# =============================================================================


@mcp.tool()
def setup_crowd_agent(
    agent_name: str,
    clip_paths: Optional[List[str]] = None,
    parent_path: str = "/obj",
    agent_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a complete agent definition chain for crowd simulation.

    Builds the standard agent pipeline: agent -> agentclip -> agentprep -> agentdefinitioncache.
    All nodes are created inside a geometry container, wired, and the display flag is set.

    Args:
        agent_name: Name for the agent (used for container and node naming)
        clip_paths: Optional list of motion clip file paths (.bclip, .fbx)
        parent_path: Parent path for the geo container (default: "/obj")
        agent_file: Optional path to agent definition file (.agent, .fbx)

    Returns:
        Dict with geo_path, agent_path, prep_path, cache_path, clip_path (if clips provided)

    Examples:
        setup_crowd_agent("soldier")
        setup_crowd_agent("civilian", agent_file="/path/to/char.fbx",
                         clip_paths=["/path/to/walk.bclip"])
    """
    return tools.setup_crowd_agent(
        agent_name, clip_paths, parent_path, agent_file, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def setup_crowd_source(
    parent_path: str,
    agent_setup_path: str,
    source_type: str = "scatter",
    count: int = 100,
    initial_state: str = "idle",
) -> Dict[str, Any]:
    """
    Create crowd source/placement setup.

    Sets up how agents are scattered in the scene. Creates a crowdsource SOP
    linked to the agent definition.

    Args:
        parent_path: Path to the crowd sim geo container (e.g., "/obj/crowd_sim")
        agent_setup_path: Path to agent definition cache node
        source_type: "scatter" (random on grid) or "points" (from existing points)
        count: Number of agents to scatter (default: 100)
        initial_state: Initial crowd state name (default: "idle")

    Returns:
        Dict with geo_path, source_path, scatter_path, grid_path

    Examples:
        setup_crowd_source("/obj/crowd_sim", "/obj/soldier_agent_setup/soldier_cache")
    """
    return tools.setup_crowd_source(
        parent_path, agent_setup_path, source_type, count, initial_state,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def setup_crowd_dop(
    parent_path: str = "/obj",
    crowd_source_path: Optional[str] = None,
    solver_substeps: int = 1,
) -> Dict[str, Any]:
    """
    Create a complete DOP simulation network for crowd.

    Builds: dopnet with crowdobject, crowdsolver, groundplane, and gravity.

    Args:
        parent_path: Parent path for the dopnet (default: "/obj")
        crowd_source_path: Optional path to crowd source SOP for linking
        solver_substeps: Number of simulation substeps (default: 1)

    Returns:
        Dict with dopnet_path, crowdobject_path, crowdsolver_path,
        groundplane_path, gravity_path

    Examples:
        setup_crowd_dop("/obj", "/obj/crowd_sim/crowd_source")
    """
    return tools.setup_crowd_dop(
        parent_path, crowd_source_path, solver_substeps, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def add_crowd_behavior(
    dopnet_path: str,
    behavior_type: str,
    behavior_name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Add a POP Steer behavior to the crowd solver.

    Creates a steering behavior that controls how agents move.

    Args:
        dopnet_path: Path to the DOP network
        behavior_type: Type of behavior:
            "wander", "seek", "avoid", "follow_path",
            "separate", "cohesion", "alignment", "obstacle"
        behavior_name: Optional custom name
        settings: Optional parameter values (e.g., {"maxforce": 1.0, "maxspeed": 2.0})

    Returns:
        Dict with behavior_path, behavior_type, settings_applied

    Examples:
        add_crowd_behavior("/obj/crowd_sim_dopnet", "wander")
        add_crowd_behavior("/obj/crowd_sim_dopnet", "seek",
                          settings={"targetposition": [10, 0, 0]})
    """
    return tools.add_crowd_behavior(
        dopnet_path, behavior_type, behavior_name, settings, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def add_crowd_state(
    dopnet_path: str,
    state_name: str,
    clip_name: Optional[str] = None,
    locomotion: bool = False,
    playback_speed: float = 1.0,
    variance: float = 0.0,
) -> Dict[str, Any]:
    """
    Add a Crowd State to the simulation's state machine.

    Crowd States define animation states (walk, idle, run) and are essential for
    agent animation. Without states, agents will not animate even with behaviors.
    States are automatically merged and wired to the Crowd Solver's 3rd input.

    Args:
        dopnet_path: Path to the DOP network
        state_name: State name (e.g., "walk", "idle", "run"). Also used as the
            default clip name.
        clip_name: Optional explicit clip name (default: uses state_name)
        locomotion: If True, sets Locomotive playback for in-place clips
        playback_speed: Clip speed multiplier (default: 1.0)
        variance: Random speed variance 0.0-1.0 for natural movement (default: 0.0)

    Returns:
        Dict with state_path, state_name, clip_name, merge_path, solver_wired

    Examples:
        add_crowd_state("/obj/crowd_sim_dopnet", "idle")
        add_crowd_state("/obj/crowd_sim_dopnet", "walk", locomotion=True)
        add_crowd_state("/obj/crowd_sim_dopnet", "run", locomotion=True, variance=0.1)
    """
    return tools.add_crowd_state(
        dopnet_path, state_name, clip_name, locomotion, playback_speed, variance,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def add_crowd_transition(
    dopnet_path: str,
    from_state: str,
    to_state: str,
    trigger_type: str,
    trigger_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a state transition with trigger for the crowd state machine.

    Defines when agents change behavioral states.

    Args:
        dopnet_path: Path to the DOP network
        from_state: Source state name (e.g., "idle")
        to_state: Destination state name (e.g., "walk")
        trigger_type: Trigger type:
            "distance", "time", "random", "speed", "attribute", "custom"
        trigger_settings: Optional trigger parameters, e.g.:
            - time: {"mintime": 2.0, "maxtime": 5.0}
            - random: {"probability": 0.1}
            - speed: {"attribute": "speed", "threshold": 0.5}

    Returns:
        Dict with trigger_path, transition_path, from_state, to_state

    Examples:
        add_crowd_transition("/obj/dopnet", "idle", "walk", "time",
                           {"mintime": 2.0, "maxtime": 5.0})
    """
    return tools.add_crowd_transition(
        dopnet_path, from_state, to_state, trigger_type, trigger_settings,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def setup_crowd_terrain(
    parent_path: str,
    terrain_source: Optional[str] = None,
    enable_foot_locking: bool = True,
) -> Dict[str, Any]:
    """
    Set up terrain for crowd simulation with terrain adaptation.

    Creates ground geometry and adaptation nodes for proper agent-terrain interaction.

    Args:
        parent_path: Path to the crowd sim geo container
        terrain_source: Optional path to existing terrain geometry.
            If not provided, creates a flat grid.
        enable_foot_locking: Enable foot locking for terrain adaptation (default: True)

    Returns:
        Dict with terrain_path, adaptation_path

    Examples:
        setup_crowd_terrain("/obj/crowd_sim")
        setup_crowd_terrain("/obj/crowd_sim", terrain_source="/obj/terrain/output")
    """
    return tools.setup_crowd_terrain(
        parent_path, terrain_source, enable_foot_locking, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def get_crowd_info(
    root_path: str = "/obj",
) -> Dict[str, Any]:
    """
    Scan and report existing crowd simulation setup.

    Finds all crowd-related nodes and reports their configuration.

    Args:
        root_path: Root path to scan from (default: "/obj")

    Returns:
        Dict with agent_definitions, crowd_sources, dop_networks,
        behaviors, transitions, triggers, total_crowd_nodes

    Examples:
        get_crowd_info()  # Scan all of /obj
        get_crowd_info("/obj/my_crowd_setup")  # Scan subtree
    """
    return tools.get_crowd_info(root_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def validate_crowd_setup(
    root_path: str = "/obj",
) -> Dict[str, Any]:
    """
    Validate crowd setup and check for common mistakes.

    Checks for missing components, unconnected nodes, and configuration issues.

    Args:
        root_path: Root path to validate from (default: "/obj")

    Returns:
        Dict with is_valid, issues (with severity/category/message/fix_hint),
        summary, issue_counts

    Examples:
        validate_crowd_setup()
        validate_crowd_setup("/obj/my_crowd")
    """
    return tools.validate_crowd_setup(root_path, HOUDINI_HOST, HOUDINI_PORT)


@mcp.tool()
def setup_crowd_formation(
    parent_path: str,
    agent_setup_path: str,
    rows: int = 5,
    columns: int = 5,
    spacing: float = 2.0,
    initial_state: str = "idle",
) -> Dict[str, Any]:
    """
    Create formation-based crowd placement.

    Sets up agents in a structured grid formation instead of random scattering.

    Args:
        parent_path: Path to the geometry container (e.g., "/obj/crowd_sim")
        agent_setup_path: Path to the agent definition cache node
        rows: Number of rows in the formation (default: 5)
        columns: Number of columns in the formation (default: 5)
        spacing: Distance between agents (default: 2.0)
        initial_state: Initial crowd state name (default: "idle")

    Returns:
        Dict with geo_path, source_path, agent_count, formation config

    Examples:
        setup_crowd_formation("/obj/crowd_sim", "/obj/agent_setup/cache",
                             rows=10, columns=10, spacing=3.0)
    """
    return tools.setup_crowd_formation(
        parent_path, agent_setup_path, rows, columns, spacing, initial_state,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def add_crowd_clip_transition_graph(
    parent_path: str,
    agent_setup_path: str,
    clip_names: Optional[List[str]] = None,
    blend_frames: int = 10,
    auto_compute: bool = True,
) -> Dict[str, Any]:
    """
    Create a Clip Transition Graph SOP for smooth animation blending.

    Defines optimal transition points between clips for seamless state changes.

    Args:
        parent_path: Path to the agent setup geometry container
        agent_setup_path: Path to the agent definition cache node
        clip_names: Optional list of clip names to include
        blend_frames: Number of frames for transition blending (default: 10)
        auto_compute: Auto-compute sync frames from foot timing (default: True)

    Returns:
        Dict with graph_path, cache_path, blend_frames, auto_compute

    Examples:
        add_crowd_clip_transition_graph("/obj/agent_setup", "/obj/agent_setup/cache")
    """
    return tools.add_crowd_clip_transition_graph(
        parent_path, agent_setup_path, clip_names, blend_frames, auto_compute,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def add_crowd_layer(
    parent_path: str,
    agent_setup_path: str,
    layer_name: str,
    shape_geometry_path: Optional[str] = None,
    transform_name: Optional[str] = None,
    layer_assignments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Set up agent layers for visual diversity (props, clothing variants).

    Creates agentlayer SOP for adding geometry layers and optionally
    crowdassignlayers SOP for random layer assignment.

    Args:
        parent_path: Path to the agent setup geometry container
        agent_setup_path: Path to the agent definition cache node
        layer_name: Name for the layer (e.g., "sword", "shield", "hat")
        shape_geometry_path: Optional path to geometry SOP for the layer shape
        transform_name: Optional joint name to attach the shape to (e.g., "RightHand")
        layer_assignments: Optional list of assignment configs with "name" and "weight"

    Returns:
        Dict with layer_path, assign_path (if assignments), cache_path, layer_name

    Examples:
        add_crowd_layer("/obj/agent_setup", agent_cache, "sword",
                       transform_name="RightHand")
    """
    return tools.add_crowd_layer(
        parent_path, agent_setup_path, layer_name, shape_geometry_path,
        transform_name, layer_assignments, HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def setup_crowd_ragdoll(
    agent_setup_path: str,
    dopnet_path: str,
    collision_shape: str = "capsule",
    create_joints: bool = True,
) -> Dict[str, Any]:
    """
    Set up ragdoll physics for crowd agents.

    Creates SOP-level collision layer and joint configuration, plus DOP-level
    Multisolver + Bullet Solver setup for ragdoll transitions.

    Args:
        agent_setup_path: Path to the agent setup geometry container
        dopnet_path: Path to the DOP network with crowd solver
        collision_shape: Default collision shape: "capsule", "box", or "sphere"
        create_joints: If True, create joint configuration node (default: True)

    Returns:
        Dict with collision_layer_path, configure_joints_path, multisolver_path,
        bullet_solver_path, ragdoll_state_path

    Examples:
        setup_crowd_ragdoll("/obj/soldier_agent_setup", "/obj/crowd_sim_dopnet")
    """
    return tools.setup_crowd_ragdoll(
        agent_setup_path, dopnet_path, collision_shape, create_joints,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def add_crowd_look_at(
    dopnet_path: str,
    target_position: Optional[List[float]] = None,
    target_node_path: Optional[str] = None,
    eye_offset: Optional[List[float]] = None,
    head_joint: str = "Head",
) -> Dict[str, Any]:
    """
    Add Agent Look At behavior to the crowd simulation.

    Creates agentlookat and agentlookatapply DOP nodes for head tracking.

    Args:
        dopnet_path: Path to the DOP network
        target_position: Optional [x, y, z] target position
        target_node_path: Optional path to a target node
        eye_offset: Optional [x, y, z] offset from head joint
        head_joint: Name of the head joint (default: "Head")

    Returns:
        Dict with lookat_path, apply_path, target

    Examples:
        add_crowd_look_at("/obj/crowd_sim_dopnet", target_position=[10, 2, 0])
    """
    return tools.add_crowd_look_at(
        dopnet_path, target_position, target_node_path, eye_offset, head_joint,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def setup_motion_path(
    parent_path: str,
    agent_setup_path: str,
    clip_name: str = "walk",
    clip_speed: float = 1.0,
    start_frame: int = 1,
    end_frame: int = 240,
    enable_avoid: bool = False,
) -> Dict[str, Any]:
    """
    Create SOP-based Motion Path workflow for crowd (Houdini 20+).

    Creates the chain: crowdmotionpath -> (optional avoid) -> crowdmotionpathevaluate.
    Art-directable alternative to DOP simulation for fast iteration.

    Args:
        parent_path: Path to the geometry container
        agent_setup_path: Path to the agent definition/source node
        clip_name: Animation clip name (default: "walk")
        clip_speed: Clip playback speed (default: 1.0)
        start_frame: Start frame for path generation (default: 1)
        end_frame: End frame for path generation (default: 240)
        enable_avoid: Add collision avoidance between agents (default: False)

    Returns:
        Dict with motion_path_path, evaluate_path, avoid_path (if enabled), clip_name

    Examples:
        setup_motion_path("/obj/crowd_sim", "/obj/agent_setup/cache",
                         clip_name="walk", end_frame=500)
    """
    return tools.setup_motion_path(
        parent_path, agent_setup_path, clip_name, clip_speed, start_frame,
        end_frame, enable_avoid, HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def add_steer_weight(
    dopnet_path: str,
    behavior_name: str,
    weight: float,
) -> Dict[str, Any]:
    """
    Set the steering weight on an existing POP Steer behavior node.

    Controls behavior influence relative to other steering behaviors.
    The Crowd Solver normalizes all weights automatically.

    Args:
        dopnet_path: Path to the DOP network
        behavior_name: Name of the POP Steer behavior node
        weight: Weight value (0.0-10.0). Higher = more influence.

    Returns:
        Dict with behavior_path, weight, behavior_type

    Examples:
        add_steer_weight("/obj/crowd_sim_dopnet", "steer_wander", 0.5)
    """
    return tools.add_steer_weight(
        dopnet_path, behavior_name, weight, HOUDINI_HOST, HOUDINI_PORT
    )


@mcp.tool()
def configure_foot_planting(
    agent_setup_path: str,
    ankle_joint: str = "LeftAnkle",
    toe_joint: str = "LeftToe",
    upper_leg_joint: str = "LeftUpperLeg",
    knee_joint: str = "LeftKnee",
    enable_foot_locking: bool = True,
    adjust_hips: bool = True,
) -> Dict[str, Any]:
    """
    Configure foot planting for crowd terrain adaptation.

    Sets up Agent Prep SOP's lower limb definitions for natural foot-ground contact.
    Prevents foot skating and floating on terrain.

    Args:
        agent_setup_path: Path to the agent setup geometry container
        ankle_joint: Ankle joint name (default: "LeftAnkle")
        toe_joint: Toe joint name (default: "LeftToe")
        upper_leg_joint: Upper leg joint name (default: "LeftUpperLeg")
        knee_joint: Knee joint name (default: "LeftKnee")
        enable_foot_locking: Enable foot locking (default: True)
        adjust_hips: Enable hip height adjustment (default: True)

    Returns:
        Dict with prep_path, joints_configured, foot_locking, hip_adjustment

    Examples:
        configure_foot_planting("/obj/soldier_agent_setup")
    """
    return tools.configure_foot_planting(
        agent_setup_path, ankle_joint, toe_joint, upper_leg_joint, knee_joint,
        enable_foot_locking, adjust_hips, HOUDINI_HOST, HOUDINI_PORT,
    )


# =============================================================================
# Workflow Management Tools
# =============================================================================


@mcp.tool()
def ensure_project(project_root: Optional[str] = None) -> Dict[str, Any]:
    """
    Initialize or verify the .agent/ workflow management directory.

    Auto-bootstraps a new .agent/ directory with template files if it doesn't exist.
    Repairs partial setups by creating any missing files. Includes a readiness check
    to determine if spec.md and stages.yaml are sufficiently defined for implementation.

    Args:
        project_root: Optional path to the project root directory.
            If not provided, uses the current working directory.

    Returns:
        Dict with:
        - status: "success" or "error"
        - project_root: Resolved project root path
        - bootstrapped: True if .agent/ was created from scratch
        - repaired_files: List of files that were repaired/created
        - ready_for_implementation: Whether spec and stages are defined
        - missing_requirements: List of missing items preventing readiness
        - next_action: Suggested next step if not ready
        - active_stage: Currently active stage ID or null
        - stage_summary: Map of stage_id -> status

    Examples:
        ensure_project()
        ensure_project("/path/to/project")
    """
    return tools.workflow_ensure_project(project_root)


@mcp.tool()
def read_project(
    target: str = "all",
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read project configuration files as structured data.

    Reads spec.md, stages.yaml, contracts.yaml, and/or build_state.json
    and returns their contents in a structured format. Always includes
    readiness check information.

    Args:
        target: What to read:
            - "spec": Project specification (Markdown)
            - "stages": Workflow stages (YAML parsed to dict)
            - "contracts": Artifact contracts (YAML parsed to dict)
            - "build_state": Current build state (JSON parsed to dict)
            - "all": All of the above
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with requested data plus readiness info

    Examples:
        read_project("spec")
        read_project("build_state")
        read_project("all")
    """
    return tools.workflow_read_project(target, project_root)


@mcp.tool()
def plan_stage(
    stage_id: str,
    description: str,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a plan for a workflow stage and start it.

    Writes a plan file to .agent/stage_plans/{stage_id}.md and transitions
    the stage from 'pending' to 'in_progress'. Refuses if the project is
    not ready for implementation (spec/stages not defined).

    Args:
        stage_id: Stage ID matching an entry in stages.yaml
        description: Markdown description of what this stage will do
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with plan file path and updated stage status

    Examples:
        plan_stage("S01", "## Create base terrain\\n- Add heightfield\\n- Apply noise")
    """
    return tools.workflow_plan_stage(stage_id, description, project_root)


@mcp.tool()
def record_scan(
    scan_data: Dict[str, Any],
    stage_id: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record a Houdini scene scan result.

    Saves the output from get_scene_info, serialize_scene, or similar
    Houdini MCP tools as a timestamped JSON file in .agent/scene_scans/.

    Args:
        scan_data: Scene data from Houdini MCP tools (e.g., get_scene_info output)
        stage_id: Optional stage to associate the scan with.
            If not provided, uses the active stage.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with scan file path

    Examples:
        record_scan({"nodes": [...], "hip_file": "scene.hip"})
        record_scan(scene_info, stage_id="S01")
    """
    return tools.workflow_record_scan(scan_data, stage_id, project_root)


@mcp.tool()
def validate_stage(
    stage_id: str,
    mode: str = "light",
    scan_data: Optional[Dict[str, Any]] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run validation checks on a workflow stage.

    Light mode checks (default):
    1. Plan file exists for the stage
    2. A scene scan has been recorded
    3. Artifact contracts are satisfied (future)
    4. All dependency stages are completed

    Strict mode adds:
    5. Acceptance criteria defined for the stage
    6. Definition of Done in spec.md is non-placeholder

    Use "strict" for normal stages before completion.
    Use "light" for spike stages or quick checks.

    Args:
        stage_id: Stage ID to validate
        mode: Validation mode: "light" (default) or "strict"
        scan_data: Optional fresh scan data (currently unused)
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with:
        - passed: Overall pass/fail
        - mode: Validation mode used
        - checks: List of individual check results

    Examples:
        validate_stage("S01")
        validate_stage("S01", mode="strict")
    """
    return tools.workflow_validate_stage(stage_id, mode, scan_data, project_root)


@mcp.tool()
def update_stage(
    stage_id: str,
    status: str,
    reason: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update a workflow stage's status with transition rule enforcement.

    Enforces the state machine: pending -> in_progress -> validated -> completed.
    Failed stages can return to in_progress. Setting 'validated' requires a
    passing validation. Setting 'completed' requires 'validated' status.

    Args:
        stage_id: Stage ID to update
        status: Target status: "pending", "in_progress", "validated",
            "failed", or "completed"
        reason: Optional reason for the transition
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with previous and new status

    Examples:
        update_stage("S01", "validated")
        update_stage("S01", "completed")
        update_stage("S01", "failed", reason="Node connections broken")
    """
    return tools.workflow_update_stage(stage_id, status, reason, project_root)


@mcp.tool()
def propose_diff(
    stage_id: str,
    diff_spec: Dict[str, Any],
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Propose a set of changes for approval before execution.

    Creates a structured diff proposal that must be approved via approve_diff()
    before change tools (create_node, set_parameter, etc.) can be used.
    This enforces the "diff proposal → user OK → execute" workflow.

    Args:
        stage_id: Stage ID this diff belongs to (must be the active in-progress stage)
        diff_spec: Dict describing planned changes:
            - nodes_to_add: list of {type, name, parent_path}
            - nodes_to_delete: list of node_paths to remove
            - connections_to_add: list of {src_path, dst_path, dst_input_index, src_output_index}
            - connections_to_remove: list of {node_path, input_index}
            - params_to_change: list of {node_path, param_name, value}
            - scope: affected network paths (string)
            - rationale: why this change is needed (string)
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with diff_id to use in approve_diff

    Examples:
        propose_diff("S01", {
            "nodes_to_add": [{"type": "sphere", "name": "sphere1", "parent_path": "/obj/geo1"}],
            "params_to_change": [{"node_path": "/obj/geo1/sphere1", "param_name": "radx", "value": 2.0}],
            "scope": "/obj/geo1",
            "rationale": "Add sphere with custom radius for base shape"
        })
    """
    return tools.workflow_propose_diff(stage_id, diff_spec, project_root)


@mcp.tool()
def approve_diff(
    diff_id: str,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Approve a pending diff proposal, unlocking change tools.

    After a diff is proposed via propose_diff() and the user has reviewed
    and approved it, call this to unlock change tools. Without approval,
    all Houdini change tools will be blocked by the PreToolUse hook.

    Args:
        diff_id: The diff ID returned by propose_diff
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with approval confirmation

    Examples:
        approve_diff("S01_20260228_abc123de")
    """
    return tools.workflow_approve_diff(diff_id, project_root)


@mcp.tool()
def safe_set_parameter(
    node_path: str,
    param_name: str,
    value: Union[float, int, str, bool, List[float], List[int], List[str]],
    diff_id: str,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set a parameter with automatic schema validation and Japanese help.

    Combines get_parameter_schema(include_description_ja=True) + validation +
    set_parameter into a single safe operation. Validates:
    1. Parameter exists in node's schema
    2. Value type matches schema type
    3. Menu values are valid options
    4. Value is within min/max range
    5. Diff is approved

    Preferred over raw set_parameter. Raw set_parameter is blocked by the
    PreToolUse hook unless get_parameter_schema was called first.

    Args:
        node_path: Full path to the node (e.g., "/obj/geo1/sphere1")
        param_name: Parameter name from schema (e.g., "radx")
        value: Value to set (type must match schema)
        diff_id: Approved diff ID from approve_diff
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with status, validated schema info, and set result

    Examples:
        safe_set_parameter("/obj/geo1/sphere1", "radx", 2.5, "S01_20260228_abc123de")
    """
    return tools.workflow_safe_set_parameter(
        node_path, param_name, value, diff_id, project_root,
        HOUDINI_HOST, HOUDINI_PORT,
    )


@mcp.tool()
def update_spec_and_stages(
    change_summary: str,
    spec_patch: Optional[Dict[str, Any]] = None,
    stages_patch: Optional[Dict[str, Any]] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update spec.md and/or stages.yaml with change tracking.

    Supports atomic updates with automatic changelog, event recording,
    and optional ADR (Architecture Decision Record) creation.

    Use this tool when:
    - Changing direction mid-project (VEX→OpenCL, Volume→Grid, etc.)
    - Adding/modifying stages for agile iteration
    - Recording architectural decisions
    - Updating spec sections (Definition of Done, Requirements, etc.)

    Args:
        change_summary: Human-readable summary of the change.
        spec_patch: Optional spec.md modifications:
            - changelog_entry: {reason: str, impact: str}
            - section_updates: {section_name: new_content}
            - dod_append: str (appends to Definition of Done)
            - decision: {context, decision, alternatives, consequences}
        stages_patch: Optional stages.yaml modifications:
            - add_stage: {id, name, purpose, depends_on, produces, consumes,
                          mode, notes, replaces, parallel_with, acceptance}
            - update_stage: {id, ...fields_to_update}
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with files_updated list and change_summary

    Examples:
        update_spec_and_stages(
            "Switch solver approach from VEX to OpenCL",
            spec_patch={"changelog_entry": {"reason": "Performance", "impact": "S02 rebuilt"}},
            stages_patch={"add_stage": {"id": "S02b", "name": "OpenCL Solver", "purpose": "GPU solver", "depends_on": ["S01"], "produces": ["volume"], "consumes": ["heightfield"], "mode": "spike"}}
        )
    """
    return tools.workflow_update_spec_and_stages(
        change_summary, spec_patch, stages_patch, project_root,
    )


@mcp.tool()
def record_norm_check(
    kind: str,
    summary: str,
    impact: str,
    reason: Optional[str] = None,
    links: Optional[str] = None,
    also_record_decision: bool = False,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record a norm check result (e.g. NotebookLM sanity check).

    Records whether a non-mandatory sanity check was consulted or skipped,
    leaving a traceable footprint in events.jsonl and CHANGELOG.md.
    This is a lightweight, non-blocking record — no gate enforcement.

    Args:
        kind: Norm type identifier. Use "notebooklm" for NotebookLM checks.
        summary: One-line summary of the check result or question asked.
        impact: Outcome of the check:
            - "adopt": Findings applied to current approach
            - "hold": Noted for later consideration
            - "reject": Reviewed but not applicable
            - "skipped": Check was not performed (use reason to explain why)
        reason: Why the check was skipped (when impact is "skipped").
            Common values: "low-risk", "confirmed-by-docs", "timebox", "other"
        links: Optional memo — notebook ID, reference note, etc.
        also_record_decision: If True, also creates an ADR entry in DECISIONS.md.
            Default False to keep things lightweight.
        project_root: Optional project root path. Defaults to cwd.

    Returns:
        Dict with:
        - status: "success", "skipped", or "error"
        - event_id: Generated event ID (when successful)
        - impact: The recorded impact value
        - recorded_to: List of files written to (e.g. ["events", "changelog"])

    Examples:
        record_norm_check("notebooklm", "Volume migration uses voxel O(1) access", "adopt")
        record_norm_check("notebooklm", "Parameter check", "skipped", reason="confirmed-by-docs")
    """
    return tools.workflow_record_norm_check(
        kind, summary, impact, reason, links, also_record_decision, project_root,
    )


@mcp.tool()
def doctor() -> Dict[str, Any]:
    """
    Run a full environment diagnosis for the Houdini MCP system.

    Checks the whole chain in dependency order: Python env, rpyc version,
    houdini_mcp install, Houdini process, RPC port, RPC roundtrip, and
    Houdini-side package install. Each failing check includes a concrete
    fix instruction, and the summary names the first failure to fix.

    Read-only and safe to call anytime -- works even when Houdini is down.
    Use this first whenever the connection misbehaves.
    """
    from .doctor import run_doctor

    return run_doctor(HOUDINI_HOST, HOUDINI_PORT)


def run_server(transport: str = "http", port: int = 3055) -> None:
    """Run the MCP server."""
    logger.info(f"Starting Houdini MCP Server on {transport}://0.0.0.0:{port}")
    logger.info(f"Houdini connection target: {HOUDINI_HOST}:{HOUDINI_PORT}")
    logger.info(f"Log level: {log_level}")

    # Cast transport to literal type for FastMCP
    transport_literal: Literal["stdio", "http", "sse", "streamable-http"] = "http"
    if transport in ("stdio", "http", "sse", "streamable-http"):
        transport_literal = transport  # type: ignore

    if transport_literal == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport_literal, host="0.0.0.0", port=port)


if __name__ == "__main__":
    run_server()
