"""Local OpenAI-compatible adapter (Ollama, vLLM, LM Studio, etc.).

Subclasses OpenAIAdapter with sane defaults for local endpoints:
``base_url`` defaults to ``http://localhost:11434/v1`` (Ollama) and
``api_key`` defaults to a dummy string since most local servers
don't authenticate. Override either via the constructor or via
``AXIOM_BASE_URL`` / ``AXIOM_API_KEY`` env vars — same precedence
as ``axiom_constitutional.client._build_client``.
"""
from __future__ import annotations

import os

from axiom_5cat_benchmark.adapters.openai import OpenAIAdapter


_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_DUMMY_LOCAL_KEY  = "local-no-auth-required"


class LocalAdapter(OpenAIAdapter):
    name: str = "local"

    def __init__(
        self,
        model_id: str = "llama3.3",
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        resolved_url = (
            base_url
            or os.environ.get("AXIOM_BASE_URL")
            or os.environ.get("NVIDIA_BASE_URL")
            or _DEFAULT_BASE_URL
        )
        resolved_key = (
            api_key
            or os.environ.get("AXIOM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or _DUMMY_LOCAL_KEY
        )
        super().__init__(
            model_id=model_id,
            api_key=resolved_key,
            base_url=resolved_url,
            timeout_s=timeout_s,
        )
        # Override the inherited name + version to reflect we're a
        # local backend, not a hosted OpenAI call.
        self.name = "local"
        self.version = f"openai-compat@{resolved_url}"
