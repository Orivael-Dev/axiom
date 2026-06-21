"""axiom_output_shaper.py — post-generation output normalisation.

Sits at the Layer 4/5 boundary (Governance Guard → Adversarial Lab):
strips CoT preamble, politeness boilerplate, and shapes structured
outputs for classification intents before the result reaches the caller.

Three transforms, applied in order:

  1. cot_preamble     — removes chain-of-thought reasoning block that
                        ends in "... the answer/classification/verdict is:"
  2. politeness       — strips opener filler ("Of course!", "Certainly!")
                        and closer filler ("I hope this helps!", etc.)
  3. intent_shape     — for CLASSIFY / INFORM post-CoT: emits a compact
                        "Category: X. Evidence: Y." prefix when the model
                        output is a bare short label

Generation-time hint (separate from post-processing):
  output_format_hint(intent_class) → str
    Returns a short system-prompt suffix that tells the model to skip
    its CoT block and reply in the target structured format.  Injecting
    this upstream reduces the model's output token count rather than
    just stripping it after the fact.
"""
from __future__ import annotations

import re
import sys
import types as _types
from dataclasses import dataclass
from typing import List


# ── CANNOT_MUTATE module freeze ───────────────────────────────────────────────

def _module_setattr(self: object, name: str, value: object) -> None:
    raise AttributeError(f"CANNOT_MUTATE: {name} is immutable in axiom_output_shaper")

_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})

OUTPUT_SHAPER_VERSION: str = "1.0"  # CANNOT_MUTATE

# Intent classes that benefit from output shaping
_SHAPE_INTENTS: frozenset = frozenset({"INFORM", "CLARIFY", "CLASSIFY"})


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShapedOutput:
    """Result of one OutputShaper.shape() call."""
    text:         str
    tokens_saved: int    # chars_removed // 4
    transforms:   tuple  # tuple[str, ...] — names of applied transforms


# ── Compiled patterns ─────────────────────────────────────────────────────────

# CoT preamble: ≥60 char reasoning block ending in "... is: ANSWER"
# Matches patterns like:
#   "Analyzing the ticket: <long text>... the classification is: "
#   "After reviewing the context, the correct answer is: "
_RE_COT_PREAMBLE = re.compile(
    r"^.{60,}\b(?:classification|answer|verdict|response|category|result)\s+"
    r"(?:among\s+\{[^}]{1,200}\}\s+)?is:\s+",
    re.IGNORECASE | re.DOTALL,
)

# Standalone "Analyzing…" opener not ending in "is:" (strip whole sentence)
_RE_ANALYZING_OPENER = re.compile(
    r"^Analyzing\s+.{10,}?\.\s+",
    re.IGNORECASE | re.DOTALL,
)

# Politeness openers — must match at the very start of the text
_RE_OPENER = re.compile(
    r"^(?:"
    r"Of course!?|Certainly!?|Sure(?:\s+thing)?!?|Absolutely!?|"
    r"Great question!?|No problem!?|"
    r"I(?:'d| would) be (?:happy|glad) to (?:help|assist)[^.!]*[.!]|"
    r"I(?:'m| am) (?:happy|glad) to (?:help|assist)[^.!]*[.!]|"
    r"Let me help you(?: with that)?[^.!]*[.!]|"
    r"Thank you for (?:reaching out|your (?:question|message|inquiry|email))[^.!]*[.!]|"
    r"Thanks for (?:reaching out|your (?:question|message|inquiry))[^.!]*[.!]|"
    r"I(?:'d| would) be happy to help[^.!]*[.!]"
    r")\s*",
    re.IGNORECASE,
)

# Politeness closers — must match at the very end of the text
_RE_CLOSER = re.compile(
    r"\s*(?:"
    r"Please (?:let me know|don'?t hesitate to (?:reach out|ask|contact us))[^.!]*[.!]|"
    r"Feel free to (?:ask|reach out|contact us)[^.!]*[.!]|"
    r"I hope this (?:helps?|answers? your question|clears? things up)[^.!]*[.!]?|"
    r"Is there anything else (?:I can|you(?:'d| would) like)[^?]*\?|"
    r"Don'?t hesitate to (?:ask|reach out)[^.!]*[.!]|"
    r"(?:If you have|For any)(?: further| additional)? questions[^.!]*[.!]"
    r")$",
    re.IGNORECASE,
)


# ── OutputShaper ──────────────────────────────────────────────────────────────

class OutputShaper:
    """Deterministic, LLM-free post-generation output normaliser.

    All transforms are regex-based and run in microseconds.  No network
    calls, no model calls, no randomness.
    """

    def shape(self, text: str, intent_class: str = "INFORM") -> ShapedOutput:
        """Apply all applicable transforms to *text*.

        Never returns an empty string — if all transforms would reduce the
        text to nothing, the original is returned unchanged.
        """
        original_len = len(text)
        transforms: List[str] = []
        t = text.strip()

        # 1. CoT preamble strip ("Analyzing… the classification is: billing")
        m = _RE_COT_PREAMBLE.match(t)
        if m:
            t = t[m.end():].strip()
            transforms.append("cot_preamble")
        else:
            # Narrower: strip standalone "Analyzing…" opener sentence
            m2 = _RE_ANALYZING_OPENER.match(t)
            if m2:
                t = t[m2.end():].strip()
                transforms.append("cot_preamble")

        # 2. Politeness opener strip
        m = _RE_OPENER.match(t)
        if m:
            t = t[m.end():].strip()
            transforms.append("politeness_opener")

        # 3. Politeness closer strip
        m = _RE_CLOSER.search(t)
        if m:
            t = t[:m.start()].rstrip()
            transforms.append("politeness_closer")

        # 4. Intent shaping: if CoT was stripped and residual is a bare label
        #    (≤ 4 words), prefix with "Category: "
        if (
            "cot_preamble" in transforms
            and intent_class in _SHAPE_INTENTS
            and t
            and len(t.split()) <= 4
            and not t.lower().startswith("category")
        ):
            t = f"Category: {t}."
            transforms.append("intent_shape")

        result_text = t if t else text
        tokens_saved = max(0, (original_len - len(result_text)) // 4)
        return ShapedOutput(
            text=result_text,
            tokens_saved=tokens_saved,
            transforms=tuple(transforms),
        )

    def output_format_hint(self, intent_class: str) -> str:
        """Short system-prompt suffix that steers the model toward compact output.

        Injecting this at generation time reduces OUTPUT tokens upstream
        rather than stripping verbose text after the fact — the two
        techniques are complementary.
        """
        if intent_class == "CLASSIFY":
            return (
                "\n\nOutput format: Category: <label>. Evidence: <one sentence>. "
                "Do not explain your reasoning before giving the answer."
            )
        if intent_class in ("INFORM", "CLARIFY"):
            return (
                "\n\nBe concise and direct. "
                "Skip chain-of-thought preamble. "
                "Avoid opener/closer filler phrases."
            )
        return ""
