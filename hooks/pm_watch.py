# coding: utf-8
"""PM Watch — lightweight hook for project monitoring.

Reads .agent/ state files (events.jsonl, build_state, gate_state, stages)
and writes PM_STATUS.md / PM_ALERTS.md to .agent/reports/.

Designed to run as a Claude Code command hook on Stop, TaskCompleted,
and PostToolUseFailure events.  Never calls any tools — record & suggest only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML
except Exception:
    yaml = None


DEFAULT_TAIL_EVENTS = 50
MAX_PARENT_SEARCH = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _safe_write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except Exception:
        return


def _safe_append_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        return


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(_safe_read_text(path) or "{}")
    except Exception:
        return {}


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        return {}
    try:
        return yaml.safe_load(_safe_read_text(path)) or {}
    except Exception:
        return {}


def _find_agent_dir(start_dir: Path) -> Optional[Path]:
    """Walk up from *start_dir* looking for .agent/ directory."""
    cur = start_dir
    for _ in range(MAX_PARENT_SEARCH):
        agent = cur / ".agent"
        if agent.exists() and agent.is_dir():
            return agent
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _tail_jsonl(path: Path, n: int) -> List[Dict[str, Any]]:
    """Read last *n* JSONL lines.  Fail-soft."""
    if not path.exists():
        return []
    try:
        data = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        lines = data[-n:] if len(data) > n else data
        out: List[Dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# State extraction
# ---------------------------------------------------------------------------

def _stage_name_map(stages_yaml: Dict[str, Any]) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for s in stages_yaml.get("stages", []) or []:
        sid = str(s.get("id", "")).strip()
        name = str(s.get("name", "")).strip()
        if sid:
            m[sid] = name or sid
    return m


def _extract_current_state(
    build_state: Dict[str, Any],
    stage_names: Dict[str, str],
) -> Dict[str, Any]:
    active = build_state.get("active_stage")
    stages = build_state.get("stages", {})
    active_status = ""
    if isinstance(active, str) and active:
        st = stages.get(active, {}) if isinstance(stages, dict) else {}
        active_status = str(st.get("status", "")).strip()
    active_name = stage_names.get(active, active) if isinstance(active, str) else ""
    return {
        "active_stage_id": active if isinstance(active, str) else "",
        "active_stage_name": active_name,
        "active_stage_status": active_status,
    }


def _last_event_ts(events: List[Dict[str, Any]], event_type: str) -> str:
    for e in reversed(events):
        if e.get("event_type") == event_type:
            return str(e.get("ts", "")).strip()
    return ""


# ---------------------------------------------------------------------------
# Detections (A–D)
# ---------------------------------------------------------------------------

def _recent_failure_streak(events: List[Dict[str, Any]]) -> Tuple[str, int]:
    """Detect consecutive failure streak at tail of events.

    Returns ``(tool_name, count)``.  ``("", 0)`` when none.
    """
    streak_tool = ""
    streak = 0
    for e in reversed(events):
        et = str(e.get("event_type", "")).strip()
        details = e.get("details", {}) if isinstance(e.get("details"), dict) else {}
        tool = str(details.get("tool_name", "") or details.get("tool") or "").strip()

        is_fail = et in ("post_tool_use_failure", "tool_failed", "houdini_tool_failed")
        if not is_fail:
            break

        if not tool:
            tool = "unknown_tool"

        if streak_tool == "":
            streak_tool = tool
            streak = 1
        elif tool == streak_tool:
            streak += 1
        else:
            break
    return streak_tool, streak


def _detect_external_changes(events: List[Dict[str, Any]]) -> List[str]:
    found: List[str] = []
    for e in reversed(events):
        et = str(e.get("event_type", "")).strip()
        if et in ("spec_updated_external", "stages_updated_external"):
            found.append(et)
    # unique, keep order
    out: List[str] = []
    for x in found:
        if x not in out:
            out.append(x)
    return out


def _detect_stagnation(
    state: Dict[str, Any],
    events: List[Dict[str, Any]],
    lookback: int = DEFAULT_TAIL_EVENTS,
) -> bool:
    """True if active stage is in_progress but no scan/validate in recent events."""
    sid = state.get("active_stage_id", "")
    st = state.get("active_stage_status", "")
    if not sid or st != "in_progress":
        return False

    recent = events[-lookback:] if len(events) > lookback else events
    has_scan = any(e.get("event_type") == "scene_scanned" for e in recent)
    has_validate = any(e.get("event_type") == "stage_validated" for e in recent)
    return (not has_scan) or (not has_validate)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_pm_status(
    *,
    updated: str,
    state: Dict[str, Any],
    last_scan: str,
    last_validation: str,
    diff_status: str,
    diff_id: str,
    next_actions: List[str],
    alerts_active: List[str],
) -> str:
    stage_line = "- **Active Stage:** none"
    if state.get("active_stage_id"):
        stage_line = "- **Active Stage:** {0} ({1}) — {2}".format(
            state["active_stage_id"],
            state.get("active_stage_name") or state["active_stage_id"],
            state.get("active_stage_status") or "unknown",
        )

    lines: List[str] = [
        "# PM Status",
        "",
        "**Updated:** {0}".format(updated),
        "",
        "## Current State",
        stage_line,
        "- **Last Scan:** {0}".format(last_scan or "none"),
        "- **Last Validation:** {0}".format(last_validation or "none"),
        "- **Diff Status:** {0}{1}".format(
            diff_status or "unknown",
            " (diff_id: {0})".format(diff_id) if diff_id else "",
        ),
        "",
        "## Next Actions",
    ]
    if next_actions:
        for i, a in enumerate(next_actions[:3], 1):
            lines.append("{0}. {1}".format(i, a))
    else:
        lines.append("1. (none)")
    lines.append("")
    lines.append("## Alerts (active)")
    if alerts_active:
        for a in alerts_active[:10]:
            lines.append("- {0}".format(a))
    else:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


def _append_alert(path: Path, title: str, body_lines: List[str]) -> None:
    ts = _utc_now_iso()
    block = ["", "## {0} — {1}".format(ts, title)]
    for ln in body_lines:
        block.append("- {0}".format(ln))
    block.append("---")
    block.append("")
    _safe_append_text(path, "\n".join(block))


def _summarize_stdout(
    state: Dict[str, Any],
    next_actions: List[str],
    alerts_active: List[str],
) -> str:
    """1–3 line summary for TaskCompleted stdout injection."""
    stage = "none"
    if state.get("active_stage_id"):
        stage = "{0}:{1}".format(
            state["active_stage_id"],
            state.get("active_stage_status") or "unknown",
        )
    line1 = "PM: stage={0}".format(stage)
    line2 = "Next: {0}".format(next_actions[0] if next_actions else "none")
    out = [line1, line2]
    if alerts_active:
        out.append("Alert: {0}".format(alerts_active[0].replace("\u26a0 ", "")))
    return "\n".join(out[:3])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Read stdin payload (fail-soft)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    try:
        hook_event = str(
            payload.get("hook_event_name", "")
            or payload.get("event", "")
            or ""
        ).strip()
        cwd = str(payload.get("cwd", "") or os.getcwd()).strip()
    except Exception:
        hook_event = ""
        cwd = os.getcwd()

    agent_dir = _find_agent_dir(Path(cwd).resolve())
    if agent_dir is None:
        return 0  # no .agent/ → silent exit

    # Paths
    events_path = agent_dir / "history" / "events.jsonl"
    build_state_path = agent_dir / "build_state.json"
    gate_state_path = agent_dir / "gate_state.json"
    stages_path = agent_dir / "stages.yaml"
    reports_dir = agent_dir / "reports"
    status_path = reports_dir / "PM_STATUS.md"
    alerts_path = reports_dir / "PM_ALERTS.md"

    # Load state (all fail-soft)
    events = _tail_jsonl(events_path, DEFAULT_TAIL_EVENTS)
    build_state = _load_json(build_state_path)
    gate_state = _load_json(gate_state_path)
    stages_yaml = _load_yaml(stages_path)
    stage_names = _stage_name_map(stages_yaml)
    state = _extract_current_state(build_state, stage_names)

    # Timestamps
    last_scan = _last_event_ts(events, "scene_scanned")
    last_validation = _last_event_ts(events, "stage_validated")

    # Diff status (best effort from gate_state)
    diff_id = ""
    diff_status = "unknown"
    if isinstance(gate_state, dict):
        diff_id = str(
            gate_state.get("approved_diff_id", "")
            or gate_state.get("diff_id", "")
            or ""
        ).strip()
        diff_status = "approved" if diff_id else "none"

    # -----------------------------------------------------------------------
    # Detections → next_actions + alerts_active
    # -----------------------------------------------------------------------
    next_actions: List[str] = []
    alerts_active: List[str] = []

    # A: stagnation
    if _detect_stagnation(state, events):
        sid = state.get("active_stage_id")
        next_actions.append(
            '`record_scan(scan_data, stage_id="{0}")` — scan が未記録または古い'.format(sid)
        )
        next_actions.append(
            '`validate_stage("{0}")` — scan 後に検証'.format(sid)
        )

    # B: external changes
    ext = _detect_external_changes(events)
    for x in ext:
        if x == "spec_updated_external":
            alerts_active.append(
                "\u26a0 spec.md が外部変更されています → `ensure_project()` で再確認"
            )
        if x == "stages_updated_external":
            alerts_active.append(
                "\u26a0 stages.yaml が外部変更されています → `ensure_project()` で再確認"
            )

    # B: failure streak
    tool, streak = _recent_failure_streak(events)
    if streak >= 3:
        alerts_active.append(
            "\u26a0 {0} が {1} 回連続失敗 → docs参照/手順見直し".format(tool, streak)
        )

    # Default guidance when no specific next_actions
    if not next_actions:
        if (
            state.get("active_stage_id")
            and state.get("active_stage_status") == "in_progress"
        ):
            sid = state["active_stage_id"]
            next_actions.append(
                '`record_scan(scan_data, stage_id="{0}")` → `validate_stage("{0}")`'.format(sid)
            )
        else:
            next_actions.append(
                '`ensure_project()` → `read_project(target="all")`（未初期化なら）'
            )

    # Optional: NotebookLM sanity check prompt (non-blocking suggestion)
    _NBML_EVENT_TYPES = {
        "norm_check_recorded", "decision_recorded",
        "spec_updated", "stages_updated",
        "spec_updated_external", "stages_updated_external",
    }
    show_optional_nbml = bool(ext) or streak >= 2 or _detect_stagnation(state, events)
    if not show_optional_nbml:
        show_optional_nbml = any(
            e.get("event_type") in _NBML_EVENT_TYPES for e in events
        )
    if show_optional_nbml:
        next_actions.append(
            'Optional: NotebookLM sanity check — '
            '`record_norm_check(kind="notebooklm", summary="...", impact="adopt|skipped")`'
        )

    # -----------------------------------------------------------------------
    # Write PM_STATUS.md (overwrite)
    # -----------------------------------------------------------------------
    status_text = _render_pm_status(
        updated=_utc_now_iso(),
        state=state,
        last_scan=last_scan,
        last_validation=last_validation,
        diff_status=diff_status,
        diff_id=diff_id,
        next_actions=next_actions,
        alerts_active=alerts_active,
    )
    _safe_write_text(status_path, status_text)

    # -----------------------------------------------------------------------
    # Write PM_ALERTS.md (append)
    # -----------------------------------------------------------------------
    if hook_event == "PostToolUseFailure":
        tool_name = str(payload.get("tool_name", "") or "unknown_tool").strip()
        err = str(payload.get("error", "") or "").strip()
        _append_alert(
            alerts_path,
            "PostToolUseFailure",
            [
                "Tool: {0}".format(tool_name),
                "Action: まず `get_parameter_schema()` / `get_node_info()` / `query_houdini_docs()` を検討",
                "Error: {0}".format(err[:200] if err else "(none)"),
            ],
        )
    else:
        for x in ext:
            if x == "spec_updated_external":
                _append_alert(
                    alerts_path,
                    "External Spec Change",
                    ["File: spec.md", "Action: `ensure_project()` で readiness 再確認"],
                )
            if x == "stages_updated_external":
                _append_alert(
                    alerts_path,
                    "External Stages Change",
                    ["File: stages.yaml", "Action: `ensure_project()` で readiness 再確認"],
                )
        if streak >= 3:
            _append_alert(
                alerts_path,
                "Tool Failure Streak",
                [
                    "Tool: {0}".format(tool),
                    "Count: {0} consecutive failures".format(streak),
                    'Action: docs参照 → `query_houdini_docs("...")` / `get_parameter_schema()`',
                ],
            )

    # -----------------------------------------------------------------------
    # stdout summary (TaskCompleted only — sync hook)
    # -----------------------------------------------------------------------
    if hook_event == "TaskCompleted":
        sys.stdout.write(_summarize_stdout(state, next_actions, alerts_active))
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # absolute fail-soft
        raise SystemExit(0)
