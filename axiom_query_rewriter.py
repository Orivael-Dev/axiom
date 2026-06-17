"""Latent-reasoning query rewriter for FTS5/BM25 retrieval.

Addresses the semantic gap in lexical retrieval: correct passages exist but the
question uses different vocabulary than the passage.  A small LLM rewrites the
question into 3 alternative phrasings tuned to the target domain's vocabulary,
then all token variants are OR-joined into a single FTS5 MATCH expression.

The rewriter is:
  - Backend-agnostic: works with any SLMBackend (Ollama, NIM, DeepSeek, Custom)
  - Latency-bounded: max_output_tokens=120, typical Qwen 0.5B latency ~80–150 ms
  - Fail-safe: any exception falls back to the original query unchanged
  - Domain-pluggable: system prompt is passed in; a legal prompt differs from OBD

HyDE mode (Hypothetical Document Embeddings):
    rewrite_hyde() implements the Forward Reverse Pipeline from the Orivael RAG paper:
        User Query → LLM generates a "Fake" Textbook Answer → Dense Vector Search → Real Statement
    Instead of expanding the query into BM25 search terms, it generates a complete
    hypothetical answer in domain vocabulary for use with a dense encoder.  Even if
    the LLM hallucinates facts, the vocabulary will be domain-correct, steering the
    embedding toward the right knowledge cluster.  Use rewrite_with_hyde() to obtain
    both retrieval signals in a single call.

Usage (standalone):
    from axiom_query_rewriter import QueryRewriter
    from axiom_event_token.backends import LocalNanoBackend

    rewriter = QueryRewriter(LocalNanoBackend())
    expanded = rewriter.rewrite(
        "What must a plaintiff prove for wrongful termination?",
        domain="legal",
    )
    # expanded → FTS5 MATCH string: '"plaintiff" OR "claimant" OR "prove" OR ...'

Usage (HyDE / dense retrieval):
    rewriter = QueryRewriter(LocalNanoBackend())
    hyde_text = rewriter.rewrite_hyde(
        "What must a plaintiff prove for wrongful termination?",
        domain="legal",
    )
    # hyde_text → prose paragraph in legal vocabulary; pass to a dense encoder

Usage (combined BM25 + HyDE):
    bm25_expr, hyde_text = rewriter.rewrite_with_hyde(question, domain="legal")

Usage (in benchmark):
    from axiom_query_rewriter import QueryRewriter, LEGAL_SYSTEM_PROMPT
    rewriter = QueryRewriter(backend, system_prompt=LEGAL_SYSTEM_PROMPT)
    match_expr = rewriter.rewrite(question, domain="legal")
    # pass match_expr directly to conn.execute("... WHERE cve MATCH ?", (match_expr, k))

Usage (in research server):
    Set AXIOM_QUERY_REWRITE=legal (or "obd", "medical") to enable automatically.
    The server calls rewriter.rewrite(query) before shard_router.query().
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Shared alphanumeric tokeniser — same pattern as axiom_cve_retriever._TOKEN_RE
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Stop-words that add noise to an OR-expanded FTS5 query
_STOP = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "at", "by", "from", "with", "and", "or", "but", "not", "no",
    "if", "as", "it", "its", "this", "that", "these", "those", "what",
    "which", "who", "whom", "how", "when", "where", "why",
})

# ── Domain-specific system prompts ───────────────────────────────────────────

LEGAL_SYSTEM_PROMPT = (
    "You are a legal vocabulary expert. "
    "Given a legal question, produce exactly 3 short alternative phrasings "
    "that use the precise vocabulary found in court opinions and statutes — "
    "e.g. 'claimant' instead of 'plaintiff', 'dismissed' instead of 'fired', "
    "'damages' instead of 'money'. "
    "Output ONLY the 3 phrasings, one per line, no numbers or bullets."
)

OBD_SYSTEM_PROMPT = (
    "You are an automotive diagnostics expert. "
    "Given a fault query, produce exactly 3 short alternative phrasings "
    "using OBD-II diagnostic code vocabulary, SAE J1979 terms, and "
    "manufacturer-specific terminology. "
    "Output ONLY the 3 phrasings, one per line, no numbers or bullets."
)

MEDICAL_SYSTEM_PROMPT = (
    "You are a clinical terminology expert. "
    "Given a medical question, produce exactly 3 short alternative phrasings "
    "using ICD-10, SNOMED CT, and clinical note vocabulary. "
    "Output ONLY the 3 phrasings, one per line, no numbers or bullets."
)

GENERAL_SYSTEM_PROMPT = (
    "You are a retrieval expert. "
    "Given a question, produce exactly 3 short alternative phrasings "
    "that use different but synonymous vocabulary likely to appear in a "
    "technical knowledge base. "
    "Output ONLY the 3 phrasings, one per line, no numbers or bullets."
)

_DOMAIN_PROMPTS = {
    "legal":   LEGAL_SYSTEM_PROMPT,
    "obd":     OBD_SYSTEM_PROMPT,
    "medical": MEDICAL_SYSTEM_PROMPT,
}

# ── HyDE prompts (generate hypothetical answer, not alternative phrasings) ──

HYDE_LEGAL_PROMPT = (
    "You are a legal reference author. "
    "Given a legal question, write a dense 2-3 sentence paragraph that a legal textbook "
    "or court opinion would contain in answer to this question. "
    "Use precise legal vocabulary: statutes, doctrines, case law terminology. "
    "Write as factual reference text, not as a question. "
    "Output ONLY the hypothetical answer paragraph, nothing else."
)

HYDE_MEDICAL_PROMPT = (
    "You are a clinical reference author. "
    "Given a medical question, write a dense 2-3 sentence paragraph from a clinical "
    "reference manual that answers this question. "
    "Use ICD-10 codes, SNOMED CT terms, and clinical nomenclature where appropriate. "
    "Output ONLY the hypothetical answer paragraph, nothing else."
)

HYDE_OBD_PROMPT = (
    "You are an automotive diagnostics reference author. "
    "Given a diagnostic query, write a dense 2-3 sentence paragraph from an OBD-II "
    "service manual that addresses this fault. "
    "Use SAE J1979 terms, DTC codes, and manufacturer diagnostic vocabulary. "
    "Output ONLY the hypothetical answer paragraph, nothing else."
)

HYDE_GENERAL_PROMPT = (
    "You are a technical reference author. "
    "Given a question, write a dense 2-3 sentence paragraph that would appear in a "
    "technical reference document or textbook answering this question. "
    "Use precise domain vocabulary and specific technical terminology. "
    "Output ONLY the hypothetical answer paragraph, nothing else."
)

_HYDE_PROMPTS: Dict[str, str] = {
    "legal":    HYDE_LEGAL_PROMPT,
    "medical":  HYDE_MEDICAL_PROMPT,
    "obd":      HYDE_OBD_PROMPT,
    "general":  HYDE_GENERAL_PROMPT,
}


# ── Core rewriter ─────────────────────────────────────────────────────────────

class QueryRewriter:
    """LLM-powered query expansion for FTS5/BM25 retrieval.

    Parameters
    ----------
    backend       : any SLMBackend (LocalNanoBackend, NIMBackend, etc.)
    system_prompt : override the system prompt; if None, chosen by domain arg to rewrite()
    max_tokens    : upper bound on SLM output (keep small — we just want 3 phrases)
    timeout_s     : per-call timeout passed to backend.generate()
    """

    def __init__(
        self,
        backend,
        *,
        system_prompt: Optional[str] = None,
        max_tokens: int = 120,
        timeout_s: float = 30.0,
    ) -> None:
        self._backend       = backend
        self._system_prompt = system_prompt
        self._max_tokens    = max_tokens
        self._timeout_s     = timeout_s

    # ── private helpers ───────────────────────────────────────────────────────

    def _call_backend(self, prompt: str, *, max_tokens: Optional[int] = None) -> str:
        """Invoke the backend and return the raw text response.

        Parameters
        ----------
        prompt     : full prompt string (system + user content combined)
        max_tokens : token cap for this call; defaults to self._max_tokens

        Returns
        -------
        Raw text string from the backend.  Raises on any backend error —
        callers are responsible for exception handling.
        """
        tokens = max_tokens if max_tokens is not None else self._max_tokens
        result = self._backend.generate(
            system="",
            prompt=prompt,
            max_output_tokens=tokens,
            timeout_s=self._timeout_s,
        )
        return result.text

    # ── public API ────────────────────────────────────────────────────────────

    def rewrite(
        self,
        question: str,
        *,
        domain: str = "general",
    ) -> str:
        """Return an FTS5 MATCH expression that OR-joins all variant tokens.

        On any SLM error or empty output falls back to tokenising the original
        question — retrieval is never worse than plain BM25.

        Parameters
        ----------
        question : the raw user question
        domain   : "legal" | "obd" | "medical" | "general" — picks system prompt
                   if no override was passed at construction time

        Returns
        -------
        FTS5 MATCH expression string, e.g.:
            '"plaintiff" OR "claimant" OR "petitioner" OR "prove" OR ...'
        """
        system = self._system_prompt or _DOMAIN_PROMPTS.get(domain, GENERAL_SYSTEM_PROMPT)
        try:
            result = self._backend.generate(
                system=system,
                prompt=question,
                max_output_tokens=self._max_tokens,
                timeout_s=self._timeout_s,
            )
            variants = _parse_variants(result.text)
        except Exception:
            variants = []

        # Always include the original question tokens as baseline
        all_texts = [question] + variants
        return _build_fts5_match(all_texts)

    def rewrite_variants(
        self,
        question: str,
        *,
        domain: str = "general",
    ) -> List[str]:
        """Return raw variant strings (original + SLM output) for inspection."""
        system = self._system_prompt or _DOMAIN_PROMPTS.get(domain, GENERAL_SYSTEM_PROMPT)
        try:
            result = self._backend.generate(
                system=system,
                prompt=question,
                max_output_tokens=self._max_tokens,
                timeout_s=self._timeout_s,
            )
            variants = _parse_variants(result.text)
        except Exception:
            variants = []
        return [question] + variants

    def rewrite_hyde(
        self,
        question: str,
        *,
        domain: str = "general",
    ) -> str:
        """Generate a hypothetical textbook answer for dense-embedding retrieval (HyDE).

        Unlike rewrite() which returns a BM25 MATCH expression, this returns a
        full prose paragraph in domain vocabulary. The caller passes this to a
        dense encoder (not FTS5) to retrieve semantically similar passages.

        Falls back to the original question on any error (same fail-safe as rewrite()).

        Parameters
        ----------
        question : The user's raw question.
        domain   : "legal" | "medical" | "obd" | "general" (default)

        Returns
        -------
        A 2-3 sentence hypothetical answer in domain vocabulary, or the original
        question unchanged if the backend call fails.
        """
        system = _HYDE_PROMPTS.get(domain, HYDE_GENERAL_PROMPT)
        prompt = f"{system}\n\nQuestion: {question}\n\nHypothetical answer:"
        try:
            result = self._call_backend(prompt, max_tokens=200)
            if result and len(result.strip()) > 20:
                return result.strip()
        except Exception:
            pass
        return question

    def rewrite_with_hyde(
        self,
        question: str,
        *,
        domain: str = "general",
    ) -> Tuple[str, str]:
        """Return both the BM25 MATCH expression and the HyDE hypothetical answer.

        Convenience method for pipelines that use both retrieval modes in parallel.

        Returns
        -------
        Tuple of (bm25_match_expr, hyde_answer)
        """
        bm25 = self.rewrite(question, domain=domain)
        hyde = self.rewrite_hyde(question, domain=domain)
        return bm25, hyde


# ── Parsing + FTS5 expression builder ────────────────────────────────────────

def _parse_variants(text: str) -> List[str]:
    """Extract non-empty lines from SLM output as query variants."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        # Strip leading numbering/bullets the model might add despite instructions
        line = re.sub(r"^[\d]+[.)]\s*", "", line)
        line = re.sub(r"^[-*•]\s*", "", line)
        if line:
            lines.append(line)
    return lines[:4]   # cap at 4 to bound FTS5 expression size


def _build_fts5_match(texts: List[str]) -> str:
    """Union all tokens from all texts into a single OR FTS5 MATCH expression.

    Tokens are deduplicated (case-insensitive) and stop-words are removed.
    Short tokens (≤2 chars) are also dropped to avoid noise.
    """
    seen: set = set()
    tokens: List[str] = []
    for text in texts:
        for tok in _TOKEN_RE.findall(text):
            key = tok.lower()
            if key in _STOP or len(key) <= 2 or key in seen:
                continue
            seen.add(key)
            tokens.append(tok)

    if not tokens:
        # Absolute fallback: tokenise without filtering
        tokens = list({t for text in texts for t in _TOKEN_RE.findall(text)})

    return " OR ".join(f'"{t}"' for t in tokens)


# ── Factory ───────────────────────────────────────────────────────────────────

def from_env(domain: str = "general") -> Optional["QueryRewriter"]:
    """Build a QueryRewriter from environment variables.

    Returns None when AXIOM_QUERY_REWRITE is unset or the backend is
    unavailable — callers degrade gracefully to plain BM25.

    Env vars:
        AXIOM_QUERY_REWRITE   : domain name to enable (e.g. "legal") or "1"/"true"
        AXIOM_BACKEND / ...   : backend selection (same vars as the rest of Axiom)
    """
    import os
    val = os.environ.get("AXIOM_QUERY_REWRITE", "").strip().lower()
    if not val or val in ("0", "false", "off"):
        return None

    rewrite_domain = val if val not in ("1", "true", "on") else domain

    try:
        from axiom_event_token.backends import default_backend
        backend = default_backend()
        return QueryRewriter(backend, system_prompt=_DOMAIN_PROMPTS.get(rewrite_domain))
    except Exception:
        return None
