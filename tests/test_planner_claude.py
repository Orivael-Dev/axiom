# -*- coding: utf-8 -*-
"""
Claude planner tests — fallback + structured-output parsing, fully offline.
===========================================================================
No real network: the happy path injects a fake `anthropic` module.
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aui import planner_claude as pc  # noqa: E402
from aui.plan import plan_panels  # noqa: E402


def test_validate_filters_unknown_and_dedups():
    out = pc._validate(["tools", "tools", "bogus", "files"], "g", None)
    assert out == ["tools", "files"]


def test_validate_falls_back_when_empty():
    out = pc._validate(["nonsense", "alsobad"], "work on the repo branch", "dev")
    assert out == plan_panels("work on the repo branch", "dev")


def test_get_planner_toggle(monkeypatch):
    monkeypatch.delenv("AX_OS_PLANNER", raising=False)
    assert pc.get_planner() is None
    monkeypatch.setenv("AX_OS_PLANNER", "claude")
    assert pc.get_planner() is pc.claude_suggest


def test_claude_suggest_falls_back_without_key(monkeypatch):
    # No API key (and likely no SDK) → rule-based result.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    goal = "sort out my quarterly tax invoice"
    assert pc.claude_suggest(goal, "financial") == plan_panels(goal, "financial")


def _fake_anthropic(panels):
    """Build a stand-in `anthropic` module whose client returns `panels`."""
    mod = types.ModuleType("anthropic")

    class _Block:
        type = "text"
        text = json.dumps({"panels": panels})

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            # sanity: structured output + cached system prompt are wired
            assert kwargs["output_config"]["format"]["type"] == "json_schema"
            assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
            return _Resp()

    class Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    mod.Anthropic = Anthropic
    return mod


def test_claude_suggest_happy_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setitem(sys.modules, "anthropic",
                        _fake_anthropic(["session", "tracks", "bogus", "plugins"]))
    out = pc.claude_suggest("open my mixing session", "music")
    assert out == ["session", "tracks", "plugins"]  # validated against the vocab


def test_claude_suggest_handles_api_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    broken = types.ModuleType("anthropic")

    class Anthropic:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")

    broken.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", broken)
    goal = "work on the launch demo branch"
    assert pc.claude_suggest(goal, "dev") == plan_panels(goal, "dev")
