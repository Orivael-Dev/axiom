"""Axiom Research — signed multi-branch research engine.

Pipeline (signed end-to-end via the event-token Coordinator):

    user query
        │
        ▼
    [1] firewall /v1/guard/check          intent classifier — refuses unsafe queries
        │
        ▼
    [2] Retriever.retrieve(query)         top-K documents (local FS, web, axiom_files)
        │
        ▼
    [3] QRFEngine.forecast(query)         N weighted reasoning branches with probability weights
        │
        ▼
    [4] Synthesizer.synthesize(           LLM turns (query, docs, branches) into a written
            query, docs, branches)        report with inline citations
        │
        ▼
    [5] firewall /v1/guard/output         output-side classifier — catches hallucinations
        │
        ▼
    [6] EventToken Coordinator            wraps the whole thing in a signed event_token

This module owns steps 2–4 + 6 (the firewall calls in 1, 5 stay in the
firewall layer; callers wire them in themselves).

The synthesizer LLM client is pluggable — tests inject a deterministic
stub, the demo points at Ollama-on-Orin or Claude. Three concrete
LLM clients ship out of the box:

    OllamaClient    — talks to a local Ollama at http://localhost:11434
    ClaudeClient    — talks to the Anthropic API (requires ANTHROPIC_API_KEY)
    StubLLMClient   — deterministic synthetic responses for tests

Reuse hooks (already in the codebase):

    axiom_qrf.QRFEngine                  step [3] — probability-weighted branches
    axiom_event_token.Coordinator        step [6] — wraps everything in signed token
    axiom_signing.derive_key             namespaced HMAC root for axiom-research-v1
"""
from __future__ import annotations

from .engine import ResearchEngine, run_research
from .report import RESEARCH_KEY_NS, ResearchReport
from .retrieve import LocalFilesRetriever, RetrievedDoc, Retriever
from .synthesize import (
    ClaudeClient, LLMClient, OllamaClient, StubLLMClient, Synthesizer,
)

__all__ = [
    "ClaudeClient",
    "LLMClient",
    "LocalFilesRetriever",
    "OllamaClient",
    "RESEARCH_KEY_NS",
    "ResearchEngine",
    "ResearchReport",
    "RetrievedDoc",
    "Retriever",
    "StubLLMClient",
    "Synthesizer",
    "run_research",
]
