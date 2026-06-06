"""
AUI workspace plan — the adaptive layout model + planner.
=========================================================
Turns an assembled workspace (+ goal/domain) into a ``WorkspacePlan``: an
ordered list of typed panels the front-end draws. This is the "adaptive"
core — *what to show* is decided here, *how to draw it* is the front-end's
job, so the same plan renders in the terminal, Streamlit, or a desktop
shell.

The panel selection is rule-based by default and **Claude-pluggable**: pass
a ``suggest`` callable ``(goal, domain) -> list[str]`` to let a model pick
the scene's panels. No LLM dependency lives in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Panel:
    kind: str                 # intent | safety | context | memory | <scene panel>
    title: str
    items: List[str] = field(default_factory=list)
    status: str = "ready"     # ready | pending | blocked

    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title,
                "items": list(self.items), "status": self.status}


@dataclass
class WorkspacePlan:
    goal: str
    allowed: bool
    scene: str
    panels: List[Panel]
    signature: str = ""

    def to_dict(self) -> dict:
        return {"goal": self.goal, "allowed": self.allowed, "scene": self.scene,
                "panels": [p.to_dict() for p in self.panels],
                "signature": self.signature}


# Scene → the panels that workspace would gather for it. The adaptive map.
DOMAIN_PANELS = {
    "general":   ["files", "tools", "notes"],
    "dev":       ["branch", "tests", "docs", "tools"],
    "financial": ["documents", "reminders", "tools"],
    "music":     ["session", "tracks", "plugins"],
    "medical":   ["documents", "guidelines", "reminders"],
}

# Goal keywords → scene, when no explicit domain is given.
_KEYWORD_SCENE = [
    (("branch", "repo", "code", "pull request", "merge", "test"), "dev"),
    (("mix", "track", "beat", "session", "master", "plugin"), "music"),
    (("tax", "invoice", "budget", "expense", "payroll", "finance"), "financial"),
    (("patient", "clinical", "diagnos", "symptom", "intake"), "medical"),
]


def infer_scene(goal: str, domain: Optional[str]) -> str:
    """Pick a scene: explicit domain wins, else infer from goal keywords."""
    if domain and domain in DOMAIN_PANELS:
        return domain
    g = (goal or "").lower()
    for keywords, scene in _KEYWORD_SCENE:
        if any(k in g for k in keywords):
            return scene
    return "general"


def plan_panels(goal: str, domain: Optional[str]) -> List[str]:
    """Rule-based scene-panel selection — the default and the fallback."""
    return list(DOMAIN_PANELS[infer_scene(goal, domain)])


def _context_panel(context: dict) -> Panel:
    items = [f"domain: {context.get('domain', '')}",
             f"constraints: {', '.join(context.get('active_constraints') or []) or '(none)'}",
             f"resolution: {context.get('resolution', '')}"]
    return Panel(kind="context", title="Recalled context", items=items)


def build_plan(assembled, *, domain: Optional[str] = None,
               suggest: Optional[Callable[[str, Optional[str]], List[str]]] = None
               ) -> WorkspacePlan:
    """Assemble a WorkspacePlan from a workspace assembler result.

    ``assembled`` is a ``workspace.assembler.AssembledWorkspace``.
    ``suggest`` optionally overrides scene-panel selection (e.g. a Claude
    planner); it must return a list of panel kinds.
    """
    scene = infer_scene(assembled.goal, domain)

    intent = Panel(
        kind="intent", title="Goal",
        items=[assembled.goal,
               f"safety: {'ALLOWED' if assembled.allowed else 'REFUSED'} "
               f"({assembled.intent_class}, conf {assembled.confidence:.2f})"],
        status="ready" if assembled.allowed else "blocked",
    )

    # Refused goals get a safety panel and nothing else — no context gathered.
    if not assembled.allowed:
        safety = Panel(kind="safety", title="Refused",
                       items=[f"the intent gate refused this goal ({assembled.intent_class})",
                              f"reason: {assembled.refusal or 'blocked'}"],
                       status="blocked")
        return WorkspacePlan(goal=assembled.goal, allowed=False, scene=scene,
                             panels=[intent, safety], signature=assembled.signature)

    panels: List[Panel] = [intent]
    if assembled.has_context and assembled.context:
        panels.append(_context_panel(assembled.context))
    else:
        panels.append(Panel(kind="memory", title="Local memory",
                            items=["no prior local context for this goal yet"],
                            status="pending"))

    kinds = list(suggest(assembled.goal, domain)) if suggest else plan_panels(assembled.goal, domain)
    for kind in kinds:
        panels.append(Panel(kind=kind, title=kind.capitalize(),
                            items=[], status="pending"))

    return WorkspacePlan(goal=assembled.goal, allowed=True, scene=scene,
                         panels=panels, signature=assembled.signature)
