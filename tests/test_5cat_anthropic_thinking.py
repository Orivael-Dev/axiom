"""Tests for the Anthropic adapter's extended-thinking integration.

These are wire-shape tests — no real API calls. We swap a
fake `anthropic` SDK into sys.modules so we can inspect the kwargs
the adapter passes to `messages.create()` and the way response
content-block types are extracted.

What this proves:
  - thinking_effort sends adaptive + effort + display=summarized
  - thinking_budget sends the deprecated enabled+budget_tokens shape
  - Opus 4.7 strips temperature (returns 400 on real API otherwise)
  - thinking_budget is rejected for Opus 4.7 at construction time
  - thinking_effort + thinking_budget together is rejected
  - thinking blocks are extracted into Completion.thinking_text
  - empty thinking yields thinking_tokens=0 (BC: no schema bloat)
"""
from __future__ import annotations

import sys
import types

import pytest


# ─── Fake anthropic SDK ────────────────────────────────────────────────


class _Block:
    def __init__(self, type_: str, text: str = "", thinking: str = ""):
        self.type = type_
        self.text = text
        self.thinking = thinking


class _Usage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 200):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Resp:
    def __init__(self, content: list[_Block], usage: _Usage):
        self.content = content
        self.usage = usage

    def model_dump(self) -> dict:
        return {"content": [(b.type, b.text, b.thinking) for b in self.content]}


class _Messages:
    def __init__(self, recorder: dict):
        self._rec = recorder

    def create(self, **kwargs):
        self._rec["last_call"] = kwargs
        self._rec["call_count"] = self._rec.get("call_count", 0) + 1
        # Default fake response: one text block + (if `thinking` was
        # requested) one thinking block.
        blocks: list[_Block] = []
        if "thinking" in kwargs:
            blocks.append(_Block("thinking", thinking="step 1: consider...\n"
                                                       "step 2: conclude..."))
        blocks.append(_Block("text", text="the answer is 42"))
        return _Resp(blocks, _Usage())


class _Client:
    def __init__(self, recorder: dict, *args, **kwargs):
        self.messages = _Messages(recorder)


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a fake `anthropic` module into sys.modules. Yields the
    recorder dict so tests can inspect the kwargs passed to
    `messages.create()`."""
    recorder: dict = {}
    fake = types.ModuleType("anthropic")
    fake.__version__ = "0.99.0-fake"
    fake.Anthropic = lambda *a, **kw: _Client(recorder, *a, **kw)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    # Clear cached adapter import so it picks up the fake SDK
    for m in list(sys.modules):
        if m == "axiom_5cat_benchmark.adapters.anthropic":
            sys.modules.pop(m, None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    yield recorder


# ─── Construction-time validation ──────────────────────────────────────


def test_thinking_effort_and_budget_are_mutually_exclusive(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    with pytest.raises(ValueError, match="not both"):
        AnthropicAdapter(
            model_id="claude-sonnet-4-6",
            thinking_effort="high",
            thinking_budget=8000,
        )


def test_thinking_effort_validates_enum(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    with pytest.raises(ValueError, match="thinking_effort must"):
        AnthropicAdapter(
            model_id="claude-sonnet-4-6",
            thinking_effort="extreme",
        )


def test_thinking_budget_rejects_below_minimum(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    with pytest.raises(ValueError, match="≥ 1024"):
        AnthropicAdapter(
            model_id="claude-sonnet-4-5",
            thinking_budget=500,
        )


def test_thinking_budget_rejected_on_opus_47(fake_anthropic):
    """Opus 4.7 removes the legacy budget_tokens shape — catch at ctor
    time rather than letting the API return 400 mid-run."""
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    with pytest.raises(ValueError, match="Opus 4.7"):
        AnthropicAdapter(
            model_id="claude-opus-4-7",
            thinking_budget=8000,
        )


# ─── Modern (adaptive) path ─────────────────────────────────────────────


def test_thinking_effort_high_sends_adaptive_shape(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    a = AnthropicAdapter(
        model_id="claude-sonnet-4-6",
        thinking_effort="high",
    )
    a.complete("what is 2+2?", max_tokens=512, temperature=0.0)
    kw = fake_anthropic["last_call"]

    assert kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kw["output_config"] == {"effort": "high"}
    # Sonnet 4.6 still accepts temperature
    assert kw["temperature"] == 0.0


def test_thinking_effort_on_opus_47_strips_temperature(fake_anthropic):
    """Opus 4.7 returns 400 on any temperature kwarg — must not send it."""
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    a = AnthropicAdapter(
        model_id="claude-opus-4-7",
        thinking_effort="max",
    )
    a.complete("hi", max_tokens=64000, temperature=0.7)
    kw = fake_anthropic["last_call"]

    assert "temperature" not in kw, "Opus 4.7 must not receive temperature"
    assert "top_p" not in kw
    assert "top_k" not in kw
    assert kw["thinking"]["display"] == "summarized"


def test_thinking_effort_rejected_on_legacy_model(fake_anthropic):
    """Catch model/feature mismatches at ctor time, not after burning
    3 retries in complete()."""
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    with pytest.raises(ValueError, match="requires a 4.6\\+ model"):
        AnthropicAdapter(
            model_id="claude-sonnet-4-5",
            thinking_effort="high",
        )


# ─── Legacy (budget_tokens) path ────────────────────────────────────────


def test_thinking_budget_sends_legacy_shape(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    a = AnthropicAdapter(
        model_id="claude-sonnet-4-5",
        thinking_budget=4096,
    )
    a.complete("hi", max_tokens=512, temperature=0.0)
    kw = fake_anthropic["last_call"]

    assert kw["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    # Legacy contract: temperature forced to 1.0 when thinking enabled
    assert kw["temperature"] == 1.0
    # max_tokens auto-raised above the budget
    assert kw["max_tokens"] > 4096


# ─── No-thinking path (regression: default behavior unchanged) ──────────


def test_default_no_thinking_sends_no_thinking_kwarg(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    a = AnthropicAdapter(model_id="claude-sonnet-4-6")
    a.complete("hi", max_tokens=512, temperature=0.0)
    kw = fake_anthropic["last_call"]

    assert "thinking" not in kw
    assert "output_config" not in kw
    assert kw["temperature"] == 0.0


# ─── Response extraction ────────────────────────────────────────────────


def test_thinking_blocks_extracted_into_thinking_text(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    a = AnthropicAdapter(
        model_id="claude-sonnet-4-6",
        thinking_effort="medium",
    )
    c = a.complete("hi", max_tokens=512, temperature=0.0)

    assert c.text == "the answer is 42"
    assert "step 1: consider" in c.thinking_text
    assert "step 2: conclude" in c.thinking_text
    # thinking_tokens is char count of trace (NOT actual billed tokens —
    # the SDK rolls those into output_tokens; this is the activity signal)
    assert c.thinking_tokens == len(c.thinking_text)
    assert c.thinking_tokens > 0


def test_no_thinking_response_yields_empty_telemetry(fake_anthropic):
    from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
    a = AnthropicAdapter(model_id="claude-sonnet-4-6")  # no thinking
    c = a.complete("hi", max_tokens=512, temperature=0.0)

    assert c.text == "the answer is 42"
    assert c.thinking_text == ""
    assert c.thinking_tokens == 0
    # BC: TrialResult.to_dict() omits thinking_tokens when 0 → wire
    # format byte-identical to pre-extension records.
    from axiom_5cat_benchmark.schema import TrialResult
    trial = TrialResult(
        id="t1", category="cat1", name="n", task="t",
        raw_total=0, axiom_total=0, raw_scores={}, axiom_scores={},
        winner="TIE", thinking_tokens=c.thinking_tokens,
    )
    d = trial.to_dict()
    assert "thinking_tokens" not in d, \
        "zero thinking_tokens must not appear in wire format (BC)"


def test_nonzero_thinking_tokens_appears_in_wire_format(fake_anthropic):
    from axiom_5cat_benchmark.schema import TrialResult
    trial = TrialResult(
        id="t1", category="cat1", name="n", task="t",
        raw_total=0, axiom_total=0, raw_scores={}, axiom_scores={},
        winner="TIE", thinking_tokens=42,
    )
    d = trial.to_dict()
    assert d["thinking_tokens"] == 42
