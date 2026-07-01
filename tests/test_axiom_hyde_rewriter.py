"""Tests for HyDE (Hypothetical Document Embeddings) additions to QueryRewriter."""
from __future__ import annotations
import os
import pytest
os.environ.setdefault("AXIOM_MASTER_KEY", "d" * 64)
from axiom_query_rewriter import (
    QueryRewriter,
    HYDE_LEGAL_PROMPT, HYDE_MEDICAL_PROMPT, HYDE_OBD_PROMPT, HYDE_GENERAL_PROMPT,
    _HYDE_PROMPTS,
    LEGAL_SYSTEM_PROMPT, OBD_SYSTEM_PROMPT, MEDICAL_SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------

class _OKBackend:
    """Returns a fixed hypothetical answer string."""
    class _Result:
        def __init__(self, text): self.text = text
    def generate(self, prompt, *, max_tokens=200, **kw):
        return self._Result("This is a hypothetical answer with legal vocabulary including liability and damages.")


class _ErrorBackend:
    """Always raises RuntimeError."""
    def generate(self, *a, **kw):
        raise RuntimeError("backend down")


class _ShortBackend:
    """Returns a very short response (< 20 chars)."""
    class _Result:
        text = "ok"
    def generate(self, *a, **kw): return self._Result()


class _RecordingBackend:
    def __init__(self): self.last_prompt = ""
    class _Result:
        text = "A sufficiently long hypothetical answer for the test to pass with proper vocabulary."
    def generate(self, prompt, **kw):
        self.last_prompt = prompt
        return self._Result()


class _FTSBackend:
    """Backend that supports the full generate signature used by rewrite()."""
    class _Result:
        text = "claimant wrongful termination\nplaintiff dismissed unlawfully\nemployee discharged without cause"
    def generate(self, system="", prompt="", max_output_tokens=120, timeout_s=30.0, **kw):
        return self._Result()


class _ErrorFTSBackend:
    """Always raises for the BM25 generate call too."""
    def generate(self, *a, **kw):
        raise RuntimeError("backend totally down")


# ---------------------------------------------------------------------------
# TestHyDEPrompts
# ---------------------------------------------------------------------------

class TestHyDEPrompts:
    def test_hyde_legal_prompt_nonempty(self):
        assert isinstance(HYDE_LEGAL_PROMPT, str)
        assert len(HYDE_LEGAL_PROMPT) > 0

    def test_hyde_medical_prompt_nonempty(self):
        assert isinstance(HYDE_MEDICAL_PROMPT, str)
        assert len(HYDE_MEDICAL_PROMPT) > 0

    def test_hyde_obd_prompt_nonempty(self):
        assert isinstance(HYDE_OBD_PROMPT, str)
        assert len(HYDE_OBD_PROMPT) > 0

    def test_hyde_general_prompt_nonempty(self):
        assert isinstance(HYDE_GENERAL_PROMPT, str)
        assert len(HYDE_GENERAL_PROMPT) > 0

    def test_hyde_prompts_dict_has_required_keys(self):
        assert set(_HYDE_PROMPTS.keys()) == {"legal", "medical", "obd", "general"}

    def test_hyde_legal_differs_from_bm25_legal(self):
        # HyDE prompts generate a hypothetical answer; BM25 prompts generate 3 phrasings
        assert HYDE_LEGAL_PROMPT != LEGAL_SYSTEM_PROMPT

    def test_hyde_medical_differs_from_bm25_medical(self):
        assert HYDE_MEDICAL_PROMPT != MEDICAL_SYSTEM_PROMPT

    def test_hyde_obd_differs_from_bm25_obd(self):
        assert HYDE_OBD_PROMPT != OBD_SYSTEM_PROMPT

    def test_hyde_general_differs_from_bm25_general(self):
        assert HYDE_GENERAL_PROMPT != GENERAL_SYSTEM_PROMPT

    def test_each_hyde_prompt_value_matches_constant(self):
        assert _HYDE_PROMPTS["legal"] == HYDE_LEGAL_PROMPT
        assert _HYDE_PROMPTS["medical"] == HYDE_MEDICAL_PROMPT
        assert _HYDE_PROMPTS["obd"] == HYDE_OBD_PROMPT
        assert _HYDE_PROMPTS["general"] == HYDE_GENERAL_PROMPT


# ---------------------------------------------------------------------------
# TestRewriteHyDE
# ---------------------------------------------------------------------------

class TestRewriteHyDE:
    def test_returns_string(self):
        rewriter = QueryRewriter(_OKBackend())
        result = rewriter.rewrite_hyde("What must a plaintiff prove?", domain="legal")
        assert isinstance(result, str)

    def test_returns_backend_text_on_success(self):
        rewriter = QueryRewriter(_OKBackend())
        result = rewriter.rewrite_hyde("What must a plaintiff prove?", domain="legal")
        expected = "This is a hypothetical answer with legal vocabulary including liability and damages."
        assert result == expected

    def test_falls_back_on_error_backend(self):
        question = "What must a plaintiff prove for wrongful termination?"
        rewriter = QueryRewriter(_ErrorBackend())
        result = rewriter.rewrite_hyde(question, domain="legal")
        assert result == question

    def test_falls_back_on_short_backend(self):
        question = "What are the elements of negligence?"
        rewriter = QueryRewriter(_ShortBackend())
        result = rewriter.rewrite_hyde(question, domain="legal")
        assert result == question

    def test_legal_domain_uses_legal_hyde_prompt(self):
        backend = _RecordingBackend()
        rewriter = QueryRewriter(backend)
        rewriter.rewrite_hyde("What must a plaintiff prove?", domain="legal")
        # The prompt sent to the backend should contain the HyDE legal prompt text
        assert HYDE_LEGAL_PROMPT in backend.last_prompt

    def test_general_domain_uses_general_hyde_prompt(self):
        backend = _RecordingBackend()
        rewriter = QueryRewriter(backend)
        rewriter.rewrite_hyde("Tell me about compensation", domain="general")
        assert HYDE_GENERAL_PROMPT in backend.last_prompt

    def test_unknown_domain_falls_back_to_general_prompt(self):
        backend = _RecordingBackend()
        rewriter = QueryRewriter(backend)
        rewriter.rewrite_hyde("Some question", domain="nonexistent_domain_xyz")
        assert HYDE_GENERAL_PROMPT in backend.last_prompt

    def test_returned_text_is_stripped(self):
        class _WhitespaceBackend:
            class _Result:
                text = "   A sufficiently long hypothetical answer about legal liability.   "
            def generate(self, *a, **kw): return self._Result()

        rewriter = QueryRewriter(_WhitespaceBackend())
        result = rewriter.rewrite_hyde("What is liability?", domain="legal")
        assert result == result.strip()
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_medical_domain_uses_medical_hyde_prompt(self):
        backend = _RecordingBackend()
        rewriter = QueryRewriter(backend)
        rewriter.rewrite_hyde("What are the symptoms of pneumonia?", domain="medical")
        assert HYDE_MEDICAL_PROMPT in backend.last_prompt

    def test_obd_domain_uses_obd_hyde_prompt(self):
        backend = _RecordingBackend()
        rewriter = QueryRewriter(backend)
        rewriter.rewrite_hyde("What causes a P0300 fault code?", domain="obd")
        assert HYDE_OBD_PROMPT in backend.last_prompt


# ---------------------------------------------------------------------------
# TestRewriteWithHyDE
# ---------------------------------------------------------------------------

class TestRewriteWithHyDE:
    def test_returns_tuple_of_length_2(self):
        rewriter = QueryRewriter(_FTSBackend())
        result = rewriter.rewrite_with_hyde("What must a plaintiff prove?", domain="legal")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_first_element_is_string(self):
        rewriter = QueryRewriter(_FTSBackend())
        bm25_expr, _ = rewriter.rewrite_with_hyde("What must a plaintiff prove?", domain="legal")
        assert isinstance(bm25_expr, str)

    def test_second_element_is_string(self):
        # Use a backend that handles both generate signatures
        class _DualBackend:
            class _Result:
                text = "A sufficiently long hypothetical answer for the legal question."
            def generate(self, *a, **kw):
                return self._Result()

        rewriter = QueryRewriter(_DualBackend())
        _, hyde_text = rewriter.rewrite_with_hyde("What must a plaintiff prove?", domain="legal")
        assert isinstance(hyde_text, str)

    def test_both_nonempty_when_backend_succeeds(self):
        class _DualBackend:
            class _Result:
                text = "A sufficiently long hypothetical answer about legal liability and damages."
            def generate(self, *a, **kw):
                return self._Result()

        rewriter = QueryRewriter(_DualBackend())
        bm25_expr, hyde_text = rewriter.rewrite_with_hyde(
            "What must a plaintiff prove?", domain="legal"
        )
        assert len(bm25_expr) > 0
        assert len(hyde_text) > 0

    def test_graceful_fallback_with_error_backend(self):
        question = "What must a plaintiff prove for wrongful termination?"
        rewriter = QueryRewriter(_ErrorFTSBackend())
        bm25_expr, hyde_text = rewriter.rewrite_with_hyde(question, domain="legal")
        # Both should fall back gracefully — BM25 falls back to original tokens, HyDE to original question
        assert isinstance(bm25_expr, str)
        assert len(bm25_expr) > 0
        assert hyde_text == question


# ---------------------------------------------------------------------------
# TestExistingRewriteUnchanged
# ---------------------------------------------------------------------------

class TestExistingRewriteUnchanged:
    """Regression tests — rewrite() and _call_backend still work after HyDE additions."""

    def test_rewrite_still_works(self):
        class _BM25Backend:
            class _Result:
                text = "claimant wrongful dismissal\nplaintiff terminated without cause\nemployee discharged unlawfully"
            def generate(self, system="", prompt="", max_output_tokens=120, timeout_s=30.0, **kw):
                return self._Result()

        rewriter = QueryRewriter(_BM25Backend())
        result = rewriter.rewrite("What must a plaintiff prove?", domain="legal")
        assert isinstance(result, str)
        assert len(result) > 0
        # FTS5 MATCH expression should contain OR-joined quoted tokens
        assert "OR" in result or '"' in result

    def test_call_backend_helper_exists(self):
        rewriter = QueryRewriter(_OKBackend())
        assert hasattr(rewriter, "_call_backend")
        assert callable(rewriter._call_backend)

    def test_call_backend_returns_string(self):
        rewriter = QueryRewriter(_OKBackend())
        result = rewriter._call_backend("test prompt")
        assert isinstance(result, str)

    def test_rewrite_variants_still_works(self):
        class _BM25Backend:
            class _Result:
                text = "claimant wrongful dismissal\nplaintiff terminated without cause"
            def generate(self, system="", prompt="", max_output_tokens=120, timeout_s=30.0, **kw):
                return self._Result()

        rewriter = QueryRewriter(_BM25Backend())
        variants = rewriter.rewrite_variants("What must a plaintiff prove?", domain="legal")
        assert isinstance(variants, list)
        assert len(variants) >= 1
        # Original question should always be first
        assert "What must a plaintiff prove?" == variants[0]

    def test_rewrite_with_hyde_callable_after_hyde_additions(self):
        class _DualBackend:
            class _Result:
                text = "A long enough hypothetical paragraph about legal liability damages plaintiff."
            def generate(self, *a, **kw):
                return self._Result()

        rewriter = QueryRewriter(_DualBackend())
        result = rewriter.rewrite_with_hyde("What is liability?", domain="legal")
        assert isinstance(result, tuple)
        assert len(result) == 2
