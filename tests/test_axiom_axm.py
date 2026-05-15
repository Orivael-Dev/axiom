# -*- coding: utf-8 -*-
"""
AXIOM eXchange Model (.AXM) — ORVL-023 unit tests
==================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

Exercises the hybrid trust model — open container, per-delegate HMAC
signatures, ANF coprocessor driven on verify, MKB BlockRegistry receives
lazy-loaded skills.

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_axm"

from axiom_axm import (
    AXMContainer, AXMHeader, AXMSignatureMismatch, AXMNotVerified, AXMError,
)
from examples.axm_pack_starter import STARTER_SPEC


@pytest.fixture()
def container(tmp_path):
    cpath = tmp_path / "starter.axm"
    return AXMContainer.pack(STARTER_SPEC, str(cpath))


# ===========================================================================
# SECTION 1 — BLOCKED (signature + verification refusals)
# ===========================================================================

class TestAXMBlocked:

    def test_blocked_tampered_header_fails_load(self, tmp_path):
        cpath = tmp_path / "tampered.axm"
        AXMContainer.pack(STARTER_SPEC, str(cpath))
        # Flip one field in header.json so the signature no longer verifies.
        header_path = cpath / "header.json"
        data = json.loads(header_path.read_text(encoding="utf-8"))
        data["core_logic"] = "tampered_core"
        header_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(AXMSignatureMismatch):
            AXMContainer.from_path(str(cpath))

    def test_blocked_unsigned_delegate_fails_verify(self, tmp_path):
        cpath = tmp_path / "with_unsigned.axm"
        AXMContainer.pack(STARTER_SPEC, str(cpath))
        # Corrupt one delegate signature.
        skill_path = cpath / "delegates" / "pii_redactor" / "skill.json"
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        data["signature"] = "0" * 64
        skill_path.write_text(json.dumps(data), encoding="utf-8")
        c = AXMContainer.from_path(str(cpath))
        assert c.verify_proofs() is False

    def test_blocked_route_before_verify_refused(self, container):
        # verify_proofs() has NOT been called yet.
        with pytest.raises(AXMNotVerified):
            container.route("any task")


# ===========================================================================
# SECTION 2 — PASSED (end-to-end exercise of MKB + ANF wiring)
# ===========================================================================

class TestAXMPassed:

    def test_passed_pack_then_load_round_trip(self, tmp_path):
        cpath = tmp_path / "rt.axm"
        original = AXMContainer.pack(STARTER_SPEC, str(cpath))
        reloaded = AXMContainer.from_path(str(cpath))
        assert original.header.signature == reloaded.header.signature
        assert len(original.delegates) == len(reloaded.delegates)
        assert len(original.proofs) == len(reloaded.proofs)

    def test_passed_verify_proofs_invokes_anf(self, container):
        """The ANF coprocessor must be driven once per proof entry."""
        from unittest.mock import MagicMock
        fake_anf = MagicMock()
        fake_anf.process.return_value = {
            "gate_fired": False, "intent_class": "INFORM",
            "cores_active": 20, "energy_ratio": 1.0,
            "distance": 0.0, "latency_ns": 1, "fused_rom_rules": 0,
            "hmac": "0" * 64,
        }
        ok = container.verify_proofs(anf_emulator=fake_anf)
        assert ok is True
        assert fake_anf.process.call_count == len(container.proofs)

    def test_passed_route_lazy_loads_matched_delegates_only(self, container):
        container.verify_proofs()
        r = container.route("Explain the transformer architecture briefly")
        # INFORM intent → pii_redactor + anf_governance, NOT vector_recall.
        loaded = set(r.loaded_skills)
        assert "pii_redactor" in loaded
        assert "anf_governance" in loaded
        assert "vector_recall" not in loaded
        assert "vector_recall" in r.skipped_skills

    def test_passed_loaded_skill_lands_in_mkb_block_registry(self, container):
        container.verify_proofs()
        container.route("Explain transformers")
        # MKB registry is built lazily on first route. Reuse the
        # container's accessor so this matches production semantics.
        registry = container._mkb_registry
        assert registry is not None
        kb = registry.find("pii_redactor")
        assert kb is not None
        assert kb.block_type == "AXM_SKILL"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestAXMInvariants:

    def test_invariant_container_header_is_frozen(self, container):
        # Frozen dataclass — assignment must raise.
        with pytest.raises((AttributeError, Exception)):
            container.header.core_logic = "rewrite"  # type: ignore

    def test_invariant_signing_key_never_exposed(self, container):
        """repr/str must NOT contain the raw HMAC key bytes in any form.
        Only the public fingerprint is allowed."""
        from axiom_signing import derive_key
        raw_container_key = derive_key(b"axiom-axm-container-v1").hex()
        raw_delegate_key  = derive_key(b"axiom-axm-delegate-v1").hex()
        text = repr(container) + str(container)
        assert raw_container_key not in text
        assert raw_delegate_key  not in text
        # Sanity: fingerprint IS exposed.
        assert container.fingerprint() in text
