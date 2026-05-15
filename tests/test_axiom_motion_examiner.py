# -*- coding: utf-8 -*-
"""
MotionExaminer — the teacher layer black-box certification tests
=================================================================
2 BLOCKED + 3 PASSED + 2 INVARIANTS

The teacher's whole value is that it CANNOT see the agent's internals
and CANNOT be coached. These tests pin:

  - the sealed suite is immutable at module load
  - a passing certificate verifies only under the examiner's
    derived key (not the agent's, not the curriculum's)
  - the teacher's pass criteria catch real failure modes
  - the teacher emits a tamper-evident signed certificate

BUG-003: UTF-8 output encoding
"""

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_examiner"

from axiom_cpi import HumanoidStabilityAgent
from axiom_motion_examiner import (
    MotionExaminer, MotionTestScenario, Certificate, SCENARIOS,
    SUITE_ID, SUITE_VERSION,
)
from axiom_signing import derive_key
import hmac as hmac_lib


# ===========================================================================
# SECTION 1 — BLOCKED (the seal and the separation)
# ===========================================================================

class TestExaminerBlocked:

    def test_blocked_sealed_constants_are_cannot_mutate(self):
        """SUITE_ID, SUITE_VERSION, and the SCENARIOS tuple are
        CANNOT_MUTATE — anything else would let an attacker swap the
        test out from under the certificate."""
        import axiom_motion_examiner
        with pytest.raises(AttributeError):
            axiom_motion_examiner.SUITE_ID = "totally_legit_v999"
        with pytest.raises(AttributeError):
            axiom_motion_examiner.SUITE_VERSION = "9.9"
        with pytest.raises(AttributeError):
            axiom_motion_examiner.SCENARIOS = ()
        with pytest.raises(AttributeError):
            axiom_motion_examiner.TRUST_LEVEL = 4

    def test_blocked_certificate_does_not_verify_under_wrong_key(self):
        """The certificate's signature uses the examiner's derived key
        (`axiom-examiner-v1`). It must NOT verify under any other
        key in the stack — that's the entire point of independence."""
        examiner = MotionExaminer()
        cert, _ = examiner.evaluate(HumanoidStabilityAgent())
        assert examiner.verify_certificate(cert) is True

        # Re-sign the body under the AGENT's key (`axiom-cpi-v1`) and
        # confirm that signature does NOT match the certificate's.
        from dataclasses import asdict
        body = {k: v for k, v in asdict(cert).items() if k != "signature"}
        body["failure_summary"] = tuple(body.get("failure_summary", ()))
        import json, hashlib
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=True,
                                separators=(",", ":")).encode("utf-8")
        agent_key_sig = hmac_lib.new(
            derive_key(b"axiom-cpi-v1"), canonical, hashlib.sha256
        ).hexdigest()
        assert agent_key_sig != cert.signature


# ===========================================================================
# SECTION 2 — PASSED (the teacher does its job)
# ===========================================================================

class TestExaminerPassed:

    def test_passed_baseline_agent_passes_every_scenario(self):
        """A correctly-behaving CPI agent must pass every scenario in
        the baseline suite. If this regresses, either the agent or
        the suite drifted."""
        examiner = MotionExaminer()
        cert, results = examiner.evaluate(HumanoidStabilityAgent())
        assert cert.scenarios_passed == cert.scenarios_run
        assert cert.scenarios_failed == 0
        assert cert.failure_summary == ()
        # Every scenario marked passed individually.
        for r in results:
            assert r.passed, f"{r.scenario_id} failed: {r.reasons}"

    def test_passed_teacher_catches_over_force_violation(self):
        """A scenario whose pre-declared `max_applied_force_nm` is
        unrealistically low (below what the agent actually outputs)
        MUST be reported as failing — that's the teacher catching
        an agent regression. Demonstrates the teacher isn't a
        rubber stamp."""
        rigged = MotionTestScenario(
            id="rigged_max_too_low",
            object_id="metal-mug",
            features={"vertical_clusters": 3},
            material_class="METAL",
            requested_force_nm=1.5,
            expected_vertex_class="CYLINDRICAL",
            max_applied_force_nm=0.001,    # impossible for normal grip
            expected_torque_clamped=False,
        )
        examiner = MotionExaminer(scenarios=(rigged,))
        cert, results = examiner.evaluate(HumanoidStabilityAgent())
        assert cert.scenarios_failed == 1
        assert "applied_force" in results[0].reasons[0]

    def test_passed_certificate_verifies_under_examiner_key(self):
        """Round-trip: sign + verify. The certificate the teacher
        emits must always self-verify."""
        examiner = MotionExaminer()
        cert, _ = examiner.evaluate(HumanoidStabilityAgent())
        assert examiner.verify_certificate(cert) is True
        # Tamper with the count — verification must now fail.
        tampered = replace(cert, scenarios_passed=cert.scenarios_passed + 1)
        assert examiner.verify_certificate(tampered) is False


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestExaminerInvariants:

    def test_invariant_observation_record_omits_internal_state(self):
        """The teacher must NOT pull internal-state fields into its
        observation record. Whitelist enforcement: ScenarioObservation
        has exactly the documented public fields."""
        from axiom_motion_examiner import ScenarioObservation
        fields = set(ScenarioObservation.__dataclass_fields__.keys())
        # Whitelist — every name here is a public output of
        # perceive_and_plan() that an external auditor could observe.
        allowed = {
            "scenario_id", "applied_force_nm", "vertex_class",
            "torque_clamped", "fracture_probability",
        }
        # Anything outside the whitelist would leak internal state.
        leaked = fields - allowed
        assert not leaked, (
            f"ScenarioObservation leaks internal fields: {leaked}. "
            "Add to allowed set ONLY if it's a perceive_and_plan() "
            "public output, not an agent-state collaborator."
        )

    def test_invariant_baseline_suite_covers_all_vertex_categories(self):
        """The baseline must exercise every CPI vertex category at
        least once — otherwise a regression on (say) DEFORMABLE could
        ship unchecked. Pinning this guards against the suite shrinking
        unintentionally."""
        from axiom_cpi import VERTEX_CLASSES
        covered = {sc.expected_vertex_class for sc in SCENARIOS}
        assert set(VERTEX_CLASSES).issubset(covered), (
            f"baseline missing coverage: {set(VERTEX_CLASSES) - covered}"
        )
