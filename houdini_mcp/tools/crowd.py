"""Crowd simulation helper tools for Houdini.

This module provides high-level tools for setting up crowd simulations
in Houdini. Each tool handles a specific aspect of the crowd pipeline:

Agent Setup:
    - setup_crowd_agent: Create agent definition chain (agent -> clip -> prep -> cache)

Crowd Source:
    - setup_crowd_source: Configure crowd placement/scattering

DOP Simulation:
    - setup_crowd_dop: Build DOP network (crowdobject, crowdsolver, ground, gravity)
    - add_crowd_behavior: Add POP Steer behaviors (wander, seek, avoid, etc.)
    - add_crowd_transition: Create state transitions with triggers

Terrain:
    - setup_crowd_terrain: Set up terrain adaptation for agents

Introspection:
    - get_crowd_info: Scan and report existing crowd setup
    - validate_crowd_setup: Check for common mistakes and missing components

Typical crowd pipeline:
    1. setup_crowd_agent() -> agent definition
    2. setup_crowd_source() -> scatter agents on surface
    3. setup_crowd_dop() -> simulation network
    4. add_crowd_behavior() -> steering behaviors
    5. add_crowd_transition() -> state machine
    6. validate_crowd_setup() -> check for issues
"""

import logging
from typing import Any, Dict, List, Optional

from ._common import (
    ensure_connected,
    get_connection,
    HoudiniConnectionError,
    CONNECTION_ERRORS,
    _handle_connection_error,
    handle_connection_errors,
)

logger = logging.getLogger("houdini_mcp.tools.crowd")


# =============================================================================
# Behavior type mappings
# =============================================================================

# Maps human-friendly behavior names to Houdini DOP node types
BEHAVIOR_TYPE_MAP = {
    "wander": "popsteerwander",
    "seek": "popsteerseek",
    "avoid": "popsteeravoid",
    "follow_path": "popsteerfollowpath",
    "separate": "popsteerseparate",
    "cohesion": "popsteercohesion",
    "alignment": "popsteeralign",
    "obstacle": "popsteerobstacle",
}

# Maps trigger type names to Houdini crowdtrigger logic types
TRIGGER_TYPE_MAP = {
    "distance": "distancefromposition",
    "time": "timeinstate",
    "random": "random",
    "speed": "objectattribute",
    "attribute": "objectattribute",
    "custom": "custom",
}


# =============================================================================
# Agent Setup
# =============================================================================


@handle_connection_errors("setup_crowd_agent")
def setup_crowd_agent(
    agent_name: str,
    clip_paths: Optional[List[str]] = None,
    parent_path: str = "/obj",
    agent_file: Optional[str] = None,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create a complete agent definition chain for crowd simulation.

    Builds the standard Houdini crowd agent pipeline:
    agent -> agentclip (optional) -> agentprep -> agentdefinitioncache

    All nodes are created inside a new geometry container, wired together,
    and the display flag is set on the final cache node.

    Args:
        agent_name: Name for the agent (used for geo container and agent node)
        clip_paths: Optional list of clip file paths (.bclip, .fbx) to load.
            If provided, an agentclip node is created for additional clips.
        parent_path: Parent path to create the geo container in (default: "/obj")
        agent_file: Optional path to agent definition file (.agent) or FBX.
            If provided, sets the agent node's input parameter.
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - geo_path: Path to the geometry container
        - agent_path: Path to the agent SOP node
        - clip_path: Path to agentclip node (if clips provided)
        - prep_path: Path to agentprep node
        - cache_path: Path to agentdefinitioncache node
        - node_count: Number of nodes created

    Examples:
        # Basic agent from default character
        setup_crowd_agent("soldier")

        # Agent with FBX file and motion clips
        setup_crowd_agent("civilian", agent_file="/path/to/character.fbx",
                         clip_paths=["/path/to/walk.bclip", "/path/to/run.bclip"])
    """
    hou = ensure_connected(host, port)

    parent = hou.node(parent_path)
    if parent is None:
        return {"status": "error", "message": f"Parent node not found: {parent_path}"}

    # Create geometry container for the agent setup
    geo_name = f"{agent_name}_agent_setup"
    geo = parent.createNode("geo", geo_name)
    if geo is None:
        return {"status": "error", "message": f"Failed to create geo node: {geo_name}"}

    # Delete default file node if present
    file_node = geo.node("file1")
    if file_node:
        file_node.destroy()

    nodes_created = []

    # 1. Create agent node
    agent = geo.createNode("agent", f"{agent_name}_agent")
    nodes_created.append(agent)

    if agent_file:
        # Set input file if provided
        if agent.parm("agentname"):
            agent.parm("agentname").set(agent_name)
        if agent.parm("input"):
            agent.parm("input").set(1)  # File mode
        if agent.parm("agentfile"):
            agent.parm("agentfile").set(agent_file)

    last_node = agent
    clip_node = None

    # 2. Create agentclip node (optional, for additional clips)
    if clip_paths:
        clip_node = geo.createNode("agentclip", f"{agent_name}_clips")
        clip_node.setFirstInput(last_node)
        nodes_created.append(clip_node)

        # Configure clip loading
        if clip_node.parm("numclips"):
            clip_node.parm("numclips").set(len(clip_paths))
            for i, clip_path in enumerate(clip_paths):
                clip_parm = clip_node.parm(f"clipfile{i+1}")
                if clip_parm:
                    clip_parm.set(clip_path)

        last_node = clip_node

    # 3. Create agentprep node
    prep = geo.createNode("agentprep", f"{agent_name}_prep")
    prep.setFirstInput(last_node)
    nodes_created.append(prep)
    last_node = prep

    # 4. Create agentdefinitioncache node
    cache = geo.createNode("agentdefinitioncache", f"{agent_name}_cache")
    cache.setFirstInput(last_node)
    nodes_created.append(cache)

    # Set display/render flags on the final node
    cache.setDisplayFlag(True)
    cache.setRenderFlag(True)

    # Layout the nodes
    geo.layoutChildren()

    result = {
        "status": "success",
        "geo_path": geo.path(),
        "agent_path": agent.path(),
        "prep_path": prep.path(),
        "cache_path": cache.path(),
        "node_count": len(nodes_created) + 1,  # +1 for geo container
    }

    if clip_node:
        result["clip_path"] = clip_node.path()

    return result


# =============================================================================
# Crowd Source
# =============================================================================


@handle_connection_errors("setup_crowd_source")
def setup_crowd_source(
    parent_path: str,
    agent_setup_path: str,
    source_type: str = "scatter",
    count: int = 100,
    initial_state: str = "idle",
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create crowd source/placement setup.

    Sets up how agents are placed in the scene. Creates a crowdsource SOP
    that references the agent definition and configures initial placement.

    Args:
        parent_path: Path to the geometry container (e.g., "/obj/crowd_sim")
        agent_setup_path: Path to the agent definition cache node
            (e.g., "/obj/soldier_agent_setup/soldier_cache")
        source_type: Placement type - "scatter" (random on grid), "points" (from points)
        count: Number of agents to scatter (default: 100)
        initial_state: Initial crowd state name (default: "idle")
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - geo_path: Path to the crowd sim geometry container
        - source_path: Path to the crowdsource node
        - scatter_path: Path to the scatter node (if source_type="scatter")
        - grid_path: Path to the grid node (if source_type="scatter")

    Examples:
        setup_crowd_source("/obj/crowd_sim", "/obj/soldier_agent_setup/soldier_cache")
        setup_crowd_source("/obj/crowd_sim", agent_path, count=500, initial_state="walk")
    """
    hou = ensure_connected(host, port)

    # Check if parent exists; if not, create a new geo container
    parent = hou.node(parent_path)
    if parent is None:
        obj = hou.node("/obj")
        if obj is None:
            return {"status": "error", "message": "Cannot find /obj context"}
        parent_name = parent_path.split("/")[-1]
        parent = obj.createNode("geo", parent_name)
        # Delete default file node
        file_node = parent.node("file1")
        if file_node:
            file_node.destroy()

    # Verify agent setup path exists
    agent_node = hou.node(agent_setup_path)
    if agent_node is None:
        return {
            "status": "error",
            "message": f"Agent setup node not found: {agent_setup_path}. "
            "Run setup_crowd_agent() first.",
        }

    result = {
        "status": "success",
        "geo_path": parent.path(),
    }

    if source_type == "scatter":
        # Create grid for scattering surface
        grid = parent.createNode("grid", "crowd_scatter_grid")
        grid.parm("sizex").set(50.0)
        grid.parm("sizey").set(50.0)
        result["grid_path"] = grid.path()

        # Create scatter for agent placement
        scatter = parent.createNode("scatter", "crowd_scatter")
        scatter.setFirstInput(grid)
        scatter.parm("npts").set(count)
        result["scatter_path"] = scatter.path()

        # Create object merge to bring in agent definition
        obj_merge = parent.createNode("object_merge", "agent_definition_in")
        obj_merge.parm("objpath1").set(agent_setup_path)

        # Create crowdsource
        crowd_source = parent.createNode("crowdsource", "crowd_source")
        crowd_source.setInput(0, obj_merge)  # Agent definition
        crowd_source.setInput(1, scatter)    # Source points

    else:
        # Simple: just merge agent def and create source
        obj_merge = parent.createNode("object_merge", "agent_definition_in")
        obj_merge.parm("objpath1").set(agent_setup_path)

        crowd_source = parent.createNode("crowdsource", "crowd_source")
        crowd_source.setInput(0, obj_merge)

    # Configure initial state
    if crowd_source.parm("initialstate"):
        crowd_source.parm("initialstate").set(initial_state)

    # Set display flag
    crowd_source.setDisplayFlag(True)
    crowd_source.setRenderFlag(True)

    # Layout
    parent.layoutChildren()

    result["source_path"] = crowd_source.path()
    return result


# =============================================================================
# DOP Simulation
# =============================================================================


@handle_connection_errors("setup_crowd_dop")
def setup_crowd_dop(
    parent_path: str = "/obj",
    crowd_source_path: Optional[str] = None,
    solver_substeps: int = 1,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create a complete DOP simulation network for crowd.

    Builds a standard crowd DOP network:
    - dopnet containing: crowdobject + crowdsolver + groundplane + gravity
    - Optionally links to a crowd source for agent input

    Args:
        parent_path: Parent path for the dopnet (default: "/obj")
        crowd_source_path: Optional path to crowd source SOP.
            If provided, the dopnet's SOP Path is set to reference this source.
        solver_substeps: Number of simulation substeps (default: 1)
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - dopnet_path: Path to the DOP network
        - crowdobject_path: Path to the crowd object node
        - crowdsolver_path: Path to the crowd solver node
        - groundplane_path: Path to the ground plane node
        - gravity_path: Path to the gravity node

    Examples:
        setup_crowd_dop("/obj", "/obj/crowd_sim/crowd_source")
        setup_crowd_dop(solver_substeps=2)
    """
    hou = ensure_connected(host, port)

    parent = hou.node(parent_path)
    if parent is None:
        return {"status": "error", "message": f"Parent node not found: {parent_path}"}

    # Create DOP network
    dopnet = parent.createNode("dopnet", "crowd_sim_dopnet")
    if dopnet is None:
        return {"status": "error", "message": "Failed to create dopnet"}

    # Delete default nodes inside dopnet
    for child in dopnet.children():
        child.destroy()

    # Create crowd object
    crowd_obj = dopnet.createNode("crowdobject", "crowdobject1")

    # Set SOP path to crowd source if provided.
    # soppath must point to the crowdsource SOP node itself, NOT its geo container.
    if crowd_source_path:
        source_node = hou.node(crowd_source_path)
        if source_node and crowd_obj.parm("soppath"):
            crowd_obj.parm("soppath").set(crowd_source_path)

    # Create crowd solver
    crowd_solver = dopnet.createNode("crowdsolver", "crowdsolver1")
    crowd_solver.setFirstInput(crowd_obj)

    if crowd_solver.parm("substeps"):
        crowd_solver.parm("substeps").set(solver_substeps)

    # Create ground plane
    ground = dopnet.createNode("groundplane", "ground1")

    # Create gravity
    gravity = dopnet.createNode("gravity", "gravity1")

    # Wire: crowdsolver gets merge of crowd_obj, then ground and gravity
    # apply to same simulation
    # Standard DOP wiring: solver takes crowd object as first input
    # ground and gravity are merged separately and applied to simulation
    merge = dopnet.createNode("merge", "merge_forces")
    merge.setInput(0, crowd_solver)
    merge.setInput(1, ground)
    merge.setInput(2, gravity)

    # Layout
    dopnet.layoutChildren()

    return {
        "status": "success",
        "dopnet_path": dopnet.path(),
        "crowdobject_path": crowd_obj.path(),
        "crowdsolver_path": crowd_solver.path(),
        "groundplane_path": ground.path(),
        "gravity_path": gravity.path(),
    }


# =============================================================================
# Behaviors
# =============================================================================


@handle_connection_errors("add_crowd_behavior")
def add_crowd_behavior(
    dopnet_path: str,
    behavior_type: str,
    behavior_name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Add a POP Steer behavior to the crowd solver.

    Creates a steering behavior node inside the crowd solver's POP network.
    Behaviors control how agents move and interact with each other.

    Args:
        dopnet_path: Path to the DOP network (e.g., "/obj/crowd_sim_dopnet")
        behavior_type: Type of behavior to add. Supported types:
            - "wander": Random wandering movement
            - "seek": Move toward a target position
            - "avoid": Avoid obstacles or other agents
            - "follow_path": Follow a curve/path
            - "separate": Maintain distance from neighbors
            - "cohesion": Stay close to group center
            - "alignment": Align direction with neighbors
            - "obstacle": Avoid static obstacles
        behavior_name: Optional custom name (auto-generated if not provided)
        settings: Optional dict of parameter values to set on the behavior node.
            Common settings vary by type:
            - wander: {"maxforce": 1.0, "maxspeed": 2.0}
            - seek: {"targetposition": [10, 0, 0]}
            - avoid: {"avoidradius": 2.0}

    Returns:
        Dict with:
        - status: "success" or "error"
        - behavior_path: Path to the created behavior node
        - behavior_type: Type of behavior created
        - settings_applied: List of settings applied

    Examples:
        add_crowd_behavior("/obj/crowd_sim_dopnet", "wander")
        add_crowd_behavior("/obj/crowd_sim_dopnet", "seek",
                          settings={"targetposition": [10, 0, 0]})
    """
    hou = ensure_connected(host, port)

    # Validate behavior type
    dop_node_type = BEHAVIOR_TYPE_MAP.get(behavior_type.lower())
    if dop_node_type is None:
        return {
            "status": "error",
            "message": f"Unknown behavior type: {behavior_type}",
            "available_types": list(BEHAVIOR_TYPE_MAP.keys()),
        }

    dopnet = hou.node(dopnet_path)
    if dopnet is None:
        return {"status": "error", "message": f"DOP network not found: {dopnet_path}"}

    # Find the crowd solver (type may be versioned, e.g. "crowdsolver::3.0")
    crowd_solver = None
    for child in dopnet.children():
        if child.type().name().startswith("crowdsolver"):
            crowd_solver = child
            break

    if crowd_solver is None:
        return {
            "status": "error",
            "message": f"No crowdsolver found in {dopnet_path}. Run setup_crowd_dop() first.",
        }

    # Create behavior node inside the DOP network
    name = behavior_name or f"steer_{behavior_type}"
    behavior_node = dopnet.createNode(dop_node_type, name)
    if behavior_node is None:
        return {
            "status": "error",
            "message": f"Failed to create {dop_node_type} node. "
            "This node type may not be available in your Houdini version.",
        }

    # Wire behavior into the crowd solver's inputs.
    # Crowd solver input 0 = crowd object. Inputs 1+ = POP Steer behaviors.
    # We must connect behavior → solver (solver.setInput), NOT solver → behavior.
    existing_inputs = crowd_solver.inputs()
    next_input = len(existing_inputs)
    if next_input < 1:
        next_input = 1
    try:
        crowd_solver.setInput(next_input, behavior_node)
    except Exception as e:
        logger.warning(f"Could not wire behavior to solver input {next_input}: {e}")

    # Apply settings
    settings_applied = []
    if settings:
        for parm_name, value in settings.items():
            parm = behavior_node.parm(parm_name)
            if parm:
                try:
                    parm.set(value)
                    settings_applied.append({"name": parm_name, "value": value})
                except Exception as e:
                    logger.warning(f"Failed to set {parm_name}: {e}")

    dopnet.layoutChildren()

    return {
        "status": "success",
        "behavior_path": behavior_node.path(),
        "behavior_type": behavior_type,
        "dop_node_type": dop_node_type,
        "settings_applied": settings_applied,
    }


# =============================================================================
# State Transitions
# =============================================================================


@handle_connection_errors("add_crowd_transition")
def add_crowd_transition(
    dopnet_path: str,
    from_state: str,
    to_state: str,
    trigger_type: str,
    trigger_settings: Optional[Dict[str, Any]] = None,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create a state transition with trigger for the crowd state machine.

    Creates a crowdtrigger + crowdtransition pair inside the DOP network.
    Transitions define when agents change from one behavioral state to another.

    Args:
        dopnet_path: Path to the DOP network
        from_state: Name of the source state (e.g., "idle")
        to_state: Name of the destination state (e.g., "walk")
        trigger_type: Type of trigger. Supported types:
            - "distance": Trigger based on distance from a position
            - "time": Trigger after time spent in current state
            - "random": Random chance trigger
            - "speed": Trigger based on agent speed
            - "attribute": Trigger based on agent attribute value
            - "custom": Custom VEXpression trigger
        trigger_settings: Optional dict of trigger parameters.
            Common settings by type:
            - distance: {"distance": 5.0, "position": [0, 0, 0]}
            - time: {"mintime": 2.0, "maxtime": 5.0}
            - random: {"probability": 0.1}
            - speed: {"attribute": "speed", "threshold": 0.5}

    Returns:
        Dict with:
        - status: "success" or "error"
        - trigger_path: Path to the crowdtrigger node
        - transition_path: Path to the crowdtransition node
        - from_state: Source state name
        - to_state: Destination state name
        - trigger_type: Type of trigger used

    Examples:
        add_crowd_transition("/obj/crowd_sim_dopnet", "idle", "walk", "time",
                           {"mintime": 2.0, "maxtime": 5.0})
        add_crowd_transition("/obj/crowd_sim_dopnet", "walk", "run", "speed",
                           {"attribute": "speed", "threshold": 2.0})
    """
    hou = ensure_connected(host, port)

    # Validate trigger type
    if trigger_type.lower() not in TRIGGER_TYPE_MAP:
        return {
            "status": "error",
            "message": f"Unknown trigger type: {trigger_type}",
            "available_types": list(TRIGGER_TYPE_MAP.keys()),
        }

    dopnet = hou.node(dopnet_path)
    if dopnet is None:
        return {"status": "error", "message": f"DOP network not found: {dopnet_path}"}

    # Create trigger node
    trigger_name = f"trigger_{from_state}_to_{to_state}"
    trigger = dopnet.createNode("crowdtrigger", trigger_name)
    if trigger is None:
        return {"status": "error", "message": "Failed to create crowdtrigger node"}

    # Configure trigger type
    trigger_logic = TRIGGER_TYPE_MAP[trigger_type.lower()]
    if trigger.parm("triggerlogic"):
        trigger.parm("triggerlogic").set(trigger_logic)

    # Apply trigger settings
    if trigger_settings:
        for parm_name, value in trigger_settings.items():
            parm = trigger.parm(parm_name)
            if parm:
                try:
                    parm.set(value)
                except Exception as e:
                    logger.warning(f"Failed to set trigger param {parm_name}: {e}")

    # Create transition node
    transition_name = f"transition_{from_state}_to_{to_state}"
    transition = dopnet.createNode("crowdtransition", transition_name)
    if transition is None:
        return {"status": "error", "message": "Failed to create crowdtransition node"}

    # Configure transition states
    if transition.parm("inputstate"):
        transition.parm("inputstate").set(from_state)
    if transition.parm("outputstate"):
        transition.parm("outputstate").set(to_state)

    # Wire trigger into transition
    transition.setFirstInput(trigger)

    # Find crowd solver and wire transition (type may be versioned, e.g. "crowdsolver::3.0")
    crowd_solver = None
    for child in dopnet.children():
        if child.type().name().startswith("crowdsolver"):
            crowd_solver = child
            break

    if crowd_solver:
        # Connect transition to solver
        existing_inputs = crowd_solver.inputs()
        next_input = len(existing_inputs)
        if next_input < 1:
            next_input = 1
        try:
            crowd_solver.setInput(next_input, transition)
        except Exception as e:
            logger.warning(f"Could not wire transition to solver: {e}")

    dopnet.layoutChildren()

    return {
        "status": "success",
        "trigger_path": trigger.path(),
        "transition_path": transition.path(),
        "from_state": from_state,
        "to_state": to_state,
        "trigger_type": trigger_type,
    }


# =============================================================================
# Terrain
# =============================================================================


@handle_connection_errors("setup_crowd_terrain")
def setup_crowd_terrain(
    parent_path: str,
    terrain_source: Optional[str] = None,
    enable_foot_locking: bool = True,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Set up terrain for crowd simulation with terrain adaptation.

    Creates ground geometry and terrain adaptation nodes for agents to
    properly interact with the terrain surface.

    Args:
        parent_path: Path to the crowd simulation geo container
        terrain_source: Optional path to existing terrain/ground geometry node.
            If not provided, creates a flat grid as ground plane.
        enable_foot_locking: Enable foot locking for terrain adaptation (default: True)
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - terrain_path: Path to the terrain geometry
        - adaptation_path: Path to agentterrainadaptation node (if created)

    Examples:
        setup_crowd_terrain("/obj/crowd_sim")
        setup_crowd_terrain("/obj/crowd_sim", terrain_source="/obj/terrain/height_output")
    """
    hou = ensure_connected(host, port)

    parent = hou.node(parent_path)
    if parent is None:
        return {"status": "error", "message": f"Node not found: {parent_path}"}

    result = {"status": "success"}

    if terrain_source:
        # Use existing terrain via object merge
        terrain_merge = parent.createNode("object_merge", "terrain_in")
        terrain_merge.parm("objpath1").set(terrain_source)
        result["terrain_path"] = terrain_merge.path()
    else:
        # Create flat ground grid
        grid = parent.createNode("grid", "ground_terrain")
        grid.parm("sizex").set(100.0)
        grid.parm("sizey").set(100.0)
        grid.parm("rows").set(50)
        grid.parm("cols").set(50)
        result["terrain_path"] = grid.path()

    # Create agent terrain adaptation node
    try:
        adaptation = parent.createNode("agentterrainadaptation", "terrain_adapt")
        if adaptation:
            if enable_foot_locking and adaptation.parm("enablefootlocking"):
                adaptation.parm("enablefootlocking").set(True)
            result["adaptation_path"] = adaptation.path()
    except Exception as e:
        logger.info(f"agentterrainadaptation not available: {e}")
        result["adaptation_note"] = "agentterrainadaptation node type not available"

    parent.layoutChildren()

    return result


# =============================================================================
# Introspection
# =============================================================================


@handle_connection_errors("get_crowd_info")
def get_crowd_info(
    root_path: str = "/obj",
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Scan and report existing crowd simulation setup.

    Introspects the scene to find all crowd-related nodes and reports
    their configuration. Useful for understanding what has been set up
    before making modifications.

    Args:
        root_path: Root path to scan from (default: "/obj")
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - agent_definitions: List of agent setup chains found
        - crowd_sources: List of crowdsource nodes found
        - dop_networks: List of DOP networks with crowd solvers
        - behaviors: List of POP Steer behavior nodes
        - transitions: List of crowd transition nodes
        - triggers: List of crowd trigger nodes
        - total_crowd_nodes: Total number of crowd-related nodes

    Examples:
        get_crowd_info()  # Scan all of /obj
        get_crowd_info("/obj/my_crowd_setup")  # Scan specific subtree
    """
    hou = ensure_connected(host, port)

    root = hou.node(root_path)
    if root is None:
        return {"status": "error", "message": f"Root node not found: {root_path}"}

    # Crowd-related node type keywords
    crowd_keywords = {
        "agent", "agentclip", "agentprep", "agentdefinitioncache",
        "crowdsource", "crowdobject", "crowdsolver", "crowdtrigger",
        "crowdtransition", "crowdstate",
        "agentterrainadaptation", "agentterraiprojection",
    }
    steer_prefix = "popsteer"

    agent_definitions = []
    crowd_sources = []
    dop_networks = []
    behaviors = []
    transitions = []
    triggers = []
    other_crowd_nodes = []

    def _scan_node(node, depth=0):
        if depth > 20:
            return

        try:
            node_type = node.type().name()
        except Exception:
            return

        # Classify crowd nodes
        if node_type == "agent":
            agent_definitions.append({
                "path": node.path(),
                "name": node.name(),
                "type": node_type,
            })
        elif node_type.startswith("crowdsource"):
            info = {"path": node.path(), "name": node.name()}
            if node.parm("initialstate"):
                try:
                    info["initial_state"] = node.parm("initialstate").eval()
                except Exception:
                    pass
            crowd_sources.append(info)
        elif node_type.startswith("crowdsolver"):
            dop_networks.append({
                "solver_path": node.path(),
                "dopnet_path": node.parent().path() if node.parent() else None,
            })
        elif node_type.startswith(steer_prefix):
            behaviors.append({
                "path": node.path(),
                "name": node.name(),
                "type": node_type,
            })
        elif node_type.startswith("crowdtrigger"):
            trigger_info = {"path": node.path(), "name": node.name()}
            if node.parm("triggerlogic"):
                try:
                    trigger_info["logic"] = node.parm("triggerlogic").eval()
                except Exception:
                    pass
            triggers.append(trigger_info)
        elif node_type.startswith("crowdtransition"):
            trans_info = {"path": node.path(), "name": node.name()}
            if node.parm("inputstate"):
                try:
                    trans_info["from_state"] = node.parm("inputstate").eval()
                except Exception:
                    pass
            if node.parm("outputstate"):
                try:
                    trans_info["to_state"] = node.parm("outputstate").eval()
                except Exception:
                    pass
            transitions.append(trans_info)
        elif any(node_type.startswith(kw) for kw in crowd_keywords):
            other_crowd_nodes.append({
                "path": node.path(),
                "name": node.name(),
                "type": node_type,
            })

        # Recurse into children
        try:
            for child in node.children():
                _scan_node(child, depth + 1)
        except Exception:
            pass

    _scan_node(root)

    total = (
        len(agent_definitions) + len(crowd_sources) + len(dop_networks)
        + len(behaviors) + len(transitions) + len(triggers) + len(other_crowd_nodes)
    )

    return {
        "status": "success",
        "root_path": root_path,
        "agent_definitions": agent_definitions,
        "crowd_sources": crowd_sources,
        "dop_networks": dop_networks,
        "behaviors": behaviors,
        "transitions": transitions,
        "triggers": triggers,
        "other_crowd_nodes": other_crowd_nodes,
        "total_crowd_nodes": total,
    }


# =============================================================================
# Validation
# =============================================================================


@handle_connection_errors("validate_crowd_setup")
def validate_crowd_setup(
    root_path: str = "/obj",
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Validate crowd simulation setup and check for common mistakes.

    Scans the scene for crowd-related nodes and checks for:
    - Missing agent definition cache
    - Unconnected or missing crowd source
    - Missing DOP network or crowd solver
    - Missing ground plane
    - No behaviors defined
    - Missing state transitions

    Args:
        root_path: Root path to validate from (default: "/obj")
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - is_valid: True if no critical issues found
        - issues: List of issues found, each with:
            - severity: "critical", "warning", or "info"
            - category: Issue category
            - message: Human-readable description
            - fix_hint: Suggested fix
        - summary: Quick summary of the validation result

    Examples:
        validate_crowd_setup()
        validate_crowd_setup("/obj/my_crowd")
    """
    hou = ensure_connected(host, port)

    # First get the crowd info
    info = get_crowd_info(root_path, host, port)
    if info.get("status") != "success":
        return info

    issues = []

    # Check 1: Agent definitions
    if not info["agent_definitions"]:
        issues.append({
            "severity": "critical",
            "category": "agent",
            "message": "No agent definitions found in the scene",
            "fix_hint": "Use setup_crowd_agent() to create an agent definition chain",
        })

    # Check 2: Agent definition cache
    # Look for agentdefinitioncache nodes
    has_cache = False
    root = hou.node(root_path)
    if root:
        def _find_cache(node, depth=0):
            nonlocal has_cache
            if depth > 20 or has_cache:
                return
            try:
                if node.type().name() == "agentdefinitioncache":
                    has_cache = True
                    return
                for child in node.children():
                    _find_cache(child, depth + 1)
            except Exception:
                pass
        _find_cache(root)

    if info["agent_definitions"] and not has_cache:
        issues.append({
            "severity": "warning",
            "category": "agent",
            "message": "Agent definitions found but no agentdefinitioncache node. "
            "Cache improves performance for large crowds.",
            "fix_hint": "Add an agentdefinitioncache SOP after agentprep",
        })

    # Check 3: Crowd sources
    if not info["crowd_sources"]:
        if info["agent_definitions"]:
            issues.append({
                "severity": "critical",
                "category": "source",
                "message": "No crowd sources found. Agents need a source for placement.",
                "fix_hint": "Use setup_crowd_source() to create agent placement",
            })
    else:
        # Verify sources are connected
        for source in info["crowd_sources"]:
            source_node = hou.node(source["path"])
            if source_node:
                try:
                    inputs = source_node.inputs()
                    if not inputs or all(i is None for i in inputs):
                        issues.append({
                            "severity": "warning",
                            "category": "source",
                            "message": f"Crowd source {source['path']} has no inputs connected",
                            "fix_hint": "Connect agent definition and scatter source to crowdsource inputs",
                        })
                except Exception:
                    pass

    # Check 4: DOP network
    if not info["dop_networks"]:
        if info["crowd_sources"]:
            issues.append({
                "severity": "critical",
                "category": "simulation",
                "message": "No crowd DOP network found. Simulation requires a DOP network.",
                "fix_hint": "Use setup_crowd_dop() to create a simulation network",
            })
    else:
        # Check for ground plane in each DOP network
        for dop_info in info["dop_networks"]:
            dopnet_path = dop_info.get("dopnet_path")
            if dopnet_path:
                dopnet = hou.node(dopnet_path)
                if dopnet:
                    has_ground = False
                    try:
                        for child in dopnet.children():
                            if child.type().name() == "groundplane":
                                has_ground = True
                                break
                    except Exception:
                        pass
                    if not has_ground:
                        issues.append({
                            "severity": "warning",
                            "category": "simulation",
                            "message": f"DOP network {dopnet_path} has no ground plane",
                            "fix_hint": "Add a groundplane DOP node for agents to walk on",
                        })

    # Check 5: Behaviors
    if info["dop_networks"] and not info["behaviors"]:
        issues.append({
            "severity": "warning",
            "category": "behavior",
            "message": "No steering behaviors found. Agents won't move without behaviors.",
            "fix_hint": "Use add_crowd_behavior() to add wandering, seeking, or other movement",
        })

    # Check 6: Transitions
    if info["dop_networks"] and not info["transitions"]:
        issues.append({
            "severity": "info",
            "category": "state_machine",
            "message": "No state transitions defined. Agents will stay in their initial state.",
            "fix_hint": "Use add_crowd_transition() to add state changes (e.g., idle -> walk)",
        })

    # Check for cook errors on crowd nodes
    error_nodes = []
    if root:
        def _check_errors(node, depth=0):
            if depth > 20:
                return
            try:
                node_type = node.type().name()
                if any(kw in node_type for kw in ["crowd", "agent", "popsteer"]):
                    errors = node.errors()
                    if errors:
                        error_nodes.append({
                            "path": node.path(),
                            "type": node_type,
                            "errors": [str(e) for e in errors[:3]],
                        })
                for child in node.children():
                    _check_errors(child, depth + 1)
            except Exception:
                pass
        _check_errors(root)

    if error_nodes:
        for err_node in error_nodes:
            issues.append({
                "severity": "critical",
                "category": "errors",
                "message": f"Cook errors on {err_node['path']}: {err_node['errors'][0]}",
                "fix_hint": f"Check node {err_node['path']} for configuration issues",
            })

    # Determine overall validity
    critical_count = sum(1 for i in issues if i["severity"] == "critical")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")
    info_count = sum(1 for i in issues if i["severity"] == "info")

    if not issues:
        summary = "Crowd setup looks good! No issues found."
    elif critical_count > 0:
        summary = (
            f"Found {critical_count} critical issue(s), {warning_count} warning(s). "
            "Critical issues must be fixed before simulation will work."
        )
    else:
        summary = f"Found {warning_count} warning(s) and {info_count} info note(s). No critical issues."

    return {
        "status": "success",
        "is_valid": critical_count == 0,
        "issues": issues,
        "issue_counts": {
            "critical": critical_count,
            "warning": warning_count,
            "info": info_count,
        },
        "summary": summary,
        "crowd_info": info,
    }
