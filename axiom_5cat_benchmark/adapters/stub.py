"""Canned-response adapter for CI.

The stub adapter returns deterministic Completions keyed by the
SHA-256 of the prompt. CI tests use this so the full 5-category
benchmark runs in <30s with zero API spend and zero network calls.

Behaviour:
  - If the prompt's hash is in the response table, return the
    matching canned text + synthetic token counts + synthetic
    latency.
  - Otherwise return a default "stub: <first 80 chars>" reply so
    no test ever crashes on a missing key.
  - call_count exposed as a test-side assertion that nothing
    accidentally hit a real adapter.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any

from axiom_5cat_benchmark.adapters.base import (
    ModelAdapter, hash_response, now_ms,
)
from axiom_5cat_benchmark.schema import Completion


# Default canned responses keyed by sha256(prompt). Tests register
# more via StubAdapter.register(prompt, text).
_DEFAULT_TABLE: dict[str, str] = {}


def _prompt_key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class StubAdapter:
    """Deterministic, no-network LLM adapter for CI.

    Implements the ModelAdapter Protocol. Reproducibility is via the
    sha256(prompt) key — same prompt under the same registered table
    always returns the same Completion."""

    name:    str = "stub"
    version: str = "stub-v1"

    def __init__(
        self,
        model_id: str = "stub:fixed-v1",
        *,
        seed: int = 1729,
        synthetic_latency_ms: int = 7,
        table: dict[str, str] | None = None,
    ) -> None:
        self._model_id = model_id
        self._seed = seed
        self._latency = synthetic_latency_ms
        # Per-instance table layered on top of _DEFAULT_TABLE so a
        # test's registrations don't leak into the next test.
        self._table: dict[str, str] = dict(_DEFAULT_TABLE)
        if table:
            self._table.update({_prompt_key(k): v for k, v in table.items()})
        self.call_count: int = 0
        # Per-instance RNG so two stubs created in the same test
        # don't share state.
        self._rng = random.Random(seed)

    # ── ModelAdapter contract ──────────────────────────────────────

    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> Completion:
        self.call_count += 1
        key = _prompt_key(prompt)
        text = self._table.get(key)
        if text is None:
            # Default: echo a deterministic stub.  Helpful so tests
            # not focused on response content still see something.
            text = f"stub: {prompt[:80]}"
        # Token counts are synthetic but stable: ~1 token per 4 chars
        # is a reasonable rule of thumb for ASCII English. We don't
        # claim accuracy — we only need a number Cat 2 can score
        # against a budget without burning real credit.
        in_tok  = max(1, len(prompt) // 4)
        out_tok = max(1, len(text) // 4)
        if out_tok > max_tokens:
            out_tok = max_tokens
            text = text[: max_tokens * 4]
        raw = {"choices": [{"text": text}], "stub": True}
        return Completion(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=self._latency,
            model_id=self._model_id,
            raw_response_sha=hash_response(raw),
        )

    # ── Test helpers ───────────────────────────────────────────────

    def register(self, prompt: str, response_text: str) -> None:
        """Add a canned response for `prompt` to this instance's table."""
        self._table[_prompt_key(prompt)] = response_text

    def reset_calls(self) -> None:
        self.call_count = 0


# Convenience for tests that want a one-shot adapter without
# instantiating the class.
def make_stub(table: dict[str, str] | None = None) -> StubAdapter:
    return StubAdapter(table=table)


# Compile-time Protocol check.
_protocol_check: ModelAdapter = StubAdapter()  # noqa: F841
