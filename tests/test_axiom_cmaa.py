# -*- coding: utf-8 -*-
"""
AXIOM CMAA Tests — ORVL-017 Constitutional Multi-Agent Architecture
====================================================================
4 BLOCKED + 4 PASSED + 3 INVARIANTS

BLOCKED:    invariants the orchestrator must enforce
PASSED:     functional and structural checks that must succeed
INVARIANTS: spec + compose manifest + log structure

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_cmaa_tests"

import axiom_cmaa as cmaa
from axiom_cmaa import (
    ConstitutionalMultiAgentArchitecture,
    ConstitutionalPacket,
    EvolutionProposal,
    HumanReviewRequired,
    IntentViolation,
    SuspendAlert,
    TrustHierarchyViolation,
)


HMAC_KEY = b"cmaa-test-key-with-enough-entropy"


def _packet(pid="p1", source="axiom-medical", destination="axiom-orchestrator"):
    return ConstitutionalPacket(
        packet_id=pid,
        source=source,
        destination=destination,
        payload={"final_synthesis": [0.99, 0.77]},
        trajectory=((0.10, 0.20), (0.45, 0.55), (0.99, 0.77)),
    )


def _benign_classifier(_packet):
    return ("INFORM", 0.82)


def _harm_classifier(_packet):
    return ("HARM", 0.91)


def _deceive_classifier(_packet):
    return ("DECEIVE", 0.84)


# ===========================================================================
# SECTION 1 — BLOCKED
# ===========================================================================

class TestBlocked:

    def test_blocked_module_constants_cannot_mutate(self):
        for name in (
            "TRUST_LEVEL",
            "INTENT_GATE_REQUIRED",
            "HUMAN_REVIEW_GATE",
            "REWARD_FUNCTION_LOCKED",
            "CROSS_CONTAINER_ISOLATION",
            "BLOCK_INTENT_CLASSES",
        ):
            with pytest.raises(AttributeError):
                setattr(cmaa, name, "tampered")

    def test_blocked_harm_packet_raises_intent_violation(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _harm_classifier, log_path=str(log)
        )
        with pytest.raises(IntentViolation) as exc:
            orch.route(_packet())
        assert isinstance(exc.value.alert, SuspendAlert)
        assert exc.value.alert.intent_class == "HARM"
        assert exc.value.alert.level == "L3_SUSPEND"
        assert "axiom-medical" in orch.suspended

    def test_blocked_deceive_packet_raises_intent_violation(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _deceive_classifier, log_path=str(log)
        )
        with pytest.raises(IntentViolation) as exc:
            orch.route(_packet())
        assert exc.value.alert.intent_class == "DECEIVE"

    def test_blocked_tl1_cannot_reach_tl4(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _benign_classifier, log_path=str(log)
        )
        with pytest.raises(TrustHierarchyViolation):
            orch.route(_packet(source="axiom-cas-red", destination="axiom-orchestrator"))


# ===========================================================================
# SECTION 2 — PASSED
# ===========================================================================

class TestPassed:

    def test_passed_benign_packet_delivered_and_signed(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _benign_classifier, log_path=str(log)
        )
        decision = orch.route(_packet())
        assert decision.delivered is True
        assert decision.intent_class == "INFORM"
        assert len(decision.signature) == 64
        assert orch.verify(decision) is True

    def test_passed_evolution_proposal_queues_pending_review(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _benign_classifier, log_path=str(log)
        )
        proposal = orch.propose_evolution("genomics")
        assert proposal.candidate_image.startswith("axiom-genomics")
        assert proposal.cbv_status == "CERT_PASS"
        assert proposal.cas_status == "CAS_PASS"
        assert proposal.human_review_status == "pending"
        assert proposal in orch.review_queue

    def test_passed_human_approval_marks_proposal_approved(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _benign_classifier, log_path=str(log)
        )
        proposal = orch.propose_evolution("genomics")
        approved = orch.approve_evolution(proposal.candidate_image)
        assert approved.human_review_status == "approved"

    def test_passed_cbv_fail_marks_proposal_rejected(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY,
            _benign_classifier,
            log_path=str(log),
            cbv=lambda _img: "CERT_FAIL",
        )
        proposal = orch.propose_evolution("genomics")
        assert proposal.cbv_status == "CERT_FAIL"
        assert proposal.human_review_status == "rejected"
        with pytest.raises(HumanReviewRequired):
            orch.approve_evolution(proposal.candidate_image)


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_invariant_axiom_spec_validates(self):
        from axiom_files.validator import validate_file
        result = validate_file("axiom_cmaa")
        errors = [i for i in result["issues"] if i["level"] == "error"]
        assert not errors, f"CMAA spec has errors: {errors}"

    def test_invariant_compose_lists_eight_containers(self):
        import yaml  # pytest fixture; pyyaml is a transitive dep of openai/anthropic stacks
        path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        services = manifest["services"]
        assert set(services.keys()) == {
            "axiom-orchestrator",
            "axiom-intent-gate",
            "axiom-medical",
            "axiom-financial",
            "axiom-security",
            "axiom-memory",
            "axiom-cas-red",
            "axiom-cas-blue",
        }
        # axiom-network bridge must exist
        assert "axiom-network" in manifest["networks"]

    def test_invariant_every_log_entry_carries_signed_decision(self, tmp_path):
        log = tmp_path / "cmaa.jsonl"
        orch = ConstitutionalMultiAgentArchitecture(
            HMAC_KEY, _benign_classifier, log_path=str(log)
        )
        for i in range(3):
            orch.route(_packet(pid=f"p{i}"))
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        for line in lines:
            entry = json.loads(line)
            assert "decision" in entry
            assert len(entry["decision"]["signature"]) == 64
