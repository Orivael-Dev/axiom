"""Adapter factory.

Subjects under test are specified as ``provider:model_id`` strings,
e.g. ``anthropic:claude-opus-4-7``, ``openai:gpt-4o``,
``local:llama3.3@http://localhost:11434``, ``stub:fixed-v1``.

The factory routes the spec to the right adapter class. Anthropic,
OpenAI, and local adapters are imported lazily so a stub-only CI run
doesn't require either SDK to be installed."""
from __future__ import annotations

from axiom_5cat_benchmark.adapters.base import (
    ModelAdapter, RetryExhausted, Completion,
)
from axiom_5cat_benchmark.adapters.stub import StubAdapter, make_stub


def build_adapter(spec: str, **kwargs) -> ModelAdapter:
    """Parse ``provider:model_id[@endpoint]`` and return an adapter.

    Raises ValueError on an unknown provider. Anthropic/OpenAI/local
    adapters are imported lazily so a stub-only run never requires
    their SDKs."""
    if ":" not in spec:
        raise ValueError(
            f"adapter spec must be 'provider:model_id', got {spec!r}"
        )
    provider, rest = spec.split(":", 1)
    provider = provider.lower().strip()

    if provider == "stub":
        return StubAdapter(model_id=spec, **kwargs)

    if provider == "anthropic":
        from axiom_5cat_benchmark.adapters.anthropic import AnthropicAdapter
        return AnthropicAdapter(model_id=rest, **kwargs)

    if provider == "openai":
        from axiom_5cat_benchmark.adapters.openai import OpenAIAdapter
        return OpenAIAdapter(model_id=rest, **kwargs)

    if provider == "local":
        # local:<model>@<base_url> — base_url is optional and falls
        # back to AXIOM_BASE_URL / NVIDIA_BASE_URL env.
        if "@" in rest:
            model, base_url = rest.split("@", 1)
        else:
            model, base_url = rest, None
        from axiom_5cat_benchmark.adapters.local import LocalAdapter
        return LocalAdapter(model_id=model, base_url=base_url, **kwargs)

    raise ValueError(f"unknown adapter provider: {provider!r}")


__all__ = [
    "ModelAdapter", "RetryExhausted", "Completion",
    "StubAdapter", "make_stub", "build_adapter",
]
