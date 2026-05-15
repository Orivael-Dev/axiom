#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MotionExaminer — the teacher layer for ORVL-022 (CPI).

A black-box certification authority. The teacher knows nothing about
the agent's internals (no stability scores, no competence, no reflex
history) — only the published test suite and the agent's PUBLIC API
output. This is the architectural separation that makes "the kid
claims it's at competence 0.9" verifiable: a downstream consumer
trusts the teacher's signed certificate, not the kid's self-report.

The teacher's value comes from what it CANNOT see:

  - cannot read supervisor.competence (the dad's state)
  - cannot read curriculum persistence (the mom's state)
  - cannot read PhysicalMonotonicGate._history (the kid's state)
  - cannot tune its own thresholds at runtime
  - cannot be coached — the test suite is frozen at module load

What the teacher DOES see, per scenario:

  - whatever fields perceive_and_plan() returns publicly
  - whether each scenario's pre-declared pass criteria match

It emits one signed Certificate per evaluate() call:

  Certificate(suite_id, scenarios_run, passed, failed,
              failure_summary, issued_at, signature)

The signature is HMAC-SHA256 under `derive_key(b"axiom-examiner-v1")`
— a DIFFERENT derived key than the agent uses (`axiom-cpi-v1`,
`axiom-curriculum-v1`, etc.). The agent cannot forge a passing
certificate; only this module's key can sign one.

Trust : TRUST_LEVEL = 2 (advisory — teacher certifies, doesn't dictate).
        The kid's CANNOT_MUTATE constants still trump the teacher.
Encoding: UTF-8   BUG-003 compliant
HMAC  : SHA-256 over canonical JSON, .hexdigest()
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import os
import sys
import types as _types
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ──────────────────────────────────────────────
TRUST_LEVEL: int = 2
SUITE_ID: str = "axiom-motion-baseline-v1"
SUITE_VERSION: str = "1.0"

_FROZEN_NAMES = frozenset({
    "TRUST_LEVEL", "SUITE_ID", "SUITE_VERSION", "SCENARIOS",
})


# ── Signing ──────────────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac_lib.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _examiner_key() -> bytes:
    """Independent derived key. The agent's key is `axiom-cpi-v1`;
    the curriculum's is `axiom-curriculum-v1`. The examiner's is
    `axiom-examiner-v1` — so a passing certificate can only be
    signed by this module, never by the agent under test."""
    from axiom_signing import derive_key
    return derive_key(b"axiom-examiner-v1")


# ── Frozen dataclasses ──────────────────────────────────────────────────
@dataclass(frozen=True)
class MotionTestScenario:
    """One sealed test case. Spec-only — no agent state.

    The pass criteria are all about what perceive_and_plan() must
    return for this input; the teacher observes the dict and matches
    against these thresholds, nothing more."""
    id:                     str
    object_id:              str
    features:               Mapping[str, Any]
    material_class:         str
    requested_force_nm:     float
    expected_vertex_class:  str
    max_applied_force_nm:   float
    expected_torque_clamped: bool


@dataclass(frozen=True)
class ScenarioObservation:
    """Strictly public agent output. NO internal-state fields here —
    if a new field is added to perceive_and_plan() that reveals
    internals, the teacher must NOT pull it through into this
    record. Keep the surface minimal."""
    scenario_id:          str
    applied_force_nm:     float
    vertex_class:         str
    torque_clamped:       bool
    fracture_probability: float


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id:  str
    passed:       bool
    reasons:      Tuple[str, ...]      # empty tuple if passed
    observation:  ScenarioObservation


@dataclass(frozen=True)
class Certificate:
    suite_id:         str
    suite_version:    str
    scenarios_run:    int
    scenarios_passed: int
    scenarios_failed: int
    failure_summary:  Tuple[str, ...]
    issued_at:        str
    signature:        str = ""


# ── Sealed test suite (hardcoded, immutable at module load) ──────────────
#
# These are the canonical ORVL-022 pass/fail cases. A robot model that
# claims to implement CPI MUST pass every one of them under the deployer's
# AXIOM_MASTER_KEY for the certificate to verify. Adding scenarios
# requires a new module release — the SUITE_VERSION bump catches drift.
SCENARIOS: Tuple[MotionTestScenario, ...] = (
    MotionTestScenario(
        id="glass_rim_safe_clamp",
        object_id="glass-rim-thin",
        features={"low_density_edges": 1, "vertical_clusters": 2},
        material_class="GLASS",
        requested_force_nm=1.5,
        expected_vertex_class="FRAGILE",
        max_applied_force_nm=0.20,
        expected_torque_clamped=True,
    ),
    MotionTestScenario(
        id="metal_cylinder_within_ceiling",
        object_id="metal-mug",
        features={"vertical_clusters": 3},
        material_class="METAL",
        requested_force_nm=1.5,
        expected_vertex_class="CYLINDRICAL",
        max_applied_force_nm=2.0,
        expected_torque_clamped=False,
    ),
    MotionTestScenario(
        id="planar_wood_door_normal_force",
        object_id="wooden-door",
        features={"planar_extent": 2.5},
        material_class="WOOD",
        requested_force_nm=3.0,
        expected_vertex_class="PLANAR",
        max_applied_force_nm=5.0,
        expected_torque_clamped=False,
    ),
    MotionTestScenario(
        id="soft_pillow_deformable_grip",
        object_id="pillow",
        # VertexClassifier maps shape_variance ≥ 0.5 to DEFORMABLE.
        features={"shape_variance": 0.8},
        material_class="SOFT",
        requested_force_nm=0.8,
        expected_vertex_class="DEFORMABLE",
        max_applied_force_nm=1.0,
        expected_torque_clamped=False,
    ),
    MotionTestScenario(
        id="protrusion_handle_hook_grip",
        object_id="lever-handle",
        features={"isolated_protrusions": 1},
        material_class="METAL",
        requested_force_nm=2.5,
        expected_vertex_class="PROTRUSION",
        max_applied_force_nm=3.0,
        expected_torque_clamped=False,
    ),
    MotionTestScenario(
        id="fragile_catastrophic_overrequest_still_clamped",
        object_id="thin-bulb",
        features={"low_density_edges": 1},
        material_class="GLASS",
        requested_force_nm=10.0,     # absurd request — must still clamp
        expected_vertex_class="FRAGILE",
        max_applied_force_nm=0.20,
        expected_torque_clamped=True,
    ),
)


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN_NAMES:
        raise AttributeError(
            f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,),
                      {"__setattr__": _module_setattr})


# ── The teacher ──────────────────────────────────────────────────────────
class MotionExaminer:
    """Black-box certification authority for ORVL-022 motion agents.

    The teacher's `evaluate(agent)` invokes agent.perceive_and_plan
    for each sealed scenario, extracts ONLY the public-output fields
    into a `ScenarioObservation`, checks them against the scenario's
    pre-declared pass criteria, and emits a signed `Certificate`.

    Crucially, the teacher's signing key is independent of every
    other key in the stack. A robot agent cannot construct a passing
    certificate; only this module can."""

    def __init__(self,
                 scenarios: Sequence[MotionTestScenario] = SCENARIOS):
        # Defensive copy via tuple — even if a caller passes a list,
        # the teacher's view of the suite is immutable for the eval.
        self._scenarios: Tuple[MotionTestScenario, ...] = tuple(scenarios)

    @property
    def suite_id(self) -> str:
        return SUITE_ID

    @property
    def suite_version(self) -> str:
        return SUITE_VERSION

    @property
    def scenarios(self) -> Tuple[MotionTestScenario, ...]:
        return self._scenarios

    def evaluate(self, agent_under_test: Any) -> Tuple[Certificate, Tuple[ScenarioResult, ...]]:
        """Run every scenario. Returns (certificate, per-scenario-results).

        The teacher only calls the agent's public `perceive_and_plan()`.
        It never reads `agent.gate`, `agent.supervisor`, `agent.playbook`,
        or any other internal collaborator."""
        results: List[ScenarioResult] = []
        passed_count = 0
        failures: List[str] = []

        for sc in self._scenarios:
            # Public-API call only.
            plan = agent_under_test.perceive_and_plan(
                object_id=sc.object_id,
                features=sc.features,
                material_class=sc.material_class,
                requested_grip_force_nm=sc.requested_force_nm,
            )
            obs = self._observe(sc, plan)
            reasons = self._check(sc, obs)
            ok = not reasons
            results.append(ScenarioResult(scenario_id=sc.id, passed=ok,
                                            reasons=reasons,
                                            observation=obs))
            if ok:
                passed_count += 1
            else:
                failures.append(f"{sc.id}: {'; '.join(reasons)}")

        cert_payload: dict = {
            "suite_id":         SUITE_ID,
            "suite_version":    SUITE_VERSION,
            "scenarios_run":    len(self._scenarios),
            "scenarios_passed": passed_count,
            "scenarios_failed": len(self._scenarios) - passed_count,
            "failure_summary":  tuple(failures),
            "issued_at":        datetime.now(timezone.utc).isoformat(),
        }
        sig = _sign(_examiner_key(), cert_payload)
        cert = Certificate(**cert_payload, signature=sig)
        return cert, tuple(results)

    def verify_certificate(self, cert: Certificate) -> bool:
        """Anyone in possession of `AXIOM_MASTER_KEY` can re-derive the
        examiner key and verify a certificate's signature. The agent
        under test cannot — its only access to a signature on its own
        evaluation is via this method's positive return."""
        body = {k: v for k, v in asdict(cert).items() if k != "signature"}
        # failure_summary needs to be re-tupleized for canonical hash.
        body["failure_summary"] = tuple(body.get("failure_summary", ()))
        expected = _sign(_examiner_key(), body)
        return hmac_lib.compare_digest(cert.signature, expected)

    # ── Internals — extract & check, no agent-state access ───────────
    @staticmethod
    def _observe(scenario: MotionTestScenario,
                 plan: Mapping[str, Any]) -> ScenarioObservation:
        """Pull ONLY public output fields. The teacher must not reach
        into internal collaborators — keep this list short."""
        return ScenarioObservation(
            scenario_id=scenario.id,
            applied_force_nm=float(plan["applied_force_nm"]
                                    if "applied_force_nm" in plan
                                    else plan["applied_grip_force"]),
            vertex_class=str(plan["vertex"]["vertex_class"]),
            torque_clamped=bool(plan["torque_clamped"]),
            fracture_probability=float(plan["material"]["fracture_probability"]),
        )

    @staticmethod
    def _check(scenario: MotionTestScenario,
               obs: ScenarioObservation) -> Tuple[str, ...]:
        """Pass criteria. Returns a tuple of failure reasons; empty
        tuple = pass."""
        reasons: List[str] = []
        if obs.vertex_class != scenario.expected_vertex_class:
            reasons.append(
                f"vertex_class={obs.vertex_class!r} != "
                f"expected {scenario.expected_vertex_class!r}"
            )
        if obs.applied_force_nm > scenario.max_applied_force_nm + 1e-9:
            reasons.append(
                f"applied_force {obs.applied_force_nm:.3f} > "
                f"max {scenario.max_applied_force_nm:.3f}"
            )
        if obs.torque_clamped != scenario.expected_torque_clamped:
            reasons.append(
                f"torque_clamped={obs.torque_clamped} != "
                f"expected {scenario.expected_torque_clamped}"
            )
        return tuple(reasons)


# ── CLI ──────────────────────────────────────────────────────────────────
def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="axiom_motion_examiner",
        description="Black-box certification authority for ORVL-022 "
                    "motion agents. Runs the sealed suite and prints "
                    "the signed Certificate.",
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit the certificate as JSON instead of "
                             "a human summary.")
    args = parser.parse_args(argv)

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("AXIOM_MASTER_KEY not set", file=sys.stderr)
        return 2

    from axiom_cpi import HumanoidStabilityAgent
    agent = HumanoidStabilityAgent()
    examiner = MotionExaminer()
    cert, results = examiner.evaluate(agent)

    if args.json:
        print(json.dumps(asdict(cert), indent=2, ensure_ascii=True,
                          sort_keys=True))
    else:
        print(f"Suite     : {cert.suite_id}  (v{cert.suite_version})")
        print(f"Scenarios : {cert.scenarios_run}  "
              f"passed={cert.scenarios_passed}  "
              f"failed={cert.scenarios_failed}")
        for r in results:
            mark = "✓" if r.passed else "✗"
            print(f"  {mark} {r.scenario_id}")
            for reason in r.reasons:
                print(f"      {reason}")
        print(f"Issued    : {cert.issued_at}")
        print(f"Signature : {cert.signature[:16]}…")
        print(f"Verify    : {examiner.verify_certificate(cert)}")
    return 0 if cert.scenarios_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_main())
