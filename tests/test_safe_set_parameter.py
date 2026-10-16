"""Tests for safe_set_parameter value validation logic.

Note: Full safe_set_parameter integration tests require a Houdini connection.
These tests focus on the _validate_value function which is pure logic.
"""


from houdini_mcp.tools.workflow.safe_set_parameter import _validate_value


class TestValidateFloat:
    def test_float_accepts_float(self):
        schema = {"type": "float", "name": "radx"}
        assert _validate_value(2.5, schema) == []

    def test_float_accepts_int(self):
        schema = {"type": "float", "name": "radx"}
        assert _validate_value(2, schema) == []

    def test_float_rejects_string(self):
        schema = {"type": "float", "name": "radx"}
        errors = _validate_value("bad", schema)
        assert len(errors) == 1
        assert "Expected float" in errors[0]


class TestValidateInt:
    def test_int_accepts_int(self):
        schema = {"type": "int", "name": "divs"}
        assert _validate_value(10, schema) == []

    def test_int_rejects_float(self):
        schema = {"type": "int", "name": "divs"}
        errors = _validate_value(2.5, schema)
        assert len(errors) == 1
        assert "Expected int" in errors[0]

    def test_int_rejects_string(self):
        schema = {"type": "int", "name": "divs"}
        errors = _validate_value("bad", schema)
        assert len(errors) == 1


class TestValidateString:
    def test_string_accepts_string(self):
        schema = {"type": "string", "name": "file"}
        assert _validate_value("path/to/file", schema) == []

    def test_string_rejects_int(self):
        schema = {"type": "string", "name": "file"}
        errors = _validate_value(42, schema)
        assert len(errors) == 1


class TestValidateToggle:
    def test_toggle_accepts_bool(self):
        schema = {"type": "toggle", "name": "enable"}
        assert _validate_value(True, schema) == []
        assert _validate_value(False, schema) == []

    def test_toggle_accepts_int(self):
        schema = {"type": "toggle", "name": "enable"}
        assert _validate_value(0, schema) == []
        assert _validate_value(1, schema) == []

    def test_toggle_rejects_string(self):
        schema = {"type": "toggle", "name": "enable"}
        errors = _validate_value("yes", schema)
        assert len(errors) == 1


class TestValidateMenu:
    def test_menu_accepts_valid_value(self):
        schema = {
            "type": "menu",
            "name": "type",
            "menu_items": [
                {"label": "Polygon", "value": 0},
                {"label": "Mesh", "value": 1},
            ],
        }
        assert _validate_value(0, schema) == []
        assert _validate_value(1, schema) == []

    def test_menu_rejects_invalid_value(self):
        schema = {
            "type": "menu",
            "name": "type",
            "menu_items": [
                {"label": "Polygon", "value": 0},
                {"label": "Mesh", "value": 1},
            ],
        }
        errors = _validate_value(99, schema)
        assert len(errors) == 1
        assert "not in menu items" in errors[0]

    def test_menu_string_values(self):
        schema = {
            "type": "menu",
            "name": "mode",
            "menu_items": [
                {"label": "Add", "value": "add"},
                {"label": "Replace", "value": "replace"},
            ],
        }
        assert _validate_value("add", schema) == []
        errors = _validate_value("delete", schema)
        assert len(errors) == 1


class TestValidateVector:
    def test_vector_accepts_correct_size(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        assert _validate_value([1.0, 2.0, 3.0], schema) == []

    def test_vector_accepts_tuple(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        assert _validate_value((1.0, 2.0, 3.0), schema) == []

    def test_vector_rejects_wrong_size(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        errors = _validate_value([1.0, 2.0], schema)
        assert len(errors) == 1
        assert "3 components" in errors[0]

    def test_vector_rejects_non_list(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        errors = _validate_value(1.0, schema)
        assert len(errors) == 1

    def test_vector_type_alias(self):
        schema = {"type": "vector", "name": "t", "tuple_size": 3}
        assert _validate_value([1.0, 2.0, 3.0], schema) == []

    def test_vector_no_tuple_size_accepts_any(self):
        schema = {"type": "float_vector", "name": "t"}
        assert _validate_value([1.0, 2.0], schema) == []


class TestValidateRange:
    def test_float_within_range(self):
        schema = {"type": "float", "name": "radx", "min": 0.0, "max": 10.0}
        assert _validate_value(5.0, schema) == []

    def test_float_at_boundaries(self):
        schema = {"type": "float", "name": "radx", "min": 0.0, "max": 10.0}
        assert _validate_value(0.0, schema) == []
        assert _validate_value(10.0, schema) == []

    def test_float_below_min(self):
        schema = {"type": "float", "name": "radx", "min": 0.0, "max": 10.0}
        errors = _validate_value(-1.0, schema)
        assert len(errors) == 1
        assert "below minimum" in errors[0]

    def test_float_above_max(self):
        schema = {"type": "float", "name": "radx", "min": 0.0, "max": 10.0}
        errors = _validate_value(11.0, schema)
        assert len(errors) == 1
        assert "above maximum" in errors[0]

    def test_no_range_constraint(self):
        schema = {"type": "float", "name": "radx"}
        assert _validate_value(999.0, schema) == []

    def test_int_range(self):
        schema = {"type": "int", "name": "divs", "min": 1, "max": 100}
        assert _validate_value(50, schema) == []
        errors = _validate_value(0, schema)
        assert len(errors) == 1


class TestValidateUnknownType:
    def test_unknown_type_passes(self):
        schema = {"type": "ramp", "name": "ramp1"}
        assert _validate_value("anything", schema) == []


# ---- _coerce_value tests ----

import pytest

from houdini_mcp.tools.workflow.safe_set_parameter import _coerce_value


class TestCoerceInt:
    def test_str_to_int(self):
        val, warn = _coerce_value("256", {"type": "int", "name": "divs"})
        assert val == 256
        assert warn is not None

    def test_float_whole_to_int(self):
        val, warn = _coerce_value(2.0, {"type": "int", "name": "divs"})
        assert val == 2
        assert isinstance(val, int)
        assert warn is not None

    def test_float_fractional_to_int_fails(self):
        with pytest.raises(ValueError, match="fractional"):
            _coerce_value(2.5, {"type": "int", "name": "divs"})

    def test_str_float_to_int_fails(self):
        with pytest.raises(ValueError, match="fractional"):
            _coerce_value("2.5", {"type": "int", "name": "divs"})

    def test_str_non_numeric_to_int_fails(self):
        with pytest.raises(ValueError, match="Cannot convert"):
            _coerce_value("abc", {"type": "int", "name": "divs"})


class TestCoerceFloat:
    def test_str_to_float(self):
        val, warn = _coerce_value("0.5", {"type": "float", "name": "radx"})
        assert val == 0.5
        assert warn is not None

    def test_str_invalid_to_float_fails(self):
        with pytest.raises(ValueError, match="Cannot convert"):
            _coerce_value("abc", {"type": "float", "name": "radx"})

    def test_int_to_float_no_coercion_needed(self):
        """int is already accepted by _validate_value for float; no coercion."""
        val, warn = _coerce_value(5, {"type": "float", "name": "radx"})
        assert val == 5
        assert warn is None


class TestCoerceToggle:
    def test_str_true(self):
        val, warn = _coerce_value("true", {"type": "toggle", "name": "enable"})
        assert val is True
        assert warn is not None

    def test_str_false(self):
        val, warn = _coerce_value("false", {"type": "toggle", "name": "enable"})
        assert val is False
        assert warn is not None

    def test_str_one(self):
        val, warn = _coerce_value("1", {"type": "toggle", "name": "enable"})
        assert val is True
        assert warn is not None

    def test_str_yes(self):
        val, warn = _coerce_value("yes", {"type": "toggle", "name": "enable"})
        assert val is True

    def test_str_invalid_toggle_fails(self):
        with pytest.raises(ValueError, match="Cannot convert"):
            _coerce_value("maybe", {"type": "toggle", "name": "enable"})


class TestCoerceVector:
    def test_comma_str_to_float_vector(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        val, warn = _coerce_value("1,2,3", schema)
        assert val == [1.0, 2.0, 3.0]
        assert warn is not None

    def test_int_list_to_float_vector(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        val, warn = _coerce_value([1, 2, 3], schema)
        assert val == [1.0, 2.0, 3.0]
        assert all(isinstance(v, float) for v in val)
        assert warn is not None

    def test_scalar_broadcast_to_vector(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        val, warn = _coerce_value(1.0, schema)
        assert val == [1.0, 1.0, 1.0]
        assert warn is not None

    def test_comma_str_to_int_vector(self):
        schema = {"type": "int_vector", "name": "res", "tuple_size": 2}
        val, warn = _coerce_value("640,480", schema)
        assert val == [640, 480]
        assert warn is not None

    def test_comma_str_invalid_fails(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        with pytest.raises(ValueError, match="Cannot convert"):
            _coerce_value("a,b,c", schema)


class TestCoerceNoOp:
    def test_int_for_int_no_coercion(self):
        val, warn = _coerce_value(256, {"type": "int", "name": "divs"})
        assert val == 256
        assert warn is None

    def test_float_for_float_no_coercion(self):
        val, warn = _coerce_value(2.5, {"type": "float", "name": "radx"})
        assert val == 2.5
        assert warn is None

    def test_bool_for_toggle_no_coercion(self):
        val, warn = _coerce_value(True, {"type": "toggle", "name": "enable"})
        assert val is True
        assert warn is None

    def test_list_for_vector_no_coercion(self):
        schema = {"type": "float_vector", "name": "t", "tuple_size": 3}
        val, warn = _coerce_value([1.0, 2.0, 3.0], schema)
        assert val == [1.0, 2.0, 3.0]
        assert warn is None
