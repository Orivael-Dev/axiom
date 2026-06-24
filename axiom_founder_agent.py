"""axiom_founder_agent.py — MicroLM Founder Agent distillation pipeline.

Layer: 2 (Memory + EventToken Cache) — ORVL-015

Autonomously distills raw InteractionRecords into signed ConstitutionalPackets
without any manual field specification.  Uses RAG via the LSHIndex to retrieve
constitutionally similar past packets and fold their governance signals into
each new packet — implementing the Complementary Learning System (CLS) loop
described in the Constitutional Memory paper.

Pipeline:
  1. Embed interaction text → raw_vec
  2. RAG: retrieve constitutionally similar past packets from memory
  3. Extract domain (keyword match + RAG domain vote)
  4. Extract constraints (pattern match + RAG inheritance)
  5. Compute boundary_proximity (intent signals + RAG blend)
  6. Blend raw_vec with retrieved constitutional vectors
  7. Detect resolution from response output signals
  8. Build sovereign_history from pipeline stage markers
  9. Compress → HMAC-signed ConstitutionalPacket

"Lossless for governance, lossy for language." — ORVL-015
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from axiom_memory_engine import (
    ConstitutionalMemoryEngine,
    ConstitutionalPacket,
    FounderAgent,
    embed_text,
    _quantize_vec,
    SIMILARITY_THRESHOLD,
)
from axiom_signing import derive_key

# Optional intent classifier — graceful degradation if unavailable
try:
    from axiom_intent_classifier import AxiomIntentClassifier
    _CLASSIFIER_KEY = derive_key(b"axiom-intent-classifier-v1")
    _intent_clf: Optional[AxiomIntentClassifier] = AxiomIntentClassifier(
        hmac_key=_CLASSIFIER_KEY
    )
except Exception:
    _intent_clf = None

# ── CANNOT_MUTATE sentinels ───────────────────────────────────────
_MAX_RAG_HITS: int      = 3     # retrieved packets to blend per distillation
_RAG_VEC_BLEND: float   = 0.20  # weight of retrieved vectors in final embedding
_RAG_PROX_BLEND: float  = 0.30  # weight of retrieved proximity in final proximity
_MAX_CONSTRAINTS: int   = 8     # cap on active_constraints per packet

MAX_RAG_HITS     = _MAX_RAG_HITS
RAG_VEC_BLEND    = _RAG_VEC_BLEND
RAG_PROX_BLEND   = _RAG_PROX_BLEND
MAX_CONSTRAINTS  = _MAX_CONSTRAINTS

import types as _types
_mod = sys.modules[__name__]
_LOCKED = frozenset({"MAX_RAG_HITS", "RAG_VEC_BLEND", "RAG_PROX_BLEND", "MAX_CONSTRAINTS"})


class _FrozenMod(type(_mod)):
    def __setattr__(self, name: str, value: object) -> None:
        if name in _LOCKED:
            raise AttributeError(f"{name} is CANNOT_MUTATE")
        super().__setattr__(name, value)


_mod.__class__ = _FrozenMod

# ── Domain keyword table ──────────────────────────────────────────
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "legal":       ["law", "legal", "contract", "nda", "clause", "gdpr",
                    "compliance", "statute", "liability", "court", "patent"],
    "medical":     ["medical", "clinical", "patient", "diagnosis", "drug",
                    "dosage", "symptom", "treatment", "therapy", "health"],
    "financial":   ["financial", "audit", "tax", "budget", "revenue",
                    "payment", "invoice", "accounting", "investment", "cost"],
    "os_security": ["security", "vulnerability", "exploit", "cve", "patch",
                    "firewall", "attack", "malware", "threat", "breach"],
    "research":    ["research", "paper", "study", "hypothesis", "dataset",
                    "experiment", "benchmark", "evaluation", "metric"],
}

# ── Constraint extraction patterns ───────────────────────────────
_CONSTRAINT_RE: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bmust\s+([\w][\w\s]{2,40}?)(?:[.,;]|$)', re.I),   "must {}"),
    (re.compile(r'\bcannot\s+([\w][\w\s]{2,40}?)(?:[.,;]|$)', re.I), "cannot {}"),
    (re.compile(r'\bshould\s+([\w][\w\s]{2,40}?)(?:[.,;]|$)', re.I), "should {}"),
    (re.compile(r'\brequires?\s+([\w][\w\s]{2,40}?)(?:[.,;]|$)', re.I), "requires {}"),
    # threshold constraints: "latency <= 1ms", "accuracy >= 0.95"
    (re.compile(r'([\w_]+\s*[<>]=?\s*[\d.]+\s*\w{0,8})', re.I), "{}"),
]

# ── Resolution signal table ───────────────────────────────────────
_RESOLUTION_SIGNALS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"i (cannot|can't|am unable|won't|will not)", re.I), "refused"),
    (re.compile(r"(resolved|done|completed|finished|answered)[:\s]", re.I), "answered"),
    (re.compile(r"(escalat|requires approval|sovereignty)", re.I), "escalated"),
    (re.compile(r"(not sure|unclear|ambiguous|uncertain)", re.I), "uncertain"),
    (re.compile(r"(however|but i|partially|in part)", re.I), "partial"),
]

# ── Intent class → base boundary proximity ────────────────────────
_INTENT_PROXIMITY: dict[str, float] = {
    "HARM":      0.92,
    "DECEIVE":   0.90,
    "REFUSE":    0.78,
    "UNCERTAIN": 0.50,
    "CLARIFY":   0.38,
    "INFORM":    0.18,
}


# ── InteractionRecord ─────────────────────────────────────────────

@dataclass
class InteractionRecord:
    """Raw interaction — the input unit to the MicroLM distillation pipeline.

    Attributes:
        query:           The user's input or prompt.
        response:        The model's output or response text.
        domain_hint:     Optional caller-provided domain override.
        pipeline_stages: Ordered stage names visited (for sovereign_history).
        metadata:        Arbitrary caller metadata (not stored in packet).
    """
    query:           str
    response:        str
    domain_hint:     Optional[str]     = None
    pipeline_stages: tuple[str, ...]   = ()
    metadata:        dict              = field(default_factory=dict)

    def combined_text(self) -> str:
        return f"{self.query} {self.response}".strip()


# ── Internal helpers ──────────────────────────────────────────────

def _extract_domain(text: str, rag_hits: list[tuple[float, ConstitutionalPacket]],
                    hint: Optional[str]) -> str:
    """Domain extraction: keyword match + RAG domain vote."""
    if hint and hint in _DOMAIN_KEYWORDS:
        return hint

    # Keyword match on combined text
    lower = text.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for kw in keywords if kw in lower)

    # RAG domain vote: weight each retrieved packet's domain by similarity score
    rag_votes: dict[str, float] = {}
    for sim, pkt in rag_hits:
        rag_votes[pkt.domain_cluster] = rag_votes.get(pkt.domain_cluster, 0.0) + sim

    # Blend: text score (normalised to [0,1]) + RAG vote weight
    best_domain = "general"
    best_score = 0.0
    max_kw = max(scores.values()) or 1
    for domain, kw_score in scores.items():
        combined = (kw_score / max_kw) * 0.6 + rag_votes.get(domain, 0.0) * 0.4
        if combined > best_score:
            best_score = combined
            best_domain = domain

    return best_domain if best_score > 0.1 else "general"


def _extract_constraints(text: str, domain: str,
                         rag_hits: list[tuple[float, ConstitutionalPacket]]) -> tuple[str, ...]:
    """Constraint extraction: regex patterns + RAG inheritance from same domain."""
    seen: set[str] = set()
    constraints: list[str] = []

    # Pattern extraction from combined text
    for pattern, template in _CONSTRAINT_RE:
        for m in pattern.finditer(text):
            c = template.format(m.group(1).strip().lower()[:60])
            if c not in seen:
                seen.add(c)
                constraints.append(c)
                if len(constraints) >= _MAX_CONSTRAINTS:
                    break
        if len(constraints) >= _MAX_CONSTRAINTS:
            break

    # RAG constraint inheritance — only from same domain, above threshold
    for sim, pkt in rag_hits:
        if pkt.domain_cluster == domain and sim >= SIMILARITY_THRESHOLD:
            for c in pkt.active_constraints:
                if c not in seen and len(constraints) < _MAX_CONSTRAINTS:
                    seen.add(c)
                    constraints.append(c)

    return tuple(constraints)


def _detect_resolution(response: str) -> str:
    """Heuristic resolution detection from response output signals."""
    for pattern, resolution in _RESOLUTION_SIGNALS:
        if pattern.search(response):
            return resolution
    return "answered"


def _boundary_proximity(
    text: str,
    intent_class: Optional[str],
    rag_hits: list[tuple[float, ConstitutionalPacket]],
) -> float:
    """Compute boundary_proximity [0.0–1.0].

    Base from intent class (high = closer to constitutional boundary),
    blended with the proximity scores of retrieved similar packets.
    """
    base = _INTENT_PROXIMITY.get(intent_class or "INFORM", 0.20)

    if rag_hits:
        rag_avg = sum(sim * p.boundary_proximity for sim, p in rag_hits) / len(rag_hits)
        proximity = base * (1.0 - _RAG_PROX_BLEND) + rag_avg * _RAG_PROX_BLEND
    else:
        proximity = base

    return round(min(max(proximity, 0.01), 0.99), 4)


def _blend_vec(raw_vec: list[float],
               rag_hits: list[tuple[float, ConstitutionalPacket]]) -> list[float]:
    """Blend raw text embedding with retrieved constitutional vectors.

    Pulls the new packet toward the constitutional cluster of similar past
    interactions, implementing the CLS resonance replay from the paper.
    """
    if not rag_hits:
        return raw_vec

    blended = list(raw_vec)
    for sim, pkt in rag_hits:
        w = _RAG_VEC_BLEND * sim
        for i, v in enumerate(pkt.compressed_vec):
            blended[i] += w * v

    mag = math.sqrt(sum(x * x for x in blended)) or 1.0
    return [x / mag for x in blended]


# ── MicroLM ───────────────────────────────────────────────────────

class MicroLM:
    """Founder Agent — lightweight buffer model that autonomously distills
    InteractionRecords into ConstitutionalPackets.

    Uses RAG via the engine's LSHIndex to retrieve constitutionally similar
    past packets and fold their governance signals (domain, constraints,
    proximity, vector position) into each new packet.  No LLM calls are made;
    all extraction is deterministic and sub-millisecond.

    Usage::

        micro = MicroLM()
        record = InteractionRecord(
            query="What does GDPR Article 9 require?",
            response="Article 9 prohibits processing of special categories...",
            pipeline_stages=("intent_gate", "retrieval", "generation", "audit"),
        )
        packet = micro.distill(record, engine)
    """

    def distill(
        self,
        record: InteractionRecord,
        engine: ConstitutionalMemoryEngine,
        *,
        store: bool = True,
    ) -> ConstitutionalPacket:
        """Full distillation pipeline.

        Args:
            record: Raw interaction (query + response + metadata).
            engine: ConstitutionalMemoryEngine (provides LSH index + store).
            store:  If True (default), persists the packet and indexes it.

        Returns:
            Signed ConstitutionalPacket.
        """
        combined = record.combined_text()

        # 1. Embed raw text
        raw_vec = embed_text(combined)

        # 2. RAG: retrieve similar past packets
        rag_hits = self._rag(raw_vec, engine)

        # 3. Domain
        domain = _extract_domain(combined, rag_hits, record.domain_hint)

        # 4. Constraints
        constraints = _extract_constraints(combined, domain, rag_hits)

        # 5. Intent → boundary proximity
        intent_class = self._classify_intent(record.response)
        proximity = _boundary_proximity(combined, intent_class, rag_hits)

        # 6. Blend embedding with retrieved constitutional vectors
        blended_vec = _blend_vec(raw_vec, rag_hits)

        # 7. Resolution
        resolution = _detect_resolution(record.response)

        # 8. Sovereign history
        history = list(record.pipeline_stages) + ["distill"]

        # 9. Compress → signed ConstitutionalPacket
        agent = FounderAgent()
        packet = agent.compress(
            conversation_text=combined,
            final_synthesis_vec=blended_vec,
            domain=domain,
            active_constraints=list(constraints),
            resolution=resolution,
            sovereign_history=history,
        )

        # Patch boundary_proximity with our computed value (FounderAgent uses random)
        from dataclasses import replace
        from axiom_memory_engine import _sign_packet
        unsigned = replace(packet, boundary_proximity=proximity, hmac_signature="")
        sig = _sign_packet(unsigned)
        packet = replace(unsigned, hmac_signature=sig)

        if store:
            agent.store(packet, engine._path, engine._lsh)

        return packet

    # ── internal ─────────────────────────────────────────────────

    def _rag(
        self,
        raw_vec: list[float],
        engine: ConstitutionalMemoryEngine,
    ) -> list[tuple[float, ConstitutionalPacket]]:
        """Retrieve top-k constitutionally similar packets via LSH."""
        hits = engine._lsh.retrieve(raw_vec, k=_MAX_RAG_HITS)
        return [(sim, pkt) for sim, pkt in hits if sim >= SIMILARITY_THRESHOLD * 0.8]

    def _classify_intent(self, response: str) -> Optional[str]:
        """Run intent classifier if available; fall back to heuristic."""
        if _intent_clf is not None:
            try:
                result = _intent_clf.classify(response)
                return result.intent_class
            except Exception:
                pass
        # Heuristic fallback
        lower = response.lower()
        if any(w in lower for w in ["i cannot", "i can't", "unable to", "won't"]):
            return "REFUSE"
        if any(w in lower for w in ["harmful", "dangerous", "illegal"]):
            return "HARM"
        return "INFORM"
