# -*- coding: utf-8 -*-
"""
WorkspacePlan / planner tests — pure, deterministic.
====================================================
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workspace.assembler import AssembledWorkspace  # noqa: E402
from aui.plan import build_plan, infer_scene, DOMAIN_PANELS  # noqa: E402


def _ws(goal="do a thing", allowed=True, has_context=False, context=None):
    return AssembledWorkspace(
        goal=goal, allowed=allowed, intent_class="INFORM" if allowed else "HARM",
        confidence=0.55, refusal=None if allowed else "intent_gate: harm",
        has_context=has_context, context=context, signature="sig0123456789abcdef")


def test_infer_scene_from_domain_and_keywords():
    assert infer_scene("anything", "music") == "music"
    assert infer_scene("work on the repo branch and run tests", None) == "dev"
    assert infer_scene("sort out my tax invoice", None) == "financial"
    assert infer_scene("mix the new beat session", None) == "music"
    assert infer_scene("just chatting", None) == "general"


def test_allowed_plan_has_intent_context_and_scene_panels():
    ws = _ws(goal="open my mixing session", has_context=True,
             context={"domain": "music", "active_constraints": ["local_first"],
                      "resolution": "loaded", "packet_signature": "abc"})
    plan = build_plan(ws, domain="music")
    kinds = [p.kind for p in plan.panels]
    assert kinds[0] == "intent"
    assert "context" in kinds
    assert kinds[-len(DOMAIN_PANELS["music"]):] == DOMAIN_PANELS["music"]
    assert plan.allowed and plan.scene == "music"
    assert plan.signature == "sig0123456789abcdef"


def test_no_context_uses_memory_panel():
    plan = build_plan(_ws(has_context=False), domain="general")
    assert any(p.kind == "memory" and p.status == "pending" for p in plan.panels)


def test_refused_plan_stops_at_safety():
    plan = build_plan(_ws(allowed=False))
    kinds = [p.kind for p in plan.panels]
    assert kinds == ["intent", "safety"]
    assert plan.allowed is False
    assert plan.panels[1].status == "blocked"


def test_suggest_hook_overrides_scene_panels():
    called = {}

    def suggest(goal, domain):
        called["args"] = (goal, domain)
        return ["custom_a", "custom_b"]

    plan = build_plan(_ws(goal="x"), domain="general", suggest=suggest)
    kinds = [p.kind for p in plan.panels]
    assert "custom_a" in kinds and "custom_b" in kinds
    assert called["args"] == ("x", "general")
