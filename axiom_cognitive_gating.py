"""Cognitive gating pipeline — single entry point that ties the four RAG
gating layers into one retrieval + context flow.

The individual pieces already exist as standalone modules:
  - axiom_semantic_router     : route a query to the relevant domain pack(s)
  - axiom_domain_pack         : installed-pack store + per-pack index paths
  - axiom_research_retriever  : BM25 with intent_filter on chunk content type
  - axiom_query_rewriter      : HyDE query-to-hypothetical expansion
  - axiom_knowledge_cookie    : cross-session fragment store + hot-knowledge

This module wires them together so a caller does one thing — `retrieve()` —
and gets routing, intent filtering, optional HyDE expansion, and automatic
knowledge-cookie recording, plus an `extra_context()` block of promoted hot
knowledge ready to inject into an LLM call.

Pipeline (each stage degrades gracefully if its layer is absent):

    query
      │
      ├─ SemanticRouter.route()         → pick domain pack(s)   [if domain store]
      ├─ _detect_query_intent()         → intent_filter          [always cheap]
      ├─ QueryRewriter.rewrite_hyde()   → expand to dense answer  [if AXIOM_HYDE]
      ├─ LocalRetriever.retrieve(...)   → BM25 over routed index
      └─ KnowledgeCookieStore.record_hit() per hit  → promotion   [if cookie]

    extra_context()  → {"hot_knowledge": "..."} from promoted fragments

Environment wiring (all optional; each layer is independent):
    AXIOM_DOMAIN_STORE      path to installed domain-pack store (enables routing)
    AXIOM_KNOWLEDGE_COOKIE  path to knowledge cookie (enables hit recording)
    AXIOM_QUERY_REWRITE     domain for the HyDE/rewrite backend (legal|medical|obd|general)
    AXIOM_HYDE              "1" to expand queries via rewrite_hyde() before retrieval
    AXIOM_GATING_INTENT     "0" to disable intent filtering (default on)

Usage:
    from axiom_cognitive_gating import CognitiveGatingPipeline

    gating = CognitiveGatingPipeline.from_env()
    sources, telemetry = gating.retrieve("how do I reduce quantization error?", k=5)
    ctx = gating.extra_context()        # inject into the LLM call
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger("axiom.cognitive_gating")


# ── telemetry ──────────────────────────────────────────────────────────────────

@dataclass
class GatingTelemetry:
    """What the pipeline did for one retrieve() call — for logging / UI / tests."""
    routed_to: str = ""                 # domain pack chosen by the router ("" = none)
    intent_filter: str = ""             # query content-type detected ("" = unfiltered)
    hyde_used: bool = False             # query was HyDE-expanded
    hyde_source: str = ""               # "live-backend" | "fallback-original" | ""
    effective_query: str = ""           # the query string actually sent to retrieval
    candidates_before_intent: int = 0   # hits before intent filtering
    hits_returned: int = 0
    fragments_recorded: int = 0         # knowledge-cookie record_hit calls
    layers_active: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "routed_to": self.routed_to,
            "intent_filter": self.intent_filter,
            "hyde_used": self.hyde_used,
            "hyde_source": self.hyde_source,
            "effective_query": self.effective_query[:200],
            "candidates_before_intent": self.candidates_before_intent,
            "hits_returned": self.hits_returned,
            "fragments_recorded": self.fragments_recorded,
            "layers_active": list(self.layers_active),
        }


# ── pipeline ─────────────────────────────────────────────────────────────────

class CognitiveGatingPipeline:
    """Facade over the four gating layers.

    All collaborators are optional; pass None to disable a layer.  ``from_env()``
    builds the default wiring from environment variables.

    Parameters
    ----------
    router:          SemanticRouter, or None to skip domain routing
    domain_store:    DomainPackStore (needed to resolve a routed pack's index)
    knowledge_store: KnowledgeCookieStore, or None to skip hit recording
    rewriter:        QueryRewriter, or None to skip HyDE expansion
    fallback_retriever: a retriever used when no pack is routed (the server's
                     existing LocalRetriever / shard router); must expose
                     ``retrieve(query, k=..., domain=...)``
    enable_hyde:     run rewrite_hyde() before retrieval (needs rewriter)
    enable_intent:   apply intent_filter to chunk content type (default True)
    session_id:      session identifier for cross-session promotion; a per-process
                     UUID is generated when omitted (one server run == one session)
    """

    def __init__(
        self,
        *,
        router: Any = None,
        domain_store: Any = None,
        knowledge_store: Any = None,
        rewriter: Any = None,
        fallback_retriever: Any = None,
        enable_hyde: bool = False,
        enable_intent: bool = True,
        rewrite_domain: str = "general",
        session_id: Optional[str] = None,
    ) -> None:
        self.router = router
        self.domain_store = domain_store
        self.knowledge_store = knowledge_store
        self.rewriter = rewriter
        self.fallback_retriever = fallback_retriever
        self.enable_hyde = enable_hyde and rewriter is not None
        self.enable_intent = enable_intent
        self.rewrite_domain = rewrite_domain
        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:12]}"

        # Cache LocalRetriever instances per pack index dir so we don't rebuild
        # the BM25 index on every query.
        self._retriever_cache: Dict[str, Any] = {}

    # ── construction ────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, *, fallback_retriever: Any = None) -> "CognitiveGatingPipeline":
        """Build a pipeline from environment variables (see module docstring)."""
        router = None
        domain_store = None
        store_path = os.environ.get("AXIOM_DOMAIN_STORE", "").strip()
        if store_path:
            try:
                from axiom_domain_pack import DomainPackStore
                from axiom_semantic_router import SemanticRouter
                domain_store = DomainPackStore(base_dir=Path(store_path).expanduser())
                router = SemanticRouter(domain_store)
                router.build_indexes()
                LOG.info("semantic router enabled: store=%s", store_path)
            except Exception as exc:
                LOG.warning("semantic router init failed: %s", exc)
                router = None
                domain_store = None

        knowledge_store = None
        kc_path = os.environ.get("AXIOM_KNOWLEDGE_COOKIE", "").strip()
        if kc_path:
            try:
                from axiom_knowledge_cookie import KnowledgeCookieStore
                knowledge_store = KnowledgeCookieStore(Path(kc_path).expanduser())
                LOG.info("knowledge cookie enabled: %s", kc_path)
            except Exception as exc:
                LOG.warning("knowledge cookie init failed: %s", exc)

        rewrite_domain = os.environ.get("AXIOM_QUERY_REWRITE", "").strip().lower()
        enable_hyde = os.environ.get("AXIOM_HYDE", "").strip() in ("1", "true", "on")
        rewriter = None
        if enable_hyde:
            try:
                from axiom_query_rewriter import from_env as qr_from_env
                rewriter = qr_from_env(domain=rewrite_domain or "general")
                if rewriter is not None:
                    LOG.info("HyDE expansion enabled: domain=%s",
                             rewrite_domain or "general")
            except Exception as exc:
                LOG.warning("HyDE rewriter init failed: %s", exc)

        enable_intent = os.environ.get("AXIOM_GATING_INTENT", "1").strip() not in (
            "0", "false", "off")

        return cls(
            router=router,
            domain_store=domain_store,
            knowledge_store=knowledge_store,
            rewriter=rewriter,
            fallback_retriever=fallback_retriever,
            enable_hyde=enable_hyde,
            enable_intent=enable_intent,
            rewrite_domain=rewrite_domain or "general",
        )

    @property
    def active(self) -> bool:
        """True when at least one gating layer beyond the fallback is wired."""
        return any((self.router, self.knowledge_store, self.enable_hyde))

    def layers(self) -> List[str]:
        out = []
        if self.router is not None:
            out.append("router")
        if self.enable_intent:
            out.append("intent")
        if self.enable_hyde:
            out.append("hyde")
        if self.knowledge_store is not None:
            out.append("knowledge")
        return out

    # ── core ──────────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        *,
        k: int = 5,
        domain: Optional[str] = None,
    ) -> Tuple[List[Any], GatingTelemetry]:
        """Run the full gating pipeline for one query.

        Returns ``(sources, telemetry)`` where ``sources`` is a list of
        RetrievedSource objects (empty if nothing matched and no fallback is
        wired).  Never raises — each stage degrades to a safe default.
        """
        tel = GatingTelemetry(effective_query=query, layers_active=self.layers())

        # 1. route → pick a domain pack and its retriever
        retriever, tel.routed_to = self._route_and_get_retriever(query)

        # 2. intent → detect the query's content type for filtering
        intent = self._detect_intent(query) if self.enable_intent else ""
        tel.intent_filter = intent

        # 3. HyDE → expand a vocabulary-poor query into a dense hypothetical
        effective_query = query
        if self.enable_hyde:
            expanded, source = self._hyde_expand(query)
            effective_query = expanded
            tel.hyde_used = expanded != query
            tel.hyde_source = source
        tel.effective_query = effective_query

        # 4. retrieve (with graceful intent-filter fallback)
        hits = self._do_retrieve(
            retriever, effective_query, k=k, domain=domain, intent=intent, tel=tel)
        tel.hits_returned = len(hits)

        # 5. record into the knowledge cookie for cross-session promotion
        if self.knowledge_store is not None and hits:
            tel.fragments_recorded = self._record_hits(hits)

        return hits, tel

    def extra_context(self, *, max_fragments: int = 5) -> dict:
        """Return the promoted hot-knowledge block for LLM injection.

        Empty dict when no cookie is wired or nothing has been promoted yet.
        """
        if self.knowledge_store is None:
            return {}
        try:
            cookie = self.knowledge_store.load()
            if cookie is None:
                return {}
            cookie.promote()
            return cookie.to_extra_context(max_fragments=max_fragments)
        except Exception as exc:
            LOG.debug("extra_context failed: %s", exc)
            return {}

    def promote(self) -> None:
        """Flush promotion to disk (call at session end)."""
        if self.knowledge_store is not None:
            try:
                self.knowledge_store.promote_and_save()
            except Exception as exc:
                LOG.debug("promote_and_save failed: %s", exc)

    # ── internals ─────────────────────────────────────────────────────────────

    def _route_and_get_retriever(self, query: str) -> Tuple[Any, str]:
        """Route the query to a domain pack; return (retriever, routed_domain).

        Falls back to ``self.fallback_retriever`` and "" when routing is
        disabled or no pack scores above threshold.
        """
        if self.router is None or self.domain_store is None:
            return self.fallback_retriever, ""
        try:
            packs = self.router.route(query, top_k=1)
        except Exception as exc:
            LOG.debug("route failed: %s", exc)
            return self.fallback_retriever, ""
        if not packs:
            return self.fallback_retriever, ""
        pack = packs[0]
        retriever = self._retriever_for_pack(pack)
        if retriever is None:
            return self.fallback_retriever, ""
        return retriever, pack.domain

    def _retriever_for_pack(self, pack: Any) -> Any:
        """Build (and cache) a LocalRetriever over a routed pack's index dir."""
        try:
            index_dir = self.domain_store.index_path(pack)
        except Exception as exc:
            LOG.debug("index_path failed: %s", exc)
            return None
        key = str(index_dir)
        if key in self._retriever_cache:
            return self._retriever_cache[key]
        try:
            from axiom_research_retriever import LocalRetriever
            r = LocalRetriever(roots=[index_dir])
            r.build()
            self._retriever_cache[key] = r
            return r
        except Exception as exc:
            LOG.debug("LocalRetriever build failed: %s", exc)
            return None

    def _detect_intent(self, query: str) -> str:
        try:
            from axiom_semantic_router import _detect_query_intent
            intent = _detect_query_intent(query)
            return "" if intent == "general" else intent
        except Exception:
            return ""

    def _hyde_expand(self, query: str) -> Tuple[str, str]:
        """Return (expanded_query, source). Falls back to the original query."""
        try:
            out = self.rewriter.rewrite_hyde(query, domain=self.rewrite_domain)
            if out and out.strip() and out.strip() != query.strip():
                # Combine original + hypothetical: keeps exact-match terms while
                # adding the dense domain vocabulary HyDE generates.
                return f"{query} {out.strip()}", "live-backend"
        except Exception as exc:
            LOG.debug("hyde_expand failed: %s", exc)
        return query, "fallback-original"

    def _do_retrieve(
        self,
        retriever: Any,
        query: str,
        *,
        k: int,
        domain: Optional[str],
        intent: str,
        tel: GatingTelemetry,
    ) -> List[Any]:
        if retriever is None:
            return []

        # LocalRetriever supports intent_filter; other retrievers may not.
        def _call(intent_filter: Optional[str]):
            try:
                return retriever.retrieve(
                    query, k=k, domain=domain, intent_filter=intent_filter)
            except TypeError:
                # Retriever doesn't accept intent_filter (e.g. shard router) —
                # call without it.
                return retriever.retrieve(query, k=k, domain=domain)
            except Exception as exc:
                LOG.debug("retrieve raised: %s", exc)
                return []

        # First retrieve unfiltered to measure the candidate pool, then apply
        # the intent filter — but fall back to unfiltered hits if filtering
        # empties the result (graceful degradation).
        unfiltered = _call(None)
        tel.candidates_before_intent = len(unfiltered)
        if intent and self.enable_intent:
            filtered = _call(intent)
            if filtered:
                return filtered
        return unfiltered

    def _record_hits(self, hits: List[Any]) -> int:
        recorded = 0
        for h in hits:
            content = getattr(h, "snippet", "") or getattr(h, "title", "")
            uri = getattr(h, "uri", "") or ""
            if not content:
                continue
            try:
                self.knowledge_store.record_hit(
                    content=content, source_uri=uri, session_id=self.session_id)
                recorded += 1
            except Exception as exc:
                LOG.debug("record_hit failed: %s", exc)
        return recorded
