"""SPLADE sparse-neural re-ranker — second pass over FTS5 hits.

FTS5 BM25 handles exact identifier queries (CVE-2021-44228) with perfect
precision. Free-text semantic queries ("what are the architectural implications
of JNDI injection?") are weaker for pure BM25 — the document may be relevant
but the exact query tokens may not appear.

SPLADE (Sparse Lexical and Expansion Model) closes this gap by running a tiny
sparse transformer that expands the query and document representations into a
high-dimensional sparse bag-of-terms, then re-ranks the FTS5 top-N by dot
product. It runs on CPU at ~10–50 ms/query, requires no GPU, and produces no
dense vector embeddings — consistent with the "zero-GPU, no-embedding-model"
appliance story from the FTS5 Technical Brief.

When the ``transformers`` package or the SPLADE model is unavailable, the
reranker degrades transparently to identity (returns hits in their original
FTS5 order). Production callers need no try/except — just wire it in and
the fallback is automatic.

Usage:
    from axiom_splade_reranker import SPLADEReranker
    from axiom_shard_router import ShardRouter

    reranker = SPLADEReranker()          # loads model lazily on first call
    router   = ShardRouter(shards=[...])
    hits     = router.query("JNDI injection architectural risk", reranker=reranker)

Model:
    Default: ``naver/splade-v3`` (HuggingFace Hub, ~100 MB, CPU-friendly)
    Override: set AXIOM_SPLADE_MODEL env var or pass ``model_name`` to __init__
    Offline:  copy model files to AXIOM_SPLADE_LOCAL_PATH — the loader checks
              the local path first so the appliance needs no internet access.
"""
from __future__ import annotations

import os
from typing import List, Optional

from axiom_research_retriever import RetrievedSource

_DEFAULT_MODEL = "naver/splade-v3"


class SPLADEReranker:
    """Two-pass re-ranker: FTS5 top-N → SPLADE sparse dot-product → re-ordered top-k.

    Parameters
    ----------
    model_name  : HuggingFace model id or local path (default naver/splade-v3)
    top_n       : number of FTS5 hits to feed into SPLADE (default 20)
    max_length  : tokenizer max_length (default 256 — fine for datasheets)
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        *,
        top_n:       int = 20,
        max_length:  int = 256,
    ) -> None:
        self._model_name = (
            model_name
            or os.environ.get("AXIOM_SPLADE_MODEL", "").strip()
            or _DEFAULT_MODEL
        )
        self._top_n      = top_n
        self._max_length = max_length
        self._tok        = None   # AutoTokenizer — lazy
        self._model      = None   # AutoModelForMaskedLM — lazy
        self._available  = None   # None=untried, True=ok, False=unavailable

    # ── public API ────────────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        hits: List[RetrievedSource],
        *,
        top_n: Optional[int] = None,
    ) -> List[RetrievedSource]:
        """Re-rank `hits` by SPLADE sparse dot-product score.

        Falls back to identity (original FTS5 order) if:
          - ``transformers`` is not installed
          - the SPLADE model is not available
          - any unexpected error occurs during inference

        Parameters
        ----------
        query  : the original user query
        hits   : FTS5 retrieval results (any order)
        top_n  : how many hits to consider for reranking (default: self.top_n)
        """
        if not hits:
            return hits
        if not self._ensure_model():
            return hits   # transparent fallback
        candidates = hits[: (top_n or self._top_n)]
        remainder  = hits[(top_n or self._top_n):]
        try:
            reranked = self._splade_rerank(query, candidates)
        except Exception:
            return hits   # any inference error → identity
        return reranked + remainder

    @property
    def available(self) -> bool:
        """True when the SPLADE model loaded successfully."""
        return bool(self._ensure_model())

    # ── model loading ─────────────────────────────────────────────────────────

    def _ensure_model(self) -> bool:
        if self._available is not None:
            return self._available
        self._available = self._try_load()
        return self._available

    def _try_load(self) -> bool:
        try:
            from transformers import AutoTokenizer, AutoModelForMaskedLM  # type: ignore
            import torch  # type: ignore
        except ImportError:
            return False
        try:
            local = os.environ.get("AXIOM_SPLADE_LOCAL_PATH", "").strip()
            src   = local or self._model_name
            self._tok   = AutoTokenizer.from_pretrained(src)
            self._model = AutoModelForMaskedLM.from_pretrained(src)
            self._model.eval()
            return True
        except Exception:
            return False

    # ── inference ─────────────────────────────────────────────────────────────

    def _encode(self, text: str):
        """Return a sparse SPLADE vector for `text` as a dict {term_id: weight}."""
        import torch
        inputs = self._tok(
            text,
            return_tensors="pt",
            max_length=self._max_length,
            truncation=True,
            padding=True,
        )
        with torch.no_grad():
            out  = self._model(**inputs)
            # SPLADE activation: log(1 + ReLU(logits)) max-pooled over tokens
            vecs = torch.log(1 + torch.relu(out.logits))
            vec  = vecs.max(dim=1).values.squeeze(0)
        # Convert to sparse dict — only non-zero entries
        nz    = vec.nonzero(as_tuple=True)[0]
        return {int(idx): float(vec[idx]) for idx in nz}

    def _dot(self, a: dict, b: dict) -> float:
        return sum(a.get(k, 0.0) * v for k, v in b.items())

    def _splade_rerank(
        self,
        query: str,
        hits: List[RetrievedSource],
    ) -> List[RetrievedSource]:
        q_vec = self._encode(query)
        scored: List[tuple[float, RetrievedSource]] = []
        for hit in hits:
            d_vec  = self._encode(hit.snippet)
            score  = self._dot(q_vec, d_vec)
            scored.append((score, hit))
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored]
