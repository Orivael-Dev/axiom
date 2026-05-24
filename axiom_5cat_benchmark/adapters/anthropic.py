"""Anthropic Messages API adapter.

Wraps the `anthropic` SDK. The SDK is an optional dep — install via
``pip install axiom-constitutional[llm]`` (which already pins
``anthropic>=1.0.0`` per pyproject.toml). The import is lazy so a
stub-only CI run doesn't require the SDK to be present.
"""
from __future__ import annotations

import os
import time

from axiom_5cat_benchmark.adapters.base import (
    Completion, RetryExhausted, hash_response, now_ms,
)


_MAX_RETRIES = 3
_BACKOFF_S   = (1.0, 2.0, 4.0)


class AnthropicAdapter:
    name:    str = "anthropic"

    def __init__(
        self,
        model_id: str = "claude-sonnet-4-6",
        *,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as e:
            raise ImportError(
                "AnthropicAdapter needs the `anthropic` SDK. Install with:\n"
                "  pip install 'axiom-constitutional[llm]'\n"
                "or directly: pip install anthropic"
            ) from e
        self._anthropic = _anthropic
        self.version = getattr(_anthropic, "__version__", "unknown")
        self._model_id = model_id
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Export it or pass api_key="
            )
        self._client = _anthropic.Anthropic(api_key=key, timeout=timeout_s)

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
        start = now_ms()
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                kwargs = {
                    "model":       self._model_id,
                    "max_tokens":  max_tokens,
                    "temperature": temperature,
                    "messages":    [{"role": "user", "content": prompt}],
                }
                if system:
                    kwargs["system"] = system
                resp = self._client.messages.create(**kwargs)
                # Concatenate every text block — modern Claude responses
                # can carry multiple content blocks.
                text = "".join(
                    getattr(b, "text", "") for b in resp.content
                    if getattr(b, "type", "") == "text"
                )
                usage = getattr(resp, "usage", None)
                in_tok = int(getattr(usage, "input_tokens",  0) or 0)
                out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                return Completion(
                    text=text,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    latency_ms=now_ms() - start,
                    model_id=self._model_id,
                    raw_response_sha=hash_response(
                        getattr(resp, "model_dump", lambda: repr(resp))(),
                    ),
                )
            except Exception as e:
                last_err = e
                if attempt + 1 < _MAX_RETRIES:
                    time.sleep(_BACKOFF_S[attempt])
        # Exhausted retries — return empty Completion with the full
        # wallclock recorded so Cat 2 perf-per-watt counts the waste.
        return Completion(
            text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=now_ms() - start,
            model_id=self._model_id,
            raw_response_sha=hash_response({"error": repr(last_err)}),
        )
