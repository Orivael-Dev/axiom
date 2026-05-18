#!/usr/bin/env python3
"""End-to-end research-engine demo.

Pipeline (matches axiom_research.__doc__):

    query → LocalFilesRetriever(./docs) → QRF → Synthesizer(LLM) → signed ResearchReport

Run modes — pick by env var (no flags to keep the demo terse):

    AXIOM_RESEARCH_BACKEND=stub      no network, deterministic — default
    AXIOM_RESEARCH_BACKEND=ollama    POST to http://localhost:11434 (or OLLAMA_URL)
    AXIOM_RESEARCH_BACKEND=claude    POST to api.anthropic.com (needs ANTHROPIC_API_KEY)

Usage:

    export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')

    # No external deps — runs anywhere
    python3 examples/research_demo.py

    # On the Orin Nano (or laptop reaching it via Tailscale)
    OLLAMA_URL=http://localhost:11434 \
    AXIOM_RESEARCH_BACKEND=ollama \
    python3 examples/research_demo.py

    # Cloud
    ANTHROPIC_API_KEY=sk-... \
    AXIOM_RESEARCH_BACKEND=claude \
    python3 examples/research_demo.py

Prints the signed ResearchReport as JSON to stdout; verification
status to stderr. Both human-readable and pipe-into-jq friendly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from axiom_research import (
    ClaudeClient, LocalFilesRetriever, OllamaClient,
    ResearchEngine, StubLLMClient,
)


QUERIES = [
    ("Does the AXIOM event-token support selective activation?", "general"),
    ("How does the QRF probability band classify uncertainty?",  "general"),
]


def _pick_llm() -> "LLMClient":  # noqa: F821
    backend = os.environ.get("AXIOM_RESEARCH_BACKEND", "stub").lower()
    if backend == "ollama":
        return OllamaClient(
            model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
            host=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        )
    if backend == "claude":
        return ClaudeClient(
            model=os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        )
    return StubLLMClient(response=(
        "## Stub answer\n\n"
        "This is a deterministic stub. Set AXIOM_RESEARCH_BACKEND=ollama "
        "or =claude to see a real LLM-generated answer here. The pipeline "
        "around it (retrieve → QRF → synth → sign) ran for real.\n"
    ))


def main() -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set. Generate one:\n"
            "  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
        )

    llm = _pick_llm()
    print(f"  Using LLM: {llm.name}", file=sys.stderr)

    # Ground on the repo's docs/ directory so the demo cites real files
    retriever = LocalFilesRetriever(REPO_ROOT / "docs")
    engine = ResearchEngine(llm=llm, retriever=retriever, domain="general")

    for query, _domain in QUERIES:
        print(f"\n  ── {query}", file=sys.stderr)
        report = engine.run(query)
        print(report.to_json(indent=2))
        print(f"  verified: {report.verify()}", file=sys.stderr)
        print(f"  citations: {len(report.payload['citations'])}", file=sys.stderr)
        print(f"  top_branch: {report.payload['top_branch']}", file=sys.stderr)
        print(f"  probability_band: {report.payload['probability_band']}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
