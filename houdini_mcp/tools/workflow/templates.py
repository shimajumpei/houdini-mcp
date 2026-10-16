"""Template strings for .agent/ bootstrap files.

These templates are used when auto-bootstrapping a new project.
Existing files are never overwritten.
"""

SPEC_MD_TEMPLATE = """\
# Project Specification

## Goal

(Describe the goal of this project)

## Requirements

(Describe the requirements)

## Inputs

(Describe the inputs)

## Outputs

(Describe the expected outputs)

## Acceptance Criteria

(Describe the acceptance criteria)

## Definition of Done

(Define what "done" means for this project)

## Assumptions

(List assumptions that may change)

## Open Questions

(List unresolved questions)

## Change Log

| Date | Change | Reason | Impact |
|------|--------|--------|--------|
"""

STAGES_YAML_TEMPLATE = """\
# Workflow Stages
# Define each stage with: id, name, purpose, depends_on, produces, consumes
stages: []
"""

CONTRACTS_YAML_TEMPLATE = """\
# Artifact Contracts
# Define expected artifacts and their validation rules
artifacts: {}
"""

# Known event types for events.jsonl.
# Custom event types are allowed — this list is for documentation and validation hints.
EVENT_TYPES = [
    "project_bootstrap",
    "spec_updated",
    "stages_updated",
    "stage_planned",
    "diff_proposed",
    "diff_approved",
    "houdini_change_applied",
    "scene_scanned",
    "stage_validated",
    "stage_status_changed",
    "decision_recorded",
]
