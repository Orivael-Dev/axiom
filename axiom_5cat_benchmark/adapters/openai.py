"""OpenAI Chat Completions adapter.

Wraps the `openai` SDK (in core deps as of v1.8.8). Used for any
OpenAI-compatible endpoint that speaks /v1/chat/completions: the
real openai.com, plus Ollama, vLLM, LM Studio, OpenRouter, etc.
(For Ollama/vLLM/LM Studio specifically, prefer ``LocalAdapter``,
which sets the right defaults.)"""
from __future__ import annotations

import os
import time

from axiom_5cat_benchmark.adapters.base import (
    Completion, hash_response, now_ms,
)


_MAX_RETRIES = 3
_BACKOFF_S   = (1.0, 2.0, 4.0)


class OpenAIAdapter:
    name:    str = "openai"

    def __init__(
        self,
        model_id: str = "gpt-4o",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        try:
            import openai as _openai
        except ImportError as e:
            raise ImportError(
                "OpenAIAdapter needs the `openai` SDK. Install with:\n"
                "  pip install openai\n"
                "(it's a default dep of axiom-constitutional since v1.8.8)"
            ) from e
        self._openai = _openai
        self.version = getattr(_openai, "__version__", "unknown")
        self._model_id = model_id
        kwargs = {"timeout": timeout_s}
        # api_key=None lets the SDK pick up OPENAI_API_KEY itself.
        if api_key:
            kwargs["api_key"] = api_key
        elif os.environ.get("OPENAI_API_KEY"):
            pass   # SDK default
        else:
            raise EnvironmentError(
                "OPENAI_API_KEY not set. Export it or pass api_key="
            )
        if base_url:
            kwargs["base_url"] = base_url
        self._client = _openai.OpenAI(**kwargs)

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
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                )
                text = (resp.choices[0].message.content or "") if resp.choices else ""
                usage = getattr(resp, "usage", None)
                in_tok  = int(getattr(usage, "prompt_tokens",     0) or 0)
                out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
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
        return Completion(
            text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=now_ms() - start,
            model_id=self._model_id,
            raw_response_sha=hash_response({"error": repr(last_err)}),
        )
