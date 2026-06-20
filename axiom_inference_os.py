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
    timestamp:       str
    signature:       str

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
        self._retriever  = None
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
        t0 = time.perf_counter()
        try:
            if self._backend is not None:
                route       = self._backend.name
                model_used  = self._backend.model
            stages.append(InferenceStageResult.make(
                "route", "ok", _ms(t0),
                {"route": route, "model": model_used}
            ))
        except Exception as exc:
            stages.append(InferenceStageResult.make(
                "route", "degraded", _ms(t0), {"error": str(exc)[:120]}
            ))

        # ── Stage 2 / Step 4: Retrieval ───────────────────────────────────────
        context_str, context_hits, context_snippet = "", 0, ""
        t0 = time.perf_counter()
        if request.use_retrieval and self._retriever is not None:
            try:
                hits = self._retriever.retrieve(
                    request.query,
                    k=_MAX_RETRIEVAL_K,
                    domain=request.domain,
                )
                if hits:
                    context_str, context_hits = self._pack_context(
                        hits, request.max_context_chars
                    )
                    context_snippet = context_str[:200]
                    stages.append(InferenceStageResult.make(
                        "retrieval", "ok", _ms(t0),
                        {"hits": context_hits, "snippet": context_snippet}
                    ))
                else:
                    stages.append(InferenceStageResult.make(
                        "retrieval", "ok", _ms(t0), {"hits": 0}
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
        tokens_saved = len(context_str) // 4 if context_str else 0

        # ── Stage 3 / Step 5: Generation ──────────────────────────────────────
        output, input_tokens, output_tokens = "", 0, 0
        t0 = time.perf_counter()
        if self._backend is not None:
            try:
                system_prompt = (
                    "You are a helpful, accurate assistant governed by the Axiom Inference OS. "
                    "Answer concisely based on the provided context."
                )
                if context_str:
                    system_prompt = context_str + "\n\n" + system_prompt

                result = self._backend.generate(
                    system=system_prompt,
                    prompt=request.query,
                    max_output_tokens=512,
                )
                output        = result.text
                input_tokens  = result.input_tokens
                output_tokens = result.output_tokens
                route         = result.backend
                model_used    = result.model
                stages.append(InferenceStageResult.make(
                    "generation", "ok", _ms(t0),
                    {
                        "backend":       result.backend,
                        "model":         result.model,
                        "input_tokens":  input_tokens,
                        "output_tokens": output_tokens,
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
        )

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
    def _pack_context(hits: list, max_chars: int) -> tuple[str, int]:
        """Assemble retrieved sources into a context string for the system prompt."""
        parts: List[str] = ["=== RETRIEVED CONTEXT ==="]
        total = len(parts[0])
        count = 0
        for h in hits:
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
            entry = LedgerEntry(**payload, signature=sig)
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
