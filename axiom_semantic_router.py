"""Two-tiered semantic router for domain pack dispatch.

Routes queries to relevant installed domain packs using vocabulary anchor
overlap from chunk metadata sidecars. No LLM required — fast enough to run
on every query.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from axiom_domain_pack import DomainPackManifest, DomainPackStore

logger = logging.getLogger("axiom.semantic_router")


# ---------------------------------------------------------------------------
# Stop words and tokenisation
# ---------------------------------------------------------------------------

_ROUTER_STOP: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "have", "has", "had", "will", "would", "could",
    "should", "what", "which", "who", "how", "when", "where", "why",
    "to", "of", "in", "for", "on", "at", "by", "from", "with", "and",
    "or", "but", "not", "this", "that", "can", "may",
})


def _tokenize_query(query: str) -> frozenset[str]:
    """Tokenise a query string into a frozenset of meaningful lowercase tokens.

    Extracts tokens that start with a letter and are at least 3 characters
    long, then filters out stop words.
    """
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}', query.lower())
    return frozenset(t for t in tokens if t not in _ROUTER_STOP)


# ---------------------------------------------------------------------------
# Intent detection (Tier 2, rule-based)
# ---------------------------------------------------------------------------

def _detect_query_intent(query: str) -> str:
    """Lightweight rule-based intent detection for a query string.

    Uses the same signal patterns as DomainIngester._classify_intent() so
    that query intents are comparable to chunk intent_type labels.

    Returns one of: "definition" | "procedure" | "ruling" | "warning" |
    "specification" | "general"
    """
    lower = query.lower()

    definition_signals = (
        "means ", "is defined as", "refers to", "defined as",
        "is the process of", "what is", "what are", "definition of",
    )
    if any(sig in lower for sig in definition_signals):
        return "definition"

    procedure_signals = (
        "step 1", "step one", "first,", "then,", "how to",
        "in order to", "procedure:", "follow these", "instructions:",
        "how do", "how does",
    )
    if any(sig in lower for sig in procedure_signals):
        return "procedure"

    ruling_signals = (
        "held that", "the court", "ruled that", "decided that",
        "judgment", "verdict", "opinion of", "penalties for",
        "liability", "breach of",
    )
    if any(sig in lower for sig in ruling_signals):
        return "ruling"

    warning_signals = (
        "warning:", "caution:", "must not", "shall not",
        "prohibited", "danger:", "do not",
    )
    if any(sig in lower for sig in warning_signals):
        return "warning"

    if re.search(r'\d+\s*(MHz|GHz|KB|MB|GB|ms|ns|V|A|W|°C|%|±)', query):
        return "specification"

    # Additional query-oriented signals for specification
    spec_signals = ("pinout", "datasheet", "register", "schematic", "specifications", "specs")
    if any(sig in lower for sig in spec_signals):
        return "specification"

    return "general"


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------

def _jaccard(query_tokens: frozenset[str], anchor_set: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets.

    Returns 0.0 when either set is empty.
    """
    if not query_tokens or not anchor_set:
        return 0.0
    intersection = len(query_tokens & anchor_set)
    union = len(query_tokens | anchor_set)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# DomainVocabIndex
# ---------------------------------------------------------------------------

@dataclass
class DomainVocabIndex:
    """Vocabulary index for a single domain pack, built from chunk .meta.json sidecars."""

    domain: str
    pack_name: str
    anchor_set: frozenset[str]        # all unique anchor terms from all chunk sidecars
    intent_counts: Dict[str, int]     # intent_type -> chunk count
    total_chunks: int
    built_at: str                     # ISO timestamp


# ---------------------------------------------------------------------------
# SemanticRouter
# ---------------------------------------------------------------------------

class SemanticRouter:
    """Routes queries to relevant domain packs using vocabulary anchor overlap.

    Two-tier scoring:
    Tier 1 (always): Jaccard similarity between query tokens and domain
        anchor set.
    Tier 2 (optional): Boost score if query intent matches the domain's
        dominant intent type.
    """

    def __init__(
        self,
        store: "DomainPackStore",
        *,
        min_score: float = 0.01,
        intent_boost: float = 0.15,
        enable_intent_boost: bool = True,
    ) -> None:
        self._store = store
        self.min_score = min_score
        self.intent_boost = intent_boost
        self.enable_intent_boost = enable_intent_boost
        self._indexes: Optional[Dict[str, DomainVocabIndex]] = None

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def build_indexes(self) -> Dict[str, DomainVocabIndex]:
        """Scan all installed packs' index directories, read .meta.json sidecars,
        and build a DomainVocabIndex per pack.

        Results are cached in self._indexes; call refresh() to invalidate.
        Packs with no .meta.json sidecars (old-format packs) are silently
        skipped.
        """
        indexes: Dict[str, DomainVocabIndex] = {}

        for manifest in self._store.list_installed():
            index_dir = self._store.index_path(manifest)

            if not index_dir.exists() or not index_dir.is_dir():
                logger.debug(
                    "Pack %r: index dir %s not found — skipping",
                    manifest.name, index_dir,
                )
                continue

            sidecar_paths = list(index_dir.glob("*.meta.json"))
            if not sidecar_paths:
                logger.debug(
                    "Pack %r: no .meta.json sidecars in %s — skipping (old-format pack)",
                    manifest.name, index_dir,
                )
                continue

            all_anchors: set[str] = set()
            intent_counts: Dict[str, int] = {}
            total_chunks = 0

            for sidecar_path in sidecar_paths:
                try:
                    meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.debug("Could not read sidecar %s: %s", sidecar_path, exc)
                    continue

                anchors = meta.get("vocab_anchors", [])
                if isinstance(anchors, list):
                    all_anchors.update(str(a).lower() for a in anchors)

                intent = meta.get("intent_type", "general")
                intent_counts[intent] = intent_counts.get(intent, 0) + 1
                total_chunks += 1

            indexes[manifest.name] = DomainVocabIndex(
                domain=manifest.domain,
                pack_name=manifest.name,
                anchor_set=frozenset(all_anchors),
                intent_counts=intent_counts,
                total_chunks=total_chunks,
                built_at=_iso_now(),
            )
            logger.debug(
                "Built vocab index for %r: %d anchors, %d chunks",
                manifest.name, len(all_anchors), total_chunks,
            )

        self._indexes = indexes
        return indexes

    def _ensure_indexes(self) -> Dict[str, DomainVocabIndex]:
        """Return cached indexes, building them on first call."""
        if self._indexes is None:
            self.build_indexes()
        return self._indexes  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(
        self,
        query: str,
        index: DomainVocabIndex,
        *,
        intent_hint: Optional[str] = None,
    ) -> float:
        """Compute relevance score for a single domain.

        score = jaccard(query_tokens, domain_anchor_set)
                + intent_boost * (1 if intent matches domain's dominant intent else 0)

        Parameters
        ----------
        query:
            The raw query string.
        index:
            The DomainVocabIndex for the pack being scored.
        intent_hint:
            If provided, used directly as the query intent (skips Tier 2
            auto-detection).  Pass None to enable automatic detection.
        """
        query_tokens = _tokenize_query(query)
        tier1 = _jaccard(query_tokens, index.anchor_set)

        tier2_bonus = 0.0
        if self.enable_intent_boost and index.intent_counts:
            # Determine query intent
            if intent_hint is not None:
                query_intent = intent_hint
            else:
                query_intent = _detect_query_intent(query)

            # Dominant intent for this domain (highest chunk count)
            dominant_intent = max(index.intent_counts, key=lambda k: index.intent_counts[k])
            if query_intent == dominant_intent:
                tier2_bonus = self.intent_boost

        return tier1 + tier2_bonus

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(
        self,
        query: str,
        *,
        top_k: int = 2,
        intent_hint: Optional[str] = None,
    ) -> List["DomainPackManifest"]:
        """Return up to top_k packs ordered by relevance to the query.

        Returns all installed packs (ordered by score) when no packs score
        above min_score — this ensures graceful degradation when the router
        cannot decide.

        Parameters
        ----------
        query:
            The raw user query string.
        top_k:
            Maximum number of packs to return.
        intent_hint:
            Optional intent label to skip Tier 2 auto-detection
            (e.g. "ruling", "definition").
        """
        indexes = self._ensure_indexes()
        installed = self._store.list_installed()

        if not installed:
            return []

        # Score every installed pack
        scored: List[tuple[float, "DomainPackManifest"]] = []
        for manifest in installed:
            index = indexes.get(manifest.name)
            if index is None:
                # No sidecar data for this pack; score 0.0
                pack_score = 0.0
            else:
                pack_score = self.score(query, index, intent_hint=intent_hint)
            scored.append((pack_score, manifest))

        # Sort descending by score
        scored.sort(key=lambda t: t[0], reverse=True)

        # Check if any pack clears the minimum threshold
        above_threshold = [(s, m) for s, m in scored if s >= self.min_score]

        if not above_threshold:
            # Graceful degradation: return all packs in score order
            logger.debug(
                "route(%r): no packs above min_score=%.3f — returning all %d packs",
                query, self.min_score, len(installed),
            )
            return [m for _, m in scored]

        top = above_threshold[:top_k]
        logger.debug(
            "route(%r): top-%d matches: %s",
            query, top_k,
            [(m.name, f"{s:.4f}") for s, m in top],
        )
        return [m for _, m in top]

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def explain(self, query: str) -> Dict[str, float]:
        """Return {pack_name: score} for all installed packs.

        Useful for debugging and logging. Includes packs with zero scores.
        """
        indexes = self._ensure_indexes()
        result: Dict[str, float] = {}
        for manifest in self._store.list_installed():
            index = indexes.get(manifest.name)
            if index is None:
                result[manifest.name] = 0.0
            else:
                result[manifest.name] = self.score(query, index)
        return result

    def refresh(self) -> None:
        """Invalidate the index cache and re-scan pack index directories.

        Call this after installing or removing domain packs at runtime.
        """
        self._indexes = None
        self.build_indexes()
        logger.debug("SemanticRouter: index cache refreshed")


# ---------------------------------------------------------------------------
# from_env factory
# ---------------------------------------------------------------------------

def from_env() -> Optional["SemanticRouter"]:
    """Build a SemanticRouter from environment variables.

    Reads AXIOM_DOMAIN_STORE (path to the pack store base directory).
    Returns None when AXIOM_DOMAIN_STORE is not set.

    Used by research server startup code to lazily wire up the router.
    """
    store_path = os.environ.get("AXIOM_DOMAIN_STORE", "").strip()
    if not store_path:
        return None

    from axiom_domain_pack import DomainPackStore  # type: ignore[import]

    store = DomainPackStore(Path(store_path))
    return SemanticRouter(store)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_route(router: "SemanticRouter", query: str, top_k: int = 2) -> int:
    results = router.route(query, top_k=top_k)
    if not results:
        print("No domain packs installed.")
        return 0
    print(f"Query: {query!r}")
    print(f"Matched packs (top {top_k}):")
    for rank, m in enumerate(results, start=1):
        print(f"  {rank}. {m.name:<32} [{m.domain}]  {m.title}")
    return 0


def _cli_explain(router: "SemanticRouter", query: str) -> int:
    scores = router.explain(query)
    if not scores:
        print("No domain packs installed.")
        return 0
    print(f"Query: {query!r}")
    print(f"{'PACK':<32} {'SCORE':>8}")
    print("-" * 42)
    for name, s in sorted(scores.items(), key=lambda t: t[1], reverse=True):
        print(f"{name:<32} {s:>8.4f}")
    return 0


def _cli_list_domains(router: "SemanticRouter") -> int:
    indexes = router.build_indexes()
    installed = router._store.list_installed()
    if not installed:
        print("No domain packs installed.")
        return 0
    print(f"{'PACK':<32} {'DOMAIN':<16} {'CHUNKS':>7}  {'ANCHORS':>8}  DOMINANT INTENT")
    print("-" * 80)
    for m in installed:
        idx = indexes.get(m.name)
        if idx is None:
            print(f"{m.name:<32} {m.domain:<16} {'—':>7}  {'—':>8}  (no sidecars)")
        else:
            dominant = (
                max(idx.intent_counts, key=lambda k: idx.intent_counts[k])
                if idx.intent_counts else "—"
            )
            print(
                f"{m.name:<32} {m.domain:<16} {idx.total_chunks:>7}  "
                f"{len(idx.anchor_set):>8}  {dominant}"
            )
    return 0


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="axiom_semantic_router",
        description="Two-tiered semantic router for domain pack dispatch",
    )
    ap.add_argument(
        "--store",
        default=None,
        metavar="DIR",
        help="Pack store directory (overrides AXIOM_DOMAIN_STORE env var)",
    )
    ap.add_argument(
        "--top-k",
        type=int,
        default=2,
        metavar="N",
        help="Maximum packs to return for 'route' (default: 2)",
    )
    sub = ap.add_subparsers(dest="cmd")

    p_route = sub.add_parser("route", help="Route a query to the most relevant packs")
    p_route.add_argument("query", help="Query string to route")

    p_explain = sub.add_parser(
        "explain",
        help="Show scores for all installed packs (debugging)",
    )
    p_explain.add_argument("query", help="Query string to score")

    sub.add_parser("list-domains", help="List all installed packs with vocab index stats")

    args = ap.parse_args(argv)

    # Resolve store path
    from axiom_domain_pack import DomainPackStore  # type: ignore[import]

    if args.store:
        store = DomainPackStore(Path(args.store).expanduser().resolve())
    else:
        env_path = os.environ.get("AXIOM_DOMAIN_STORE", "").strip()
        store = DomainPackStore(Path(env_path) if env_path else None)

    router = SemanticRouter(store)

    if args.cmd == "route":
        return _cli_route(router, args.query, top_k=args.top_k)
    elif args.cmd == "explain":
        return _cli_explain(router, args.query)
    elif args.cmd == "list-domains":
        return _cli_list_domains(router)
    else:
        ap.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
