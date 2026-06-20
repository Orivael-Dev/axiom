"""Semantic Cosmos Model — layered BM25 retrieval.

Documents are tagged at three cosmos levels using the existing
``intent_type`` sidecar mechanism in ``axiom_research_retriever``:

  galaxy      — broad domain coverage (many diverse token families)
  planet      — dense concept cluster (high co-occurrence, medium length)
  star        — specific fact or constraint (short, focused vocabulary)
  constellation — recognisable reasoning pattern (reusable path)
  wormhole    — cross-domain analogy bridge
  void        — low-probability, unrelated semantic space (rejected)

``CosmosLayeredRetriever`` wraps ``LocalRetriever`` and runs three
sequential BM25 passes (galaxy → planet → star), with an optional
anticipatory warmup that fires the next-layer query in a background
thread while the caller processes the current result.

This is the experiment-first implementation.  It uses *only* the
existing ``intent_filter`` parameter on ``LocalRetriever.retrieve()``
— no changes to ``axiom_research_retriever`` are required.
"""
from __future__ import annotations

import json
import math
import sys
import time
import types as _types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ── module freeze (CANNOT_MUTATE) ────────────────────────────────────

def _module_setattr(self, name, value):
    raise AttributeError(f"CANNOT_MUTATE: {name} is immutable")

_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})

TRUST_LEVEL: int = 1

COSMOS_LEVELS: tuple = ("galaxy", "planet", "star", "constellation", "wormhole", "void")

# Vocabulary richness thresholds for auto-tagging.
# Designed for keyword-structured docs (MKB blocks, config entries, datasheets)
# not for natural prose (which tends to have high richness regardless of level).
_GALAXY_RICHNESS_FLOOR: float = 0.65   # unique_tokens/total > 0.65 → galaxy
_PLANET_RICHNESS_FLOOR: float = 0.13   # 0.13–0.65 → planet
# < 0.13 OR len < 60 tokens → star

# Reasoning-pattern keywords that nudge a doc toward "constellation"
_CONSTELLATION_KEYWORDS: frozenset = frozenset({
    "pattern", "heuristic", "guideline", "procedure", "protocol",
    "workflow", "reasoning", "approach", "methodology", "framework",
    "strategy", "algorithm", "decision", "policy", "rule",
})

# Cross-domain bridge keywords that nudge toward "wormhole"
_WORMHOLE_KEYWORDS: frozenset = frozenset({
    "analogy", "similar", "equivalent", "compared", "parallel",
    "bridge", "maps", "translates", "mirrors", "corresponds",
    "like the", "same as", "just as",
})

MKB_COSMOS_LEVEL: dict = {
    "SOVEREIGN":   "galaxy",
    "AGENT":       "planet",
    "SPEC":        "planet",
    "GUARD":       "star",
    "VALIDATOR":   "star",
    "REWARD":      "constellation",
}



# ── cosmos-level tagging ─────────────────────────────────────────────

def cosmos_tag_doc(content: str) -> str:
    """Assign a cosmos level to a document based on vocabulary statistics.

    Returns one of: 'galaxy', 'planet', 'star', 'constellation', 'wormhole'.

    Algorithm:
      1. Tokenise (simple lowercase alpha run, ≥3 chars).
      2. Check for constellation / wormhole keyword presence first
         (they override length-based heuristics).
      3. Short docs (< 60 tokens) → 'star'.
      4. richness = unique_tokens / total_tokens:
           > _GALAXY_RICHNESS_FLOOR  → 'galaxy'
           > _PLANET_RICHNESS_FLOOR  → 'planet'
           else                       → 'star'
    """
    import re
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z]{2,}", content)]
    if not tokens:
        return "star"

    token_set = set(tokens)

    # Wormhole / constellation override (check raw text, case-insensitive)
    content_lower = content.lower()
    wormhole_hits = sum(1 for kw in _WORMHOLE_KEYWORDS if kw in content_lower)
    constellation_hits = sum(1 for kw in _CONSTELLATION_KEYWORDS if kw in content_lower)

    if wormhole_hits >= 3:
        return "wormhole"
    if constellation_hits >= 4:
        return "constellation"

    n = len(tokens)
    if n < 60:
        return "star"

    richness = len(token_set) / n
    if richness > _GALAXY_RICHNESS_FLOOR:
        return "galaxy"
    if richness > _PLANET_RICHNESS_FLOOR:
        return "planet"
    return "star"


def mkb_to_cosmos_level(block_type: str) -> str:
    """Map a KnowledgeBlock block_type to a cosmos level string."""
    return MKB_COSMOS_LEVEL.get(block_type, "star")


def write_cosmos_meta(
    doc_path: Path,
    level: str,
    anchors: list[str] | None = None,
) -> None:
    """Write a .meta.json sidecar alongside doc_path so LocalRetriever
    picks up intent_type=level and the optional vocab_anchors list.
    """
    meta: dict = {"intent_type": level}
    if anchors:
        meta["vocab_anchors"] = list(anchors)
    sidecar = doc_path.with_name(doc_path.stem + ".meta.json")
    sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ── result dataclasses ───────────────────────────────────────────────

@dataclass
class PassResult:
    level: str
    hits: list
    latency_ms: float


@dataclass
class CosmosResult:
    query:       str
    galaxy_pass: PassResult
    planet_pass: PassResult
    star_pass:   PassResult
    anticipate:  bool
    total_latency_ms: float

    def all_hits(self) -> list:
        seen: set[str] = set()
        out = []
        for hit in (
            self.galaxy_pass.hits
            + self.planet_pass.hits
            + self.star_pass.hits
        ):
            if hit.uri not in seen:
                seen.add(hit.uri)
                out.append(hit)
        return out

    def level_counts(self) -> dict[str, int]:
        return {
            "galaxy":  len(self.galaxy_pass.hits),
            "planet":  len(self.planet_pass.hits),
            "star":    len(self.star_pass.hits),
        }


# ── layered retriever ────────────────────────────────────────────────

class CosmosLayeredRetriever:
    """3-pass BM25 retrieval (galaxy → planet → star) over a LocalRetriever.

    The underlying ``LocalRetriever`` must already be built with documents
    tagged via ``write_cosmos_meta()`` so that ``intent_filter`` filtering
    works correctly.

    Args:
        retriever: An already-built (or lazily-building) LocalRetriever
                   instance whose corpus has cosmos-level meta sidecars.
    """

    def __init__(self, retriever) -> None:
        self._r = retriever

    def retrieve_layered(
        self,
        query: str,
        *,
        k: int = 5,
        anticipate: bool = True,
    ) -> CosmosResult:
        """Run 3-pass cosmos retrieval with optional anticipatory warmup.

        When ``anticipate=True``, the star-level query is fired in a
        background thread as soon as the planet-level pass begins, so
        the star results are ready by the time we need them.

        Returns a CosmosResult with per-pass timings and results.
        """
        t_total = time.perf_counter()

        # Pass 1 — galaxy
        t0 = time.perf_counter()
        galaxy_hits = self._r.retrieve(query, intent_filter="galaxy", k=3)
        galaxy_ms = (time.perf_counter() - t0) * 1000

        # Pass 2 — planet  +  anticipatory star pre-fire
        if anticipate:
            executor = ThreadPoolExecutor(max_workers=1)
            star_future = executor.submit(
                self._r.retrieve, query, intent_filter="star", k=k
            )

        t0 = time.perf_counter()
        planet_hits = self._r.retrieve(query, intent_filter="planet", k=3)
        planet_ms = (time.perf_counter() - t0) * 1000

        # Pass 3 — star (collect pre-fired result or run now)
        t0 = time.perf_counter()
        if anticipate:
            star_hits = star_future.result()
            executor.shutdown(wait=False)
        else:
            star_hits = self._r.retrieve(query, intent_filter="star", k=k)
        star_ms = (time.perf_counter() - t0) * 1000

        total_ms = (time.perf_counter() - t_total) * 1000

        return CosmosResult(
            query=query,
            galaxy_pass=PassResult("galaxy", galaxy_hits, galaxy_ms),
            planet_pass=PassResult("planet", planet_hits, planet_ms),
            star_pass=PassResult("star",   star_hits,   star_ms),
            anticipate=anticipate,
            total_latency_ms=total_ms,
        )
