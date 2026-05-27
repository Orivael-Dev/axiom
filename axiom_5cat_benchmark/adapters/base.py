"""ModelAdapter Protocol + shared helpers.

Every concrete adapter (anthropic, openai, local, stub) implements
the same Protocol so the runner is model-agnostic. The Protocol is
intentionally narrow: one method, one return type — enough to drive
every category without leaking provider-specific concepts.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Protocol, runtime_checkable

from axiom_5cat_benchmark.schema import Completion


@runtime_checkable
class ModelAdapter(Protocol):
    """A thin facade over an LLM provider.

    Implementations:
      - return a fully-populated Completion (text + token counts +
        latency + raw response hash)
      - never raise on transient failure — wrap in retry, then return
        a Completion with empty text and a `latency_ms` reflecting the
        full retry budget (so Cat 2 perf-per-watt still scores the
        wallclock waste)
    """

    name:    str    # "anthropic" | "openai" | "local" | "stub"
    version: str    # SDK version or "stub-v1"

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> Completion: ...

    def model_id(self) -> str: ...


# ─── Shared helpers ────────────────────────────────────────────────


def now_ms() -> int:
    """Wall-clock milliseconds since boot, monotonic.

    Mirrors axiom_firewall/db.py + auth.py — perf_counter() gives
    monotonic latency unaffected by NTP drift mid-call."""
    return int(time.perf_counter() * 1000)


def hash_response(raw: Any) -> str:
    """SHA-256 of the canonical string form of a raw provider response.

    Used as Completion.raw_response_sha so a verifier can detect
    post-hoc tampering of the recorded completion bytes. We hash a
    `repr()` (deterministic for str/dict/list of primitives) so the
    same response always produces the same hash regardless of which
    SDK happens to wrap it in a Pydantic model vs a plain dict."""
    s = repr(raw)
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


def truncate(text: str, n: int = 300) -> str:
    """Match benchmark_v1_0.py's [:300] convention on stored outputs."""
    if not text:
        return ""
    return text[:n]


class RetryExhausted(Exception):
    """Raised internally by adapters that decide to surface a hard
    failure (e.g. auth error) rather than absorb it as empty text.
    The runner catches this and records a zero-score trial with a
    clear `notes` field."""
