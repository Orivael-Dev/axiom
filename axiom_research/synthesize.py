"""Synthesizer — LLM turns (query, docs, branches) into a Markdown report.

The LLMClient interface is intentionally tiny — one method,
`generate(prompt) -> str` — so it's easy to swap implementations.
Three concrete clients ship:

  OllamaClient    — POST to http://<host>:11434/api/generate. Default
                    matches the Sovereign-Box / Orin-Nano dev setup.
  ClaudeClient    — POST to api.anthropic.com via the anthropic SDK.
                    Requires ANTHROPIC_API_KEY env var.
  StubLLMClient   — deterministic synthetic output for tests; never
                    touches the network.

Synthesizer composes the final prompt and asks the LLM to produce a
Markdown report with citation markers `[doc_N]` mapping to retrieved
documents. The prompt is canonical — same wording every call — so
behavior is comparable across LLM backends.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from .retrieve import RetrievedDoc


class LLMClient(Protocol):
    """Any LLM that can take a prompt and return text."""

    name: str

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        ...


# ─── OllamaClient ───────────────────────────────────────────────────────


class OllamaClient:
    """Talks to a local or LAN-reachable Ollama server.

    Defaults match the Orin Nano dev setup: model `llama3.2:3b`, host
    `http://localhost:11434`. Pass `host="http://orin.tailnet.ts.net:11434"`
    (or your Tailscale IP) to call the Orin from your laptop.
    """

    def __init__(
        self,
        model: str = "llama3.2:3b",
        host: str = "http://localhost:11434",
        temperature: float = 0.3,
        timeout_s: int = 120,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.name = f"ollama/{model}"

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        import urllib.error
        import urllib.request

        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": max_tokens,
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama call failed: {e}") from e
        return json.loads(body).get("response", "")


# ─── ClaudeClient ───────────────────────────────────────────────────────


class ClaudeClient:
    """Talks to the Anthropic API.

    Loaded lazily so the `anthropic` SDK isn't required to import this
    module. Falls back to a clear error message if the SDK is missing.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: str | None = None,
        temperature: float = 0.3,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.temperature = temperature
        self.name = f"anthropic/{model}"

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — required for ClaudeClient. "
                "Use OllamaClient or StubLLMClient if you don't have one."
            )
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed; pip install anthropic"
            ) from e
        client = anthropic.Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text


# ─── StubLLMClient (test fixture) ───────────────────────────────────────


@dataclass
class StubLLMClient:
    """Deterministic LLM stub for tests. Never touches the network.

    Returns `response` verbatim, OR if `respond_with` is set, calls it
    with the prompt to compute the response dynamically (lets tests
    assert on the exact prompt content).
    """
    response: str = (
        "## Stub answer\n\nThis is a deterministic stub response used "
        "for testing. The real synthesizer would produce a written "
        "report here grounded in [doc_0] and the QRF branches.\n"
    )
    respond_with: "callable | None" = None
    name: str = "stub/test"
    last_prompt: str = ""

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        self.last_prompt = prompt
        if self.respond_with is not None:
            return self.respond_with(prompt)
        return self.response


# ─── Synthesizer ────────────────────────────────────────────────────────


SYSTEM_PROMPT = (
    "You are a research analyst writing a concise, well-cited report.\n"
    "Use Markdown. Cite every factual claim with [doc_N] referring to "
    "the indexed source documents below.\n"
    "If the documents don't support a claim, do NOT invent one — say "
    "the evidence is mixed or absent.\n"
    "Output ONLY the report body; no preamble, no sign-off.\n"
)


class Synthesizer:
    """Composes the LLM prompt and asks for a Markdown report.

    Stateless across calls — same (query, docs, branches) input yields
    the same prompt. Token budgets are intentional: total prompt stays
    under ~6 KB so even small LLMs (Phi-3-mini, Llama-3.2-3B) have
    room to respond meaningfully.
    """

    MAX_DOCS_IN_PROMPT = 5
    MAX_BRANCHES_IN_PROMPT = 6
    MAX_SNIPPET_CHARS = 400

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def synthesize(
        self,
        query: str,
        docs: list[RetrievedDoc],
        branches: list[dict],
        *,
        max_tokens: int = 1024,
    ) -> str:
        prompt = self._build_prompt(query, docs, branches)
        return self.llm.generate(prompt, max_tokens=max_tokens)

    def _build_prompt(
        self,
        query: str,
        docs: list[RetrievedDoc],
        branches: list[dict],
    ) -> str:
        parts = [SYSTEM_PROMPT, "", "## Query", query, ""]

        parts.append("## Source documents")
        if not docs:
            parts.append("(No documents retrieved — answer ONLY if you "
                         "can refuse safely. Otherwise explain that no "
                         "grounding evidence was available.)")
        for i, d in enumerate(docs[:self.MAX_DOCS_IN_PROMPT]):
            snip = d.snippet[:self.MAX_SNIPPET_CHARS]
            parts.append(f"[doc_{i}] {d.path}  (score={d.score})")
            parts.append(f"  {snip}")
        parts.append("")

        parts.append("## Reasoning branches (QRF probability forecast)")
        for b in branches[:self.MAX_BRANCHES_IN_PROMPT]:
            label = b.get("branch_label", b.get("label", "(unlabeled)"))
            weight = b.get("probability_weight", 0.0)
            parts.append(f"  - {label}  (weight={weight:.3f})")
        parts.append("")

        parts.append(
            "Write a 3-5 paragraph Markdown report answering the query. "
            "Cite each factual claim with [doc_N]. End with a one-line "
            "'## Confidence' section summarizing whether the evidence "
            "strongly supports one branch or is split."
        )
        return "\n".join(parts)
