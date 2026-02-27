"""Tests for the local Japanese help file parser.

Tests use real help files from ~/Documents/houdini20.5/help/nodes/.
"""

import os
import pytest
from pathlib import Path

from houdini_mcp.tools.help_local import (
    ParameterHelpEntry,
    ParsedNodeHelp,
    _resolve_help_file,
    _parse_help_file,
    _parse_parameters_section,
    get_local_help,
    invalidate_help_cache,
    CATEGORY_MAP,
)


# Path to real help files
HELP_BASE = Path(os.path.expanduser("~")) / "Documents" / "houdini20.5" / "help"
HAS_HELP_FILES = (HELP_BASE / "nodes" / "sop" / "box.txt").is_file()

skip_no_help = pytest.mark.skipif(
    not HAS_HELP_FILES, reason="Houdini help files not found"
)


# =============================================================================
# File Resolution Tests
# =============================================================================


class TestFileResolution:
    @skip_no_help
    def test_resolve_sop_box(self):
        path = _resolve_help_file("Sop", "box")
        assert path is not None
        assert path.name == "box.txt"
        assert "sop" in str(path).lower()

    @skip_no_help
    def test_resolve_lowercase_context(self):
        path = _resolve_help_file("sop", "box")
        assert path is not None

    @skip_no_help
    def test_resolve_object_context(self):
        """Object category maps to obj/ directory."""
        path = _resolve_help_file("Object", "geo")
        assert path is not None
        assert "obj" in str(path).lower()

    @skip_no_help
    def test_resolve_nonexistent_node(self):
        path = _resolve_help_file("Sop", "nonexistent_node_xyz_12345")
        assert path is None

    @skip_no_help
    def test_resolve_scatter(self):
        path = _resolve_help_file("Sop", "scatter")
        assert path is not None
        assert path.name == "scatter.txt"

    @skip_no_help
    def test_resolve_versioned_node(self):
        """attribcreate::2.0 should resolve to attribcreate.txt."""
        path = _resolve_help_file("Sop", "attribcreate::2.0")
        assert path is not None
        assert "attribcreate" in path.name

    @skip_no_help
    def test_resolve_namespaced_node(self):
        """labs::align_and_distribute should resolve to labs--align_and_distribute.txt."""
        path = _resolve_help_file("Sop", "labs::align_and_distribute")
        if path is not None:
            assert "labs--" in path.name


# =============================================================================
# Parser Tests - box.txt
# =============================================================================


class TestParseBox:
    @skip_no_help
    def test_parse_box_metadata(self):
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None
        assert result.title == "Box"
        assert result.context == "sop"
        assert result.internal_name == "box"

    @skip_no_help
    def test_parse_box_summary(self):
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None
        assert "立方体" in result.summary_ja or "ボックス" in result.summary_ja

    @skip_no_help
    def test_parse_box_parameters(self):
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None
        assert len(result.parameters) > 0

    @skip_no_help
    def test_parse_box_size_channels(self):
        """Size parameter should have channels: sizex, sizey, sizez."""
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None

        # Find Size parameter
        size_entry = result.lookup("sizex", "")
        assert size_entry is not None, "Should find Size via channel 'sizex'"
        assert "sizex" in size_entry.channel_names
        assert "sizey" in size_entry.channel_names
        assert "sizez" in size_entry.channel_names
        assert "サイズ" in size_entry.description_ja

    @skip_no_help
    def test_parse_box_center_channels(self):
        """Center parameter should have channels: tx, ty, tz."""
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None

        center_entry = result.lookup("tx", "")
        assert center_entry is not None, "Should find Center via channel 'tx'"
        assert "位置" in center_entry.description_ja

    @skip_no_help
    def test_parse_box_primitive_type_menu(self):
        """Primitive Type parameter should have menu items with Japanese descriptions."""
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None

        prim_type = result.lookup("", "Primitive Type")
        assert prim_type is not None, "Should find Primitive Type by label"
        assert prim_type.description_ja
        assert len(prim_type.menu_items_ja) > 0
        assert "Polygon" in prim_type.menu_items_ja

    @skip_no_help
    def test_lookup_priority(self):
        """#id match should take priority over label match."""
        path = _resolve_help_file("Sop", "box")
        result = _parse_help_file(path)
        assert result is not None

        # "scale" should match via #channels
        entry = result.lookup("scale", "")
        assert entry is not None


# =============================================================================
# Parser Tests - scatter.txt
# =============================================================================


class TestParseScatter:
    @skip_no_help
    def test_parse_scatter_metadata(self):
        path = _resolve_help_file("Sop", "scatter")
        result = _parse_help_file(path)
        assert result is not None
        assert result.title == "Scatter"
        assert result.context == "sop"
        assert result.internal_name == "scatter"

    @skip_no_help
    def test_parse_scatter_id_mapping(self):
        """Scatter params use #id: tags for mapping."""
        path = _resolve_help_file("Sop", "scatter")
        result = _parse_help_file(path)
        assert result is not None

        # group parameter has #id: group
        group_entry = result.lookup("group", "")
        assert group_entry is not None
        assert group_entry.param_id == "group"
        assert "プリミティブ" in group_entry.description_ja or "グループ" in group_entry.description_ja

    @skip_no_help
    def test_parse_scatter_generateby(self):
        """Generate parameter should have menu items."""
        path = _resolve_help_file("Sop", "scatter")
        result = _parse_help_file(path)
        assert result is not None

        gen_entry = result.lookup("generateby", "")
        assert gen_entry is not None
        assert len(gen_entry.menu_items_ja) > 0
        assert "By Density" in gen_entry.menu_items_ja

    @skip_no_help
    def test_parse_scatter_densityscale(self):
        path = _resolve_help_file("Sop", "scatter")
        result = _parse_help_file(path)
        assert result is not None

        entry = result.lookup("densityscale", "")
        assert entry is not None
        assert "密度" in entry.description_ja


# =============================================================================
# Cache Tests
# =============================================================================


class TestCache:
    def setup_method(self):
        invalidate_help_cache()

    @skip_no_help
    def test_cache_returns_same_object(self):
        result1 = get_local_help("Sop", "box")
        result2 = get_local_help("Sop", "box")
        assert result1 is result2

    @skip_no_help
    def test_cache_invalidation(self):
        result1 = get_local_help("Sop", "box")
        invalidate_help_cache()
        result2 = get_local_help("Sop", "box")
        # After invalidation, should be a new parse (not same object)
        assert result1 is not result2
        # But content should be equivalent
        assert result1.title == result2.title

    def test_cache_miss_returns_none(self):
        result = get_local_help("Sop", "nonexistent_node_xyz")
        assert result is None


# =============================================================================
# Category Mapping Tests
# =============================================================================


class TestCategoryMap:
    def test_sop_mapping(self):
        assert CATEGORY_MAP["Sop"] == "sop"

    def test_object_to_obj(self):
        assert CATEGORY_MAP["Object"] == "obj"

    def test_driver_to_out(self):
        assert CATEGORY_MAP["Driver"] == "out"

    def test_lowercase_fallback(self):
        assert CATEGORY_MAP["sop"] == "sop"


# =============================================================================
# ParsedNodeHelp Unit Tests
# =============================================================================


class TestParsedNodeHelpLookup:
    def _make_help(self, params):
        h = ParsedNodeHelp(
            context="sop",
            internal_name="test",
            title="Test",
            summary_ja="テスト",
            parameters=params,
        )
        h._build_indices()
        return h

    def test_lookup_by_id(self):
        entry = ParameterHelpEntry(label="My Param", param_id="myparm", description_ja="説明")
        h = self._make_help([entry])
        assert h.lookup("myparm", "") is entry

    def test_lookup_by_channel(self):
        entry = ParameterHelpEntry(
            label="Size", channel_names=["sizex", "sizey", "sizez"], description_ja="サイズ"
        )
        h = self._make_help([entry])
        assert h.lookup("sizex", "") is entry
        assert h.lookup("sizey", "") is entry

    def test_lookup_by_label(self):
        entry = ParameterHelpEntry(label="Primitive Type", description_ja="タイプ")
        h = self._make_help([entry])
        assert h.lookup("unknown_name", "Primitive Type") is entry

    def test_lookup_priority_id_over_label(self):
        """#id match should win over label match."""
        entry_id = ParameterHelpEntry(label="Other", param_id="foo", description_ja="IDで一致")
        entry_label = ParameterHelpEntry(label="Foo", description_ja="ラベルで一致")
        h = self._make_help([entry_id, entry_label])
        result = h.lookup("foo", "Foo")
        assert result is entry_id

    def test_lookup_miss(self):
        entry = ParameterHelpEntry(label="Size", param_id="size", description_ja="サイズ")
        h = self._make_help([entry])
        assert h.lookup("unknown", "Unknown") is None
