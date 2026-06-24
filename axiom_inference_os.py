"""Axiom Inference OS — 7-layer orchestrator.

Chains all built-but-unwired layers into one pipeline:

  Layer 0  Intent Kernel     axiom_intent_classifier.IntentClassifier
  Layer 1  Inference Router  axiom_event_token.router.LatencyAwareRouter
           Backend           axiom_event_token.backends.default_backend
  Layer 2  Retrieval         axiom_research_retriever.default_retriever
           Context pack      axiom_semantic_cosmos.CosmosContextBuilder
  Layer 4  Governance Guard  axiom_firewall.policy.apply_policy
  Layer 6  Audit             axiom_audit_ledger.AuditLedger
           Exo ledger        axiom_exoskeleton_ledger (direct write)

Usage::

    from axiom_inference_os import InferenceOS, InferenceRequest
    ios = InferenceOS()
    result = ios.run(InferenceRequest(
        query="What is GDPR Article 9?",
        session_id="demo-1",
        tenant_id="demo",
        domain="legal",
    ))
    print(result.output, result.audit_id)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import types as _types
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ── CANNOT_MUTATE module freeze ───────────────────────────────────────────────

def _module_setattr(self: object, name: str, value: object) -> None:
    raise AttributeError(f"CANNOT_MUTATE: {name} is immutable in axiom_inference_os")

_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})

TRUST_LEVEL:        int   = 1
FORMAT_VERSION:     str   = "1.0"
KEY_NS:             bytes = b"axiom-inference-os-v1"
EXO_KEY_NS:         bytes = b"axiom-exoskeleton-ledger-v1"
STAGE_KEY_NS:       bytes = b"axiom-inference-os-stage-v1"
_BLOCKING_CLASSES:  frozenset = frozenset({"HARM", "DECEIVE"})
_MAX_CONTEXT_CHARS: int   = 8_000
_MAX_RETRIEVAL_K:   int   = 6


# ── Signing helpers ───────────────────────────────────────────────────────────

def _sign(payload: dict, ns: bytes) -> str:
    from axiom_signing import derive_key
    key  = derive_key(ns)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def _now_iso() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _ms(t0: float) -> int:
    return max(0, int((time.perf_counter() - t0) * 1000))


# ── Public dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InferenceRequest:
    """Input to the Inference OS pipeline."""
    query:            str
    session_id:       str
    tenant_id:        str
    domain:           Optional[str] = None   # "healthcare" | "legal" | "finance" | …
    use_retrieval:    bool = True
    max_context_chars: int = _MAX_CONTEXT_CHARS


@dataclass(frozen=True)
class InferenceStageResult:
    """Output of a single pipeline stage."""
    stage:      str          # "intent" | "route" | "retrieval" | "generation" | "governance" | "audit"
    status:     str          # "ok" | "blocked" | "degraded" | "skipped"
    latency_ms: int
    detail:     dict
    signature:  str

    @classmethod
    def make(cls, stage: str, status: str, latency_ms: int, detail: dict) -> "InferenceStageResult":
        payload = {"stage": stage, "status": status, "latency_ms": latency_ms, **detail}
        return cls(stage=stage, status=status, latency_ms=latency_ms,
                   detail=detail, signature=_sign(payload, STAGE_KEY_NS))

    def to_dict(self) -> dict:
        return {"stage": self.stage, "status": self.status,
                "latency_ms": self.latency_ms, "detail": self.detail,
                "signature": self.signature}


@dataclass(frozen=True)
class InferenceOSResult:
    """Full Inference OS pipeline result — all 8 demo-flow fields present."""
    request_id:      str
    query:           str
    # Layer 0
    intent_class:    str          # INFORM | CLARIFY | REFUSE | HARM | DECEIVE | UNCERTAIN
    intent_confidence: float
    # Layer 1
    route:           str          # "local" | "nim" | "subq" | "specialist" | "fallback"
    model_used:      str
    # Layer 2
    context_hits:    int
    context_snippet: str          # first 200 chars of assembled context
    # Layer 3-5
    output:          str          # generated answer (empty on block)
    output_verdict:  str          # "allow" | "block"
    # Telemetry
    input_tokens:    int
    output_tokens:   int
    tokens_saved:    int          # approx cache / context reuse savings
    total_latency_ms: int
    fallback_used:   bool
    risk_class:      str          # intent_class of the generated output
    # Layer 6
    audit_id:        str          # first 24 chars of AuditEvent.signature
    stages:          tuple        # tuple[InferenceStageResult, ...]
    # Chain
    timestamp:       str = ""
    signature:       str = ""
    # Memory Trifecta telemetry (Pillars 2 + 3)
    delta_turn:            int = 0   # DeltaState.turn_count after this call
    memory_lod:            int = 0   # MemoryLOD used (0/1/2)
    memory_token_estimate: int = 0   # tokens consumed by memory injection
    # Output shaping telemetry
    shaping_tokens_saved:  int   = 0   # tokens removed by OutputShaper
    shaping_transforms:    tuple = ()  # tuple[str, ...] — applied transforms

    def to_dict(self) -> dict:
        return {
            "request_id":      self.request_id,
            "query":           self.query,
            "intent_class":    self.intent_class,
            "intent_confidence": self.intent_confidence,
            "route":           self.route,
            "model_used":      self.model_used,
            "context_hits":    self.context_hits,
            "context_snippet": self.context_snippet,
            "output":          self.output,
            "output_verdict":  self.output_verdict,
            "input_tokens":    self.input_tokens,
            "output_tokens":   self.output_tokens,
            "tokens_saved":    self.tokens_saved,
            "total_latency_ms": self.total_latency_ms,
            "fallback_used":   self.fallback_used,
            "risk_class":      self.risk_class,
            "audit_id":        self.audit_id,
            "stages":          [s.to_dict() for s in self.stages],
            "delta_turn":            self.delta_turn,
            "memory_lod":            self.memory_lod,
            "memory_token_estimate": self.memory_token_estimate,
            "shaping_tokens_saved":  self.shaping_tokens_saved,
            "shaping_transforms":    list(self.shaping_transforms),
            "timestamp":       self.timestamp,
            "signature":       self.signature,
        }

    def verify(self) -> bool:
        from axiom_signing import derive_key
        payload = {k: v for k, v in self.to_dict().items() if k != "signature"}
        expected = _sign(payload, KEY_NS)
        return hmac.compare_digest(self.signature, expected)


# ── Orchestrator ──────────────────────────────────────────────────────────────

_UNSET = object()  # sentinel: distinguishes "not provided" from "explicitly None"


class InferenceOS:
    """Seven-layer Inference OS orchestrator.

    All heavy components are initialised lazily on the first `run()` call
    so construction is cheap and import-time safe. Components that fail to
    import (missing optional deps) degrade gracefully — the pipeline
    continues with reduced capability rather than crashing.

    Pass ``backend=None`` explicitly to disable generation entirely (useful
    for testing). Omit ``backend`` to let the orchestrator auto-select via
    ``default_backend()``.
    """

    def __init__(
        self,
        *,
        retriever=_UNSET,    # LocalRetriever | None — None = disabled; omit = auto
        backend=_UNSET,      # SLMBackend | None — None = disabled; omit = auto
        audit_ledger=_UNSET, # AuditLedger | None — None = disabled; omit = auto
        classifier=None,     # IntentClassifier | None — always built if omitted
        policy=None,         # policy module | None
    ) -> None:
        self._retriever_arg   = retriever
        self._backend_arg     = backend
        self._audit_arg       = audit_ledger
        self._classifier_arg  = classifier
        self._policy_arg      = policy
        # Lazily initialised
        self._retriever        = None
        self._cosmos_retriever = None   # CosmosLayeredRetriever (wraps _retriever)
        self._cosmos_builder   = None   # CosmosContextBuilder
        self._prefix_cache     = None   # DomainPrefixCache — static system prompts
        self._delta_store      = None   # DeltaMemoryStore — Trifecta Pillar 2
        self._delta_map        = None   # DeltaMemoryMap
        self._mr_memory        = None   # MultiResolutionMemory — Trifecta Pillar 3
        self._output_shaper    = None   # OutputShaper — post-gen normalisation
        self._backend    = None
        self._audit      = None
        self._classifier = None
        self._policy     = None
        self._ready      = False

    # ── initialisation ────────────────────────────────────────────────────────

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        self._classifier = self._classifier_arg or self._build_classifier()
        # _UNSET → auto-discover; None → explicitly disabled; anything else → use as-is
        self._backend   = (self._build_backend()   if self._backend_arg   is _UNSET
                           else self._backend_arg)
        self._retriever = (self._build_retriever() if self._retriever_arg is _UNSET
                           else self._retriever_arg)
        self._audit     = (self._build_audit()     if self._audit_arg     is _UNSET
                           else self._audit_arg)
        self._policy    = self._policy_arg or self._build_policy()
        # Domain prefix cache — static system prompts + startup warming
        try:
            from axiom_prefix_cache import DomainPrefixCache, DOMAIN_SEED_QUERIES
            self._prefix_cache = DomainPrefixCache()
            # Build domain preambles using local retriever before warming Ollama,
            # so warm_domain() seeds the KV cache with the full preamble string.
            if self._retriever is not None:
                local_r = self._extract_local_retriever(self._retriever)
                if local_r is None:
                    local_r = self._build_local_retriever()
                if local_r is not None:
                    try:
                        for domain in DOMAIN_SEED_QUERIES:
                            self._prefix_cache.build_preamble(domain, local_r)
                    except Exception:
                        pass
            if self._backend is not None and hasattr(self._backend, "_url"):
                self._prefix_cache.warm_all(self._backend._url, self._backend.model)
        except Exception:
            pass
        # Cosmos layered retrieval needs a LocalRetriever that accepts intent_filter.
        # MultiProviderRetriever wraps local + remote APIs; only local docs have
        # cosmos-level sidecars, so we build a plain LocalRetriever for cosmos use.
        try:
            from axiom_research_retriever import LocalRetriever
            from axiom_semantic_cosmos import CosmosLayeredRetriever, CosmosContextBuilder
            cosmos_local = self._extract_local_retriever(self._retriever)
            if cosmos_local is None:
                cosmos_local = self._build_local_retriever()
            if cosmos_local is not None:
                self._cosmos_retriever = CosmosLayeredRetriever(cosmos_local)
                self._cosmos_builder   = CosmosContextBuilder()
        except Exception:
            pass
        # Memory Trifecta Pillars 2 + 3
        try:
            from axiom_delta_memory import DeltaMemoryMap, DeltaMemoryStore
            from axiom_multiresolution_memory import MultiResolutionMemory
            self._delta_store = DeltaMemoryStore()
            self._delta_map   = DeltaMemoryMap()
            self._mr_memory   = MultiResolutionMemory()
        except Exception:
            pass
        try:
            from axiom_output_shaper import OutputShaper
            self._output_shaper = OutputShaper()
        except Exception:
            pass
        self._ready = True

    @staticmethod
    def _build_classifier():
        from axiom_intent_classifier import IntentClassifier
        from axiom_signing import derive_key
        return IntentClassifier(derive_key(b"axiom-firewall-v1"))

    @staticmethod
    def _build_backend():
        try:
            from axiom_event_token.backends import default_backend
            return default_backend()
        except Exception:
            return None

    @staticmethod
    def _build_retriever():
        try:
            from axiom_research_retriever import default_retriever
            return default_retriever(Path.cwd())
        except Exception:
            return None

    @staticmethod
    def _build_audit():
        try:
            from axiom_audit_ledger import AuditLedger
            return AuditLedger()
        except Exception:
            return None

    @staticmethod
    def _build_policy():
        try:
            from axiom_firewall import policy as pm
            return pm
        except Exception:
            return None

    @staticmethod
    def _extract_local_retriever(retriever):
        """Unwrap DomainRoutedRetriever → LocalRetriever for cosmos use."""
        if retriever is None:
            return None
        from axiom_research_retriever import LocalRetriever, DomainRoutedRetriever
        if isinstance(retriever, LocalRetriever):
            return retriever
        if isinstance(retriever, DomainRoutedRetriever):
            return retriever._default  # bare LocalRetriever with intent_filter support
        # MultiProviderRetriever or other wrappers — extract first provider if possible
        try:
            providers = retriever._providers
            if providers:
                inner = providers[0]
                inner_r = getattr(inner, "_retriever", None) or getattr(inner, "_inner", None)
                if isinstance(inner_r, (LocalRetriever, DomainRoutedRetriever)):
                    return inner_r if isinstance(inner_r, LocalRetriever) else inner_r._default
        except Exception:
            pass
        return None

    @staticmethod
    def _build_local_retriever():
        """Build a minimal LocalRetriever over docs/ for cosmos layered retrieval."""
        try:
            from axiom_research_retriever import LocalRetriever
            roots = []
            for name in ("docs", "README.md"):
                p = Path.cwd() / name
                if p.exists():
                    roots.append(p)
            if not roots:
                return None
            r = LocalRetriever(roots=roots)
            r.build()
            return r
        except Exception:
            return None

    # ── main pipeline ─────────────────────────────────────────────────────────

    def run(self, request: InferenceRequest) -> InferenceOSResult:
        """Execute the 8-step Inference OS pipeline for one request."""
        self._ensure_ready()
        t_total = time.perf_counter()
        request_id = f"ios_{uuid.uuid4().hex[:12]}"
        stages: List[InferenceStageResult] = []

        # ── Stage 0 / Step 2: Intent Kernel ───────────────────────────────────
        intent_class, intent_confidence, intent_sig, intent_signals = (
            "UNCERTAIN", 0.5, "", ()
        )
        t0 = time.perf_counter()
        try:
            ir = self._classifier.classify(request.query)
            intent_class    = ir.intent_class
            intent_confidence = ir.confidence
            intent_sig      = ir.signature
            intent_signals  = tuple(ir.signals)
            stages.append(InferenceStageResult.make(
                "intent", "blocked" if intent_class in _BLOCKING_CLASSES else "ok",
                _ms(t0), {
                    "intent_class": intent_class,
                    "confidence":   intent_confidence,
                    "signals":      list(intent_signals[:6]),
                }
            ))
        except Exception as exc:
            stages.append(InferenceStageResult.make(
                "intent", "degraded", _ms(t0), {"error": str(exc)[:120]}
            ))

        # Short-circuit on HARM / DECEIVE — no retrieval, no generation
        if intent_class in _BLOCKING_CLASSES:
            return self._blocked_result(
                request_id, request, intent_class, intent_confidence, stages, t_total
            )

        # ── Stage 1 / Step 3: Inference Router ────────────────────────────────
        route, model_used, fallback_used = "local", "unknown", False
        # Load (or create empty) session state for Memory Trifecta Pillars 2+3
        delta_state    = None
        mem_view       = None
        if self._delta_store is not None and self._delta_map is not None:
            try:
                from axiom_delta_memory import DeltaState
                delta_state = (
                    self._delta_store.load(request.session_id)
                    or DeltaState(session_id=request.session_id, domain=request.domain)
                )
            except Exception:
                delta_state = None
        t0 = time.perf_counter()
        try:
            if self._backend is not None:
                route       = self._backend.name
                model_used  = self._backend.model
            route_detail: dict = {"route": route, "model": model_used}
            # LOD 0 token pointer — routing metadata only, not injected into LLM
            if delta_state is not None and self._mr_memory is not None:
                try:
                    lod0_view = self._mr_memory.to_lod0(delta_state, request.domain)
                    route_detail["memory_lod0"]  = lod0_view.content
                    route_detail["delta_turn"]   = delta_state.turn_count
                except Exception:
                    pass
            stages.append(InferenceStageResult.make("route", "ok", _ms(t0), route_detail))
        except Exception as exc:
            stages.append(InferenceStageResult.make(
                "route", "degraded", _ms(t0), {"error": str(exc)[:120]}
            ))

        # ── Stage 2 / Step 4: Retrieval ───────────────────────────────────────
        context_str, context_hits, context_snippet = "", 0, ""
        cosmos_level_counts: dict = {}
        t0 = time.perf_counter()
        if request.use_retrieval and self._retriever is not None:
            try:
                # Use per-domain context budget to cap input tokens at General RAG level
                ctx_budget = (
                    self._prefix_cache.context_budget(request.domain)
                    if self._prefix_cache is not None
                    else request.max_context_chars
                )
                if self._cosmos_retriever is not None:
                    # Layered cosmos retrieval: galaxy → planet → star
                    cosmos_result = self._cosmos_retriever.retrieve_layered(
                        request.query, k=_MAX_RETRIEVAL_K, anticipate=True
                    )
                    all_hits = cosmos_result.all_hits()
                    cosmos_level_counts = cosmos_result.level_counts()
                    # Filter out docs already in the preamble KV cache; use delta budget.
                    if self._prefix_cache is not None:
                        delta_hits = self._prefix_cache.filter_preamble_hits(
                            request.domain, all_hits
                        )
                        coverage = self._prefix_cache.preamble_coverage(
                            request.domain, all_hits
                        )
                        delta_budget = self._prefix_cache.delta_budget(request.domain)
                        skip_uris = frozenset(
                            getattr(h, "uri", "") for h in all_hits
                            if h not in delta_hits
                        )
                    else:
                        delta_hits   = all_hits
                        coverage     = 0.0
                        delta_budget = ctx_budget
                        skip_uris    = frozenset()
                    # Pass base_system="" — static system prompt is handled by
                    # DomainPrefixCache.make_system_with_preamble(), not context_str.
                    context_str = self._cosmos_builder.build(
                        cosmos_result, "",
                        max_chars=delta_budget,
                    )
                    # Remove preamble-covered snippets from the assembled context.
                    if skip_uris and context_str:
                        context_str, _ = self._pack_context(
                            delta_hits, delta_budget, skip_uris=skip_uris
                        )
                    context_hits = len(all_hits)
                else:
                    # Flat BM25 fallback
                    all_hits = self._retriever.retrieve(
                        request.query,
                        k=_MAX_RETRIEVAL_K,
                        domain=request.domain,
                    )
                    if self._prefix_cache is not None:
                        delta_hits = self._prefix_cache.filter_preamble_hits(
                            request.domain, all_hits
                        )
                        coverage   = self._prefix_cache.preamble_coverage(
                            request.domain, all_hits
                        )
                        skip_uris  = frozenset(
                            getattr(h, "uri", "") for h in all_hits
                            if h not in delta_hits
                        )
                        delta_budget = self._prefix_cache.delta_budget(request.domain)
                    else:
                        delta_hits   = all_hits
                        coverage     = 0.0
                        skip_uris    = frozenset()
                        delta_budget = ctx_budget
                    if delta_hits:
                        context_str, _ = self._pack_context(
                            delta_hits, delta_budget, skip_uris=skip_uris
                        )
                    context_hits = len(all_hits)

                context_snippet = context_str[:200]
                detail: dict = {
                    "hits":               context_hits,
                    "preamble_coverage":  round(coverage, 2),
                }
                if cosmos_level_counts:
                    detail["galaxy"] = cosmos_level_counts.get("galaxy", 0)
                    detail["planet"] = cosmos_level_counts.get("planet", 0)
                    detail["star"]   = cosmos_level_counts.get("star",   0)
                stages.append(InferenceStageResult.make(
                    "retrieval", "ok", _ms(t0), detail
                ))
            except Exception as exc:
                stages.append(InferenceStageResult.make(
                    "retrieval", "degraded", _ms(t0), {"error": str(exc)[:120]}
                ))
        else:
            stages.append(InferenceStageResult.make(
                "retrieval", "skipped", 0,
                {"reason": "disabled" if not request.use_retrieval else "no retriever"}
            ))

        # Estimated tokens saved from context reuse (rough: 4 chars ≈ 1 token)
        # Only count when actual documents were retrieved, not just the base system prompt.
        tokens_saved = len(context_str) // 4 if (context_str and context_hits > 0) else 0

        # Resolve LOD for memory injection (Pillar 3) — pure function, no I/O
        mem_lod            = 0
        mem_token_estimate = 0
        if delta_state is not None and self._mr_memory is not None:
            try:
                mem_view = self._mr_memory.view(
                    delta_state, intent_class, request.domain
                )
                mem_lod            = int(mem_view.lod)
                mem_token_estimate = mem_view.token_estimate
            except Exception:
                mem_view = None

        # ── Stage 3 / Step 5: Generation ──────────────────────────────────────
        output, input_tokens, output_tokens = "", 0, 0
        t0 = time.perf_counter()
        if self._backend is not None:
            try:
                # Preamble-aware system prompt — identical per domain (+ preamble docs
                # if built at startup) → Ollama caches all of it.
                # Delta retrieved context goes in the user turn (make_user_prompt).
                if self._prefix_cache is not None:
                    system_prompt = self._prefix_cache.make_system_with_preamble(
                        request.domain
                    )
                    user_message  = self._prefix_cache.make_user_prompt(
                        context_str, request.query
                    )
                else:
                    # Fallback when prefix cache not available
                    system_prompt = (
                        "You are a helpful, accurate assistant governed by the "
                        "Axiom Inference OS. Answer concisely based on the provided context."
                    )
                    user_message = (
                        f"Context:\n{context_str}\n\nQuestion: {request.query}"
                        if context_str else request.query
                    )

                # Memory Trifecta injection: LOD 1 → user turn; LOD 2 → system prompt
                if mem_view is not None and mem_view.content:
                    from axiom_multiresolution_memory import MemoryLOD
                    if mem_view.lod == MemoryLOD.LOD1:
                        user_message = f"[Session State]\n{mem_view.content}\n\n{user_message}"
                    elif mem_view.lod == MemoryLOD.LOD2:
                        system_prompt = (
                            f"{system_prompt}\n\n[Full Session Context]\n{mem_view.content}"
                        )

                # Output shaping: inject format hint to reduce model verbosity upstream
                if self._output_shaper is not None:
                    hint = self._output_shaper.output_format_hint(intent_class)
                    if hint:
                        system_prompt = system_prompt + hint

                result = self._backend.generate(
                    system=system_prompt,
                    prompt=user_message,
                    max_output_tokens=512,
                )
                output        = result.text
                input_tokens  = result.input_tokens
                output_tokens = result.output_tokens
                route         = result.backend
                model_used    = result.model
                prefix_warm   = (
                    self._prefix_cache.detect_cache_hit(result.prefill_ms, input_tokens)
                    if self._prefix_cache is not None else False
                )
                stages.append(InferenceStageResult.make(
                    "generation", "ok", _ms(t0),
                    {
                        "backend":       result.backend,
                        "model":         result.model,
                        "input_tokens":  input_tokens,
                        "output_tokens": output_tokens,
                        "prefill_ms":    result.prefill_ms,
                        "prefix_warm":   prefix_warm,
                    }
                ))
            except Exception as exc:
                fallback_used = True
                stages.append(InferenceStageResult.make(
                    "generation", "degraded", _ms(t0), {"error": str(exc)[:120]}
                ))
        else:
            fallback_used = True
            stages.append(InferenceStageResult.make(
                "generation", "skipped", 0, {"reason": "no backend configured"}
            ))

        # ── Stage 4 / Step 6: Governance — verify generated output ────────────
        output_verdict, risk_class = "allow", intent_class
        t0 = time.perf_counter()
        if output:
            try:
                out_ir       = self._classifier.classify(output)
                risk_class   = out_ir.intent_class
                if risk_class in _BLOCKING_CLASSES:
                    output_verdict = "block"
                    output         = ""
                # Apply tenant policy if available
                if self._policy is not None:
                    try:
                        tenant_policy = self._policy.get_policy(request.tenant_id)
                        verdict_str, out_ir = self._policy.apply_policy(
                            out_ir, tenant_policy, output
                        )
                        if verdict_str == "block":
                            output_verdict = "block"
                            output         = ""
                    except Exception:
                        pass
                stages.append(InferenceStageResult.make(
                    "governance", output_verdict, _ms(t0),
                    {"risk_class": risk_class, "verdict": output_verdict}
                ))
            except Exception as exc:
                stages.append(InferenceStageResult.make(
                    "governance", "degraded", _ms(t0), {"error": str(exc)[:120]}
                ))
        else:
            stages.append(InferenceStageResult.make(
                "governance", "skipped", 0, {"reason": "no output to govern"}
            ))

        # ── Stage 5 / Step 7: Audit ───────────────────────────────────────────
        audit_id = ""
        t0 = time.perf_counter()
        audit_id = self._write_audit(
            request_id=request_id,
            request=request,
            intent_class=intent_class,
            output_verdict=output_verdict,
            route=route,
            model_used=model_used,
            context_hits=context_hits,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_latency_ms=_ms(t_total),
        )
        stages.append(InferenceStageResult.make(
            "audit", "ok", _ms(t0), {"audit_id": audit_id}
        ))

        # Also append to exoskeleton ledger so RouterPolicy can read health data
        self._write_exo_ledger(
            request_id=request_id,
            query=request.query,
            backend=route,
            model=model_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_latency_ms=_ms(t_total),
            verified=(output_verdict == "allow"),
            domain=request.domain or "",
        )

        # ── Output shaping: strip CoT / politeness post-audit ────────────────
        shaping_tokens_saved = 0
        shaping_transforms: tuple = ()
        if output and self._output_shaper is not None:
            try:
                shaped = self._output_shaper.shape(output, intent_class)
                if shaped.transforms:
                    output               = shaped.text
                    shaping_tokens_saved = shaped.tokens_saved
                    shaping_transforms   = shaped.transforms
            except Exception:
                pass

        # ── Trifecta Pillar 2: update DeltaState after audit ─────────────────
        # Only advance session state when a real output was produced; blocked
        # or empty responses must not increment turn_count (it would corrupt
        # LOD escalation thresholds on the next request for this session).
        final_delta_turn = 0
        if (output and delta_state is not None
                and self._delta_map is not None
                and self._delta_store is not None):
            try:
                from axiom_signing import derive_key
                dirty     = self._delta_map.extract_delta(output, request.query, delta_state)
                new_state = self._delta_map.apply_delta(delta_state, **dirty)
                key       = derive_key(KEY_NS)
                new_state = self._delta_map.sign(new_state, key)
                self._delta_store.save(request.session_id, new_state)
                final_delta_turn = new_state.turn_count
            except Exception:
                final_delta_turn = delta_state.turn_count

        # ── Step 8: Assemble signed result ────────────────────────────────────
        ts = _now_iso()
        base = {
            "request_id":      request_id,
            "query":           request.query,
            "intent_class":    intent_class,
            "intent_confidence": round(intent_confidence, 4),
            "route":           route,
            "model_used":      model_used,
            "context_hits":    context_hits,
            "context_snippet": context_snippet,
            "output":          output,
            "output_verdict":  output_verdict,
            "input_tokens":    input_tokens,
            "output_tokens":   output_tokens,
            "tokens_saved":    tokens_saved,
            "total_latency_ms": _ms(t_total),
            "fallback_used":   fallback_used,
            "risk_class":      risk_class,
            "audit_id":        audit_id,
            "stages":          [s.to_dict() for s in stages],
            "delta_turn":            final_delta_turn,
            "memory_lod":            mem_lod,
            "memory_token_estimate": mem_token_estimate,
            "shaping_tokens_saved":  shaping_tokens_saved,
            "shaping_transforms":    shaping_transforms,
            "timestamp":       ts,
        }
        sig = _sign(base, KEY_NS)
        return InferenceOSResult(
            **{k: v for k, v in base.items() if k != "stages"},
            stages=tuple(stages),
            signature=sig,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _blocked_result(
        self,
        request_id: str,
        request: InferenceRequest,
        intent_class: str,
        intent_confidence: float,
        stages: List[InferenceStageResult],
        t_total: float,
    ) -> InferenceOSResult:
        """Fast-path for HARM/DECEIVE — skip retrieval and generation."""
        ts  = _now_iso()
        lat = _ms(t_total)
        audit_id = self._write_audit(
            request_id=request_id, request=request,
            intent_class=intent_class, output_verdict="block",
            route="none", model_used="none",
            context_hits=0, input_tokens=0, output_tokens=0,
            total_latency_ms=lat,
        )
        stages.append(InferenceStageResult.make(
            "audit", "ok", 0, {"audit_id": audit_id}
        ))
        base = {
            "request_id":       request_id,
            "query":            request.query,
            "intent_class":     intent_class,
            "intent_confidence": round(intent_confidence, 4),
            "route":            "none",
            "model_used":       "none",
            "context_hits":     0,
            "context_snippet":  "",
            "output":           "",
            "output_verdict":   "block",
            "input_tokens":     0,
            "output_tokens":    0,
            "tokens_saved":     0,
            "total_latency_ms": lat,
            "fallback_used":    False,
            "risk_class":       intent_class,
            "audit_id":         audit_id,
            "stages":           [s.to_dict() for s in stages],
            "timestamp":        ts,
        }
        sig = _sign(base, KEY_NS)
        return InferenceOSResult(
            **{k: v for k, v in base.items() if k != "stages"},
            stages=tuple(stages),
            signature=sig,
        )

    @staticmethod
    def _pack_context(
        hits: list,
        max_chars: int,
        skip_uris: Optional[frozenset] = None,
    ) -> tuple[str, int]:
        """Assemble retrieved sources into a context string for the user turn.

        skip_uris: URIs already in the preamble KV cache — exclude from output.
        """
        skip  = skip_uris or frozenset()
        parts: List[str] = ["=== RETRIEVED CONTEXT ==="]
        total = len(parts[0])
        count = 0
        for h in hits:
            if getattr(h, "uri", "") in skip:
                continue
            snippet = getattr(h, "snippet", "") or getattr(h, "content", "")
            title   = getattr(h, "title", "") or getattr(h, "uri", "")
            chunk   = f"\n[{title}]\n{snippet}\n---"
            if total + len(chunk) > max_chars:
                break
            parts.append(chunk)
            total += len(chunk)
            count += 1
        return "\n".join(parts), count

    def _write_audit(
        self, *, request_id: str, request: InferenceRequest,
        intent_class: str, output_verdict: str,
        route: str, model_used: str,
        context_hits: int, input_tokens: int, output_tokens: int,
        total_latency_ms: int,
    ) -> str:
        """Append a signed record to the audit ledger; return audit_id."""
        if self._audit is None:
            return ""
        try:
            event = self._audit.append(
                "inference_request",
                actor=request.tenant_id,
                subject=f"query:{request.query[:80]}",
                outcome=output_verdict,
                attributes={
                    "request_id":     request_id,
                    "intent_class":   intent_class,
                    "route":          route,
                    "model":          model_used,
                    "context_hits":   context_hits,
                    "input_tokens":   input_tokens,
                    "output_tokens":  output_tokens,
                    "latency_ms":     total_latency_ms,
                    "domain":         request.domain or "",
                    "session_id":     request.session_id,
                },
            )
            return event.signature[:24] if event.signature else ""
        except Exception:
            return ""

    @staticmethod
    def _write_exo_ledger(
        *, request_id: str, query: str,
        backend: str, model: str,
        input_tokens: int, output_tokens: int,
        total_latency_ms: int, verified: bool,
        domain: str = "",
    ) -> None:
        """Direct-write a signed LedgerEntry so RouterPolicy gets health data."""
        try:
            from axiom_exoskeleton_ledger import LedgerEntry, default_ledger_path
            from axiom_signing import derive_key
            ts = (datetime.now(timezone.utc)
                  .isoformat(timespec="milliseconds")
                  .replace("+00:00", "Z"))
            payload = {
                "timestamp_utc": ts,
                "use_case":      "inference_os",
                "token_id":      request_id,
                "input_excerpt": query[:200],
                "input_chars":   len(query),
                "backend":       backend,
                "model":         model,
                "input_tokens":  input_tokens,
                "output_tokens": output_tokens,
                "latency_ms":    total_latency_ms,
                "verified":      verified,
            }
            key  = derive_key(b"axiom-exoskeleton-ledger-v1")
            data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True).encode("utf-8")
            sig  = hmac.new(key, data, hashlib.sha256).hexdigest()
            # domain is outside the HMAC payload (backward-compat with old entries)
            entry = LedgerEntry(**payload, signature=sig, domain=domain)
            path  = default_ledger_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=True) + "\n")
        except Exception:
            pass  # ledger write failures must never break the pipeline


# ── Module-level singleton helpers ────────────────────────────────────────────

_singleton: Optional[InferenceOS] = None


def get_inference_os() -> InferenceOS:
    """Return the module-level cached InferenceOS instance."""
    global _singleton
    if _singleton is None:
        _singleton = InferenceOS()
    return _singleton
