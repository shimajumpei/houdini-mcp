"""Data models for workflow management.

Defines stage status enum, state transition rules, and serializable
dataclasses for build state tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class StageStatus(str, Enum):
    """Status of a workflow stage."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VALIDATED = "validated"
    COMPLETED = "completed"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[str, list[str]] = {
    "pending": ["in_progress"],
    "in_progress": ["validated", "failed"],
    "validated": ["completed"],
    "failed": ["in_progress"],
    "completed": [],
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check if a status transition is allowed."""
    allowed = ALLOWED_TRANSITIONS.get(from_status, [])
    return to_status in allowed


@dataclass
class StageState:
    """State of a single workflow stage."""

    status: str = "pending"
    plan_file: str | None = None
    last_validation_passed: bool | None = None
    last_scan_file: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "plan_file": self.plan_file,
            "last_validation_passed": self.last_validation_passed,
            "last_scan_file": self.last_scan_file,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageState:
        return cls(
            status=data.get("status", "pending"),
            plan_file=data.get("plan_file"),
            last_validation_passed=data.get("last_validation_passed"),
            last_scan_file=data.get("last_scan_file"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class ArtifactRecord:
    """Record of a registered artifact."""

    name: str
    stage_id: str
    artifact_type: str = ""
    path: str = ""
    registered_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage_id": self.stage_id,
            "type": self.artifact_type,
            "path": self.path,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRecord:
        return cls(
            name=data.get("name", ""),
            stage_id=data.get("stage_id", ""),
            artifact_type=data.get("type", ""),
            path=data.get("path", ""),
            registered_at=data.get("registered_at", ""),
        )


@dataclass
class BuildState:
    """Overall build state for the project."""

    version: str = "1.0.0"
    project_name: str = ""
    project_root: str = ""
    created_at: str = ""
    active_stage: str | None = None
    stages: dict[str, StageState] = field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    last_scan: str | None = None
    last_updated: str = ""
    spec_hash: str | None = None
    stages_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "version": self.version,
            "project": {
                "name": self.project_name,
                "root": self.project_root,
                "created_at": self.created_at,
            },
            "active_stage": self.active_stage,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "artifacts": {k: v.to_dict() for k, v in self.artifacts.items()},
            "last_scan": self.last_scan,
            "last_updated": self.last_updated,
        }
        if self.spec_hash is not None:
            d["spec_hash"] = self.spec_hash
        if self.stages_hash is not None:
            d["stages_hash"] = self.stages_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildState:
        project = data.get("project", {})
        stages_raw = data.get("stages", {})
        artifacts_raw = data.get("artifacts", {})
        return cls(
            version=data.get("version", "1.0.0"),
            project_name=project.get("name", ""),
            project_root=project.get("root", ""),
            created_at=project.get("created_at", ""),
            active_stage=data.get("active_stage"),
            stages={k: StageState.from_dict(v) for k, v in stages_raw.items()},
            artifacts={k: ArtifactRecord.from_dict(v) for k, v in artifacts_raw.items()},
            last_scan=data.get("last_scan"),
            last_updated=data.get("last_updated", ""),
            spec_hash=data.get("spec_hash"),
            stages_hash=data.get("stages_hash"),
        )

    def save(self, path: Path) -> None:
        """Save build state to JSON file."""
        self.last_updated = _now_iso()
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BuildState:
        """Load build state from JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def create_initial(cls, project_root: str) -> BuildState:
        """Create a new initial build state."""
        now = _now_iso()
        return cls(
            version="1.0.0",
            project_name="",
            project_root=project_root,
            created_at=now,
            active_stage=None,
            stages={},
            artifacts={},
            last_scan=None,
            last_updated=now,
        )


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
