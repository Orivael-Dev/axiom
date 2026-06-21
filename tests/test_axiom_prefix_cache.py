"""Smoke tests for the DomainPrefixCache prefix-caching strategy."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from unittest.mock import MagicMock

from axiom_prefix_cache import (
    DOMAIN_CONTEXT_BUDGETS,
    DOMAIN_DELTA_BUDGETS,
    DOMAIN_SEED_QUERIES,
    DOMAIN_SYSTEM_PROMPTS,
    DomainPrefixCache,
)


# ── DOMAIN_SYSTEM_PROMPTS ─────────────────────────────────────────────────────

def test_all_known_domains_have_prompts() -> None:
    for domain in ("legal", "healthcare", "finance", "security", "hr", None):
        assert domain in DOMAIN_SYSTEM_PROMPTS, f"Missing prompt for domain={domain!r}"
        assert len(DOMAIN_SYSTEM_PROMPTS[domain]) > 20


def test_domain_prompts_are_distinct() -> None:
    prompts = list(DOMAIN_SYSTEM_PROMPTS.values())
    assert len(set(prompts)) == len(prompts), "Two domains share the same system prompt"


def test_legal_prompt_mentions_legal() -> None:
    assert "legal" in DOMAIN_SYSTEM_PROMPTS["legal"].lower()


def test_healthcare_prompt_mentions_medical_or_health() -> None:
    text = DOMAIN_SYSTEM_PROMPTS["healthcare"].lower()
    assert "medical" in text or "health" in text or "clinical" in text


# ── DOMAIN_CONTEXT_BUDGETS ────────────────────────────────────────────────────

def test_legal_budget_less_than_general() -> None:
    assert DOMAIN_CONTEXT_BUDGETS["legal"] < DOMAIN_CONTEXT_BUDGETS[None]


def test_healthcare_budget_less_than_general() -> None:
    assert DOMAIN_CONTEXT_BUDGETS["healthcare"] < DOMAIN_CONTEXT_BUDGETS[None]


def test_all_budgets_positive() -> None:
    for domain, budget in DOMAIN_CONTEXT_BUDGETS.items():
        assert budget > 0, f"Budget for {domain!r} must be > 0"


# ── DomainPrefixCache.make_system ─────────────────────────────────────────────

def test_make_system_legal_differs_from_general() -> None:
    pc = DomainPrefixCache()
    assert pc.make_system("legal") != pc.make_system(None)


def test_make_system_is_idempotent() -> None:
    pc = DomainPrefixCache()
    assert pc.make_system("legal") == pc.make_system("legal")
    assert pc.make_system(None) == pc.make_system(None)


def test_make_system_never_empty() -> None:
    pc = DomainPrefixCache()
    for domain in ("legal", "healthcare", "finance", "security", None):
        result = pc.make_system(domain)
        assert result and len(result) > 10, f"Empty system prompt for domain={domain!r}"


def test_make_system_unknown_domain_returns_general() -> None:
    pc = DomainPrefixCache()
    assert pc.make_system("unknown_xyz") == pc.make_system(None)


def test_make_system_contains_no_context_headers() -> None:
    pc = DomainPrefixCache()
    for domain in DOMAIN_SYSTEM_PROMPTS:
        text = pc.make_system(domain)
        assert "=== GALAXY" not in text
        assert "=== STAR"   not in text
        assert "=== PLANET" not in text


# ── DomainPrefixCache.make_user_prompt ────────────────────────────────────────

def test_make_user_prompt_includes_context_and_query() -> None:
    pc = DomainPrefixCache()
    result = pc.make_user_prompt("Some retrieved text.", "What is GDPR Article 9?")
    assert "Some retrieved text." in result
    assert "What is GDPR Article 9?" in result


def test_make_user_prompt_empty_context_returns_bare_query() -> None:
    pc = DomainPrefixCache()
    query = "What is GDPR Article 9?"
    assert pc.make_user_prompt("", query) == query
    assert pc.make_user_prompt("   ", query) == query


def test_make_user_prompt_no_context_header_prefix() -> None:
    pc = DomainPrefixCache()
    result = pc.make_user_prompt("", "bare query")
    assert not result.startswith("Context:")


def test_make_user_prompt_with_context_has_context_header() -> None:
    pc = DomainPrefixCache()
    result = pc.make_user_prompt("doc snippet", "What is GDPR?")
    assert result.startswith("Context:")


# ── DomainPrefixCache.context_budget ─────────────────────────────────────────

def test_context_budget_legal_less_than_general() -> None:
    pc = DomainPrefixCache()
    assert pc.context_budget("legal") < pc.context_budget(None)


def test_context_budget_unknown_domain_returns_general() -> None:
    pc = DomainPrefixCache()
    assert pc.context_budget("unknown_xyz") == pc.context_budget(None)


def test_context_budget_all_positive() -> None:
    pc = DomainPrefixCache()
    for domain in list(DOMAIN_CONTEXT_BUDGETS.keys()) + ["unknown"]:
        assert pc.context_budget(domain) > 0


# ── DomainPrefixCache.detect_cache_hit ───────────────────────────────────────

def test_detect_cache_hit_fast_prefill_is_hit() -> None:
    pc = DomainPrefixCache()
    # 2 ms for 100 tokens = 0.02 ms/tok → well below 0.5 threshold
    assert pc.detect_cache_hit(prefill_ms=2, input_tokens=100) is True


def test_detect_cache_hit_slow_prefill_is_miss() -> None:
    pc = DomainPrefixCache()
    # 500 ms for 100 tokens = 5 ms/tok → cold fill
    assert pc.detect_cache_hit(prefill_ms=500, input_tokens=100) is False


def test_detect_cache_hit_zero_tokens_returns_false() -> None:
    pc = DomainPrefixCache()
    assert pc.detect_cache_hit(prefill_ms=2, input_tokens=0) is False


def test_detect_cache_hit_zero_prefill_returns_false() -> None:
    pc = DomainPrefixCache()
    assert pc.detect_cache_hit(prefill_ms=0, input_tokens=100) is False


# ── DomainPrefixCache.warm_domain (graceful failure) ─────────────────────────

def test_warm_domain_no_ollama_returns_false() -> None:
    pc = DomainPrefixCache()
    # Port 1 is closed on virtually all machines; should fail silently
    result = pc.warm_domain("legal", "http://localhost:1", "llama3.2:3b", timeout_s=2.0)
    assert result is False


def test_warm_domain_no_ollama_does_not_raise() -> None:
    pc = DomainPrefixCache()
    try:
        pc.warm_domain(None, "http://localhost:1", "llama3.2:3b", timeout_s=2.0)
    except Exception as exc:
        pytest.fail(f"warm_domain raised unexpectedly: {exc}")


def test_is_warm_before_warming_is_false() -> None:
    pc = DomainPrefixCache()
    assert pc.is_warm("legal") is False
    assert pc.is_warm(None) is False


# ── Preamble: DOMAIN_SEED_QUERIES / DOMAIN_DELTA_BUDGETS ─────────────────────

def test_seed_queries_legal_nonempty() -> None:
    assert len(DOMAIN_SEED_QUERIES["legal"]) >= 3


def test_seed_queries_general_empty() -> None:
    assert DOMAIN_SEED_QUERIES[None] == []


def test_delta_budget_legal_less_than_general() -> None:
    assert DOMAIN_DELTA_BUDGETS["legal"] < DOMAIN_DELTA_BUDGETS[None]


# ── DomainPrefixCache.build_preamble ─────────────────────────────────────────

def _make_mock_retriever(uri: str = "docs/gdpr.txt", snippet: str = "GDPR Article 9 prohibits...") -> MagicMock:
    hit = MagicMock()
    hit.uri     = uri
    hit.snippet = snippet
    hit.title   = "GDPR Art 9"
    mock_r = MagicMock()
    mock_r.retrieve.return_value = [hit]
    return mock_r


def test_build_preamble_returns_nonzero_for_legal() -> None:
    pc = DomainPrefixCache()
    n = pc.build_preamble("legal", _make_mock_retriever())
    assert n > 0


def test_build_preamble_no_retriever_returns_zero() -> None:
    pc = DomainPrefixCache()
    assert pc.build_preamble("legal", None) == 0


def test_build_preamble_general_returns_zero() -> None:
    # DOMAIN_SEED_QUERIES[None] is empty → no preamble for general
    pc = DomainPrefixCache()
    assert pc.build_preamble(None, _make_mock_retriever()) == 0


def test_build_preamble_is_idempotent() -> None:
    pc = DomainPrefixCache()
    mock_r = _make_mock_retriever()
    pc.build_preamble("legal", mock_r)
    n2 = pc.build_preamble("legal", mock_r)  # second call replaces
    assert n2 > 0


# ── DomainPrefixCache.make_system_with_preamble ───────────────────────────────

def test_make_system_with_preamble_contains_doc_text() -> None:
    pc = DomainPrefixCache()
    pc.build_preamble("legal", _make_mock_retriever(snippet="GDPR Article 9 prohibits..."))
    sys_prompt = pc.make_system_with_preamble("legal")
    assert "GDPR" in sys_prompt


def test_make_system_with_preamble_falls_back_before_build() -> None:
    pc = DomainPrefixCache()
    # No build_preamble called → should match plain make_system
    assert pc.make_system_with_preamble("legal") == pc.make_system("legal")


def test_make_system_with_preamble_longer_than_plain() -> None:
    pc = DomainPrefixCache()
    pc.build_preamble("legal", _make_mock_retriever())
    assert len(pc.make_system_with_preamble("legal")) > len(pc.make_system("legal"))


# ── DomainPrefixCache.filter_preamble_hits ───────────────────────────────────

def _make_hit(uri: str, snippet: str = "text") -> MagicMock:
    h = MagicMock()
    h.uri     = uri
    h.snippet = snippet
    h.title   = uri
    return h


def test_filter_preamble_hits_removes_preamble_uris() -> None:
    pc    = DomainPrefixCache()
    uri   = "docs/gdpr.txt"
    mock_r = _make_mock_retriever(uri=uri)
    pc.build_preamble("legal", mock_r)

    hit  = _make_hit(uri)
    hits = [hit]
    delta = pc.filter_preamble_hits("legal", hits)
    assert len(delta) == 0


def test_filter_preamble_hits_keeps_novel_uris() -> None:
    pc    = DomainPrefixCache()
    pc.build_preamble("legal", _make_mock_retriever(uri="docs/gdpr.txt"))

    novel = _make_hit("docs/ccpa.txt")
    delta = pc.filter_preamble_hits("legal", [novel])
    assert len(delta) == 1


def test_filter_preamble_hits_no_preamble_returns_all() -> None:
    pc   = DomainPrefixCache()
    hits = [_make_hit("docs/any.txt")]
    assert pc.filter_preamble_hits("legal", hits) == hits


# ── DomainPrefixCache.preamble_coverage ──────────────────────────────────────

def test_preamble_coverage_all_covered() -> None:
    pc    = DomainPrefixCache()
    uri   = "docs/gdpr.txt"
    pc.build_preamble("legal", _make_mock_retriever(uri=uri))
    hit   = _make_hit(uri)
    assert pc.preamble_coverage("legal", [hit]) == 1.0


def test_preamble_coverage_none_covered() -> None:
    pc  = DomainPrefixCache()
    pc.build_preamble("legal", _make_mock_retriever(uri="docs/gdpr.txt"))
    hit = _make_hit("docs/ccpa.txt")
    assert pc.preamble_coverage("legal", [hit]) == 0.0


def test_preamble_coverage_no_preamble_returns_zero() -> None:
    pc  = DomainPrefixCache()
    hit = _make_hit("docs/any.txt")
    assert pc.preamble_coverage("legal", [hit]) == 0.0


def test_preamble_coverage_empty_hits_returns_zero() -> None:
    pc = DomainPrefixCache()
    assert pc.preamble_coverage("legal", []) == 0.0


# ── DomainPrefixCache.delta_budget ───────────────────────────────────────────

def test_delta_budget_legal_less_than_context_budget() -> None:
    pc = DomainPrefixCache()
    assert pc.delta_budget("legal") < pc.context_budget("legal")


def test_delta_budget_general_equals_full_budget() -> None:
    pc = DomainPrefixCache()
    assert pc.delta_budget(None) == DOMAIN_DELTA_BUDGETS[None]


# ── warm_domain uses make_system_with_preamble ────────────────────────────────

def test_warm_domain_uses_preamble_system_when_built() -> None:
    """After build_preamble, warm_domain sends the preamble-included string."""
    pc = DomainPrefixCache()
    pc.build_preamble("legal", _make_mock_retriever(snippet="GDPR Article 9 prohibits..."))

    sent_bodies: list = []

    import requests as _req
    original_post = _req.post

    def fake_post(url, json=None, timeout=None):
        sent_bodies.append(json)
        resp = MagicMock()
        resp.ok = False  # don't need a successful response
        return resp

    import unittest.mock as _mock
    with _mock.patch("requests.post", fake_post):
        pc.warm_domain("legal", "http://localhost:11434", "llama3.2:3b", timeout_s=1.0)

    assert len(sent_bodies) == 1
    system_content = sent_bodies[0]["messages"][0]["content"]
    assert "GDPR" in system_content
