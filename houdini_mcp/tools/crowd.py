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
    - add_crowd_state: Add Crowd State DOPs (walk, idle, run) to the state machine
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
    5. add_crowd_state() -> animation states (walk, idle, run)
    6. add_crowd_transition() -> state machine transitions
    7. validate_crowd_setup() -> check for issues
"""

import logging
import math
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


def _try_hscript_batch(hou: Any):
    """Try to create an HscriptBatch for faster bulk operations. Returns None if unavailable."""
    try:
        from .hscript import HscriptBatch
        batch = HscriptBatch(hou)
        if batch.is_available():
            return batch
    except Exception:
        pass
    return None


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

# Maps trigger type names to Houdini crowdtrigger logic types.
# P3-1 fix: "speed" and "attribute" were both mapped to "objectattribute".
# "speed" should map to "particlespeed" (Particle speed trigger in Houdini).
TRIGGER_TYPE_MAP = {
    "distance": "distancefromposition",
    "time": "timeinstate",
    "random": "random",
    "speed": "particlespeed",
    "attribute": "objectattribute",
    "state_duration": "currentstateduration",
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

    # Wrap node creation in try/except to rollback (destroy geo container)
    # on failure, preventing orphan nodes in the Houdini scene.
    try:
        # 1. Create agent node
        agent = geo.createNode("agent", f"{agent_name}_agent")

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
        last_node = prep

        # 4. Create agentdefinitioncache node
        cache = geo.createNode("agentdefinitioncache", f"{agent_name}_cache")
        cache.setFirstInput(last_node)

        # Set display/render flags on the final node
        cache.setDisplayFlag(True)
        cache.setRenderFlag(True)

        # Layout the nodes
        geo.layoutChildren()

    except Exception as e:
        # Rollback: destroy the entire geo container to avoid orphan nodes
        try:
            geo.destroy()
        except Exception:
            pass
        return {"status": "error", "message": f"Failed during agent setup: {e}"}

    result = {
        "status": "success",
        "geo_path": geo.path(),
        "agent_path": agent.path(),
        "prep_path": prep.path(),
        "cache_path": cache.path(),
        "node_count": 5 if clip_node else 4,  # geo + agent + [clip] + prep + cache
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
        # P2-3 fix: adaptive grid sizing based on agent count instead of
        # hardcoded 50x50. Roughly 2 units per agent, clamped to [20, 500].
        import math
        grid_size = max(20.0, min(500.0, math.sqrt(count) * 2.0))
        grid = parent.createNode("grid", "crowd_scatter_grid")
        grid.parm("sizex").set(grid_size)
        grid.parm("sizey").set(grid_size)
        result["grid_path"] = grid.path()

        # Create scatter for agent placement
        scatter = parent.createNode("scatter", "crowd_scatter")
        scatter.setFirstInput(grid)
        scatter.parm("npts").set(count)
        result["scatter_path"] = scatter.path()

        # P1-2 fix: object_merge's objpath1 expects a SOP-level path to merge
        # geometry from. agent_setup_path is already a SOP path (e.g.,
        # /obj/soldier_agent_setup/soldier_cache), which is correct for object_merge.
        obj_merge = parent.createNode("object_merge", "agent_definition_in")
        obj_merge.parm("objpath1").set(agent_setup_path)
        # Ensure xformtype is set to "None" (0) to avoid double-transforming
        if obj_merge.parm("xformtype"):
            obj_merge.parm("xformtype").set(0)

        # Create crowdsource
        crowd_source = parent.createNode("crowdsource", "crowd_source")
        crowd_source.setInput(0, obj_merge)  # Agent definition
        crowd_source.setInput(1, scatter)    # Source points

    else:
        # Simple: just merge agent def and create source
        obj_merge = parent.createNode("object_merge", "agent_definition_in")
        obj_merge.parm("objpath1").set(agent_setup_path)
        if obj_merge.parm("xformtype"):
            obj_merge.parm("xformtype").set(0)

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

    # Wrap node creation in try/except to rollback (destroy dopnet)
    # on failure, preventing orphan nodes in the Houdini scene.
    try:
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
        merge = dopnet.createNode("merge", "merge_forces")
        merge.setInput(0, crowd_solver)
        merge.setInput(1, ground)
        merge.setInput(2, gravity)

        # Layout
        dopnet.layoutChildren()

    except Exception as e:
        # Rollback: destroy the entire dopnet to avoid orphan nodes
        try:
            dopnet.destroy()
        except Exception:
            pass
        return {"status": "error", "message": f"Failed during DOP setup: {e}"}

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

    # Create behavior node inside the DOP network.
    # P3-2 fix: detect if Houdini auto-renamed the node due to name collision
    # and report the actual name in the response.
    requested_name = behavior_name or f"steer_{behavior_type}"
    behavior_node = dopnet.createNode(dop_node_type, requested_name)
    if behavior_node is None:
        return {
            "status": "error",
            "message": f"Failed to create {dop_node_type} node. "
            "This node type may not be available in your Houdini version.",
        }
    actual_name = behavior_node.name()
    name_changed = actual_name != requested_name

    # Wire behavior into the crowd solver's inputs.
    # Crowd solver input 0 = crowd object. Inputs 1+ = POP Steer behaviors.
    # P1-1 fix: find first None slot at index >= 1, not just len(inputs),
    # because Houdini input tuples can have None gaps.
    existing_inputs = crowd_solver.inputs()
    next_input = None
    for idx in range(1, len(existing_inputs)):
        if existing_inputs[idx] is None:
            next_input = idx
            break
    if next_input is None:
        next_input = max(1, len(existing_inputs))
    try:
        crowd_solver.setInput(next_input, behavior_node)
    except Exception as e:
        logger.warning(f"Could not wire behavior to solver input {next_input}: {e}")

    # P1-3 fix: validate settings dict before applying — limit key count
    # and restrict value types to prevent expression injection via parm.set()
    settings_applied = []
    if settings:
        _MAX_SETTINGS = 50
        if len(settings) > _MAX_SETTINGS:
            return {
                "status": "error",
                "message": f"Too many settings ({len(settings)}), max {_MAX_SETTINGS}",
            }
        for parm_name, value in settings.items():
            # Only allow safe value types
            if not isinstance(value, (int, float, str, list, tuple, bool)):
                logger.warning(f"Skipping {parm_name}: unsupported type {type(value).__name__}")
                continue
            parm = behavior_node.parm(parm_name)
            if parm:
                try:
                    parm.set(value)
                    settings_applied.append({"name": parm_name, "value": value})
                except Exception as e:
                    logger.warning(f"Failed to set {parm_name}: {e}")

    dopnet.layoutChildren()

    result = {
        "status": "success",
        "behavior_path": behavior_node.path(),
        "behavior_name": actual_name,
        "behavior_type": behavior_type,
        "dop_node_type": dop_node_type,
        "settings_applied": settings_applied,
    }
    if name_changed:
        result["name_changed"] = True
        result["requested_name"] = requested_name
        result["note"] = (
            f"Node renamed from '{requested_name}' to '{actual_name}' "
            "because a node with the original name already exists."
        )
    return result


# =============================================================================
# Crowd States
# =============================================================================


@handle_connection_errors("add_crowd_state")
def add_crowd_state(
    dopnet_path: str,
    state_name: str,
    clip_name: Optional[str] = None,
    locomotion: bool = False,
    playback_speed: float = 1.0,
    variance: float = 0.0,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Add a Crowd State DOP to the simulation.

    Crowd States define animation states (walk, idle, run) for the state machine.
    Each state maps to an animation clip and controls playback behavior.
    States are merged and wired to the Crowd Solver's 3rd input.

    Without at least one Crowd State, agents will not animate even if
    steering behaviors are configured.

    Args:
        dopnet_path: Path to the DOP network (e.g., "/obj/crowd_sim_dopnet")
        state_name: Name of the state (e.g., "walk", "idle", "run").
            This also becomes the node name, and by default the clip name.
        clip_name: Optional explicit clip name. If not provided, uses state_name
            (Houdini default uses $OS which maps to the node name).
        locomotion: If True, sets Clip Playback Type to "Locomotive" for
            in-place clips that have extracted locomotion data.
        playback_speed: Clip playback speed multiplier (default: 1.0)
        variance: Random variance for playback speed (0.0-1.0) to prevent
            synchronized movement across agents (default: 0.0 = disabled)

    Returns:
        Dict with:
        - status: "success" or "error"
        - state_path: Path to the crowdstate node
        - state_name: Name of the state
        - clip_name: Clip name used
        - merge_path: Path to the states merge node (if created)
        - solver_wired: Whether the merge was wired to the solver's 3rd input

    Examples:
        add_crowd_state("/obj/crowd_sim_dopnet", "idle")
        add_crowd_state("/obj/crowd_sim_dopnet", "walk", locomotion=True)
        add_crowd_state("/obj/crowd_sim_dopnet", "run",
                       clip_name="run_cycle", locomotion=True, variance=0.1)
    """
    hou = ensure_connected(host, port)

    dopnet = hou.node(dopnet_path)
    if dopnet is None:
        return {"status": "error", "message": f"DOP network not found: {dopnet_path}"}

    # Create crowd state node
    state_node = dopnet.createNode("crowdstate", state_name)
    if state_node is None:
        return {
            "status": "error",
            "message": f"Failed to create crowdstate node '{state_name}'.",
        }

    # Set clip name if explicitly provided (default $OS uses node name)
    if clip_name and state_node.parm("clipname"):
        state_node.parm("clipname").set(clip_name)

    # Set locomotion type: 0 = In-Place, 1 = Locomotive
    if locomotion and state_node.parm("locomotiontype"):
        state_node.parm("locomotiontype").set(1)

    # Set playback speed
    if playback_speed != 1.0 and state_node.parm("playbackspeed"):
        state_node.parm("playbackspeed").set(playback_speed)

    # Enable playback variance for natural-looking crowds
    if variance > 0.0:
        if state_node.parm("randomizeclips"):
            state_node.parm("randomizeclips").set(1)
        if state_node.parm("speedvariance"):
            state_node.parm("speedvariance").set(variance)

    # Find or create a merge node for states.
    # Convention: a merge named "crowd_states_merge" collects all states.
    merge_node = dopnet.node("crowd_states_merge")
    merge_created = False
    if merge_node is None:
        merge_node = dopnet.createNode("merge", "crowd_states_merge")
        merge_created = True

    # Wire the new state into the merge's next available input
    if merge_node is not None:
        existing_merge_inputs = merge_node.inputs()
        next_merge_idx = None
        for idx in range(len(existing_merge_inputs)):
            if existing_merge_inputs[idx] is None:
                next_merge_idx = idx
                break
        if next_merge_idx is None:
            next_merge_idx = len(existing_merge_inputs)
        try:
            merge_node.setInput(next_merge_idx, state_node)
        except Exception as e:
            logger.warning(f"Could not wire state to merge: {e}")

    # Wire the states merge to the crowd solver's 3rd input (index 2).
    # Per Houdini docs: solver input 0 = crowd object, input 2 = merged states.
    solver_wired = False
    crowd_solver = None
    for child in dopnet.children():
        if child.type().name().startswith("crowdsolver"):
            crowd_solver = child
            break

    if crowd_solver and merge_node:
        # Only wire if not already connected at input 2
        existing_inputs = crowd_solver.inputs()
        current_input_2 = existing_inputs[2] if len(existing_inputs) > 2 else None
        if current_input_2 is None or current_input_2.path() != merge_node.path():
            try:
                crowd_solver.setInput(2, merge_node)
                solver_wired = True
            except Exception as e:
                logger.warning(f"Could not wire states merge to solver input 2: {e}")
        else:
            # Already wired correctly
            solver_wired = True

    dopnet.layoutChildren()

    return {
        "status": "success",
        "state_path": state_node.path(),
        "state_name": state_name,
        "clip_name": clip_name or state_name,
        "locomotion": locomotion,
        "merge_path": merge_node.path() if merge_node else None,
        "merge_created": merge_created,
        "solver_wired": solver_wired,
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
        # P1-1 fix: find first available (None) input slot at index >= 1,
        # because len(inputs) doesn't account for None gaps in sparse tuples.
        existing_inputs = crowd_solver.inputs()
        next_input = None
        for idx in range(1, len(existing_inputs)):
            if existing_inputs[idx] is None:
                next_input = idx
                break
        if next_input is None:
            next_input = max(1, len(existing_inputs))
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

    # P2-1 fix: create terrain adaptation node AND wire its inputs.
    # Input 0 = agents (crowd source), Input 1 = terrain geometry.
    # Previously the node was created but left unwired.
    terrain_geo_node = hou.node(result["terrain_path"]) if "terrain_path" in result else None
    try:
        adaptation = parent.createNode("agentterrainadaptation", "terrain_adapt")
        if adaptation:
            # Wire terrain geometry as input
            if terrain_geo_node:
                adaptation.setInput(0, terrain_geo_node)
            if enable_foot_locking and adaptation.parm("enablefootlocking"):
                adaptation.parm("enablefootlocking").set(True)
            adaptation.setDisplayFlag(True)
            adaptation.setRenderFlag(True)
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
        "agentterrainadaptation", "agentterrainprojection",
        "agentcliptransitiongraph", "agentlayer", "crowdassignlayers",
        "agentcollisionlayer", "agentconfigurejoints",
        "agentlookat", "agentlookatapply",
        "crowdmotionpath", "crowdmotionpathavoid", "crowdmotionpathevaluate",
        "multisolver", "bulletrbdsolver",
    }
    steer_prefix = "popsteer"

    agent_definitions = []
    crowd_sources = []
    dop_networks = []
    behaviors = []
    states = []
    transitions = []
    triggers = []
    layers = []
    transition_graphs = []
    ragdoll_nodes = []
    motion_paths = []
    lookat_nodes = []
    other_crowd_nodes = []

    def _classify_node(path, node_type, node=None):
        """Classify a single node by its type into the appropriate list."""
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        if node_type == "agent":
            agent_definitions.append({"path": path, "name": name, "type": node_type})
        elif node_type.startswith("crowdsource"):
            info = {"path": path, "name": name}
            # Fetch initial_state param only for crowdsource nodes
            if node is None:
                node = hou.node(path)
            if node and node.parm("initialstate"):
                try:
                    info["initial_state"] = node.parm("initialstate").eval()
                except Exception:
                    pass
            crowd_sources.append(info)
        elif node_type.startswith("crowdsolver"):
            parent_path = "/".join(path.split("/")[:-1]) if "/" in path else None
            dop_networks.append({"solver_path": path, "dopnet_path": parent_path})
        elif node_type.startswith(steer_prefix):
            behaviors.append({"path": path, "name": name, "type": node_type})
        elif node_type.startswith("crowdtrigger"):
            trigger_info = {"path": path, "name": name}
            if node is None:
                node = hou.node(path)
            if node and node.parm("triggerlogic"):
                try:
                    trigger_info["logic"] = node.parm("triggerlogic").eval()
                except Exception:
                    pass
            triggers.append(trigger_info)
        elif node_type.startswith("crowdstate"):
            state_info = {"path": path, "name": name}
            if node is None:
                node = hou.node(path)
            if node and node.parm("clipname"):
                try:
                    state_info["clip_name"] = node.parm("clipname").eval()
                except Exception:
                    pass
            states.append(state_info)
        elif node_type.startswith("crowdtransition"):
            trans_info = {"path": path, "name": name}
            if node is None:
                node = hou.node(path)
            if node:
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
        elif node_type.startswith("agentcliptransitiongraph"):
            transition_graphs.append({"path": path, "name": name, "type": node_type})
        elif node_type.startswith("agentlayer") or node_type.startswith("crowdassignlayers"):
            layers.append({"path": path, "name": name, "type": node_type})
        elif node_type.startswith("agentcollisionlayer") or node_type.startswith("agentconfigurejoints"):
            ragdoll_nodes.append({"path": path, "name": name, "type": node_type})
        elif node_type.startswith("agentlookat"):
            lookat_nodes.append({"path": path, "name": name, "type": node_type})
        elif node_type.startswith("crowdmotionpath"):
            motion_paths.append({"path": path, "name": name, "type": node_type})
        elif any(node_type.startswith(kw) for kw in crowd_keywords):
            other_crowd_nodes.append({"path": path, "name": name, "type": node_type})

    # P2-3 optimization: use hscript batch when available to get all paths and types
    # in 2 RPC calls instead of N recursive calls. Falls back to per-node scan.
    batch = _try_hscript_batch(hou)
    used_batch = False
    if batch:
        try:
            all_paths = batch.list_all_paths(root_path)
            if all_paths:
                all_types = batch.get_node_types(all_paths)
                for path, node_type in all_types.items():
                    # Only process crowd-related types to minimize further RPC
                    if (node_type.startswith(steer_prefix) or
                            any(node_type.startswith(kw) for kw in crowd_keywords)):
                        _classify_node(path, node_type)
                used_batch = True
        except Exception as e:
            logger.debug(f"hscript batch unavailable, falling back to recursive scan: {e}")

    if not used_batch:
        # Fallback: recursive per-node scan (slower but always works)
        def _scan_node(node, depth=0):
            if depth > 20:
                return
            try:
                node_type = node.type().name()
            except Exception:
                return
            _classify_node(node.path(), node_type, node)
            try:
                for child in node.children():
                    _scan_node(child, depth + 1)
            except Exception:
                pass

        _scan_node(root)

    total = (
        len(agent_definitions) + len(crowd_sources) + len(dop_networks)
        + len(behaviors) + len(states) + len(transitions) + len(triggers)
        + len(layers) + len(transition_graphs) + len(ragdoll_nodes)
        + len(motion_paths) + len(lookat_nodes)
        + len(other_crowd_nodes)
    )

    return {
        "status": "success",
        "root_path": root_path,
        "agent_definitions": agent_definitions,
        "crowd_sources": crowd_sources,
        "dop_networks": dop_networks,
        "behaviors": behaviors,
        "states": states,
        "transitions": transitions,
        "triggers": triggers,
        "layers": layers,
        "transition_graphs": transition_graphs,
        "ragdoll_nodes": ragdoll_nodes,
        "motion_paths": motion_paths,
        "lookat_nodes": lookat_nodes,
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
    - No crowd states defined (agents won't animate without states)
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

    # Check 6: Crowd States — agents won't animate without at least one state
    if info["dop_networks"] and not info.get("states"):
        issues.append({
            "severity": "critical",
            "category": "state",
            "message": "No Crowd States found. Agents will not animate without states.",
            "fix_hint": "Use add_crowd_state() to define animation states (e.g., 'idle', 'walk')",
        })

    # Check 7: Transitions
    if info["dop_networks"] and not info["transitions"]:
        issues.append({
            "severity": "info",
            "category": "state_machine",
            "message": "No state transitions defined. Agents will stay in their initial state.",
            "fix_hint": "Use add_crowd_transition() to add state changes (e.g., idle -> walk)",
        })

    # Check 8: Clip Transition Graph
    if info.get("states") and len(info["states"]) > 1 and not info.get("transition_graphs"):
        issues.append({
            "severity": "info",
            "category": "clip_transition",
            "message": "Multiple states found but no Clip Transition Graph. "
            "Transitions may appear abrupt without blend curves.",
            "fix_hint": "Use add_crowd_clip_transition_graph() for smooth animation blending",
        })

    # Check 9: Layers
    if info["agent_definitions"] and not info.get("layers"):
        issues.append({
            "severity": "info",
            "category": "diversity",
            "message": "No agent layers found. All agents will look identical.",
            "fix_hint": "Use add_crowd_layer() to add visual diversity (weapons, clothing)",
        })

    # Check 10: Ragdoll setup
    if info["dop_networks"] and not info.get("ragdoll_nodes"):
        issues.append({
            "severity": "info",
            "category": "ragdoll",
            "message": "No ragdoll setup found. Agents cannot react to physical impacts.",
            "fix_hint": "Use setup_crowd_ragdoll() to add ragdoll physics capability",
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


# =============================================================================
# Formation Placement
# =============================================================================


@handle_connection_errors("setup_crowd_formation")
def setup_crowd_formation(
    parent_path: str,
    agent_setup_path: str,
    rows: int = 5,
    columns: int = 5,
    spacing: float = 2.0,
    initial_state: str = "idle",
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create formation-based crowd placement.

    Sets up agents in a structured grid formation instead of random scattering.
    Uses the Crowd Source SOP's formation layout mode with configurable
    rows, columns, and spacing.

    Args:
        parent_path: Path to the geometry container (e.g., "/obj/crowd_sim")
        agent_setup_path: Path to the agent definition cache node
        rows: Number of rows in the formation (default: 5)
        columns: Number of columns in the formation (default: 5)
        spacing: Distance between agents in the formation (default: 2.0)
        initial_state: Initial crowd state name (default: "idle")
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - geo_path: Path to the geometry container
        - source_path: Path to the crowdsource node
        - agent_count: Total number of agents (rows * columns)
        - formation: Dict with rows, columns, spacing

    Examples:
        setup_crowd_formation("/obj/crowd_sim",
                             "/obj/soldier_agent_setup/soldier_cache",
                             rows=10, columns=10, spacing=3.0)
        setup_crowd_formation("/obj/crowd_sim", agent_path,
                             rows=5, columns=20, initial_state="walk")
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

    # Merge agent definition
    obj_merge = parent.createNode("object_merge", "agent_definition_in")
    obj_merge.parm("objpath1").set(agent_setup_path)
    if obj_merge.parm("xformtype"):
        obj_merge.parm("xformtype").set(0)

    # Create crowdsource with formation layout
    crowd_source = parent.createNode("crowdsource", "crowd_formation_source")
    crowd_source.setInput(0, obj_merge)

    # Configure formation layout
    # layout parm: 0 = Random, 1 = Formation
    if crowd_source.parm("layout"):
        crowd_source.parm("layout").set(1)
    if crowd_source.parm("rows"):
        crowd_source.parm("rows").set(rows)
    if crowd_source.parm("columns"):
        crowd_source.parm("columns").set(columns)
    if crowd_source.parm("spacing"):
        crowd_source.parm("spacing").set(spacing)

    # Set initial state
    if crowd_source.parm("initialstate"):
        crowd_source.parm("initialstate").set(initial_state)

    # Set display flag
    crowd_source.setDisplayFlag(True)
    crowd_source.setRenderFlag(True)

    parent.layoutChildren()

    return {
        "status": "success",
        "geo_path": parent.path(),
        "source_path": crowd_source.path(),
        "agent_count": rows * columns,
        "formation": {
            "rows": rows,
            "columns": columns,
            "spacing": spacing,
        },
    }


# =============================================================================
# Clip Transition Graph
# =============================================================================


@handle_connection_errors("add_crowd_clip_transition_graph")
def add_crowd_clip_transition_graph(
    parent_path: str,
    agent_setup_path: str,
    clip_names: Optional[List[str]] = None,
    blend_frames: int = 10,
    auto_compute: bool = True,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create a Clip Transition Graph SOP for smooth animation blending.

    Creates an agentcliptransitiongraph SOP that defines optimal transition
    points between animation clips. This enables smooth blending when agents
    change states, avoiding jarring clip switches.

    Args:
        parent_path: Path to the agent setup geometry container
        agent_setup_path: Path to the agent definition cache node to wire as input
        clip_names: Optional list of clip names to include in the graph.
            If not provided, all clips on the agent are used.
        blend_frames: Number of frames for transition blending (default: 10)
        auto_compute: If True, auto-compute sync frames from foot timing (default: True)
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - graph_path: Path to the agentcliptransitiongraph node
        - cache_path: Path to the updated agentdefinitioncache node (if created)
        - blend_frames: Number of blend frames configured
        - auto_compute: Whether auto-compute was enabled

    Examples:
        add_crowd_clip_transition_graph("/obj/soldier_agent_setup",
                                        "/obj/soldier_agent_setup/soldier_cache")
        add_crowd_clip_transition_graph("/obj/agent_setup", agent_cache,
                                        clip_names=["walk", "run", "idle"],
                                        blend_frames=15)
    """
    hou = ensure_connected(host, port)

    parent = hou.node(parent_path)
    if parent is None:
        return {"status": "error", "message": f"Parent node not found: {parent_path}"}

    agent_cache = hou.node(agent_setup_path)
    if agent_cache is None:
        return {
            "status": "error",
            "message": f"Agent setup node not found: {agent_setup_path}",
        }

    # Create the clip transition graph node
    graph_node = parent.createNode("agentcliptransitiongraph", "clip_transition_graph")
    if graph_node is None:
        return {
            "status": "error",
            "message": "Failed to create agentcliptransitiongraph node. "
            "This node type may not be available in your Houdini version.",
        }

    # Wire agent definition as input
    graph_node.setFirstInput(agent_cache)

    # Configure blend frames
    if graph_node.parm("blendframes"):
        graph_node.parm("blendframes").set(blend_frames)

    # Configure auto-compute transition graph
    if auto_compute and graph_node.parm("computetransitiongraph"):
        graph_node.parm("computetransitiongraph").set(1)

    # Configure specific clip names if provided
    if clip_names and graph_node.parm("numclips"):
        graph_node.parm("numclips").set(len(clip_names))
        for i, clip in enumerate(clip_names):
            clip_parm = graph_node.parm(f"clipname{i+1}")
            if clip_parm:
                clip_parm.set(clip)

    # Create a new definition cache after the graph
    new_cache = parent.createNode("agentdefinitioncache", "cache_with_transitions")
    new_cache.setFirstInput(graph_node)
    new_cache.setDisplayFlag(True)
    new_cache.setRenderFlag(True)

    parent.layoutChildren()

    return {
        "status": "success",
        "graph_path": graph_node.path(),
        "cache_path": new_cache.path(),
        "blend_frames": blend_frames,
        "auto_compute": auto_compute,
    }


# =============================================================================
# Agent Layers / Diversity
# =============================================================================


@handle_connection_errors("add_crowd_layer")
def add_crowd_layer(
    parent_path: str,
    agent_setup_path: str,
    layer_name: str,
    shape_geometry_path: Optional[str] = None,
    transform_name: Optional[str] = None,
    layer_assignments: Optional[List[Dict[str, Any]]] = None,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Set up agent layers for visual diversity (props, clothing variants).

    Creates an agentlayer SOP for adding geometry layers (weapons, hats, etc.)
    and optionally a crowdassignlayers SOP for random layer assignment across
    the crowd.

    Args:
        parent_path: Path to the agent setup geometry container
        agent_setup_path: Path to the agent definition cache node to wire as input
        layer_name: Name for the layer (e.g., "sword", "shield", "hat")
        shape_geometry_path: Optional path to geometry SOP to use as the layer shape.
            If provided, wired as 2nd input to agentlayer.
        transform_name: Optional joint/transform name to attach the shape to
            (e.g., "RightHand", "Head")
        layer_assignments: Optional list of layer assignment configs for
            crowdassignlayers. Each dict has:
            - "name": layer name
            - "weight": probability weight (default: 1.0)

    Returns:
        Dict with:
        - status: "success" or "error"
        - layer_path: Path to the agentlayer node
        - assign_path: Path to crowdassignlayers node (if assignments provided)
        - cache_path: Path to the updated definition cache
        - layer_name: Name of the layer created

    Examples:
        add_crowd_layer("/obj/agent_setup", agent_cache, "sword",
                       shape_geometry_path="/obj/weapons/sword_geo",
                       transform_name="RightHand")
        add_crowd_layer("/obj/agent_setup", agent_cache, "helmet",
                       layer_assignments=[
                           {"name": "helmet_a", "weight": 0.5},
                           {"name": "helmet_b", "weight": 0.3},
                           {"name": "no_helmet", "weight": 0.2},
                       ])
    """
    hou = ensure_connected(host, port)

    parent = hou.node(parent_path)
    if parent is None:
        return {"status": "error", "message": f"Parent node not found: {parent_path}"}

    agent_cache = hou.node(agent_setup_path)
    if agent_cache is None:
        return {
            "status": "error",
            "message": f"Agent setup node not found: {agent_setup_path}",
        }

    # Create agent layer node
    layer_node = parent.createNode("agentlayer", f"layer_{layer_name}")
    if layer_node is None:
        return {
            "status": "error",
            "message": "Failed to create agentlayer node.",
        }

    # Wire agent definition as input 0
    layer_node.setFirstInput(agent_cache)

    # Wire shape geometry as input 1 if provided
    if shape_geometry_path:
        shape_node = hou.node(shape_geometry_path)
        if shape_node:
            layer_node.setInput(1, shape_node)
        else:
            # Create object_merge to reference external geometry
            shape_merge = parent.createNode("object_merge", f"shape_{layer_name}_in")
            shape_merge.parm("objpath1").set(shape_geometry_path)
            if shape_merge.parm("xformtype"):
                shape_merge.parm("xformtype").set(0)
            layer_node.setInput(1, shape_merge)

    # Set layer name
    if layer_node.parm("layername"):
        layer_node.parm("layername").set(layer_name)

    # Set transform/joint attachment
    if transform_name and layer_node.parm("transformname"):
        layer_node.parm("transformname").set(transform_name)

    last_node = layer_node

    # Create definition cache after layer
    new_cache = parent.createNode("agentdefinitioncache", f"cache_{layer_name}")
    new_cache.setFirstInput(last_node)
    last_node = new_cache

    result = {
        "status": "success",
        "layer_path": layer_node.path(),
        "cache_path": new_cache.path(),
        "layer_name": layer_name,
    }

    # Optionally create crowdassignlayers for random assignment
    if layer_assignments:
        assign_node = parent.createNode("crowdassignlayers", f"assign_{layer_name}")
        if assign_node:
            assign_node.setFirstInput(last_node)

            # Configure layer assignments
            if assign_node.parm("numlayers"):
                assign_node.parm("numlayers").set(len(layer_assignments))
                for i, assignment in enumerate(layer_assignments):
                    name_parm = assign_node.parm(f"layername{i+1}")
                    if name_parm:
                        name_parm.set(assignment.get("name", ""))
                    weight_parm = assign_node.parm(f"weight{i+1}")
                    if weight_parm:
                        weight_parm.set(assignment.get("weight", 1.0))

            assign_node.setDisplayFlag(True)
            assign_node.setRenderFlag(True)
            result["assign_path"] = assign_node.path()
        else:
            new_cache.setDisplayFlag(True)
            new_cache.setRenderFlag(True)
    else:
        new_cache.setDisplayFlag(True)
        new_cache.setRenderFlag(True)

    parent.layoutChildren()

    return result


# =============================================================================
# Ragdoll Physics
# =============================================================================


@handle_connection_errors("setup_crowd_ragdoll")
def setup_crowd_ragdoll(
    agent_setup_path: str,
    dopnet_path: str,
    collision_shape: str = "capsule",
    create_joints: bool = True,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Set up ragdoll physics for crowd agents.

    Creates the SOP-level preparation (collision layer, joint configuration)
    and DOP-level setup (Bullet Solver via Multisolver alongside Crowd Solver)
    needed for ragdoll transitions.

    SOP Setup:
    - agentcollisionlayer: Defines collision shapes per body part
    - agentconfigurejoints: Sets joint rotation limits

    DOP Setup:
    - Wraps existing Crowd Solver in a Multisolver
    - Adds Bullet Solver for rigid body physics
    - Adds ragdoll-ready state transition

    Args:
        agent_setup_path: Path to the agent setup geometry container
            (e.g., "/obj/soldier_agent_setup")
        dopnet_path: Path to the DOP network with crowd solver
        collision_shape: Default collision shape type:
            "capsule", "box", or "sphere" (default: "capsule")
        create_joints: If True, create agentconfigurejoints node (default: True)
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - collision_layer_path: Path to agentcollisionlayer SOP
        - configure_joints_path: Path to agentconfigurejoints SOP (if created)
        - multisolver_path: Path to multisolver DOP
        - bullet_solver_path: Path to Bullet Solver DOP
        - ragdoll_state_path: Path to ragdoll crowdstate DOP (if created)

    Examples:
        setup_crowd_ragdoll("/obj/soldier_agent_setup",
                           "/obj/crowd_sim_dopnet")
        setup_crowd_ragdoll("/obj/agent_setup", "/obj/dopnet",
                           collision_shape="box", create_joints=True)
    """
    hou = ensure_connected(host, port)

    # --- SOP-level: Collision Layer + Joint Config ---
    agent_parent = hou.node(agent_setup_path)
    if agent_parent is None:
        return {"status": "error", "message": f"Agent setup not found: {agent_setup_path}"}

    # Find existing agent definition cache in the setup
    cache_node = None
    for child in agent_parent.children():
        if child.type().name() == "agentdefinitioncache":
            cache_node = child
            break

    if cache_node is None:
        # Try to find last node as fallback
        children = agent_parent.children()
        cache_node = children[-1] if children else None

    result = {"status": "success"}

    # Create collision layer
    collision_layer = agent_parent.createNode("agentcollisionlayer", "collision_layer")
    if cache_node:
        collision_layer.setFirstInput(cache_node)

    # Set default collision shape type
    if collision_layer.parm("shapetype"):
        shape_map = {"capsule": 2, "box": 0, "sphere": 1}
        collision_layer.parm("shapetype").set(shape_map.get(collision_shape, 2))

    result["collision_layer_path"] = collision_layer.path()

    last_sop = collision_layer

    # Create joint configuration
    if create_joints:
        configure_joints = agent_parent.createNode("agentconfigurejoints", "configure_joints")
        configure_joints.setFirstInput(collision_layer)

        # Enable auto-create limits from collision layer
        if configure_joints.parm("createlimits"):
            configure_joints.parm("createlimits").set(1)

        result["configure_joints_path"] = configure_joints.path()
        last_sop = configure_joints

    # Add a new definition cache after ragdoll setup
    ragdoll_cache = agent_parent.createNode("agentdefinitioncache", "cache_ragdoll")
    ragdoll_cache.setFirstInput(last_sop)
    ragdoll_cache.setDisplayFlag(True)
    ragdoll_cache.setRenderFlag(True)
    result["ragdoll_cache_path"] = ragdoll_cache.path()

    agent_parent.layoutChildren()

    # --- DOP-level: Multisolver + Bullet ---
    dopnet = hou.node(dopnet_path)
    if dopnet is None:
        return {"status": "error", "message": f"DOP network not found: {dopnet_path}"}

    # Find crowd solver
    crowd_solver = None
    for child in dopnet.children():
        if child.type().name().startswith("crowdsolver"):
            crowd_solver = child
            break

    if crowd_solver is None:
        result["dop_warning"] = "No crowdsolver found. DOP-level ragdoll setup skipped."
        return result

    # Create multisolver and wire crowd solver into it
    multisolver = dopnet.createNode("multisolver", "ragdoll_multisolver")

    # Re-wire: multisolver takes crowd solver's position in the chain
    # Get crowd solver's inputs and re-route through multisolver
    solver_inputs = crowd_solver.inputs()
    for idx, inp in enumerate(solver_inputs):
        if inp is not None:
            multisolver.setInput(idx, inp)

    # Wire crowd solver as first sub-solver of multisolver
    crowd_solver.setInput(0, None)  # Disconnect old inputs
    multisolver.setInput(0, crowd_solver) if not solver_inputs else None

    # Create Bullet solver
    bullet_solver = dopnet.createNode("bulletrbdsolver", "bullet_solver")
    result["bullet_solver_path"] = bullet_solver.path()

    # Wire bullet solver into multisolver's merge inputs
    # Multisolver merges sub-solvers
    try:
        multisolver.setInput(0, crowd_solver)
        multisolver.setInput(1, bullet_solver)
    except Exception as e:
        logger.warning(f"Could not wire solvers into multisolver: {e}")

    result["multisolver_path"] = multisolver.path()

    # Create a ragdoll state
    ragdoll_state = dopnet.createNode("crowdstate", "ragdoll")
    if ragdoll_state:
        # Set RBD mode for ragdoll (animated static → full ragdoll)
        if ragdoll_state.parm("ragdoll"):
            ragdoll_state.parm("ragdoll").set(1)
        result["ragdoll_state_path"] = ragdoll_state.path()

        # Wire into states merge if it exists
        merge_node = dopnet.node("crowd_states_merge")
        if merge_node:
            existing_inputs = merge_node.inputs()
            next_idx = len(existing_inputs)
            for idx in range(len(existing_inputs)):
                if existing_inputs[idx] is None:
                    next_idx = idx
                    break
            try:
                merge_node.setInput(next_idx, ragdoll_state)
            except Exception as e:
                logger.warning(f"Could not wire ragdoll state to merge: {e}")

    dopnet.layoutChildren()

    return result


# =============================================================================
# Agent Look At
# =============================================================================


@handle_connection_errors("add_crowd_look_at")
def add_crowd_look_at(
    dopnet_path: str,
    target_position: Optional[List[float]] = None,
    target_node_path: Optional[str] = None,
    eye_offset: Optional[List[float]] = None,
    head_joint: str = "Head",
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Add Agent Look At behavior to the crowd simulation.

    Creates agentlookat and agentlookatapply DOP nodes that make agents
    turn their heads toward a target position or object.

    Args:
        dopnet_path: Path to the DOP network
        target_position: Optional [x, y, z] target position for agents to look at
        target_node_path: Optional path to a target node (alternative to position)
        eye_offset: Optional [x, y, z] offset from head joint for eye position
        head_joint: Name of the head joint/transform (default: "Head")
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - lookat_path: Path to the agentlookat DOP node
        - apply_path: Path to the agentlookatapply DOP node
        - target: Target position or node path configured

    Examples:
        add_crowd_look_at("/obj/crowd_sim_dopnet",
                         target_position=[10, 2, 0])
        add_crowd_look_at("/obj/crowd_sim_dopnet",
                         target_node_path="/obj/target_null",
                         head_joint="Head")
    """
    hou = ensure_connected(host, port)

    dopnet = hou.node(dopnet_path)
    if dopnet is None:
        return {"status": "error", "message": f"DOP network not found: {dopnet_path}"}

    # Create agent look at node
    lookat_node = dopnet.createNode("agentlookat", "agent_lookat")
    if lookat_node is None:
        return {
            "status": "error",
            "message": "Failed to create agentlookat node. "
            "This node type may not be available in your Houdini version.",
        }

    # Configure target
    if target_position and lookat_node.parm("targetposx"):
        lookat_node.parm("targetposx").set(target_position[0])
        lookat_node.parm("targetposy").set(target_position[1])
        lookat_node.parm("targetposz").set(target_position[2])
    elif target_node_path and lookat_node.parm("targetpath"):
        lookat_node.parm("targetpath").set(target_node_path)

    # Set head joint
    if lookat_node.parm("headjoint"):
        lookat_node.parm("headjoint").set(head_joint)

    # Set eye offset
    if eye_offset and lookat_node.parm("eyeoffsetx"):
        lookat_node.parm("eyeoffsetx").set(eye_offset[0])
        lookat_node.parm("eyeoffsety").set(eye_offset[1])
        lookat_node.parm("eyeoffsetz").set(eye_offset[2])

    # Create agent look at apply node
    apply_node = dopnet.createNode("agentlookatapply", "agent_lookat_apply")
    if apply_node:
        apply_node.setFirstInput(lookat_node)

    # Wire into crowd solver
    crowd_solver = None
    for child in dopnet.children():
        if child.type().name().startswith("crowdsolver"):
            crowd_solver = child
            break

    if crowd_solver and apply_node:
        existing_inputs = crowd_solver.inputs()
        next_input = None
        for idx in range(1, len(existing_inputs)):
            if existing_inputs[idx] is None:
                next_input = idx
                break
        if next_input is None:
            next_input = max(1, len(existing_inputs))
        try:
            crowd_solver.setInput(next_input, apply_node)
        except Exception as e:
            logger.warning(f"Could not wire look-at to solver: {e}")

    dopnet.layoutChildren()

    result = {
        "status": "success",
        "lookat_path": lookat_node.path(),
    }
    if apply_node:
        result["apply_path"] = apply_node.path()
    if target_position:
        result["target"] = target_position
    elif target_node_path:
        result["target"] = target_node_path

    return result


# =============================================================================
# Motion Path (SOP-based, H20+)
# =============================================================================


@handle_connection_errors("setup_motion_path")
def setup_motion_path(
    parent_path: str,
    agent_setup_path: str,
    clip_name: str = "walk",
    clip_speed: float = 1.0,
    start_frame: int = 1,
    end_frame: int = 240,
    enable_avoid: bool = False,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Create SOP-based Motion Path workflow for crowd (Houdini 20+).

    Creates the Motion Path chain: crowdmotionpath -> (optional avoid) ->
    crowdmotionpathevaluate. This provides an art-directable alternative
    to DOP-based simulation for fast iteration.

    Args:
        parent_path: Path to the geometry container
        agent_setup_path: Path to the agent definition/source node
        clip_name: Animation clip name to use (default: "walk")
        clip_speed: Clip playback speed (default: 1.0)
        start_frame: Start frame for path generation (default: 1)
        end_frame: End frame for path generation (default: 240)
        enable_avoid: If True, add crowdmotionpathavoid for collision
            avoidance between agents (default: False)
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - motion_path_path: Path to crowdmotionpath node
        - avoid_path: Path to crowdmotionpathavoid node (if enabled)
        - evaluate_path: Path to crowdmotionpathevaluate node
        - clip_name: Clip name configured

    Examples:
        setup_motion_path("/obj/crowd_sim",
                         "/obj/soldier_agent_setup/soldier_cache",
                         clip_name="walk", end_frame=500)
        setup_motion_path("/obj/crowd_sim", agent_path,
                         enable_avoid=True, clip_speed=1.2)
    """
    hou = ensure_connected(host, port)

    parent = hou.node(parent_path)
    if parent is None:
        return {"status": "error", "message": f"Parent node not found: {parent_path}"}

    agent_node = hou.node(agent_setup_path)
    if agent_node is None:
        return {
            "status": "error",
            "message": f"Agent setup node not found: {agent_setup_path}",
        }

    # Create motion path entry node
    motion_path = parent.createNode("crowdmotionpath", "crowd_motion_path")
    if motion_path is None:
        return {
            "status": "error",
            "message": "Failed to create crowdmotionpath node. "
            "This node type requires Houdini 20+.",
        }

    motion_path.setFirstInput(agent_node)

    # Configure clip and timing
    if motion_path.parm("clipname"):
        motion_path.parm("clipname").set(clip_name)
    if motion_path.parm("clipspeed"):
        motion_path.parm("clipspeed").set(clip_speed)
    if motion_path.parm("startframe"):
        motion_path.parm("startframe").set(start_frame)
    if motion_path.parm("endframe"):
        motion_path.parm("endframe").set(end_frame)

    last_node = motion_path
    result = {
        "status": "success",
        "motion_path_path": motion_path.path(),
        "clip_name": clip_name,
    }

    # Optional: collision avoidance
    if enable_avoid:
        avoid_node = parent.createNode("crowdmotionpathavoid", "motion_path_avoid")
        if avoid_node:
            avoid_node.setFirstInput(last_node)
            last_node = avoid_node
            result["avoid_path"] = avoid_node.path()

    # Create evaluate node (final output)
    evaluate = parent.createNode("crowdmotionpathevaluate", "motion_path_evaluate")
    if evaluate is None:
        return {
            "status": "error",
            "message": "Failed to create crowdmotionpathevaluate node.",
        }
    evaluate.setFirstInput(last_node)
    evaluate.setDisplayFlag(True)
    evaluate.setRenderFlag(True)

    result["evaluate_path"] = evaluate.path()

    parent.layoutChildren()

    return result


# =============================================================================
# Steer Weight Control
# =============================================================================


@handle_connection_errors("add_steer_weight")
def add_steer_weight(
    dopnet_path: str,
    behavior_name: str,
    weight: float,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Set the steering weight on an existing POP Steer behavior node.

    Adjusts the weight parameter on a POP Steer node to control how much
    influence it has relative to other steering behaviors. The Crowd Solver
    automatically normalizes all steer weights to sum to 1.0.

    Args:
        dopnet_path: Path to the DOP network
        behavior_name: Name of the POP Steer behavior node to modify
        weight: Weight value to set (0.0-10.0). Higher values give more
            influence relative to other behaviors.
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - behavior_path: Path to the modified behavior node
        - weight: Weight value set
        - behavior_type: Type of the behavior node

    Examples:
        add_steer_weight("/obj/crowd_sim_dopnet", "steer_wander", 0.5)
        add_steer_weight("/obj/crowd_sim_dopnet", "steer_seek", 2.0)
    """
    hou = ensure_connected(host, port)

    dopnet = hou.node(dopnet_path)
    if dopnet is None:
        return {"status": "error", "message": f"DOP network not found: {dopnet_path}"}

    # Find the behavior node
    behavior_node = dopnet.node(behavior_name)
    if behavior_node is None:
        # Try to find by searching children
        for child in dopnet.children():
            if child.name() == behavior_name:
                behavior_node = child
                break

    if behavior_node is None:
        return {
            "status": "error",
            "message": f"Behavior node '{behavior_name}' not found in {dopnet_path}",
        }

    # Verify it's a POP Steer node
    node_type = behavior_node.type().name()
    if not node_type.startswith("popsteer"):
        return {
            "status": "error",
            "message": f"Node '{behavior_name}' is type '{node_type}', not a POP Steer node",
        }

    # Set weight
    weight_parm = behavior_node.parm("weight")
    if weight_parm is None:
        return {
            "status": "error",
            "message": f"No 'weight' parameter found on {behavior_name}",
        }

    weight_parm.set(weight)

    return {
        "status": "success",
        "behavior_path": behavior_node.path(),
        "weight": weight,
        "behavior_type": node_type,
    }


# =============================================================================
# Foot Planting Configuration
# =============================================================================


@handle_connection_errors("configure_foot_planting")
def configure_foot_planting(
    agent_setup_path: str,
    ankle_joint: str = "LeftAnkle",
    toe_joint: str = "LeftToe",
    upper_leg_joint: str = "LeftUpperLeg",
    knee_joint: str = "LeftKnee",
    enable_foot_locking: bool = True,
    adjust_hips: bool = True,
    host: str = "localhost",
    port: int = 18811,
) -> Dict[str, Any]:
    """
    Configure foot planting for crowd terrain adaptation.

    Sets up the Agent Prep SOP's lower limb definitions and configures
    terrain adaptation parameters for natural foot-ground contact.
    Prevents foot skating and floating when agents walk on terrain.

    Args:
        agent_setup_path: Path to the agent setup geometry container
        ankle_joint: Name of the ankle joint (default: "LeftAnkle")
        toe_joint: Name of the toe joint (default: "LeftToe")
        upper_leg_joint: Name of the upper leg joint (default: "LeftUpperLeg")
        knee_joint: Name of the knee joint (default: "LeftKnee")
        enable_foot_locking: Enable foot locking to prevent skating (default: True)
        adjust_hips: Enable automatic hip height adjustment (default: True)
        host: Houdini RPC server hostname
        port: Houdini RPC server port

    Returns:
        Dict with:
        - status: "success" or "error"
        - prep_path: Path to the agentprep node configured
        - joints_configured: Dict of joint names set
        - foot_locking: Whether foot locking is enabled
        - hip_adjustment: Whether hip adjustment is enabled

    Examples:
        configure_foot_planting("/obj/soldier_agent_setup")
        configure_foot_planting("/obj/agent_setup",
                               ankle_joint="RightAnkle",
                               toe_joint="RightToe",
                               upper_leg_joint="RightUpperLeg",
                               knee_joint="RightKnee")
    """
    hou = ensure_connected(host, port)

    agent_parent = hou.node(agent_setup_path)
    if agent_parent is None:
        return {"status": "error", "message": f"Agent setup not found: {agent_setup_path}"}

    # Find agentprep node (may be versioned, e.g. "agentprep::3.0")
    prep_node = None
    for child in agent_parent.children():
        if child.type().name().startswith("agentprep"):
            prep_node = child
            break

    if prep_node is None:
        return {
            "status": "error",
            "message": f"No agentprep node found in {agent_setup_path}. "
            "Run setup_crowd_agent() first.",
        }

    # Configure lower limb joints
    joints_configured = {}

    if prep_node.parm("upperleg"):
        prep_node.parm("upperleg").set(upper_leg_joint)
        joints_configured["upper_leg"] = upper_leg_joint

    if prep_node.parm("knee"):
        prep_node.parm("knee").set(knee_joint)
        joints_configured["knee"] = knee_joint

    if prep_node.parm("ankle"):
        prep_node.parm("ankle").set(ankle_joint)
        joints_configured["ankle"] = ankle_joint

    if prep_node.parm("toe"):
        prep_node.parm("toe").set(toe_joint)
        joints_configured["toe"] = toe_joint

    # Configure foot locking
    if prep_node.parm("enablefootlocking"):
        prep_node.parm("enablefootlocking").set(1 if enable_foot_locking else 0)

    # Configure hip adjustment
    if prep_node.parm("adjusthips"):
        prep_node.parm("adjusthips").set(1 if adjust_hips else 0)

    return {
        "status": "success",
        "prep_path": prep_node.path(),
        "joints_configured": joints_configured,
        "foot_locking": enable_foot_locking,
        "hip_adjustment": adjust_hips,
    }
