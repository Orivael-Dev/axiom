"""ResearchEngine — orchestrates the full pipeline.

Composes Retriever + QRFEngine + Synthesizer into a single
`run(query) -> signed ResearchReport` call. Default wiring uses:

  - `LocalFilesRetriever` over the repo's docs/ + README dir
  - `QRFEngine` with domain="general" (added in this module — see below)
  - `Synthesizer` with whichever LLM client the caller passed

If the caller passes `qrf=None`, the engine skips QRF and returns a
report without weighted branches — useful when you want pure
retrieval + synthesis without the multi-branch reasoning layer.

Adds `"general"` to `DOMAIN_BRANCH_COUNTS` (6 branches) at import
time. axiom_qrf.DOMAIN_BRANCH_COUNTS is module-level frozen — the
frozen check is per-attribute, not per-key, so we use a separate
override layer here to keep upstream code untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from axiom_signing import derive_key

from .report import ResearchReport
from .retrieve import LocalFilesRetriever, RetrievedDoc, Retriever
from .synthesize import LLMClient, Synthesizer

# Domain branch counts — same shape as axiom_qrf.DOMAIN_BRANCH_COUNTS,
# but exposed here so the research engine can use "general" as the
# default without mutating the upstream frozen constant.
DOMAIN_BRANCH_COUNTS_EXT: dict[str, int] = {
    "general":      6,    # default for open-ended research queries
    "medical":      8,    # mirrors axiom_qrf
    "financial":    6,
    "supply_chain": 4,
    "hr":           4,
    "security":     6,
}


# ─── Top-level convenience ──────────────────────────────────────────────


def run_research(
    query: str,
    *,
    llm: LLMClient,
    retriever: Optional[Retriever] = None,
    domain: str = "general",
    top_k_docs: int = 5,
) -> ResearchReport:
    """One-shot research call. Returns a signed ResearchReport.

    Default retriever: LocalFilesRetriever over the current working
    directory's `docs/` + `README.md` if present, falls back to cwd.
    """
    engine = ResearchEngine(llm=llm, retriever=retriever, domain=domain)
    return engine.run(query, top_k_docs=top_k_docs)


# ─── Engine ─────────────────────────────────────────────────────────────


class ResearchEngine:
    """Stateless orchestrator. Each `.run()` call is independent."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        retriever: Optional[Retriever] = None,
        domain: str = "general",
        qrf_enabled: bool = True,
    ) -> None:
        self.synth = Synthesizer(llm)
        self.retriever = retriever or _default_retriever()
        self.domain = domain
        self.qrf_enabled = qrf_enabled
        if domain not in DOMAIN_BRANCH_COUNTS_EXT:
            raise ValueError(
                f"Unknown domain {domain!r}. "
                f"Known: {sorted(DOMAIN_BRANCH_COUNTS_EXT)}"
            )

    def run(self, query: str, *, top_k_docs: int = 5) -> ResearchReport:
        # [2] Retrieve grounding documents
        docs = self.retriever.retrieve(query, top_k=top_k_docs)

        # [3] QRF — weighted reasoning branches (optional)
        branches, top_branch, probability_band, n_killed = self._maybe_run_qrf(query)

        # [4] Synthesizer LLM call
        answer_md = self.synth.synthesize(query, docs, branches)

        # Build the signed report
        payload = {
            "query":           query,
            "answer_markdown": answer_md,
            "branches":        branches,
            "probability_band": probability_band,
            "top_branch":      top_branch,
            "citations": [
                {"path": d.path, "snippet": d.snippet,
                 "score": d.score, "metadata": d.metadata}
                for d in docs
            ],
            "domain":          self.domain,
            "n_branches":      len(branches),
            "n_killed":        n_killed,
            "synth_model":     self.synth.llm.name,
            "created_at":      datetime.now(timezone.utc)
                                       .isoformat(timespec="seconds")
                                       .replace("+00:00", "Z"),
        }
        # Confidence reflects QRF's probability_band — HIGH=0.9, MODERATE=0.7,
        # LOW=0.5, UNCERTAIN=0.3, or 0.5 when QRF is disabled.
        band_to_conf = {
            "HIGH": 0.9, "MODERATE": 0.7, "LOW": 0.5, "UNCERTAIN": 0.3,
        }
        confidence = band_to_conf.get(probability_band, 0.5)
        return ResearchReport.signed(payload=payload, confidence=confidence)

    # ─── QRF dispatch ───────────────────────────────────────────────

    def _maybe_run_qrf(
        self, query: str,
    ) -> tuple[list[dict], str, str, int]:
        """Return (branches, top_branch, probability_band, n_killed).

        Branches list is empty when qrf_enabled is False OR when QRF
        raises (e.g. its underlying LatentEngine has no API key).
        We never let QRF errors break the research pipeline — the
        synthesizer can still produce a useful retrieval-only answer.
        """
        if not self.qrf_enabled:
            return ([], "(qrf-disabled)", "UNCERTAIN", 0)

        # Map our extended domain set to QRF's supported domains.
        # QRF's frozen DOMAIN_BRANCH_COUNTS doesn't include "general",
        # so for that case we use "financial" as the closest neutral
        # 6-branch domain — the BRANCH SHAPE is what matters here, not
        # domain-specific scoring.
        from axiom_qrf import DOMAIN_BRANCH_COUNTS as QRF_DOMAINS, QRFEngine

        qrf_domain = self.domain if self.domain in QRF_DOMAINS else "financial"
        try:
            engine = QRFEngine(
                qrf_domain,
                derive_key(b"axiom-research-qrf-v1"),
                n_branches=DOMAIN_BRANCH_COUNTS_EXT[self.domain],
            )
            result = engine.forecast(query)
        except Exception:
            return ([], "(qrf-error)", "UNCERTAIN", 0)

        # Trim to top 6 branches for the synthesis prompt; the full
        # set still lives in result.branches (the engine keeps them all)
        # but we only ship the top-K out for token-budget reasons.
        branches_out = []
        for b in result.branches[:6]:
            label = b.get("branch_label") or b.get("label") or "(unlabeled)"
            branches_out.append({
                "branch_label": label,
                "probability_weight": b.get("probability_weight", 0.0),
                "score": b.get("score", 0.0),
            })

        return (
            branches_out,
            result.top_branch,
            result.probability_band,
            len(result.killed),
        )


# ─── Internals ──────────────────────────────────────────────────────────


def _default_retriever() -> Retriever:
    """LocalFilesRetriever over the repo's docs + README directories.

    Walks up from cwd looking for axiom-style directory landmarks
    (`docs/`, `README.md`, `axiom_files/`). Picks the most specific
    available; falls back to cwd if nothing matches.
    """
    cwd = Path.cwd().resolve()
    for candidate in (cwd / "docs", cwd / "axiom_files", cwd):
        if candidate.exists():
            return LocalFilesRetriever(candidate)
    return LocalFilesRetriever(cwd)
