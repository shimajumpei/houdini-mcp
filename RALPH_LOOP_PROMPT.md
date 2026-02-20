# Ralph Loop Prompt — Houdini MCP Production Hardening

## Completion Signal

When ALL success criteria below are met, output exactly:
<promise>ALL FIXES COMPLETE</promise>

Do NOT output the promise tag until ALL criteria are verified true.

---

## Task

Fix production-critical bugs in the Houdini MCP Server at C:\Users\LAB_005\dev-setup\houdini-mcp-analysis.

## Context

houdini-mcp is an MCP server that controls SideFX Houdini via RPyC. It provides 51 tools for AI-driven Houdini automation. A prior code review identified 5 P0 (critical) bugs and 7 P1 (important) issues across 3 files.

**READ FIRST**: `PRODUCTION_READINESS_ANALYSIS.md` in the project root contains the full analysis with line numbers, root causes, and suggested fixes for every issue.

## Target Files

1. `houdini_mcp/tools/crowd.py` — 8 Crowd Helper Tools (agent setup, DOP, behaviors, transitions, terrain, introspection)
2. `houdini_mcp/tools/rendering.py` — Viewport/quad rendering with RPyC base64 image transfer
3. `houdini_mcp/tools/pane_screenshot.py` — Pane screenshot capture via Qt/PySide2 over RPyC

## Scope: What to Fix (in priority order)

### P0 — Critical (MUST fix, these break core functionality)

1. **Behavior wiring direction is reversed** (`crowd.py` ~L529)
   - Current: `behavior_node.setInput(0, crowd_solver)` — WRONG, wires solver as input TO behavior
   - Fix: `crowd_solver.setInput(next_input, behavior_node)` — wire behavior as input TO solver
   - Impact: All POP Steer behaviors (wander, seek, avoid, etc.) are completely non-functional

2. **DOP soppath points to geo container instead of SOP** (`crowd.py` ~L394)
   - Current: `crowd_obj.parm("soppath").set(geo_parent.path())` — sets to /obj/geo, not the SOP
   - Fix: `crowd_obj.parm("soppath").set(crowd_source_path)` — use the crowdsource SOP path directly
   - Impact: Crowd simulation cannot find its agent source data

3. **No rollback of partially-created nodes on failure** (`crowd.py`, setup_crowd_agent & setup_crowd_dop)
   - `nodes_created` list exists but is never used for cleanup
   - Fix: Wrap node creation in try/except, call `geo.destroy()` on failure before re-raising
   - Impact: RPyC connection drops leave orphan nodes in Houdini scene

4. **`rop` variable unbound in finally block** (`rendering.py` ~L397)
   - Early return on L311 (`/out` not found) skips `rop` assignment, `finally` block gets NameError
   - Fix: Add `rop = None` before the `try` block; move `/out` check before `mkstemp()`
   - Impact: Temp files leak on the remote machine, NameError masks real error

5. **TOCTOU race in _read_remote_file_as_base64** (`rendering.py` ~L357-372)
   - `path.exists()` check then `open()` — file can disappear between the two calls
   - Fix: Wrap the `_read_remote_file_as_base64()` call in try/except, handle FileNotFoundError
   - Impact: Unhandled exception on transient file system issues

### P1 — Important (fix before release)

6. **Transition input index calculation is unreliable** (`crowd.py` ~L665-674)
   - `len(inputs)` doesn't account for None slots in sparse input tuples
   - Fix: Find first None slot at index >= 1, or use highest connected index + 1

7. **object_merge gets SOP path instead of OBJ path** (`crowd.py` ~L307)
   - `objpath1` parameter expects OBJ-level path, but receives a SOP path
   - Fix: Extract OBJ container path from the SOP path, or use crowdsource directly

8. **settings dict passed to parm.set() without validation** (`crowd.py` ~L532-541)
   - No type checking, no key count limit, potential expression injection via parm.set()
   - Fix: Add max key count (e.g., 50), validate value types (int/float/str/list only)

9. **Typo: `agentterraiprojection`** (`crowd.py` ~L812)
   - Missing 'n' — should be `agentterrainprojection`
   - Fix: Simple string correction in the crowd_keywords set

10. **mkstemp fd leak** (`rendering.py` ~L305-306)
    - `remote_os.close(fd)` has no try/except — RPyC failure leaks remote fd
    - Fix: Wrap in try/except, pass on failure (fd leak is minor vs. crash)

11. **No size guard before base64 transfer** (`rendering.py`, `_read_remote_file_as_base64`)
    - 4096x4096 render = ~85MB base64 string with no pre-check
    - Fix: Add `remote_os.path.getsize()` check, reject files > 50MB

12. **render_quad_view always returns status "success"** (`rendering.py` ~L673)
    - Even when all 4 views fail, status is "success"
    - Fix: Set status to "error" when success_count == 0 (match capture_multiple_panes pattern)

### P2 — Recommended (if time permits)

13. **agentterrainadaptation node not wired** (`crowd.py` ~L733-756)
    - Created but has no input connections — add setInput(0, terrain_geo) and setInput(1, crowd_source)

14. **buffer.close() before buffer.data()** (`pane_screenshot.py` ~L257-273)
    - Some Qt versions clear internal data on close() — read data() first, then close()

15. **Scatter grid hardcoded at 50x50** (`crowd.py` ~L284-286)
    - Add optional `grid_size` parameter, default to adaptive sizing based on count

## Constraints

- Preserve the `handle_connection_errors` decorator pattern used by all tools
- Do NOT change the `ensure_connected(host, port)` function signature
- Minimize changes to `server.py` MCP tool registration (only if new params are added)
- Run `pytest tests/ -x -q` after each phase — all 385 existing tests must pass
- Report progress in Japanese (日本語で進捗報告)
- When stuck on Houdini-specific behavior: use the `deep-research` skill to investigate via Gemini Deep Research API, or consult the Houdini crowd knowledge base at `C:\Users\LAB_005\js\motion_human_crawd\docs\houdini_crowd_knowledge\`

## Reference Materials

- `C:\Users\LAB_005\js\motion_human_crawd\docs\houdini_crowd_knowledge\` — 27-file Houdini Crowd knowledge base
  - `05_dop_simulation.md` — Correct DOP wiring topology (Crowd Object → Crowd Solver → States/Behaviors)
  - `06_steering_behaviors.md` — POP Steer node connection patterns and Weight normalization
  - `07_triggers_transitions.md` — Trigger/Transition DOP wiring and Clip Transition Graph
  - `16_node_reference.md` — Complete node reference by context (SOP/DOP/CHOP/LOP)
- `houdini_mcp/tools/_common.py` — Shared utilities (handle_connection_errors, validate_resolution, etc.)
- `houdini_mcp/tools/nodes.py` — Existing node operations (pattern reference for error handling)

## Success Criteria

1. All 5 P0 bugs fixed and verified
2. All 7 P1 issues fixed and verified
3. At least 3 P2 items fixed
4. pytest: 385+ tests passing (0 failures)
5. Each fix includes a clear code comment explaining WHY the change was needed
6. No regressions in existing tool behavior
```

---

## How to Use

Run this command in Claude Code (from the project root):

```
/ralph-loop Read RALPH_LOOP_PROMPT.md and execute all fixes. Use deep-research skill when stuck. Report in Japanese. --completion-promise "ALL FIXES COMPLETE" --max-iterations 50
```

## Expected Duration

- Phase 1 (P0): ~15 min
- Phase 2 (P1): ~20 min
- Phase 3 (P2): ~10 min
- Test runs: ~5 min each
- **Total: ~50-60 min** (3-4 loop iterations)
