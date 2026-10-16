"""Workflow management tools for Houdini MCP.

Provides project initialization, stage planning, scan recording,
validation, status management, diff approval, and safe parameter setting
through a structured .agent/ directory.
"""

from .approve_diff import approve_diff
from .ensure_project import ensure_project
from .plan_stage import plan_stage
from .propose_diff import propose_diff
from .read_project import read_project
from .record_norm_check import record_norm_check
from .record_scan import record_scan
from .safe_set_parameter import safe_set_parameter
from .update_spec_and_stages import update_spec_and_stages
from .update_stage import update_stage
from .validate_stage import validate_stage

__all__ = [
    "ensure_project",
    "read_project",
    "plan_stage",
    "record_scan",
    "record_norm_check",
    "validate_stage",
    "update_stage",
    "propose_diff",
    "approve_diff",
    "safe_set_parameter",
    "update_spec_and_stages",
]
