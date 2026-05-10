# -*- coding: utf-8 -*-
"""
AXIOM Memory Engine Tests — ORVL-015 Constitutional Memory Compression
=======================================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BLOCKED: tampered packet, below-threshold recall, undecayed boundary risk
PASSED:  valid compress+store, LSH recall, token savings

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_memory_engine"


def _make_vec(dim=32, seed=42):
    import random
    random.seed(seed)
    v = [random.gauss(0, 1) for _ in range(dim)]
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


def _make_engine():
    from axiom_memory_engine import ConstitutionalMemoryEngine, LSHIndex
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    return ConstitutionalMemoryEngine(store_path=f.name, lsh_index=LSHIndex()), f.name


# ===========================================================================
# SECTION 1 — BLOCKED: violations the engine must prevent
# ===========================================================================

class TestBlocked:

    def test_blocked_tampered_packet_rejected(self):
        """BLOCKED: A packet with a modified hmac_signature must fail
        verification — engine must never trust tampered data."""
        from axiom_memory_engine import FounderAgent

        agent = FounderAgent()
        packet = agent.compress(
            conversation_text="User asked about guard patterns.",
            final_synthesis_vec=_make_vec(),
            domain="general",
            active_constraints=["confidence >= 0.5"],
            resolution="answered",
            sovereign_history=["init", "compress"],
        )
        # Tamper the signature
        tampered_sig = "a" * 64
        assert tampered_sig != packet.hmac_signature
        # Verify the original is valid and tampered is not
        from axiom_memory_engine import _verify_packet
        assert _verify_packet(packet) is True
        # Create tampered copy
        from dataclasses import replace
        tampered = replace(packet, hmac_signature=tampered_sig)
        assert _verify_packet(tampered) is False

    def test_blocked_recall_below_threshold(self):
        """BLOCKED: recall() must return None when no stored packet
        exceeds SIMILARITY_THRESHOLD."""
        engine, path = _make_engine()
        try:
            # Store a packet with one vector
            engine.remember(
                conversation_text="Guard pipeline optimization.",
                final_synthesis_vec=_make_vec(seed=1),
                domain="os_security",
                active_constraints=["latency <= 1ms"],
                resolution="optimized",
                sovereign_history=["init"],
            )
            # Query with orthogonal vector — should not match
            orthogonal = _make_vec(seed=9999)
            result = engine.recall(orthogonal, domain="general")
            assert result is None, "Dissimilar query must return None"
        finally:
            os.unlink(path)

    def test_blocked_boundary_risk_detected(self):
        """BLOCKED: MemoryDecay must flag boundary risk when decayed
        proximity drops to DECAY_FLOOR."""
        from axiom_memory_engine import MemoryDecay, FounderAgent

        decay = MemoryDecay()
        agent = FounderAgent()
        packet = agent.compress(
            conversation_text="Critical security boundary check.",
            final_synthesis_vec=_make_vec(),
            domain="general",
            active_constraints=["boundary >= 0.1"],
            resolution="flagged",
            sovereign_history=["init"],
        )
        # After many days, general domain decays fast
        assert decay.is_boundary_risk(packet, days=100), (
            "Packet with general domain after 100 days must be boundary risk"
        )


# ===========================================================================
# SECTION 2 — PASSED: valid operations must succeed
# ===========================================================================

class TestPassed:

    def test_passed_compress_and_store(self):
        """PASSED: compress() must produce a valid signed packet,
        store() must persist it to JSONL."""
        engine, path = _make_engine()
        try:
            packet = engine.remember(
                conversation_text="A long conversation about axiom guards " * 20,
                final_synthesis_vec=_make_vec(),
                domain="os_security",
                active_constraints=["latency <= 1ms", "accuracy >= 0.95"],
                resolution="completed",
                sovereign_history=["init", "analyze", "synthesize"],
            )
            assert packet.compression_ratio <= 1.0
            assert packet.compression_ratio > 0.0
            assert len(packet.compressed_vec) == 32
            assert len(packet.hmac_signature) == 64
            # Verify persisted
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) >= 1
        finally:
            os.unlink(path)

    def test_passed_lsh_recall(self):
        """PASSED: recall() must return the stored packet when queried
        with the same vector."""
        engine, path = _make_engine()
        try:
            vec = _make_vec(seed=42)
            engine.remember(
                conversation_text="Pattern agent analysis.",
                final_synthesis_vec=vec,
                domain="general",
                active_constraints=["confidence >= 0.5"],
                resolution="answered",
                sovereign_history=["init"],
            )
            result = engine.recall(vec, domain="general")
            assert result is not None, "Same-vector query must recall"
            assert result.domain_cluster == "general"
            assert result.resolution == "answered"
        finally:
            os.unlink(path)

    def test_passed_token_savings(self):
        """PASSED: token_savings() must return compression_ratio."""
        from axiom_memory_engine import FounderAgent

        agent = FounderAgent()
        packet = agent.compress(
            conversation_text="Short text " * 50,
            final_synthesis_vec=_make_vec(),
            domain="financial",
            active_constraints=["audit_trail == true"],
            resolution="logged",
            sovereign_history=["init"],
        )
        from axiom_memory_engine import ConstitutionalMemoryEngine
        savings = ConstitutionalMemoryEngine.token_savings(packet)
        assert 0.0 < savings <= 1.0


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_compression_target_immutable(self):
        """COMPRESSION_TARGET must be 0.05 and not writable."""
        import axiom_memory_engine as m
        assert m.COMPRESSION_TARGET == 0.05
        with pytest.raises((AttributeError, TypeError)):
            m.COMPRESSION_TARGET = 0.99

    def test_similarity_threshold_immutable(self):
        """SIMILARITY_THRESHOLD must be 0.75 and not writable."""
        import axiom_memory_engine as m
        assert m.SIMILARITY_THRESHOLD == 0.75
        with pytest.raises((AttributeError, TypeError)):
            m.SIMILARITY_THRESHOLD = 0.01

    def test_packet_hmac_integrity(self):
        """Packet HMAC must be independently verifiable (BUG-007/008)."""
        from axiom_memory_engine import FounderAgent, _verify_packet

        agent = FounderAgent()
        packet = agent.compress(
            conversation_text="Verification test.",
            final_synthesis_vec=_make_vec(),
            domain="medical",
            active_constraints=["dosage <= 500mg"],
            resolution="verified",
            sovereign_history=["init", "verify"],
        )
        assert _verify_packet(packet), "Packet HMAC must verify"
