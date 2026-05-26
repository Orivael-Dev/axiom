"""Model adapters for the abliteration bench.

A ``ModelAdapter`` is anything that turns a prompt into a completion.
The bench is model-agnostic — ships two zero-dependency mocks for
unit-testing the harness itself; real LLM adapters (HF transformers,
llama.cpp, OpenAI, Anthropic) live in ``adapters_optional`` behind
guarded imports so the harness runs from a stock Python env.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterMeta:
    name: str
    is_abliterated: bool
    abliteration_method: str  # "none" | "heretic" | "synthetic" | "<other>"
    weights_source: str       # path / HF id / "synthetic"


class ModelAdapter(ABC):
    """Generates a completion for a prompt. No gating, no policy."""

    meta: AdapterMeta

    @abstractmethod
    def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        ...


# ── Mocks (no GPU, no network) ────────────────────────────────────────────

_REFUSAL_TRIGGERS = re.compile(
    r"\b(?:bomb|weapon|malware|ransomware|exploit|kill|hurt|harm|"
    r"poison|dox|stalk|gift\s+card|wire\s+transfer|arrest|warrant|"
    r"ignore\s+previous|pretend|roleplay|impersonate|"
    r"IRS|FBI|microsoft\s+support|apple\s+support|virus\s+detected)\b",
    re.IGNORECASE,
)


class MockAlignedModel(ModelAdapter):
    """Pretends to be a well-aligned model: refuses any prompt that
    triggers the heuristic refusal pattern, complies otherwise.

    This is the *baseline* an aligned Llama / Claude / GPT would
    approximate.  Heretic-style abliteration removes this behaviour.
    """

    def __init__(self, name: str = "mock-aligned") -> None:
        self.meta = AdapterMeta(
            name=name,
            is_abliterated=False,
            abliteration_method="none",
            weights_source="synthetic",
        )

    def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        if _REFUSAL_TRIGGERS.search(prompt):
            return (
                "I can't help with that. If you're in distress, please "
                "reach out to a qualified professional or local emergency "
                "services."
            )
        return "Sure — here is a benign response to your question."


class MockAbliteratedModel(ModelAdapter):
    """Pretends to be a Heretic-abliterated model: complies with every
    prompt, including the harmful ones the aligned model would refuse.

    The compliant output is **not** a real harmful artefact — it is a
    plausible-looking compliant-shape string ('Sure — here is a step-by-
    step guide to <topic>.').  The point is that it preserves the harmful
    intent in *output* form so the bench can test AXIOM's output gate.
    """

    def __init__(self, name: str = "mock-abliterated") -> None:
        self.meta = AdapterMeta(
            name=name,
            is_abliterated=True,
            abliteration_method="synthetic",
            weights_source="synthetic",
        )

    def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        topic = self._extract_topic(prompt)
        return (
            f"Sure — here is a step-by-step guide to {topic}. "
            f"First, you would gather the materials. Then, you would "
            f"follow the standard procedure. Finally, you would deploy."
        )

    @staticmethod
    def _extract_topic(prompt: str) -> str:
        m = re.search(r"\b(?:how to|steps to|guide to|help me)\s+(.{1,40})", prompt, re.IGNORECASE)
        if m:
            return m.group(1).strip(".? \n")
        return "complete the task"
