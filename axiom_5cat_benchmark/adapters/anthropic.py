"""Anthropic Messages API adapter.

Wraps the `anthropic` SDK. The SDK is an optional dep — install via
``pip install axiom-constitutional[llm]`` (which already pins
``anthropic>=1.0.0`` per pyproject.toml). The import is lazy so a
stub-only CI run doesn't require the SDK to be present.

Extended thinking — modern path (Claude 4.6 / 4.7 family):
    pass ``thinking_effort`` ∈ {"low","medium","high","max"} to the
    constructor. The adapter sends ``thinking={"type":"adaptive",
    "display":"summarized"}`` + ``output_config={"effort": ...}`` —
    the model decides depth, and the summarised thinking trace flows
    into ``Completion.thinking_text``.

Extended thinking — legacy path (Sonnet 4.5 and prior):
    pass ``thinking_budget=N`` (≥ 1024). The adapter sends the
    deprecated ``thinking={"type":"enabled","budget_tokens":N}`` shape
    with ``temperature=1.0`` (legacy contract). Will 400 on Opus 4.7;
    use ``thinking_effort`` for the 4.6/4.7 family instead.

Model-family rules baked into ``_build_request``:
  - Opus 4.7: sampling params (``temperature``/``top_p``/``top_k``)
    are removed at the API; we strip them. Adaptive thinking only.
  - Opus 4.6, Sonnet 4.6: adaptive thinking is the recommended path;
    ``budget_tokens`` is still functional but deprecated.
  - Older models (4.5 and prior): legacy ``budget_tokens`` works;
    adaptive thinking does not.
"""
from __future__ import annotations

import os
import re
import time

from axiom_5cat_benchmark.adapters.base import (
    Completion, RetryExhausted, hash_response, now_ms,
)


_MAX_RETRIES = 3
_BACKOFF_S   = (1.0, 2.0, 4.0)

_THINKING_TEXT_CAP = 1000

_VALID_EFFORTS = ("low", "medium", "high", "max")

# Model-family detection. Matches the public IDs from the model
# catalogue (claude-opus-4-7, claude-opus-4-6, claude-sonnet-4-6,
# claude-haiku-4-5, plus legacy 4-5 / 4-1 / 4-0 / 3-x ids).
_RE_OPUS_47 = re.compile(r"^claude-opus-4-7\b")
_RE_4_6     = re.compile(r"^claude-(?:opus|sonnet|haiku)-4-(?:6|7)\b")


def _is_opus_47(model_id: str) -> bool:
    return bool(_RE_OPUS_47.match(model_id))


def _supports_adaptive(model_id: str) -> bool:
    """True for any 4.6+ model — the family where adaptive thinking
    is the recommended path."""
    return bool(_RE_4_6.match(model_id))


class AnthropicAdapter:
    name:    str = "anthropic"

    def __init__(
        self,
        model_id: str = "claude-sonnet-4-6",
        *,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        thinking_effort: str | None = None,
        thinking_budget: int | None = None,
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

        if thinking_effort is not None and thinking_budget is not None:
            raise ValueError(
                "pass thinking_effort OR thinking_budget, not both. "
                "thinking_effort is the modern (4.6/4.7) path; "
                "thinking_budget is the legacy (≤ 4.5) escape hatch."
            )
        if thinking_effort is not None:
            if thinking_effort not in _VALID_EFFORTS:
                raise ValueError(
                    f"thinking_effort must be one of {_VALID_EFFORTS}; "
                    f"got {thinking_effort!r}"
                )
        if thinking_budget is not None:
            if not isinstance(thinking_budget, int) or thinking_budget < 1024:
                raise ValueError(
                    "thinking_budget must be an int ≥ 1024 (Anthropic API "
                    f"minimum); got {thinking_budget!r}"
                )
            if _is_opus_47(model_id):
                raise ValueError(
                    "thinking_budget (legacy budget_tokens shape) returns "
                    "400 on Opus 4.7 — use thinking_effort instead."
                )
        if thinking_effort is not None and not _supports_adaptive(model_id):
            raise ValueError(
                f"thinking_effort requires a 4.6+ model; "
                f"{model_id!r} doesn't support adaptive thinking. "
                "Use thinking_budget for legacy models."
            )

        self._thinking_effort = thinking_effort
        self._thinking_budget = thinking_budget

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Export it or pass api_key="
            )
        self._client = _anthropic.Anthropic(api_key=key, timeout=timeout_s)

    def model_id(self) -> str:
        return self._model_id

    def _build_request(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> dict:
        """Apply per-model-family rules and return messages.create kwargs."""
        kwargs: dict = {
            "model":      self._model_id,
            "max_tokens": max_tokens,
            "messages":   [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        # Sampling-parameter rule: Opus 4.7 returns 400 if temperature
        # is sent. Strip on 4.7; pass through elsewhere.
        if not _is_opus_47(self._model_id):
            kwargs["temperature"] = temperature

        # Modern adaptive-thinking path (4.6+ family — validated in ctor).
        if self._thinking_effort is not None:
            kwargs["thinking"] = {
                "type":    "adaptive",
                # `summarized` is required on Opus 4.7 to actually
                # capture the thinking trace; harmless on 4.6.
                "display": "summarized",
            }
            kwargs["output_config"] = {"effort": self._thinking_effort}
            return kwargs

        # Legacy budget_tokens path (≤ 4.5 only — ctor blocks 4.7).
        if self._thinking_budget is not None:
            if kwargs["max_tokens"] <= self._thinking_budget:
                kwargs["max_tokens"] = self._thinking_budget + 256
            kwargs["thinking"] = {
                "type":          "enabled",
                "budget_tokens": self._thinking_budget,
            }
            kwargs["temperature"] = 1.0  # legacy contract
            return kwargs

        return kwargs

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
                kwargs = self._build_request(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                )
                resp = self._client.messages.create(**kwargs)
                text_parts: list[str] = []
                think_parts: list[str] = []
                for b in resp.content:
                    btype = getattr(b, "type", "")
                    if btype == "text":
                        text_parts.append(getattr(b, "text", "") or "")
                    elif btype == "thinking":
                        think_parts.append(getattr(b, "thinking", "") or "")
                text = "".join(text_parts)
                thinking_text = "".join(think_parts)[:_THINKING_TEXT_CAP]
                usage = getattr(resp, "usage", None)
                in_tok  = int(getattr(usage, "input_tokens",  0) or 0)
                out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                # `output_tokens` already includes thinking tokens —
                # the API does not expose a separate counter. Record
                # the presence-of-thinking via thinking_text (which
                # only appears when display='summarized' produced one);
                # the trial-level signal that thinking was active is
                # whether thinking_text is non-empty.
                return Completion(
                    text=text,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    latency_ms=now_ms() - start,
                    model_id=self._model_id,
                    raw_response_sha=hash_response(
                        getattr(resp, "model_dump", lambda: repr(resp))(),
                    ),
                    thinking_tokens=len(thinking_text),  # chars, not tokens
                    thinking_text=thinking_text,
                )
            except Exception as e:
                last_err = e
                if attempt + 1 < _MAX_RETRIES:
                    time.sleep(_BACKOFF_S[attempt])
        return Completion(
            text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=now_ms() - start,
            model_id=self._model_id,
            raw_response_sha=hash_response({"error": repr(last_err)}),
        )
