# ax-os-planner — courier branch (transient)

Not part of Axiom. Temporary courier for the AX OS **Claude-driven planner**
(adaptive panel selection). Delete after transfer.

DEPENDS ON the AUI surface (ax-os-aui) being in ax-os first.

UPDATES (overwrite existing ax-os files):
- `aui/plan.py` — adds plan_panels() helper (one source of truth for scene panels)
- `aui/server.py` — /assemble uses the active planner via get_planner()
- `requirements-aui.txt` — adds anthropic>=0.69 (optional; only the claude path needs it)

NEW:
- `aui/planner_claude.py` — Claude picks/orders panels (structured output + prompt
  caching); falls back to the rule-based planner with no SDK/key or on error.
- `tests/test_planner_claude.py` — fallback + structured-output parsing (fully offline)

Enable with AX_OS_PLANNER=claude + ANTHROPIC_API_KEY; default stays rule-based.
Model via AX_OS_PLANNER_MODEL (default claude-opus-4-8). No Axiom source vendored.
