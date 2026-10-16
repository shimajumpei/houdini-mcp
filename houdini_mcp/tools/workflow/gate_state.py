"""Gate state model for architectural enforcement.

Provides a shared state file (.agent/gate_state.json) that bridges
client-side hooks (PreToolUse/PostToolUse) and server-side MCP tools.
Both sides read/write this file to enforce workflow preconditions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GATE_STATE_FILENAME = "gate_state.json"


@dataclass
class GateState:
    """Client-server gate state for enforcement hooks."""

    # Workflow initialization flags
    workflow_initialized: bool = False
    ready_for_implementation: bool = False

    # Active stage tracking
    active_stage: str | None = None
    active_stage_in_progress: bool = False

    # Diff approval
    pending_diff_id: str | None = None
    diff_approved: bool = False

    # Schema checking (node_path -> True)
    schema_checked: dict[str, bool] = field(default_factory=dict)

    # Verified node types from list_node_types (accumulated, NOT reset per stage)
    verified_node_types: list[str] = field(default_factory=list)

    # Verified params from get_parameter_schema (node_path -> param names)
    # Reset per stage (same lifecycle as schema_checked)
    verified_params: dict[str, list[str]] = field(default_factory=dict)

    # Error tracking for docs auto-reference
    error_counts: dict[str, int] = field(default_factory=dict)
    docs_referenced: bool = False

    # Metadata
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "workflow_initialized": self.workflow_initialized,
            "ready_for_implementation": self.ready_for_implementation,
            "active_stage": self.active_stage,
            "active_stage_in_progress": self.active_stage_in_progress,
            "pending_diff_id": self.pending_diff_id,
            "diff_approved": self.diff_approved,
            "schema_checked": self.schema_checked,
            "verified_node_types": self.verified_node_types,
            "verified_params": self.verified_params,
            "error_counts": self.error_counts,
            "docs_referenced": self.docs_referenced,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateState:
        """Deserialize from dict."""
        return cls(
            workflow_initialized=data.get("workflow_initialized", False),
            ready_for_implementation=data.get("ready_for_implementation", False),
            active_stage=data.get("active_stage"),
            active_stage_in_progress=data.get("active_stage_in_progress", False),
            pending_diff_id=data.get("pending_diff_id"),
            diff_approved=data.get("diff_approved", False),
            schema_checked=data.get("schema_checked", {}),
            verified_node_types=data.get("verified_node_types", []),
            verified_params=data.get("verified_params", {}),
            error_counts=data.get("error_counts", {}),
            docs_referenced=data.get("docs_referenced", False),
            last_updated=data.get("last_updated", ""),
        )

    def save(self, path: Path) -> None:
        """Save gate state to JSON file with atomic write."""
        self.last_updated = datetime.now(timezone.utc).isoformat()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> GateState:
        """Load gate state from JSON file. Returns default if missing."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load gate state from {path}: {e}")
            return cls()

    @classmethod
    def create_initial(cls) -> GateState:
        """Create a fresh gate state."""
        return cls(last_updated=datetime.now(timezone.utc).isoformat())
