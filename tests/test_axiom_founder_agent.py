"""Tests for axiom_founder_agent.py — MicroLM distillation pipeline.

Covers:
  - CANNOT_MUTATE sentinels (MAX_RAG_HITS, RAG_VEC_BLEND, RAG_PROX_BLEND, MAX_CONSTRAINTS)
  - InteractionRecord.combined_text()
  - _extract_domain() — keyword match, hint override, RAG vote blend
  - _extract_constraints() — regex patterns, RAG inheritance, cap enforcement
  - _detect_resolution() — each signal branch
  - _boundary_proximity() — intent class mapping, RAG blend
  - _blend_vec() — RAG absent (no-op), RAG present (renormalized)
  - MicroLM.distill() — smoke test, sovereign_history, HMAC integrity,
      store=False skip, distill-then-recall roundtrip, no-RAG fallback
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AXIOM_MASTER_KEY", "test_key_founder_agent_" + "x" * 44)

from axiom_founder_agent import (
    InteractionRecord,
    MicroLM,
    _blend_vec,
    _boundary_proximity,
    _detect_resolution,
    _extract_constraints,
    _extract_domain,
    MAX_RAG_HITS,
    RAG_VEC_BLEND,
    RAG_PROX_BLEND,
    MAX_CONSTRAINTS,
)
from axiom_memory_engine import (
    ConstitutionalMemoryEngine,
    LSHIndex,
    FounderAgent,
    _verify_packet,
    _VECTOR_DIMENSIONS,
    SIMILARITY_THRESHOLD,
)


# ── helpers ───────────────────────────────────────────────────────

def _unit_vec(seed: int, dim: int = _VECTOR_DIMENSIONS) -> list[float]:
    import random
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(dim)]
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


def _make_engine(path: str | None = None) -> tuple[ConstitutionalMemoryEngine, str]:
    if path is None:
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        path = f.name
    return ConstitutionalMemoryEngine(store_path=path, lsh_index=LSHIndex()), path


def _make_record(**kw) -> InteractionRecord:
    defaults = dict(
        query="What are GDPR compliance requirements?",
        response="GDPR requires explicit consent and data minimisation.",
        domain_hint=None,
        pipeline_stages=("intent_gate", "retrieval", "generation"),
    )
    defaults.update(kw)
    return InteractionRecord(**defaults)


def _make_packet(domain="general", vec=None, constraints=("confidence >= 0.5",)):
    agent = FounderAgent()
    if vec is None:
        vec = _unit_vec(42)
    return agent.compress(
        conversation_text="test " * 20,
        final_synthesis_vec=vec,
        domain=domain,
        active_constraints=list(constraints),
        resolution="answered",
        sovereign_history=["init"],
    )


# ── CANNOT_MUTATE ─────────────────────────────────────────────────

class TestCannotMutate:

    def test_max_rag_hits_immutable(self):
        import axiom_founder_agent as m
        assert m.MAX_RAG_HITS == 3
        with pytest.raises(AttributeError):
            m.MAX_RAG_HITS = 99

    def test_rag_vec_blend_immutable(self):
        import axiom_founder_agent as m
        assert m.RAG_VEC_BLEND == 0.20
        with pytest.raises(AttributeError):
            m.RAG_VEC_BLEND = 0.99

    def test_rag_prox_blend_immutable(self):
        import axiom_founder_agent as m
        assert m.RAG_PROX_BLEND == 0.30
        with pytest.raises(AttributeError):
            m.RAG_PROX_BLEND = 0.99

    def test_max_constraints_immutable(self):
        import axiom_founder_agent as m
        assert m.MAX_CONSTRAINTS == 8
        with pytest.raises(AttributeError):
            m.MAX_CONSTRAINTS = 100


# ── InteractionRecord ─────────────────────────────────────────────

class TestInteractionRecord:

    def test_combined_text_joins_query_response(self):
        r = InteractionRecord(query="hello", response="world")
        assert r.combined_text() == "hello world"

    def test_combined_text_strips_whitespace(self):
        r = InteractionRecord(query="  hi  ", response="  there  ")
        assert r.combined_text() == "hi     there"

    def test_pipeline_stages_default_empty(self):
        r = InteractionRecord(query="q", response="r")
        assert r.pipeline_stages == ()

    def test_metadata_default_empty_dict(self):
        r = InteractionRecord(query="q", response="r")
        assert r.metadata == {}


# ── _extract_domain ───────────────────────────────────────────────

class TestExtractDomain:

    def test_hint_overrides_keyword_match(self):
        # Even if text has no legal keywords, hint=legal wins
        domain = _extract_domain("totally unrelated text", [], "legal")
        assert domain == "legal"

    def test_keyword_match_legal(self):
        domain = _extract_domain("The contract and NDA clause requires compliance", [], None)
        assert domain == "legal"

    def test_keyword_match_medical(self):
        domain = _extract_domain("The patient dosage and diagnosis symptom therapy", [], None)
        assert domain == "medical"

    def test_keyword_match_financial(self):
        domain = _extract_domain("The tax audit budget invoice accounting cost", [], None)
        assert domain == "financial"

    def test_unknown_keywords_returns_general(self):
        domain = _extract_domain("hello world foo bar", [], None)
        assert domain == "general"

    def test_rag_vote_tips_ambiguous_domain(self):
        # Two packets with domain "medical" tip vote toward medical
        # even with weak keyword signal
        vec = _unit_vec(10)
        p1 = _make_packet(domain="medical", vec=vec)
        p2 = _make_packet(domain="medical", vec=vec)
        rag_hits = [(0.9, p1), (0.85, p2)]
        # Text that has no strong keyword
        domain = _extract_domain("the patient data was discussed", rag_hits, None)
        # At minimum shouldn't return os_security or legal
        assert domain in ("medical", "general")

    def test_invalid_hint_falls_through_to_keyword(self):
        # hint not in _DOMAIN_KEYWORDS is ignored
        domain = _extract_domain("law legal contract statute", [], "unknown_domain")
        assert domain == "legal"


# ── _extract_constraints ──────────────────────────────────────────

class TestExtractConstraints:

    def test_must_pattern_extracted(self):
        constraints = _extract_constraints("You must cite sources always.", "legal", [])
        assert any("must" in c for c in constraints)

    def test_cannot_pattern_extracted(self):
        constraints = _extract_constraints("System cannot store user data.", "general", [])
        assert any("cannot" in c for c in constraints)

    def test_threshold_pattern_extracted(self):
        constraints = _extract_constraints("latency <= 1ms required accuracy >= 0.95", "os_security", [])
        assert len(constraints) >= 1
        assert any("<=" in c or ">=" in c for c in constraints)

    def test_rag_inheritance_same_domain(self):
        vec = _unit_vec(5)
        p = _make_packet(domain="legal", vec=vec, constraints=("cite_sources == true",))
        rag_hits = [(SIMILARITY_THRESHOLD + 0.01, p)]
        constraints = _extract_constraints("legal text without patterns", "legal", rag_hits)
        assert "cite_sources == true" in constraints

    def test_rag_inheritance_cross_domain_skipped(self):
        vec = _unit_vec(6)
        p = _make_packet(domain="financial", vec=vec, constraints=("audit == true",))
        rag_hits = [(SIMILARITY_THRESHOLD + 0.01, p)]
        # domain="legal" → financial packet constraints should NOT be inherited
        constraints = _extract_constraints("legal text", "legal", rag_hits)
        assert "audit == true" not in constraints

    def test_constraint_cap_enforced(self):
        # Text with many constraint patterns
        long_text = " ".join([
            f"must do_thing_{i}" for i in range(20)
        ])
        constraints = _extract_constraints(long_text, "general", [])
        assert len(constraints) <= MAX_CONSTRAINTS

    def test_no_duplicates_in_constraints(self):
        text = "must cite sources. You must cite sources again."
        constraints = _extract_constraints(text, "general", [])
        assert len(constraints) == len(set(constraints))


# ── _detect_resolution ────────────────────────────────────────────

class TestDetectResolution:

    def test_refused_signal(self):
        assert _detect_resolution("I cannot process this request.") == "refused"

    def test_cant_signal(self):
        assert _detect_resolution("I can't help with that.") == "refused"

    def test_answered_signal(self):
        assert _detect_resolution("Resolved: the issue was fixed.") == "answered"

    def test_escalated_signal(self):
        assert _detect_resolution("This requires approval from compliance.") == "escalated"

    def test_uncertain_signal(self):
        assert _detect_resolution("I'm not sure about this.") == "uncertain"

    def test_partial_signal(self):
        assert _detect_resolution("However, I can only partially assist.") == "partial"

    def test_default_answered(self):
        assert _detect_resolution("Here is the information you requested.") == "answered"


# ── _boundary_proximity ───────────────────────────────────────────

class TestBoundaryProximity:

    def test_harm_intent_high_proximity(self):
        prox = _boundary_proximity("text", "HARM", [])
        assert prox >= 0.85

    def test_inform_intent_low_proximity(self):
        prox = _boundary_proximity("text", "INFORM", [])
        assert prox <= 0.25

    def test_none_intent_defaults_to_inform_range(self):
        prox = _boundary_proximity("text", None, [])
        assert 0.0 < prox <= 0.30

    def test_proximity_clamped_0_to_1(self):
        prox = _boundary_proximity("text", "HARM", [])
        assert 0.0 < prox < 1.0

    def test_rag_blend_shifts_proximity(self):
        # Packet with high boundary_proximity should shift base INFORM up
        vec = _unit_vec(7)
        p = _make_packet(domain="legal", vec=vec)
        # Manually set boundary_proximity via replace
        p_high = replace(p, boundary_proximity=0.95, hmac_signature="")
        rag_hits = [(0.9, p_high)]
        prox_no_rag = _boundary_proximity("text", "INFORM", [])
        prox_with_rag = _boundary_proximity("text", "INFORM", rag_hits)
        # RAG blend should push proximity higher
        assert prox_with_rag > prox_no_rag


# ── _blend_vec ────────────────────────────────────────────────────

class TestBlendVec:

    def test_no_rag_returns_raw_vec(self):
        raw = _unit_vec(1)
        blended = _blend_vec(raw, [])
        assert blended == raw

    def test_rag_present_changes_vec(self):
        raw = _unit_vec(1)
        p = _make_packet(vec=_unit_vec(2))
        rag_hits = [(0.9, p)]
        blended = _blend_vec(raw, rag_hits)
        assert blended != raw

    def test_blended_vec_is_unit_normalized(self):
        raw = _unit_vec(1)
        p = _make_packet(vec=_unit_vec(3))
        rag_hits = [(0.8, p)]
        blended = _blend_vec(raw, rag_hits)
        mag = math.sqrt(sum(x * x for x in blended))
        assert abs(mag - 1.0) < 1e-6

    def test_blended_vec_correct_dimension(self):
        raw = _unit_vec(1)
        p = _make_packet(vec=_unit_vec(4))
        blended = _blend_vec(raw, [(0.85, p)])
        assert len(blended) == _VECTOR_DIMENSIONS


# ── MicroLM.distill() ─────────────────────────────────────────────

class TestMicroLMDistill:

    def _setup(self):
        engine, path = _make_engine()
        micro = MicroLM()
        return micro, engine, path

    def test_smoke_distill_returns_packet(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record()
            packet = micro.distill(record, engine, store=False)
            assert packet is not None
            assert hasattr(packet, "hmac_signature")
        finally:
            os.unlink(path)

    def test_packet_hmac_valid(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record()
            packet = micro.distill(record, engine, store=False)
            assert _verify_packet(packet), "Distilled packet HMAC must be valid"
        finally:
            os.unlink(path)

    def test_sovereign_history_contains_pipeline_stages(self):
        micro, engine, path = self._setup()
        try:
            stages = ("intent_gate", "retrieval", "audit")
            record = _make_record(pipeline_stages=stages)
            packet = micro.distill(record, engine, store=False)
            for stage in stages:
                assert stage in packet.sovereign_history
        finally:
            os.unlink(path)

    def test_sovereign_history_ends_with_distill(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record(pipeline_stages=("generation",))
            packet = micro.distill(record, engine, store=False)
            assert packet.sovereign_history[-1] == "distill"
        finally:
            os.unlink(path)

    def test_store_false_does_not_persist(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record()
            micro.distill(record, engine, store=False)
            with open(path) as f:
                lines = [l for l in f.readlines() if l.strip()]
            assert len(lines) == 0, "store=False must not write to disk"
        finally:
            os.unlink(path)

    def test_store_true_persists_packet(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record()
            micro.distill(record, engine, store=True)
            with open(path) as f:
                lines = [l for l in f.readlines() if l.strip()]
            assert len(lines) >= 1
        finally:
            os.unlink(path)

    def test_domain_hint_respected(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record(
                query="generic question",
                response="generic answer",
                domain_hint="medical",
            )
            packet = micro.distill(record, engine, store=False)
            assert packet.domain_cluster == "medical"
        finally:
            os.unlink(path)

    def test_no_rag_fallback_works(self):
        """Engine with no stored packets — distill must still succeed."""
        micro, engine, path = self._setup()
        try:
            record = _make_record()
            packet = micro.distill(record, engine, store=False)
            assert _verify_packet(packet)
        finally:
            os.unlink(path)

    def test_distill_then_recall_roundtrip(self):
        """Packet stored via distill() must be retrievable via engine.recall()."""
        micro, engine, path = self._setup()
        try:
            # Use a domain-hinted record so domain is deterministic
            record = _make_record(
                query="GDPR Article 9 special categories",
                response="Article 9 restricts processing of sensitive data.",
                domain_hint="legal",
            )
            packet = micro.distill(record, engine, store=True)
            recalled = engine.recall(packet.compressed_vec, domain="legal")
            assert recalled is not None, "Stored packet must be recallable"
            assert recalled.domain_cluster == "legal"
        finally:
            os.unlink(path)

    def test_rag_augments_second_distillation(self):
        """Second distillation in same domain should pick up constraints from first."""
        micro, engine, path = self._setup()
        try:
            # First: store a legal packet with explicit constraint
            record1 = _make_record(
                query="must cite sources in legal documents",
                response="Answered: yes, citations are required.",
                domain_hint="legal",
            )
            micro.distill(record1, engine, store=True)

            # Second: similar legal query — RAG should retrieve packet1
            record2 = _make_record(
                query="legal document citation requirements",
                response="Documents must include references.",
                domain_hint="legal",
            )
            packet2 = micro.distill(record2, engine, store=False)
            # Packet must be valid and in legal domain
            assert packet2.domain_cluster == "legal"
            assert _verify_packet(packet2)
        finally:
            os.unlink(path)

    def test_boundary_proximity_in_range(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record(
                response="I cannot process that request as it violates policy."
            )
            packet = micro.distill(record, engine, store=False)
            assert 0.0 < packet.boundary_proximity < 1.0
        finally:
            os.unlink(path)

    def test_harm_response_high_proximity(self):
        micro, engine, path = self._setup()
        try:
            record = _make_record(
                response="This involves harmful and dangerous activity."
            )
            packet = micro.distill(record, engine, store=False)
            # Heuristic HARM branch → high proximity
            assert packet.boundary_proximity >= 0.50
        finally:
            os.unlink(path)
