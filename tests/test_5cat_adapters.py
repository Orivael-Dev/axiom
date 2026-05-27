"""Adapter Protocol conformance + import-laziness tests."""
from __future__ import annotations

import pytest

from axiom_5cat_benchmark.adapters import (
    ModelAdapter, StubAdapter, build_adapter,
)


def test_stub_implements_protocol():
    a = StubAdapter()
    assert isinstance(a, ModelAdapter)
    assert a.name == "stub"
    assert a.version == "stub-v1"


def test_factory_routes_stub_without_external_sdks():
    """The stub adapter must work in an env with no anthropic/openai
    package installed.  Build via the public factory."""
    a = build_adapter("stub:anything")
    assert a.name == "stub"


def test_factory_lazy_imports_real_adapters():
    """Importing axiom_5cat_benchmark.adapters does NOT import
    anthropic / openai SDKs — they only load on first build_adapter
    call with a real provider.  Verify by inspecting sys.modules."""
    import sys
    # Fresh import:
    for m in list(sys.modules):
        if (m.startswith("axiom_5cat_benchmark.adapters.anthropic") or
                m.startswith("axiom_5cat_benchmark.adapters.openai") or
                m.startswith("axiom_5cat_benchmark.adapters.local")):
            del sys.modules[m]
    import axiom_5cat_benchmark.adapters  # noqa: F401
    # Real-adapter modules must NOT be loaded just by importing the
    # adapters package.
    assert "axiom_5cat_benchmark.adapters.anthropic" not in sys.modules
    assert "axiom_5cat_benchmark.adapters.openai" not in sys.modules
    assert "axiom_5cat_benchmark.adapters.local" not in sys.modules


def test_factory_local_adapter_signature(monkeypatch):
    """LocalAdapter accepts spec of form local:<model>@<base_url>.
    We can't actually instantiate without openai installed — but the
    parsing logic in build_adapter is testable in isolation."""
    # Don't actually call openai; just verify the parser splits the
    # spec correctly by intercepting LocalAdapter.__init__.
    captured = {}

    class _FakeLocal:
        def __init__(self, *, model_id, base_url=None, **kw):
            captured["model_id"] = model_id
            captured["base_url"] = base_url

    monkeypatch.setattr(
        "axiom_5cat_benchmark.adapters.local.LocalAdapter",
        _FakeLocal,
    )
    build_adapter("local:llama3.3@http://example.com:11434/v1")
    assert captured["model_id"] == "llama3.3"
    assert captured["base_url"] == "http://example.com:11434/v1"


def test_stub_default_call_count_is_zero():
    """Tests that don't talk to the adapter must see a call_count of
    zero — gives downstream tests a clean assertion."""
    a = StubAdapter()
    assert a.call_count == 0


def test_stub_register_isolates_per_instance():
    """A registration on one instance must not leak into another."""
    a = StubAdapter()
    a.register("hello", "world")
    b = StubAdapter()
    assert b.complete("hello").text != "world"


def test_factory_with_kwargs_passes_through():
    """build_adapter forwards **kwargs to the adapter constructor.
    Verify by setting a recognised stub-only kw."""
    a = build_adapter("stub:demo", seed=42, synthetic_latency_ms=99)
    c = a.complete("anything")
    assert c.latency_ms == 99
