"""safe_set_parameter tool — Schema-validated parameter setting."""

from __future__ import annotations

import logging
from typing import Any

from .._common import handle_connection_errors
from ..parameters import get_parameter_schema, set_parameter
from ._common import (
    _load_gate_state,
    _resolve_project_root,
    _save_gate_state,
)

logger = logging.getLogger(__name__)


@handle_connection_errors("safe_set_parameter")
def safe_set_parameter(
    node_path: str,
    param_name: str,
    value: Any,
    diff_id: str,
    project_root: str | None = None,
    host: str = "localhost",
    port: int = 18811,
) -> dict[str, Any]:
    """Set a parameter with automatic schema validation.

    Combines get_parameter_schema + validation + set_parameter into a
    single safe operation. Enforces RULE 6 from CLAUDE.md.

    Args:
        node_path: Full path to the node.
        param_name: Name of the parameter.
        value: Value to set.
        diff_id: Approved diff ID.
        project_root: Optional project root path.
        host: Houdini host.
        port: Houdini port.

    Returns:
        Dict with status, schema info, and set result.
    """
    # Load gate state
    root = _resolve_project_root(project_root)
    agent_dir = root / ".agent"
    gate = _load_gate_state(agent_dir)

    # Check diff approval
    if gate.pending_diff_id != diff_id or not gate.diff_approved:
        return {
            "status": "error",
            "message": f"Diff '{diff_id}' is not approved. Call approve_diff first.",
        }

    # Step 1: Get parameter schema with Japanese help
    schema_result = get_parameter_schema(
        node_path,
        parm_name=param_name,
        include_description_ja=True,
        host=host,
        port=port,
    )
    if schema_result.get("status") == "error":
        return {
            "status": "error",
            "message": f"Schema lookup failed: {schema_result.get('message')}",
            "schema_result": schema_result,
        }

    params = schema_result.get("parameters", [])
    if not params:
        return {
            "status": "error",
            "message": f"Parameter '{param_name}' not found in schema for {node_path}.",
            "available_parameters": [
                p.get("name") for p in schema_result.get("parameters", [])
            ],
        }

    param_schema = params[0]

    # Step 2: Coerce value to match schema type
    coerce_warning = None
    try:
        value, coerce_warning = _coerce_value(value, param_schema)
    except ValueError as e:
        return {
            "status": "error",
            "message": f"Type coercion failed: {e}",
            "schema": param_schema,
        }

    # Step 3: Validate value against schema (after coercion)
    validation_errors = _validate_value(value, param_schema)
    if validation_errors:
        return {
            "status": "error",
            "message": f"Validation failed: {'; '.join(validation_errors)}",
            "schema": param_schema,
            "validation_errors": validation_errors,
        }

    # Step 4: Update gate state — mark schema as checked for this node
    gate.schema_checked[node_path] = True

    # Also update verified_params with the validated parameter name
    if node_path not in gate.verified_params:
        gate.verified_params[node_path] = []
    if param_name not in gate.verified_params[node_path]:
        gate.verified_params[node_path].append(param_name)

    _save_gate_state(agent_dir, gate)

    # Step 5: Execute set_parameter
    set_result = set_parameter(node_path, param_name, value, host, port)

    if set_result.get("status") == "error":
        return {
            "status": "error",
            "message": f"set_parameter failed: {set_result.get('message')}",
            "schema": param_schema,
        }

    result = {
        "status": "success",
        "node_path": node_path,
        "param_name": param_name,
        "value": value,
        "schema": param_schema,
        "schema_validated": True,
        "diff_id": diff_id,
    }
    if coerce_warning:
        result["coerce_warning"] = coerce_warning
    return result


def _coerce_value(value: Any, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """Try to coerce *value* to match the schema type.

    Returns:
        ``(coerced_value, warning_or_None)``
        *warning* is set when coercion was applied.
        Returns ``(value, None)`` unchanged when no coercion is needed.

    Raises:
        ValueError: If *value* cannot be safely converted.
    """
    param_type = schema.get("type", "unknown")

    # --- int -----------------------------------------------------------
    if param_type == "int" and not isinstance(value, int):
        if isinstance(value, float):
            if value != int(value):
                raise ValueError(
                    f"Cannot safely convert float {value!r} to int "
                    "(fractional part would be lost)"
                )
            return int(value), f"Coerced float {value!r} -> int {int(value)}"
        if isinstance(value, str):
            try:
                parsed = int(value)
            except ValueError:
                # Try float first to give better error
                try:
                    f = float(value)
                except ValueError:
                    raise ValueError(
                        f"Cannot convert string {value!r} to int"
                    )
                if f != int(f):
                    raise ValueError(
                        f"Cannot safely convert string {value!r} to int "
                        "(fractional part would be lost)"
                    )
                parsed = int(f)
            return parsed, f"Coerced string {value!r} -> int {parsed}"

    # --- float ---------------------------------------------------------
    if param_type == "float" and not isinstance(value, (int, float)):
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                raise ValueError(
                    f"Cannot convert string {value!r} to float"
                )
            return parsed, f"Coerced string {value!r} -> float {parsed}"

    # --- toggle --------------------------------------------------------
    if param_type == "toggle" and not isinstance(value, (bool, int)):
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "yes", "on", "1"):
                return True, f"Coerced string {value!r} -> True"
            if low in ("false", "no", "off", "0"):
                return False, f"Coerced string {value!r} -> False"
            raise ValueError(
                f"Cannot convert string {value!r} to toggle "
                "(expected true/false/yes/no/on/off/1/0)"
            )

    # --- vector (float_vector / int_vector / vector) -------------------
    if param_type in ("float_vector", "int_vector", "vector"):
        tuple_size = schema.get("tuple_size")
        is_int_vec = param_type == "int_vector"
        elem_type = int if is_int_vec else float

        # Comma-separated string -> list
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",")]
            try:
                converted = [elem_type(p) for p in parts]
            except ValueError:
                raise ValueError(
                    f"Cannot convert string {value!r} to {param_type}"
                )
            return converted, f"Coerced string {value!r} -> {converted}"

        # Scalar broadcast -> list (only when tuple_size is known)
        if isinstance(value, (int, float)) and tuple_size is not None:
            broadcast = [elem_type(value)] * tuple_size
            return broadcast, (
                f"Broadcast scalar {value!r} -> {broadcast}"
            )

        # list[int] -> list[float] for float_vector
        if (
            not is_int_vec
            and isinstance(value, (list, tuple))
            and value
            and all(isinstance(v, int) for v in value)
        ):
            converted = [float(v) for v in value]
            return converted, f"Coerced int list {list(value)!r} -> {converted}"

    # No coercion needed or applicable
    return value, None


def _validate_value(value: Any, schema: dict[str, Any]) -> list[str]:
    """Validate value against parameter schema. Returns list of errors."""
    errors: list[str] = []
    param_type = schema.get("type", "unknown")

    # Type validation
    if param_type == "float" and not isinstance(value, (int, float)):
        errors.append(f"Expected float, got {type(value).__name__}")
    elif param_type == "int" and not isinstance(value, int):
        errors.append(f"Expected int, got {type(value).__name__}")
    elif param_type == "string" and not isinstance(value, str):
        errors.append(f"Expected string, got {type(value).__name__}")
    elif param_type == "toggle" and not isinstance(value, (bool, int)):
        errors.append(f"Expected bool/int, got {type(value).__name__}")
    elif param_type in ("float_vector", "int_vector", "vector"):
        if not isinstance(value, (list, tuple)):
            errors.append(
                f"Expected list/tuple for vector, got {type(value).__name__}"
            )
        else:
            expected_size = schema.get("tuple_size")
            if expected_size is not None and len(value) != expected_size:
                errors.append(
                    f"Expected {expected_size} components, got {len(value)}"
                )

    # Menu validation
    if param_type == "menu":
        menu_items = schema.get("menu_items", [])
        if menu_items:
            valid_values = [item.get("value") for item in menu_items]
            if value not in valid_values:
                errors.append(
                    f"Value {value!r} not in menu items: {valid_values}"
                )

    # Range validation
    if param_type in ("float", "int") and isinstance(value, (int, float)):
        min_val = schema.get("min")
        max_val = schema.get("max")
        if min_val is not None and value < min_val:
            errors.append(f"Value {value} below minimum {min_val}")
        if max_val is not None and value > max_val:
            errors.append(f"Value {value} above maximum {max_val}")

    return errors
