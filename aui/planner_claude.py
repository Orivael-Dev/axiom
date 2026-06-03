"""
Claude-driven workspace planner.
================================
The adaptive-layout brain: given a goal (+ optional domain), ask Claude to
pick and order the workspace panels from a fixed vocabulary. Plugs into
``aui.plan.build_plan(..., suggest=claude_suggest)`` — same signature as
the rule-based default.

Design choices (per the Claude API skill):
- Model defaults to ``claude-opus-4-8`` (override via AX_OS_PLANNER_MODEL).
- **Structured output** via ``output_config.format`` (json_schema) so the
  result is always a clean ``{"panels": [...]}`` over the allowed vocab.
- **Prompt caching** on the stable system prompt; the volatile goal/domain
  go in the user turn after the cache breakpoint.
- **Graceful fallback** to the rule-based planner when the SDK or an API
  key is absent, or any error occurs — AX OS stays local-first and the
  AUI never hard-depends on a network call.

Panel selection is a fast, classification-shaped task, so thinking is
disabled and effort is low for snappy UI latency.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from aui.plan import DOMAIN_PANELS, plan_panels

# Fixed panel vocabulary — union of the rule-based scene panels, kept in sync.
PANEL_VOCAB: List[str] = sorted({p for panels in DOMAIN_PANELS.values() for p in panels})

MODEL = os.environ.get("AX_OS_PLANNER_MODEL", "claude-opus-4-8")

SYSTEM = (
    "You lay out the AX OS adaptive workspace. AX OS reshapes its interface "
    "around what the user is trying to do. Given a goal, choose which "
    "workspace panels to surface and in what order — most relevant first.\n\n"
    f"Allowed panel kinds (use only these): {', '.join(PANEL_VOCAB)}.\n\n"
    "Pick 3 to 6 panels that best fit the goal. Order them by relevance. "
    "Do not invent panel kinds outside the allowed list."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "panels": {"type": "array", "items": {"type": "string", "enum": PANEL_VOCAB}},
    },
    "required": ["panels"],
    "additionalProperties": False,
}


def _validate(panels, goal: str, domain: Optional[str]) -> List[str]:
    """Keep only allowed kinds (dedup, order-preserving); fall back if empty."""
    seen, cleaned = set(), []
    for p in panels or []:
        if p in PANEL_VOCAB and p not in seen:
            seen.add(p)
            cleaned.append(p)
    return cleaned or plan_panels(goal, domain)


def _call_claude(goal: str, domain: Optional[str]) -> List[str]:
    import anthropic  # imported lazily so the module loads without the SDK

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=256,
        thinking={"type": "disabled"},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user",
                   "content": f"Goal: {goal}\nDomain: {domain or '(infer from the goal)'}"}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text).get("panels", [])


def claude_suggest(goal: str, domain: Optional[str] = None) -> List[str]:
    """Suggest hook for build_plan. Uses Claude when available; else rules."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return plan_panels(goal, domain)
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return plan_panels(goal, domain)
    try:
        panels = _call_claude(goal, domain)
    except Exception:
        return plan_panels(goal, domain)
    return _validate(panels, goal, domain)


def get_planner():
    """Active suggest hook: Claude when AX_OS_PLANNER=claude, else None (rules)."""
    if os.environ.get("AX_OS_PLANNER", "").lower() == "claude":
        return claude_suggest
    return None
