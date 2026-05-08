# -*- coding: utf-8 -*-
"""
AXIOM MKB Tests — ORVL-004 Modular Constitutional Knowledge Blocks
====================================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BLOCKED: invariants the module must enforce
PASSED:  functional and structural checks that must succeed

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# BUG-003: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_mkb_tests"

HMAC_KEY = b"mkb-test-key"

# Path to a known valid .axiom spec for testing
SPEC_DIR = Path(__file__).resolve().parents[1] / "axiom_files" / "core"
CBV_SPEC = SPEC_DIR / "axiom_cbv.axiom"
CRL_SPEC = SPEC_DIR / "axiom_crl_reward.axiom"
MKB_SPEC = SPEC_DIR / "axiom_mkb.axiom"


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the module must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 3 and not writable."""
        import axiom_mkb as m
        assert m.TRUST_LEVEL == 3
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 99

    def test_blocked_block_types_cannot_mutate(self):
        """BLOCKED: BLOCK_TYPES must be a frozenset and not writable."""
        import axiom_mkb as m
        assert isinstance(m.BLOCK_TYPES, frozenset)
        assert m.BLOCK_TYPES == frozenset({
            "GUARD", "AGENT", "SPEC", "REWARD", "SOVEREIGN", "VALIDATOR",
        })
        with pytest.raises((AttributeError, TypeError)):
            m.BLOCK_TYPES = frozenset({"ROGUE"})

    def test_blocked_registry_version_cannot_mutate(self):
        """BLOCKED: REGISTRY_VERSION must be 1 and not writable."""
        import axiom_mkb as m
        assert m.REGISTRY_VERSION == 1
        with pytest.raises((AttributeError, TypeError)):
            m.REGISTRY_VERSION = 2


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_load_from_axiom_parses_valid_spec(self):
        """PASSED: load_from_axiom on a valid spec produces a complete KnowledgeBlock."""
        from axiom_mkb import KnowledgeBlock, load_from_axiom

        block = load_from_axiom(str(CBV_SPEC), hmac_key=HMAC_KEY)
        assert isinstance(block, KnowledgeBlock)
        assert block.name == "CBVEngine"
        assert block.version == "0.1"
        assert len(block.constraints) > 0
        assert block.manifest_id  # SHA-256 hex
        assert len(block.manifest_id) == 64
        assert block.hmac_signature
        assert len(block.hmac_signature) == 64

    def test_passed_register_and_find(self):
        """PASSED: register appends to registry; find retrieves by name."""
        from axiom_mkb import BlockRegistry, load_from_axiom

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            reg_path = f.name

        try:
            registry = BlockRegistry(hmac_key=HMAC_KEY, registry_path=reg_path)
            block = load_from_axiom(str(CBV_SPEC), hmac_key=HMAC_KEY)
            entry_id = registry.register(block)
            assert entry_id  # non-empty string
            assert len(entry_id) == 64  # SHA-256 hex

            found = registry.find("CBVEngine")
            assert found is not None
            assert found.name == "CBVEngine"

            # Verify file has exactly one line
            with open(reg_path, "r", encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert "entry_id" in entry
            assert "hmac_signature" in entry
        finally:
            os.unlink(reg_path)

    def test_passed_hmac_integrity(self):
        """PASSED: KnowledgeBlock HMAC must verify independently (BUG-007/008)."""
        from axiom_mkb import load_from_axiom

        block = load_from_axiom(str(CBV_SPEC), hmac_key=HMAC_KEY)

        # Re-derive HMAC independently
        canonical = json.dumps({
            "name": block.name,
            "version": block.version,
            "block_type": block.block_type,
            "manifest_id": block.manifest_id,
            "constraint_count": len(block.constraints),
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()  # BUG-007

        assert block.hmac_signature == expected, "KnowledgeBlock HMAC mismatch"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_certify_valid_block(self):
        """certify() on a valid block must produce passed=True."""
        from axiom_mkb import load_from_axiom

        block = load_from_axiom(str(CBV_SPEC), hmac_key=HMAC_KEY)
        result = block.certify()
        assert result.passed is True
        assert result.block_name == "CBVEngine"

    def test_compose_compatible_blocks(self):
        """compose() of two non-overlapping blocks produces a ComposedBlock."""
        from axiom_mkb import BlockRegistry, ComposedBlock, load_from_axiom

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            reg_path = f.name

        try:
            registry = BlockRegistry(hmac_key=HMAC_KEY, registry_path=reg_path)
            block_a = load_from_axiom(str(CBV_SPEC), hmac_key=HMAC_KEY)
            block_b = load_from_axiom(str(CRL_SPEC), hmac_key=HMAC_KEY)
            registry.register(block_a)
            registry.register(block_b)

            composed = registry.compose(block_a, block_b)
            assert isinstance(composed, ComposedBlock)
            assert composed.parent_a == block_a.name
            assert composed.parent_b == block_b.name
            # Merged constraints include both parents'
            assert len(composed.constraints) >= len(block_a.constraints)
            assert composed.hmac_signature
            assert len(composed.hmac_signature) == 64
        finally:
            os.unlink(reg_path)

    def test_list_blocks_by_type(self):
        """list_blocks filters registry by block_type."""
        from axiom_mkb import BlockRegistry, load_from_axiom

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            reg_path = f.name

        try:
            registry = BlockRegistry(hmac_key=HMAC_KEY, registry_path=reg_path)
            block_a = load_from_axiom(str(CBV_SPEC), hmac_key=HMAC_KEY)
            block_b = load_from_axiom(str(CRL_SPEC), hmac_key=HMAC_KEY)
            registry.register(block_a)
            registry.register(block_b)

            validators = registry.list_blocks("VALIDATOR")
            # CBV is a VALIDATOR type
            names = [b.name for b in validators]
            assert "CBVEngine" in names

            # Filter by a type neither block has should return empty
            guards = registry.list_blocks("GUARD")
            overlap = [b for b in guards if b.name in ("CBVEngine", "ConstitutionalRewardFunction")]
            assert len(overlap) == 0
        finally:
            os.unlink(reg_path)
