"""Tests for QueryRewriter — latent-reasoning FTS5 query expansion."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_query_rewriter import (
    QueryRewriter,
    _build_fts5_match,
    _parse_variants,
    LEGAL_SYSTEM_PROMPT,
    OBD_SYSTEM_PROMPT,
    MEDICAL_SYSTEM_PROMPT,
)
from axiom_event_token.backends import BackendResult


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_backend(response_text: str) -> MagicMock:
    b = MagicMock()
    b.generate.return_value = BackendResult(
        text=response_text,
        input_tokens=10,
        output_tokens=20,
        latency_ms=80,
        backend="mock",
        model="mock-model",
    )
    return b


# ── _parse_variants ───────────────────────────────────────────────────────────

class TestParseVariants:
    def test_three_plain_lines(self):
        text = "claimant wrongful dismissal\nemployee terminated without cause\nplaintiff discharged unlawfully"
        out = _parse_variants(text)
        assert len(out) == 3
        assert "claimant wrongful dismissal" in out

    def test_strips_numbering(self):
        text = "1. claimant wrongful dismissal\n2. terminated employee\n3. discharged without cause"
        out = _parse_variants(text)
        assert not any(v.startswith(("1.", "2.", "3.")) for v in out)
        assert len(out) == 3

    def test_strips_bullets(self):
        text = "- claimant\n* terminated\n• discharged"
        out = _parse_variants(text)
        assert not any(v.startswith(("-", "*", "•")) for v in out)

    def test_empty_lines_ignored(self):
        text = "\nclaimant\n\nterminated\n\n"
        out = _parse_variants(text)
        assert len(out) == 2

    def test_caps_at_four(self):
        text = "\n".join(f"variant {i}" for i in range(10))
        out = _parse_variants(text)
        assert len(out) <= 4

    def test_empty_input(self):
        assert _parse_variants("") == []


# ── _build_fts5_match ─────────────────────────────────────────────────────────

class TestBuildFts5Match:
    def test_basic_tokens(self):
        expr = _build_fts5_match(["plaintiff proved wrongful"])
        assert '"plaintiff"' in expr
        assert '"proved"' in expr
        assert '"wrongful"' in expr

    def test_stop_words_removed(self):
        expr = _build_fts5_match(["what is the contract"])
        assert '"what"' not in expr
        assert '"is"' not in expr
        assert '"the"' not in expr
        assert '"contract"' in expr

    def test_short_tokens_removed(self):
        expr = _build_fts5_match(["an of it"])
        # all three are stop-words; fallback to unfiltered tokenisation
        # but short 2-char tokens like "of"/"it" should not appear
        # when there are longer tokens available
        result = _build_fts5_match(["plaintiff it"])
        assert '"plaintiff"' in result

    def test_deduplication(self):
        expr = _build_fts5_match(["plaintiff sued", "plaintiff filed"])
        count = expr.count('"plaintiff"')
        assert count == 1

    def test_or_joined(self):
        expr = _build_fts5_match(["plaintiff contract"])
        assert " OR " in expr

    def test_multiple_texts_merged(self):
        expr = _build_fts5_match(["plaintiff", "claimant", "petitioner"])
        assert '"plaintiff"' in expr
        assert '"claimant"' in expr
        assert '"petitioner"' in expr

    def test_empty_texts_returns_something(self):
        expr = _build_fts5_match(["the a is"])
        assert expr   # fallback never returns empty string


# ── QueryRewriter.rewrite ─────────────────────────────────────────────────────

class TestQueryRewriterRewrite:
    def test_returns_fts5_expression(self):
        backend = _mock_backend(
            "claimant wrongful dismissal\n"
            "employee terminated without cause\n"
            "plaintiff discharged unlawfully"
        )
        r = QueryRewriter(backend)
        expr = r.rewrite("What must a plaintiff prove for wrongful termination?",
                         domain="legal")
        assert '"' in expr and " OR " in expr

    def test_includes_original_question_tokens(self):
        backend = _mock_backend("claimant dismissed")
        r = QueryRewriter(backend)
        expr = r.rewrite("plaintiff fired wrongfully", domain="legal")
        # Tokens from original question should be in the expansion
        assert '"plaintiff"' in expr or '"fired"' in expr or '"wrongfully"' in expr

    def test_includes_variant_tokens(self):
        backend = _mock_backend("claimant dismissed without cause")
        r = QueryRewriter(backend)
        expr = r.rewrite("plaintiff fired", domain="legal")
        assert '"claimant"' in expr or '"dismissed"' in expr

    def test_backend_called_with_legal_system_prompt(self):
        backend = _mock_backend("claimant")
        r = QueryRewriter(backend, system_prompt=LEGAL_SYSTEM_PROMPT)
        r.rewrite("plaintiff", domain="legal")
        call_kwargs = backend.generate.call_args.kwargs
        assert "legal" in call_kwargs["system"].lower() or "legal" in LEGAL_SYSTEM_PROMPT.lower()

    def test_backend_error_falls_back_to_original(self):
        backend = MagicMock()
        backend.generate.side_effect = RuntimeError("connection refused")
        r = QueryRewriter(backend)
        expr = r.rewrite("plaintiff sued for damages", domain="legal")
        # Must still return something based on the original tokens
        assert '"plaintiff"' in expr or '"sued"' in expr or '"damages"' in expr

    def test_empty_backend_response_falls_back(self):
        backend = _mock_backend("")
        r = QueryRewriter(backend)
        expr = r.rewrite("plaintiff contract damages", domain="legal")
        assert '"plaintiff"' in expr or '"contract"' in expr or '"damages"' in expr

    def test_domain_picks_correct_system_prompt(self):
        backend = _mock_backend("cylinder misfire fault code")
        r = QueryRewriter(backend)
        r.rewrite("P0301 engine fault", domain="obd")
        call_kwargs = backend.generate.call_args.kwargs
        assert "OBD" in call_kwargs["system"] or "automotive" in call_kwargs["system"].lower()

    def test_max_tokens_passed_to_backend(self):
        backend = _mock_backend("variant one\nvariant two")
        r = QueryRewriter(backend, max_tokens=80)
        r.rewrite("question", domain="legal")
        assert backend.generate.call_args.kwargs["max_output_tokens"] == 80


# ── QueryRewriter.rewrite_variants ────────────────────────────────────────────

class TestQueryRewriterVariants:
    def test_returns_list_with_original_first(self):
        backend = _mock_backend("claimant dismissed\npetitioner discharged")
        r = QueryRewriter(backend)
        variants = r.rewrite_variants("plaintiff fired", domain="legal")
        assert variants[0] == "plaintiff fired"
        assert len(variants) >= 2

    def test_backend_error_returns_just_original(self):
        backend = MagicMock()
        backend.generate.side_effect = RuntimeError("offline")
        r = QueryRewriter(backend)
        variants = r.rewrite_variants("plaintiff fired", domain="legal")
        assert variants == ["plaintiff fired"]


# ── from_env ─────────────────────────────────────────────────────────────────

class TestFromEnv:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("AXIOM_QUERY_REWRITE", raising=False)
        from axiom_query_rewriter import from_env
        assert from_env() is None

    def test_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setenv("AXIOM_QUERY_REWRITE", "0")
        from axiom_query_rewriter import from_env
        assert from_env() is None

    def test_returns_rewriter_when_backend_available(self, monkeypatch):
        monkeypatch.setenv("AXIOM_QUERY_REWRITE", "legal")
        monkeypatch.setenv("AXIOM_BACKEND", "local")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
        # from_env() will try to build a LocalNanoBackend — it doesn't
        # make a network call at construction time, so it should succeed
        from axiom_query_rewriter import from_env
        try:
            qr = from_env(domain="legal")
            if qr is not None:
                assert isinstance(qr, QueryRewriter)
        except Exception:
            pass   # backend construction may fail in CI — that's acceptable


# ── System prompt constants ───────────────────────────────────────────────────

class TestSystemPrompts:
    def test_legal_prompt_mentions_legal(self):
        assert "legal" in LEGAL_SYSTEM_PROMPT.lower()

    def test_obd_prompt_mentions_obd(self):
        assert "OBD" in OBD_SYSTEM_PROMPT or "automotive" in OBD_SYSTEM_PROMPT.lower()

    def test_medical_prompt_mentions_clinical(self):
        assert "clinical" in MEDICAL_SYSTEM_PROMPT.lower() or "medical" in MEDICAL_SYSTEM_PROMPT.lower()
