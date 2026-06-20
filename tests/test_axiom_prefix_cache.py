"""Smoke tests for the DomainPrefixCache prefix-caching strategy."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

from axiom_prefix_cache import (
    DOMAIN_CONTEXT_BUDGETS,
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
