#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM backends for AxiomDevAgentV2.

The four-layer agent treats the LLM as just another diff source —
the reflex, reviewer, curriculum, and examiner gates apply equally
to LLM-generated and human-written diffs. The agent does NOT trust
the LLM more than any other input; that's the constitutional
discipline this module preserves.

Three backends ship here:

  AnthropicBackend  — Claude via the `anthropic` SDK. ANTHROPIC_API_KEY
                       must be set; otherwise the factory falls through.
  OpenAIBackend     — GPT via the `openai` SDK. OPENAI_API_KEY must be
                       set; otherwise the factory falls through.
  SimulatorBackend  — deterministic canned responses keyed by task_class.
                       The default when no API keys are present so tests
                       and demos run in CI without network access.

The factory `select_backend(prefer=...)` picks the first usable backend
in the preferred-then-fallback order. Callers don't reach into the
SDKs directly — they get an `LLMBackend` and call `.generate_diff(...)`.

BUG-003: UTF-8 output encoding.
"""

from __future__ import annotations

import json
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── System prompt — same for every real backend ─────────────────────────
SYSTEM_PROMPT = """You are AxiomDevAgent — a constitutional code agent.

Your output goes through a four-layer review pipeline. Diffs that
violate any of the following are refused at Layer 0 (no retries help
— fix the diff itself):

  - Never use eval(), exec(), os.system()
  - Never use subprocess with shell=True
  - Never embed strings that look like credentials (64-hex-char keys)
  - Never include `assert False` in shipped code

Other rules:
  - Keep diffs under 500 lines (split otherwise — Layer 3 examiner
    enforces this hard).
  - For BUG_FIX tasks, cite at least one AXM TrajectoryBlock pattern
    (Layer 3 enforces). Use the form `traj-axiom-agent-<type>`.

Return ONLY the unified diff. No commentary, no fenced code blocks.
Start with `--- a/<path>` and `+++ b/<path>` lines. Use `@@` hunks."""


@dataclass(frozen=True)
class LLMResponse:
    """Result of a backend `generate_diff` call."""
    backend_name:   str
    diff:           str          # the unified diff the LLM produced
    cited_patterns: tuple        # patterns the LLM said it followed
    model:          str          # model identifier for the audit trail


# ── Base interface ──────────────────────────────────────────────────────
class LLMBackend(ABC):
    """An LLM that can generate a unified diff for a dev task."""

    name: str = "abstract"

    @abstractmethod
    def available(self) -> bool:
        """Return True if this backend can actually run (API key
        present, SDK importable, etc.)."""

    @abstractmethod
    def generate_diff(self, *, description: str, task_class: str,
                      context: str = "",
                      retry_hint: Optional[str] = None) -> LLMResponse:
        """Build a unified diff for the task. If retry_hint is set,
        the previous attempt was refused and the hint explains why —
        the backend should incorporate it into the next attempt."""


# ── Anthropic backend ───────────────────────────────────────────────────
class AnthropicBackend(LLMBackend):
    name = "anthropic"

    def __init__(self, model: str = "claude-3-5-sonnet-latest"):
        self.model = model
        self._client = None
        try:
            from anthropic import Anthropic  # noqa: F401
            self._sdk_available = True
        except ImportError:
            self._sdk_available = False

    def available(self) -> bool:
        return bool(self._sdk_available and os.environ.get("ANTHROPIC_API_KEY"))

    def generate_diff(self, *, description, task_class, context="",
                      retry_hint=None) -> LLMResponse:
        if not self.available():
            raise RuntimeError("AnthropicBackend not available — "
                                "install `anthropic` and set ANTHROPIC_API_KEY")
        from anthropic import Anthropic
        if self._client is None:
            self._client = Anthropic()
        user_msg = _build_user_message(description, task_class, context,
                                         retry_hint)
        resp = self._client.messages.create(
            model=self.model, max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        diff, cited = _parse_diff_and_citations(text)
        return LLMResponse(backend_name=self.name, diff=diff,
                            cited_patterns=cited, model=self.model)


# ── OpenAI backend ──────────────────────────────────────────────────────
class OpenAIBackend(LLMBackend):
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._client = None
        try:
            from openai import OpenAI  # noqa: F401
            self._sdk_available = True
        except ImportError:
            self._sdk_available = False

    def available(self) -> bool:
        return bool(self._sdk_available and os.environ.get("OPENAI_API_KEY"))

    def generate_diff(self, *, description, task_class, context="",
                      retry_hint=None) -> LLMResponse:
        if not self.available():
            raise RuntimeError("OpenAIBackend not available — "
                                "install `openai` and set OPENAI_API_KEY")
        from openai import OpenAI
        if self._client is None:
            self._client = OpenAI()
        user_msg = _build_user_message(description, task_class, context,
                                         retry_hint)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
        )
        text = resp.choices[0].message.content or ""
        diff, cited = _parse_diff_and_citations(text)
        return LLMResponse(backend_name=self.name, diff=diff,
                            cited_patterns=cited, model=self.model)


# ── Simulator backend (deterministic, no network) ───────────────────────
#
# Canned responses keyed by task_class. Used as the default when no
# real API keys are present — tests + demos work in CI without
# network access. The diffs here are intentionally minimal but
# Layer-0-clean — the dev agent's pipeline can be exercised end-to-
# end with this backend alone.
_CANNED_DIFFS: dict = {
    "DOCUMENTATION": (
        '--- a/module.py\n'
        '+++ b/module.py\n'
        '@@ -1,1 +1,3 @@\n'
        '+"""Module-level docstring added by AxiomDevAgent."""\n'
        '+\n'
        ' # original content unchanged\n',
        ("traj-axiom-agent-language_knowledge",),
    ),
    "BUG_FIX": (
        '--- a/regex.py\n'
        '+++ b/regex.py\n'
        '@@ -1,1 +1,1 @@\n'
        '-pattern = r"foo"\n'
        '+pattern = r"foo\\b"\n',
        ("traj-axiom-agent-bug_fix",),
    ),
    "FEATURE": (
        '--- a/feature.py\n'
        '+++ b/feature.py\n'
        '@@ -1,0 +1,1 @@\n'
        '+def new_feature() -> int:\n    return 0\n',
        ("traj-axiom-agent-implementation_pattern",),
    ),
    "EFFICIENCY": (
        '--- a/loop.py\n'
        '+++ b/loop.py\n'
        '@@ -1,1 +1,1 @@\n'
        '-total = sum([x for x in items])\n'
        '+total = sum(x for x in items)\n',
        ("traj-axiom-agent-pattern_knowledge",),
    ),
    "SPEC_WRITING": (
        '--- a/agent.axiom\n'
        '+++ b/agent.axiom\n'
        '@@ -1,0 +1,2 @@\n'
        '+CONSTRAINT Every decision is HMAC-SHA256 signed\n',
        ("traj-axiom-agent-spec_writing",),
    ),
}


class SimulatorBackend(LLMBackend):
    name = "simulator"

    def __init__(self):
        pass

    def available(self) -> bool:
        return True

    def generate_diff(self, *, description, task_class, context="",
                      retry_hint=None) -> LLMResponse:
        diff, cited = _CANNED_DIFFS.get(
            task_class,
            ('--- a/x.py\n+++ b/x.py\n@@ -1,0 +1,1 @@\n+pass\n', ()),
        )
        return LLMResponse(backend_name=self.name, diff=diff,
                            cited_patterns=cited, model="simulator-v1")


# ── Helpers ─────────────────────────────────────────────────────────────
def _build_user_message(description: str, task_class: str,
                          context: str, retry_hint: Optional[str]) -> str:
    parts = [
        f"task_class: {task_class}",
        f"description: {description}",
    ]
    if context:
        parts.append(f"context:\n{context}")
    if retry_hint:
        parts.append(
            f"\nThe previous attempt was REFUSED by the agent's review pipeline. "
            f"Reason: {retry_hint}\n"
            f"Generate a new diff that addresses the reason. Do not repeat the "
            f"refused construct."
        )
    return "\n\n".join(parts)


def _parse_diff_and_citations(text: str) -> tuple:
    """Pull a unified diff out of model output. Strips fenced code
    blocks if present; extracts cited_patterns from a trailing
    `Cited: ...` line if the model emits one."""
    # Strip ```...``` fences.
    if "```" in text:
        chunks = text.split("```")
        if len(chunks) >= 3:
            text = chunks[1]
            if text.startswith(("diff", "patch", "python")):
                text = text.split("\n", 1)[1] if "\n" in text else ""
    diff_lines: list = []
    cited: list = []
    for line in text.splitlines():
        if line.lower().startswith("cited:"):
            cited.extend(s.strip()
                          for s in line.split(":", 1)[1].split(",")
                          if s.strip())
            continue
        diff_lines.append(line)
    diff = "\n".join(diff_lines).strip() + "\n"
    return diff, tuple(cited)


# ── Factory ─────────────────────────────────────────────────────────────
def select_backend(prefer: str = "auto") -> LLMBackend:
    """Return the first usable backend in preference order.

    prefer values:
      "anthropic"  — Anthropic if available, else simulator
      "openai"     — OpenAI if available, else simulator
      "simulator"  — always the simulator
      "auto"       — Anthropic > OpenAI > simulator
    """
    if prefer == "simulator":
        return SimulatorBackend()
    if prefer == "anthropic":
        b = AnthropicBackend()
        return b if b.available() else SimulatorBackend()
    if prefer == "openai":
        b = OpenAIBackend()
        return b if b.available() else SimulatorBackend()
    # auto
    for cls in (AnthropicBackend, OpenAIBackend):
        b = cls()
        if b.available():
            return b
    return SimulatorBackend()


# ── CLI introspection ────────────────────────────────────────────────────
def _main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="axiom_dev_agent_v2_backends",
                                description="List available LLM backends.")
    p.add_argument("--prefer", default="auto",
                   choices=("auto", "anthropic", "openai", "simulator"))
    args = p.parse_args(argv)
    backend = select_backend(prefer=args.prefer)
    print(json.dumps({
        "selected":            backend.name,
        "anthropic_available": AnthropicBackend().available(),
        "openai_available":    OpenAIBackend().available(),
        "simulator_available": True,
    }, indent=2, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
