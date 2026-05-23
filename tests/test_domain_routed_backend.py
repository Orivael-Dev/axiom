"""Tests for per-domain backend routing in axiom_event_token.backends.

Three pieces under test:
  1. domain_context(...)  — sets a request-scoped contextvar
  2. DomainRoutedBackend  — dispatches to {domain: backend} or default
  3. default_backend()    — auto-wraps when AXIOM_BACKEND_<DOMAIN>
                            env vars are present

No network calls — every backend is a stub that records what it
received so we can assert dispatch went to the right place.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    # Drop any cached backend modules so re-import picks up env changes.
    for mod in list(sys.modules):
        if mod.startswith(("axiom_event_token", "axiom_signing")):
            sys.modules.pop(mod, None)
    yield


@dataclass
class _StubBackend:
    """Minimal SLMBackend that records what generate() was called with."""
    name: str
    model: str

    def __post_init__(self):
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        self.calls.append({
            "system": system, "prompt": prompt,
            "max_output_tokens": max_output_tokens,
        })
        from axiom_event_token.backends import BackendResult
        return BackendResult(
            text=f"[{self.name}] would have answered: {prompt[:40]}",
            input_tokens=10, output_tokens=20,
            backend=self.name, model=self.model,
            latency_ms=1,
        )


# ─── 1. domain_context contextvar plumbing ─────────────────────────────

class TestDomainContext:
    def test_default_is_none(self, isolated):
        from axiom_event_token.backends import current_domain
        assert current_domain() is None

    def test_with_block_sets_and_restores(self, isolated):
        from axiom_event_token.backends import current_domain, domain_context
        assert current_domain() is None
        with domain_context("medical"):
            assert current_domain() == "medical"
            with domain_context("security"):
                assert current_domain() == "security"
            assert current_domain() == "medical"
        assert current_domain() is None

    def test_case_insensitive_normalisation(self, isolated):
        from axiom_event_token.backends import current_domain, domain_context
        with domain_context("MEDICAL"):
            assert current_domain() == "medical"

    def test_empty_string_treated_as_none(self, isolated):
        from axiom_event_token.backends import current_domain, domain_context
        with domain_context(""):
            assert current_domain() is None
        with domain_context("   "):
            assert current_domain() is None
        with domain_context(None):
            assert current_domain() is None


# ─── 2. DomainRoutedBackend dispatch ───────────────────────────────────

class TestDomainRoutedBackend:
    def test_dispatches_to_per_domain_when_context_matches(self, isolated):
        from axiom_event_token.backends import (
            DomainRoutedBackend, domain_context,
        )
        default = _StubBackend(name="default", model="qwen-72b")
        medical = _StubBackend(name="medical", model="meditron-70b")
        routed = DomainRoutedBackend(
            default=default, per_domain={"medical": medical},
        )
        with domain_context("medical"):
            routed.generate(system="s", prompt="p", max_output_tokens=128)
        assert len(medical.calls) == 1
        assert len(default.calls) == 0

    def test_falls_back_to_default_outside_context(self, isolated):
        from axiom_event_token.backends import DomainRoutedBackend
        default = _StubBackend(name="default", model="qwen-72b")
        medical = _StubBackend(name="medical", model="meditron-70b")
        routed = DomainRoutedBackend(
            default=default, per_domain={"medical": medical},
        )
        routed.generate(system="s", prompt="p", max_output_tokens=128)
        assert len(default.calls) == 1
        assert len(medical.calls) == 0

    def test_unknown_domain_falls_through_to_default(self, isolated):
        from axiom_event_token.backends import (
            DomainRoutedBackend, domain_context,
        )
        default = _StubBackend(name="default", model="qwen-72b")
        medical = _StubBackend(name="medical", model="meditron-70b")
        routed = DomainRoutedBackend(
            default=default, per_domain={"medical": medical},
        )
        with domain_context("finance"):  # no override registered
            routed.generate(system="s", prompt="p", max_output_tokens=128)
        assert len(default.calls) == 1
        assert len(medical.calls) == 0

    def test_per_domain_keys_normalised_to_lowercase(self, isolated):
        from axiom_event_token.backends import (
            DomainRoutedBackend, domain_context,
        )
        default = _StubBackend(name="default", model="x")
        sec = _StubBackend(name="security", model="qwen-coder")
        # Caller registered with uppercase — should still match.
        routed = DomainRoutedBackend(
            default=default, per_domain={"SECURITY": sec},
        )
        with domain_context("security"):
            routed.generate(system="s", prompt="p", max_output_tokens=128)
        assert len(sec.calls) == 1

    def test_model_string_summarises_routing(self, isolated):
        """`/api/health` displays backend.model; the summary should
        show what's actually wired so operators can verify config."""
        from axiom_event_token.backends import DomainRoutedBackend
        routed = DomainRoutedBackend(
            default=_StubBackend(name="d", model="qwen-72b"),
            per_domain={
                "medical":  _StubBackend(name="m", model="meditron-70b"),
                "security": _StubBackend(name="s", model="qwen-coder-32b"),
            },
        )
        assert routed.name == "domain-routed"
        # Sorted alphabetically so the summary is deterministic
        assert routed.model == (
            "default=qwen-72b · medical=meditron-70b · security=qwen-coder-32b"
        )

    def test_requires_default_backend(self, isolated):
        from axiom_event_token.backends import DomainRoutedBackend
        with pytest.raises(ValueError, match="default backend"):
            DomainRoutedBackend(default=None, per_domain={})  # type: ignore[arg-type]

    def test_none_entries_in_per_domain_skipped(self, isolated):
        """`_build_domain_backend` returns None when no override env
        var is present; DomainRoutedBackend should filter those out
        cleanly rather than crashing on resolve()."""
        from axiom_event_token.backends import (
            DomainRoutedBackend, domain_context,
        )
        default = _StubBackend(name="default", model="qwen-72b")
        routed = DomainRoutedBackend(
            default=default,
            per_domain={"medical": None, "security": None},
        )
        with domain_context("medical"):
            routed.generate(system="s", prompt="p", max_output_tokens=128)
        assert len(default.calls) == 1


# ─── 3. default_backend() env-driven discovery ─────────────────────────

class TestDefaultBackendAutoWrap:
    def _clear_backend_env(self, monkeypatch):
        """Wipe every AXIOM_BACKEND* / per-domain var so each test
        starts from a known-clean environment."""
        for k in list(__import__("os").environ):
            if k.startswith(("AXIOM_BACKEND", "AXIOM_BASE_URL",
                              "AXIOM_API_KEY", "AXIOM_MODEL",
                              "OLLAMA_URL", "NVIDIA_NIM_API_KEY",
                              "DEEPSEEK_API_KEY")):
                monkeypatch.delenv(k, raising=False)

    def test_no_overrides_returns_plain_backend(self, isolated, monkeypatch):
        """When no AXIOM_BACKEND_<DOMAIN> is set, default_backend()
        returns a single backend, not a DomainRoutedBackend."""
        self._clear_backend_env(monkeypatch)
        monkeypatch.setenv("AXIOM_BACKEND", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL", "https://default/v1")
        monkeypatch.setenv("AXIOM_API_KEY", "k")
        monkeypatch.setenv("AXIOM_MODEL", "default-model")
        from axiom_event_token.backends import (
            default_backend, DomainRoutedBackend,
        )
        b = default_backend()
        assert not isinstance(b, DomainRoutedBackend)
        assert b.model == "default-model"

    def test_one_override_wraps_in_domain_routed(self, isolated, monkeypatch):
        """Setting AXIOM_BACKEND_MEDICAL flips the result to a
        DomainRoutedBackend with medical mapped to its own backend
        and the default still in place."""
        self._clear_backend_env(monkeypatch)
        # Default
        monkeypatch.setenv("AXIOM_BACKEND", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL", "https://default/v1")
        monkeypatch.setenv("AXIOM_API_KEY", "k")
        monkeypatch.setenv("AXIOM_MODEL", "default-model")
        # Medical override
        monkeypatch.setenv("AXIOM_BACKEND_MEDICAL", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL_MEDICAL", "https://med/v1")
        monkeypatch.setenv("AXIOM_API_KEY_MEDICAL", "k-med")
        monkeypatch.setenv("AXIOM_MODEL_MEDICAL", "meditron-70b")

        from axiom_event_token.backends import (
            default_backend, DomainRoutedBackend,
        )
        b = default_backend()
        assert isinstance(b, DomainRoutedBackend)
        assert "default=default-model" in b.model
        assert "medical=meditron-70b" in b.model

    def test_per_domain_does_not_pollute_default_env(self, isolated, monkeypatch):
        """_build_domain_backend temporarily shadows AXIOM_BASE_URL
        with AXIOM_BASE_URL_MEDICAL while constructing the medical
        backend. After it returns, the bare env vars must be exactly
        what they were before — no leakage."""
        self._clear_backend_env(monkeypatch)
        monkeypatch.setenv("AXIOM_BACKEND", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL", "https://default/v1")
        monkeypatch.setenv("AXIOM_API_KEY", "k-default")
        monkeypatch.setenv("AXIOM_MODEL", "default-model")
        monkeypatch.setenv("AXIOM_BACKEND_MEDICAL", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL_MEDICAL", "https://med/v1")
        monkeypatch.setenv("AXIOM_API_KEY_MEDICAL", "k-med")
        monkeypatch.setenv("AXIOM_MODEL_MEDICAL", "meditron-70b")

        from axiom_event_token.backends import default_backend
        default_backend()

        import os
        assert os.environ["AXIOM_BASE_URL"] == "https://default/v1"
        assert os.environ["AXIOM_API_KEY"]  == "k-default"
        assert os.environ["AXIOM_MODEL"]    == "default-model"

    def test_override_falls_back_to_bare_var_when_suffix_missing(
        self, isolated, monkeypatch,
    ):
        """If AXIOM_BACKEND_SECURITY=custom is set but
        AXIOM_API_KEY_SECURITY is not, the build should fall back to
        the bare AXIOM_API_KEY — useful when the user only wants to
        override the model/endpoint, not the key."""
        self._clear_backend_env(monkeypatch)
        monkeypatch.setenv("AXIOM_BACKEND", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL", "https://default/v1")
        monkeypatch.setenv("AXIOM_API_KEY", "shared-key")
        monkeypatch.setenv("AXIOM_MODEL", "default-model")
        monkeypatch.setenv("AXIOM_BACKEND_SECURITY", "custom")
        monkeypatch.setenv("AXIOM_BASE_URL_SECURITY", "https://sec/v1")
        monkeypatch.setenv("AXIOM_MODEL_SECURITY", "qwen-coder")
        # Notice: AXIOM_API_KEY_SECURITY is NOT set

        from axiom_event_token.backends import (
            default_backend, DomainRoutedBackend,
        )
        b = default_backend()
        assert isinstance(b, DomainRoutedBackend)
        assert "security=qwen-coder" in b.model

    def test_local_ollama_per_domain_routes_to_different_models(
        self, isolated, monkeypatch,
    ):
        """Local-Ollama use case: one Ollama server, multiple models
        pulled (`ollama pull meditron`, `ollama pull qwen2.5-coder`),
        different model per domain. Reads OLLAMA_MODEL_<DOMAIN>, not
        AXIOM_MODEL_<DOMAIN>."""
        self._clear_backend_env(monkeypatch)
        monkeypatch.setenv("AXIOM_BACKEND", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")

        monkeypatch.setenv("AXIOM_BACKEND_MEDICAL", "local")
        monkeypatch.setenv("OLLAMA_MODEL_MEDICAL", "meditron:70b")

        monkeypatch.setenv("AXIOM_BACKEND_SECURITY", "local")
        monkeypatch.setenv("OLLAMA_MODEL_SECURITY", "qwen2.5-coder:32b")

        from axiom_event_token.backends import (
            default_backend, DomainRoutedBackend,
        )
        b = default_backend()
        assert isinstance(b, DomainRoutedBackend)
        assert "default=qwen2.5:7b" in b.model
        assert "medical=meditron:70b" in b.model
        assert "security=qwen2.5-coder:32b" in b.model

    def test_local_ollama_per_domain_can_use_separate_hosts(
        self, isolated, monkeypatch,
    ):
        """Some setups run a GPU-only Ollama for medical (heavy
        70B model) and a CPU Ollama for everything else — different
        OLLAMA_URL per domain handles that."""
        self._clear_backend_env(monkeypatch)
        monkeypatch.setenv("AXIOM_BACKEND", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")

        monkeypatch.setenv("AXIOM_BACKEND_MEDICAL", "local")
        monkeypatch.setenv("OLLAMA_MODEL_MEDICAL", "meditron:70b")
        monkeypatch.setenv("OLLAMA_URL_MEDICAL", "http://gpu-box:11434")

        from axiom_event_token.backends import default_backend
        b = default_backend()
        # Surface check via the routing summary — internal URL
        # validation is the LocalNanoBackend's job.
        assert "medical=meditron:70b" in b.model

    def test_local_ollama_env_not_polluted_after_resolve(
        self, isolated, monkeypatch,
    ):
        """Same guarantee as the custom-backend variant: OLLAMA_MODEL
        and OLLAMA_URL must be exactly what they were before the
        per-domain build, even after we shadowed them inside."""
        self._clear_backend_env(monkeypatch)
        monkeypatch.setenv("AXIOM_BACKEND", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
        monkeypatch.setenv("AXIOM_BACKEND_MEDICAL", "local")
        monkeypatch.setenv("OLLAMA_MODEL_MEDICAL", "meditron:70b")
        monkeypatch.setenv("OLLAMA_URL_MEDICAL", "http://gpu-box:11434")

        from axiom_event_token.backends import default_backend
        default_backend()

        import os
        assert os.environ["OLLAMA_MODEL"] == "qwen2.5:7b"
        assert os.environ["OLLAMA_URL"]   == "http://localhost:11434"
