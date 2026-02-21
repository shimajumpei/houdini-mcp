"""Live integration tests for crowd simulation tools against a running Houdini.

These tests are skipped by default. To run them, set:

- RUN_INTEGRATION_TESTS=1
- HOUDINI_HOST=<host>  (default: localhost)
- HOUDINI_PORT=<port>  (default: 18811)

They create temporary nodes and clean up after themselves.
"""

from __future__ import annotations

import os
import uuid

import pytest

from houdini_mcp import tools


def _integration_enabled() -> bool:
    return os.getenv("RUN_INTEGRATION_TESTS") == "1"


def _houdini_target() -> tuple[str, int]:
    host = os.getenv("HOUDINI_HOST", "localhost")
    port_str = os.getenv("HOUDINI_PORT", "18811")
    return host, int(port_str)


pytestmark = pytest.mark.skipif(
    not _integration_enabled(),
    reason="set RUN_INTEGRATION_TESTS=1 to enable integration tests",
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# =============================================================================
# Agent Setup
# =============================================================================


@pytest.mark.integration
class TestSetupCrowdAgent:
    """Test setup_crowd_agent creates the agent definition chain."""

    def test_basic_agent_chain(self):
        host, port = _houdini_target()
        name = f"mcp_agent_{_uid()}"
        geo_path = None

        try:
            result = tools.setup_crowd_agent(name, host=host, port=port)
            assert result["status"] == "success", result.get("message", "")
            geo_path = result["geo_path"]

            # Should have created a geo container with agent nodes
            assert "agent_path" in result
            assert "prep_path" in result
            assert "cache_path" in result

            # Verify the cache node exists
            info = tools.get_node_info(result["cache_path"], host=host, port=port)
            assert info["status"] == "success"
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Crowd Source
# =============================================================================


@pytest.mark.integration
class TestSetupCrowdSource:
    """Test setup_crowd_source creates placement nodes."""

    def test_scatter_source(self):
        host, port = _houdini_target()
        agent_name = f"mcp_src_{_uid()}"
        geo_path = None

        try:
            # Create agent first
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success", agent.get("message", "")
            geo_path = agent["geo_path"]

            # Create crowd source
            result = tools.setup_crowd_source(
                geo_path, agent["cache_path"],
                source_type="scatter", count=10,
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
            assert "source_path" in result
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# DOP Network
# =============================================================================


@pytest.mark.integration
class TestSetupCrowdDop:
    """Test setup_crowd_dop creates the simulation network."""

    def test_basic_dop_network(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            result = tools.setup_crowd_dop(host=host, port=port)
            assert result["status"] == "success", result.get("message", "")
            dopnet_path = result["dopnet_path"]

            # Should have solver, crowd object, ground, gravity
            assert "crowdsolver_path" in result
            assert "crowdobject_path" in result

            # Verify DOP network exists
            info = tools.get_node_info(dopnet_path, host=host, port=port)
            assert info["status"] == "success"
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)


# =============================================================================
# Behaviors
# =============================================================================


@pytest.mark.integration
class TestAddCrowdBehavior:
    """Test add_crowd_behavior adds steering behaviors."""

    def test_add_wander(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            assert dop["status"] == "success"
            dopnet_path = dop["dopnet_path"]

            result = tools.add_crowd_behavior(
                dopnet_path, "wander", host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
            assert result["behavior_type"] == "wander"
            assert "behavior_path" in result
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)

    def test_add_multiple_behaviors(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            dopnet_path = dop["dopnet_path"]

            b1 = tools.add_crowd_behavior(dopnet_path, "wander", host=host, port=port)
            b2 = tools.add_crowd_behavior(dopnet_path, "separate", host=host, port=port)
            assert b1["status"] == "success"
            assert b2["status"] == "success"
            assert b1["behavior_path"] != b2["behavior_path"]
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)

    def test_invalid_behavior_type(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            dopnet_path = dop["dopnet_path"]

            result = tools.add_crowd_behavior(
                dopnet_path, "nonexistent_type", host=host, port=port,
            )
            assert result["status"] == "error"
            assert "available_types" in result
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)


# =============================================================================
# Crowd States
# =============================================================================


@pytest.mark.integration
class TestAddCrowdState:
    """Test add_crowd_state creates state machine states."""

    def test_add_single_state(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            dopnet_path = dop["dopnet_path"]

            result = tools.add_crowd_state(
                dopnet_path, "walk", clip_name="walk",
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
            assert "state_path" in result
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)

    def test_add_multiple_states(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            dopnet_path = dop["dopnet_path"]

            s1 = tools.add_crowd_state(dopnet_path, "idle", host=host, port=port)
            s2 = tools.add_crowd_state(dopnet_path, "walk", host=host, port=port)
            s3 = tools.add_crowd_state(dopnet_path, "run", host=host, port=port)
            assert s1["status"] == "success"
            assert s2["status"] == "success"
            assert s3["status"] == "success"
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)


# =============================================================================
# Transitions
# =============================================================================


@pytest.mark.integration
class TestAddCrowdTransition:
    """Test add_crowd_transition creates state transitions."""

    def test_add_transition(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            dopnet_path = dop["dopnet_path"]

            # Add states first
            tools.add_crowd_state(dopnet_path, "idle", host=host, port=port)
            tools.add_crowd_state(dopnet_path, "walk", host=host, port=port)

            result = tools.add_crowd_transition(
                dopnet_path, "idle", "walk", "speed",
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
            assert "trigger_path" in result
            assert "transition_path" in result
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)


# =============================================================================
# Terrain
# =============================================================================


@pytest.mark.integration
class TestSetupCrowdTerrain:
    """Test setup_crowd_terrain creates terrain adaptation nodes."""

    def test_basic_terrain(self):
        host, port = _houdini_target()
        agent_name = f"mcp_ter_{_uid()}"
        geo_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            result = tools.setup_crowd_terrain(
                geo_path, host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Introspection & Validation
# =============================================================================


@pytest.mark.integration
class TestCrowdIntrospection:
    """Test get_crowd_info and validate_crowd_setup."""

    def test_get_crowd_info_empty_scene(self):
        host, port = _houdini_target()
        result = tools.get_crowd_info(host=host, port=port)
        assert result["status"] == "success"

    def test_get_crowd_info_with_crowd(self):
        host, port = _houdini_target()
        dopnet_path = None
        geo_path = None

        try:
            # Build a minimal crowd
            agent = tools.setup_crowd_agent(f"mcp_info_{_uid()}", host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            dop = tools.setup_crowd_dop(host=host, port=port)
            assert dop["status"] == "success"
            dopnet_path = dop["dopnet_path"]

            tools.add_crowd_behavior(dopnet_path, "wander", host=host, port=port)
            tools.add_crowd_state(dopnet_path, "idle", host=host, port=port)

            # Now introspect
            info = tools.get_crowd_info(host=host, port=port)
            assert info["status"] == "success"
            assert len(info.get("agent_definitions", [])) > 0
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)
            if geo_path:
                tools.delete_node(geo_path, host, port)

    def test_validate_crowd_setup(self):
        host, port = _houdini_target()
        result = tools.validate_crowd_setup(host=host, port=port)
        assert result["status"] == "success"
        assert "issues" in result


# =============================================================================
# Formation (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestSetupCrowdFormation:
    """Test setup_crowd_formation creates grid-based placement."""

    def test_basic_formation(self):
        host, port = _houdini_target()
        agent_name = f"mcp_form_{_uid()}"
        geo_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            result = tools.setup_crowd_formation(
                geo_path, agent["cache_path"],
                rows=3, columns=3, spacing=2.0,
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Clip Transition Graph (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestAddCrowdClipTransitionGraph:
    """Test add_crowd_clip_transition_graph creates transition graph SOP."""

    def test_basic_clip_transition_graph(self):
        host, port = _houdini_target()
        agent_name = f"mcp_ctg_{_uid()}"
        geo_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            result = tools.add_crowd_clip_transition_graph(
                geo_path, agent["cache_path"],
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Agent Layer (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestAddCrowdLayer:
    """Test add_crowd_layer creates agent layer diversity setup."""

    def test_basic_layer(self):
        host, port = _houdini_target()
        agent_name = f"mcp_lyr_{_uid()}"
        geo_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            result = tools.add_crowd_layer(
                geo_path, agent["cache_path"],
                layer_name="hat_layer",
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Ragdoll (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestSetupCrowdRagdoll:
    """Test setup_crowd_ragdoll creates ragdoll physics setup."""

    def test_basic_ragdoll(self):
        host, port = _houdini_target()
        agent_name = f"mcp_rag_{_uid()}"
        geo_path = None
        dopnet_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            dop = tools.setup_crowd_dop(host=host, port=port)
            assert dop["status"] == "success"
            dopnet_path = dop["dopnet_path"]

            result = tools.setup_crowd_ragdoll(
                agent["geo_path"], dopnet_path,
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Look At (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestAddCrowdLookAt:
    """Test add_crowd_look_at creates look-at DOP nodes."""

    def test_basic_look_at(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            assert dop["status"] == "success"
            dopnet_path = dop["dopnet_path"]

            result = tools.add_crowd_look_at(
                dopnet_path,
                target_position=[10.0, 0.0, 0.0],
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)


# =============================================================================
# Motion Path (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestSetupMotionPath:
    """Test setup_motion_path creates the SOP motion path chain."""

    def test_basic_motion_path(self):
        host, port = _houdini_target()
        agent_name = f"mcp_mp_{_uid()}"
        geo_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            result = tools.setup_motion_path(
                geo_path, agent["cache_path"],
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Steer Weight (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestAddSteerWeight:
    """Test add_steer_weight modifies behavior weights."""

    def test_set_weight(self):
        host, port = _houdini_target()
        dopnet_path = None

        try:
            dop = tools.setup_crowd_dop(host=host, port=port)
            assert dop["status"] == "success"
            dopnet_path = dop["dopnet_path"]

            # Add a behavior first
            beh = tools.add_crowd_behavior(
                dopnet_path, "wander", host=host, port=port,
            )
            assert beh["status"] == "success"

            result = tools.add_steer_weight(
                dopnet_path, beh["behavior_name"],
                weight=0.5,
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)


# =============================================================================
# Foot Planting (Phase 4)
# =============================================================================


@pytest.mark.integration
class TestConfigureFootPlanting:
    """Test configure_foot_planting sets up terrain foot contact."""

    def test_basic_foot_planting(self):
        host, port = _houdini_target()
        agent_name = f"mcp_fp_{_uid()}"
        geo_path = None

        try:
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success"
            geo_path = agent["geo_path"]

            result = tools.configure_foot_planting(
                agent["geo_path"],
                host=host, port=port,
            )
            assert result["status"] == "success", result.get("message", "")
        finally:
            if geo_path:
                tools.delete_node(geo_path, host, port)


# =============================================================================
# Full Pipeline E2E
# =============================================================================


@pytest.mark.integration
class TestFullCrowdPipeline:
    """End-to-end test: build a complete crowd simulation."""

    def test_full_pipeline(self):
        """Build agent -> source -> dop -> behaviors -> states -> transition."""
        host, port = _houdini_target()
        agent_name = f"mcp_e2e_{_uid()}"
        geo_path = None
        dopnet_path = None

        try:
            # 1. Agent setup
            agent = tools.setup_crowd_agent(agent_name, host=host, port=port)
            assert agent["status"] == "success", f"Agent setup failed: {agent}"
            geo_path = agent["geo_path"]

            # 2. Crowd source
            source = tools.setup_crowd_source(
                geo_path, agent["cache_path"],
                source_type="scatter", count=5,
                host=host, port=port,
            )
            assert source["status"] == "success", f"Source failed: {source}"

            # 3. DOP network
            dop = tools.setup_crowd_dop(host=host, port=port)
            assert dop["status"] == "success", f"DOP failed: {dop}"
            dopnet_path = dop["dopnet_path"]

            # 4. Add behaviors
            wander = tools.add_crowd_behavior(
                dopnet_path, "wander", host=host, port=port,
            )
            assert wander["status"] == "success", f"Wander failed: {wander}"

            separate = tools.add_crowd_behavior(
                dopnet_path, "separate", host=host, port=port,
            )
            assert separate["status"] == "success", f"Separate failed: {separate}"

            # 5. Add states
            idle = tools.add_crowd_state(
                dopnet_path, "idle", host=host, port=port,
            )
            assert idle["status"] == "success", f"Idle state failed: {idle}"

            walk = tools.add_crowd_state(
                dopnet_path, "walk", locomotion=True,
                host=host, port=port,
            )
            assert walk["status"] == "success", f"Walk state failed: {walk}"

            # 6. Add transition
            trans = tools.add_crowd_transition(
                dopnet_path, "idle", "walk", "speed",
                host=host, port=port,
            )
            assert trans["status"] == "success", f"Transition failed: {trans}"

            # 7. Validate
            valid = tools.validate_crowd_setup(host=host, port=port)
            assert valid["status"] == "success", f"Validate failed: {valid}"

            # 8. Introspect
            info = tools.get_crowd_info(host=host, port=port)
            assert info["status"] == "success", f"Info failed: {info}"

        finally:
            if dopnet_path:
                tools.delete_node(dopnet_path, host, port)
            if geo_path:
                tools.delete_node(geo_path, host, port)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
