"""Axiom Domain Prefix Cache — aggressive KV-cache warming for local SLMs.

Implements a four-layer prefix-caching strategy to keep the local SRD-4
model consistently at the 26 ms/tok sweet spot across all query domains:

  Layer 1 — Static/dynamic prompt separation (stops Ollama cache busting)
  Layer 2 — /api/chat role separation (Ollama caches system role explicitly)
  Layer 3 — Startup pre-warming (fills Ollama KV before first real query)
  Layer 4 — Domain-adaptive context budgets (caps Legal/Healthcare input size)

The key insight: ``LocalNanoBackend`` previously embedded retrieved documents
in the system prompt, which changes every query → Ollama's prefix cache busted
on every Legal/Healthcare call.  By keeping ``system`` static per domain and
moving retrieved context to the user turn, Ollama retains the system KV across
all queries in that domain — matching General RAG's effective prefill cost.
"""
from __future__ import annotations

import sys
import time
import types as _types
from dataclasses import dataclass, field
from typing import Dict, Optional

import requests


# ── CANNOT_MUTATE module freeze ───────────────────────────────────────────────

def _module_setattr(self: object, name: str, value: object) -> None:
    raise AttributeError(f"CANNOT_MUTATE: {name} is immutable in axiom_prefix_cache")

_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})

TRUST_LEVEL: int = 1


# ── Per-domain static system prompts ─────────────────────────────────────────
# These strings are NEVER allowed to include retrieved document content.
# They must be identical across all queries for the same domain so Ollama
# can reuse the system-role KV cache on every call.

DOMAIN_SYSTEM_PROMPTS: Dict[Optional[str], str] = {
    "legal":      ("You are an expert legal assistant governed by the Axiom Inference OS. "
                   "Answer precisely and cite the relevant legal context provided."),
    "healthcare": ("You are an expert medical assistant governed by the Axiom Inference OS. "
                   "Answer clinically and accurately based on the provided healthcare context."),
    "finance":    ("You are an expert financial analyst governed by the Axiom Inference OS. "
                   "Answer precisely and concisely based on the provided financial context."),
    "security":   ("You are an expert security researcher governed by the Axiom Inference OS. "
                   "Answer technically and precisely based on the provided security context."),
    "hr":         ("You are an expert HR advisor governed by the Axiom Inference OS. "
                   "Answer based on the provided HR policy context."),
    None:         ("You are a helpful, accurate assistant governed by the Axiom Inference OS. "
                   "Answer concisely based on the provided context."),
}

# Domain-adaptive context budgets (chars, not tokens).
# Tighter budgets for verbose domains cap input token count,
# keeping Legal/Healthcare prefill cost ≈ General RAG's profile.
# Rule of thumb: 4 chars ≈ 1 token; target ≤ 800 tokens of context.
DOMAIN_CONTEXT_BUDGETS: Dict[Optional[str], int] = {
    "legal":      3_000,   # ~750 tokens — legal docs are dense; caps 5.8× blowup
    "healthcare": 4_000,   # ~1000 tokens
    "finance":    3_500,   # ~875 tokens
    "security":   4_000,
    "hr":         3_000,
    None:         8_000,   # general — full budget unchanged
}

# A prefill faster than this threshold (ms per input token) indicates
# Ollama served from its KV cache (i.e. system prompt was cached).
_CACHE_HIT_THRESHOLD_MS_PER_TOK: float = 0.5


# ── Warm-state bookkeeping ────────────────────────────────────────────────────

@dataclass
class PrefixWarmState:
    domain:          Optional[str]
    warm:            bool  = False
    warmed_at:       float = 0.0   # monotonic seconds
    cold_prefill_ms: int   = 0     # baseline from the warm-up request


# ── Main class ────────────────────────────────────────────────────────────────

class DomainPrefixCache:
    """Manages static domain system prompts, startup pre-warming, and context budgets.

    Usage in InferenceOS::

        cache = DomainPrefixCache()
        cache.warm_all(backend._url, backend.model)   # fire-and-forget on startup

        # In the generation stage:
        system = cache.make_system(request.domain)
        user   = cache.make_user_prompt(context_str, request.query)
        result = backend.generate(system=system, prompt=user, ...)

        # Log cache effectiveness:
        hit = cache.detect_cache_hit(result.prefill_ms, result.input_tokens)
    """

    def __init__(self) -> None:
        self._states: Dict[str, PrefixWarmState] = {}

    # ── prompt construction ───────────────────────────────────────────────

    def make_system(self, domain: Optional[str]) -> str:
        """Return the static per-domain system prompt.

        This string is NEVER changed between calls for the same domain —
        that invariant is what allows Ollama to serve subsequent requests
        from its KV cache without re-prefilling the system tokens.
        """
        return DOMAIN_SYSTEM_PROMPTS.get(domain, DOMAIN_SYSTEM_PROMPTS[None])

    def make_user_prompt(self, context_str: str, query: str) -> str:
        """Build the dynamic user turn: retrieved context followed by the question.

        Keeping retrieved documents in the user turn (not the system) means
        only the variable portion of each request is freshly prefilled.
        The static system prefix remains cached across all domain queries.
        """
        stripped = context_str.strip() if context_str else ""
        if stripped:
            return f"Context:\n{stripped}\n\nQuestion: {query}"
        return query

    def context_budget(self, domain: Optional[str]) -> int:
        """Maximum retrieved-context chars for this domain.

        Tighter budgets for verbose domains keep total input tokens ≈ the
        General RAG profile (~200–300 tokens), so TTFT stays proportional.
        """
        return DOMAIN_CONTEXT_BUDGETS.get(domain, DOMAIN_CONTEXT_BUDGETS[None])

    # ── pre-warming ───────────────────────────────────────────────────────

    def warm_domain(
        self,
        domain: Optional[str],
        ollama_url: str,
        model: str,
        timeout_s: float = 30.0,
    ) -> bool:
        """Pre-fill Ollama's KV cache with this domain's system prompt tokens.

        Sends a 1-token dummy /api/chat request so Ollama evaluates and
        retains the system-role KV state.  All subsequent real requests for
        this domain skip re-prefilling those tokens.

        Returns True on success; False on any error (never raises).
        """
        system = self.make_system(domain)
        body = {
            "model":      model,
            "messages":   [
                {"role": "system", "content": system},
                {"role": "user",   "content": "ready"},
            ],
            "stream":     False,
            "keep_alive": "10m",
            "options":    {"num_predict": 1, "temperature": 0.0},
        }
        try:
            resp = requests.post(
                f"{ollama_url.rstrip('/')}/api/chat",
                json=body, timeout=timeout_s,
            )
            if resp.ok:
                data = resp.json()
                prefill_ms = int(data.get("prompt_eval_duration", 0) / 1_000_000)
                key = domain or ""
                self._states[key] = PrefixWarmState(
                    domain=domain,
                    warm=True,
                    warmed_at=time.monotonic(),
                    cold_prefill_ms=prefill_ms,
                )
                return True
        except Exception:
            pass
        return False

    def warm_all(self, ollama_url: str, model: str) -> None:
        """Fire background warming threads for all registered domains simultaneously.

        Runs as daemon threads — does not block InferenceOS startup.  The
        first query to each domain may still be cold; all subsequent ones
        benefit from the warm cache.
        """
        import threading
        for domain in DOMAIN_SYSTEM_PROMPTS:
            threading.Thread(
                target=self.warm_domain,
                args=(domain, ollama_url, model),
                daemon=True,
                name=f"axiom-prefix-warm-{domain or 'general'}",
            ).start()

    def is_warm(self, domain: Optional[str]) -> bool:
        """True if a successful warm_domain() call has been completed."""
        return self._states.get(domain or "", PrefixWarmState(domain)).warm

    # ── cache-hit detection ───────────────────────────────────────────────

    def detect_cache_hit(self, prefill_ms: int, input_tokens: int) -> bool:
        """True if Ollama's prefill duration suggests the system KV was reused.

        A hit is signalled when ms-per-input-token is below the threshold
        (< 0.5 ms/tok on CPU) — orders of magnitude faster than a cold fill.
        Returns False when data is unavailable (prefill_ms or input_tokens = 0).
        """
        if input_tokens <= 0 or prefill_ms <= 0:
            return False
        return (prefill_ms / input_tokens) < _CACHE_HIT_THRESHOLD_MS_PER_TOK
