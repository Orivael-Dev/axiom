# -*- coding: utf-8 -*-
"""
AXIOM Constitutional Amputate Tests — ORVL-012 Component 3
===========================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

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
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_amputate_tests"

HMAC_KEY = b"amputate-test-key"


def _mock_registry(blocks=None, composed=None):
    """Create a mock BlockRegistry with quarantine/find_composed/rebuild_without."""
    reg = MagicMock()
    reg.quarantine = MagicMock()
    reg.find_composed = MagicMock(return_value=composed or [])
    reg.rebuild_without = MagicMock()
    return reg


# ===========================================================================
# SECTION 1 — BLOCKED: invariants the module must enforce
# ===========================================================================

class TestBlocked:

    def test_blocked_requires_trust_level_cannot_mutate(self):
        """BLOCKED: REQUIRES_TRUST_LEVEL must be 4 and not writable."""
        import axiom_amputate as m
        assert m.REQUIRES_TRUST_LEVEL == 4
        with pytest.raises((AttributeError, TypeError)):
            m.REQUIRES_TRUST_LEVEL = 0

    def test_blocked_human_review_cannot_mutate(self):
        """BLOCKED: HUMAN_REVIEW_REQUIRED must be True and not writable."""
        import axiom_amputate as m
        assert m.HUMAN_REVIEW_REQUIRED is True
        with pytest.raises((AttributeError, TypeError)):
            m.HUMAN_REVIEW_REQUIRED = False

    def test_blocked_trust_level_cannot_mutate(self):
        """BLOCKED: TRUST_LEVEL must be 4 and not writable."""
        import axiom_amputate as m
        assert m.TRUST_LEVEL == 4
        with pytest.raises((AttributeError, TypeError)):
            m.TRUST_LEVEL = 0


# ===========================================================================
# SECTION 2 — PASSED: functional and structural checks
# ===========================================================================

class TestPassed:

    def test_passed_execute_quarantines_block(self):
        """PASSED: execute() must call registry.quarantine with block_id."""
        from axiom_amputate import ConstitutionalAmputate
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            amp = ConstitutionalAmputate(hmac_key=HMAC_KEY, log_path=log_path)
            reg = _mock_registry(composed=["comp-A+B"])
            amp.execute("block-A", reg, caller_trust=4)
            reg.quarantine.assert_called_once_with("block-A")
        finally:
            os.unlink(log_path)

    def test_passed_execute_finds_affected(self):
        """PASSED: execute() must call registry.find_composed to find affected."""
        from axiom_amputate import ConstitutionalAmputate
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            amp = ConstitutionalAmputate(hmac_key=HMAC_KEY, log_path=log_path)
            reg = _mock_registry(composed=["comp-X+Y", "comp-X+Z"])
            result = amp.execute("block-X", reg, caller_trust=4)
            reg.find_composed.assert_called_once_with("block-X")
            assert result.affected_blocks == ["comp-X+Y", "comp-X+Z"]
        finally:
            os.unlink(log_path)

    def test_passed_amputate_event_hmac_integrity(self):
        """PASSED: Amputate event HMAC verifies independently (BUG-007/008)."""
        from axiom_amputate import ConstitutionalAmputate
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            amp = ConstitutionalAmputate(hmac_key=HMAC_KEY, log_path=log_path)
            reg = _mock_registry(composed=["comp-A"])
            result = amp.execute("block-A", reg, caller_trust=4)

            assert result.event_signature
            assert len(result.event_signature) == 64

            # Read logged record and verify HMAC
            with open(log_path, "r", encoding="utf-8") as f:
                record = json.loads(f.readline())
            canonical = json.dumps({
                "block_id": record["block_id"],
                "affected_count": len(record["affected_blocks"]),
                "affected_blocks": sorted(record["affected_blocks"]),
                "timestamp": record["timestamp"],
            }, sort_keys=True, ensure_ascii=True).encode("utf-8")
            expected = hmac.new(HMAC_KEY, canonical, hashlib.sha256).hexdigest()
            assert record["event_signature"] == expected
        finally:
            os.unlink(log_path)


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_insufficient_trust_raises(self):
        """Caller with trust < 4 must be rejected with PermissionError."""
        from axiom_amputate import ConstitutionalAmputate
        amp = ConstitutionalAmputate(hmac_key=HMAC_KEY)
        reg = _mock_registry()
        with pytest.raises(PermissionError, match="TRUST_LEVEL >= 4"):
            amp.execute("block-A", reg, caller_trust=3)

    def test_execute_rebuilds_affected(self):
        """execute() must call rebuild_without for each affected composition."""
        from axiom_amputate import ConstitutionalAmputate
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            amp = ConstitutionalAmputate(hmac_key=HMAC_KEY, log_path=log_path)
            reg = _mock_registry(composed=["comp-A+B", "comp-A+C"])
            result = amp.execute("block-A", reg, caller_trust=4)
            assert result.rebuilt_count == 2
            assert reg.rebuild_without.call_count == 2
        finally:
            os.unlink(log_path)

    def test_execute_logs_to_file(self):
        """Amputate event must be logged to JSONL file."""
        from axiom_amputate import ConstitutionalAmputate
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        try:
            amp = ConstitutionalAmputate(hmac_key=HMAC_KEY, log_path=log_path)
            reg = _mock_registry(composed=[])
            amp.execute("block-Z", reg, caller_trust=4)
            with open(log_path, "r", encoding="utf-8") as f:
                record = json.loads(f.readline())
            assert record["block_id"] == "block-Z"
            assert "event_signature" in record
        finally:
            os.unlink(log_path)
