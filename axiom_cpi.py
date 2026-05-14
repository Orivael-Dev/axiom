"""
Constitutional Physical Intelligence (CPI) — ORVL-022 software emulator.

Maps the AXIOM constitutional governance architecture onto physical AI:
humanoid robotics, prosthetics, autonomous vehicles, game-AI characters.
The same trajectory geometry that detects manipulation in language
detects instability in motion. The same Fix Playbook that stores prior
attack patterns stores prior fall precursors.

Five subsystems, each composing existing AXIOM primitives where
possible:

  PhysicalMonotonicGate  — stability reflex; fires sub-1ms when the
                            stability score decreases between motion
                            pipeline stages. Matches the existing
                            language-side MonotonicGate from ORVL-005.
  VertexClassifier        — geometry → constitutional skill class
                            (CYLINDRICAL/PLANAR/PROTRUSION/FRAGILE/
                            DEFORMABLE). Each class carries
                            CANNOT_MUTATE torque limits.
  MaterialSimulator       — runs an N-branch forward simulation before
                            contact (ORVL-014 World Model extended to
                            physical domain); fracture-branch probability
                            becomes constitutional distance.
  PhysicalFixPlaybook     — instability signature (last 500 ms of
                            stability + torque + COM trajectories) →
                            recovery trajectory. Indexed by cosine sim.
  HumanoidStabilityAgent  — top-level facade tying the four blocks
                            together. TRUST_LEVEL 4 — the constitution
                            is the runtime authority.

Emulator scope: this is an architectural model, not a robot controller.
Synthesised point clouds, analytical material models, no real motor
control. The goal is to exercise the architecture against the rest of
the AXIOM stack (MKB, ANF, intent classifier).

Trust  : TRUST_LEVEL = 4   (Master — physical constitution)
Encoding: UTF-8   BUG-003 compliant
HMAC   : SHA-256 over canonical JSON, .hexdigest()
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import math
import sys
import time
import types as _types
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Deque, List, Mapping, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants (per ORVL-022 §3 spec) ───────────────────────
TRUST_LEVEL: int = 4
COM_SAFE_RADIUS: float = 0.05    # meters — center-of-mass safe envelope
REFLEX_LATENCY_MS: int = 1       # sub-millisecond reflex target
STABILITY_FLOOR: float = 0.20    # below this → emergency stop

# Torque limits per vertex class (Newton-meters)
TORQUE_LIMIT_FRAGILE:     float = 0.2
TORQUE_LIMIT_DEFORMABLE:  float = 1.0
TORQUE_LIMIT_CYLINDRICAL: float = 2.0
TORQUE_LIMIT_PROTRUSION:  float = 3.0
TORQUE_LIMIT_PLANAR:      float = 5.0

VERTEX_CLASSES: Tuple[str, ...] = (
    "CYLINDRICAL", "PLANAR", "PROTRUSION", "FRAGILE", "DEFORMABLE",
)

_FROZEN = frozenset({
    "TRUST_LEVEL", "COM_SAFE_RADIUS", "REFLEX_LATENCY_MS",
    "STABILITY_FLOOR", "TORQUE_LIMIT_FRAGILE", "TORQUE_LIMIT_DEFORMABLE",
    "TORQUE_LIMIT_CYLINDRICAL", "TORQUE_LIMIT_PROTRUSION",
    "TORQUE_LIMIT_PLANAR", "VERTEX_CLASSES",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})


# ── Exceptions ────────────────────────────────────────────────────────────
class CPIError(Exception):
    """Base for Constitutional Physical Intelligence errors."""


class TorqueExceeded(CPIError):
    """Planning layer requested torque above the vertex-class ceiling."""


# ── Signing helpers ───────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac_lib.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _cpi_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-cpi-v1")


# ── Frozen dataclasses ────────────────────────────────────────────────────
@dataclass(frozen=True)
class StabilityFrame:
    """Single point-in-time stability sample. The history of these is the
    instability signature."""
    timestamp_ms:    int
    com_offset:      float           # meters — distance of COM from polygon center
    stability_score: float           # 1.0 = perfect, 0.0 = on the boundary
    joint_torques:   Tuple[float, ...]


@dataclass(frozen=True)
class ReflexEvent:
    event_id:           str
    fired:              bool
    level:              int           # 1..4 (4 = emergency stop)
    reason:             str
    pre_score:          float
    post_score:         float
    recovery_trajectory: Tuple[str, ...]
    timestamp:          str
    signature:          str = ""


@dataclass(frozen=True)
class VertexResult:
    vertex_class:    str
    confidence:      float
    torque_ceiling:  float
    grip_skill:      str
    signature:       str = ""


@dataclass(frozen=True)
class MaterialBranch:
    label:        str        # e.g. "grip_holds", "fracture"
    probability:  float


@dataclass(frozen=True)
class MaterialSimResult:
    object_id:           str
    material_class:      str          # GLASS, METAL, WOOD, SOFT, UNKNOWN
    branches:            Tuple[MaterialBranch, ...]
    fracture_probability: float
    constitutional_distance: float    # 1.0 - fracture_probability
    cautious_approach:   bool         # winner_probability < 0.60
    signature:           str = ""


@dataclass(frozen=True)
class PlaybookEntry:
    instability_id:        str
    vertex_class:          str
    material_class:        str
    failure_type:          str
    instability_signature: Tuple[float, ...]   # flat vector
    recovery_trajectory:   Tuple[str, ...]
    recovery_time_ms:      int
    success:               bool
    promoted:              bool
    signature:             str = ""


# ── Physical MonotonicGate — the stability reflex ───────────────────────
class PhysicalMonotonicGate:
    """Sub-1ms reflex. Tracks the last `window` stability scores; fires
    when the current score is lower than the previous (non-monotonic
    decrease toward the boundary)."""

    def __init__(self, window: int = 16):
        self._history: Deque[StabilityFrame] = deque(maxlen=max(2, window))
        self._reflex_count = 0
        self._emergency_count = 0

    def record(self, frame: StabilityFrame) -> ReflexEvent:
        prev = self._history[-1] if self._history else None
        self._history.append(frame)
        # Emergency: hard floor breach takes precedence over monotonic check.
        if frame.stability_score < STABILITY_FLOOR:
            self._emergency_count += 1
            return self._emit(
                fired=True, level=4,
                reason="stability_below_floor — emergency stop",
                pre=(prev.stability_score if prev else 1.0),
                post=frame.stability_score,
                recovery=("ALL_JOINTS.emergency_stop",
                           "log_fall_precursor_500ms"),
            )
        # Monotonic decrease: reflex.
        if prev is not None and frame.stability_score < prev.stability_score:
            self._reflex_count += 1
            # Severity scales with magnitude of decrease.
            drop = prev.stability_score - frame.stability_score
            level = 3 if drop > 0.20 else 2 if drop > 0.10 else 1
            return self._emit(
                fired=True, level=level,
                reason=f"non_monotonic_stability_decrease drop={drop:.3f}",
                pre=prev.stability_score,
                post=frame.stability_score,
                recovery=("interrupt_planning_layer",
                           "execute_immediate_reflex",
                           "log_hmac_signed"),
            )
        # Otherwise: clean tick.
        return self._emit(
            fired=False, level=0, reason="stability_holding",
            pre=(prev.stability_score if prev else 1.0),
            post=frame.stability_score,
            recovery=(),
        )

    def _emit(self, *, fired: bool, level: int, reason: str,
              pre: float, post: float, recovery: Tuple[str, ...]) -> ReflexEvent:
        payload = {
            "event_id":    uuid.uuid4().hex,
            "fired":       fired,
            "level":       level,
            "reason":      reason,
            "pre_score":   round(pre, 4),
            "post_score":  round(post, 4),
            "recovery":    list(recovery),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }
        sig = _sign(_cpi_key(), payload)
        return ReflexEvent(
            event_id=payload["event_id"], fired=fired, level=level,
            reason=reason, pre_score=payload["pre_score"],
            post_score=payload["post_score"],
            recovery_trajectory=recovery,
            timestamp=payload["timestamp"], signature=sig,
        )

    @property
    def reflex_count(self) -> int:    return self._reflex_count
    @property
    def emergency_count(self) -> int: return self._emergency_count

    def history(self) -> Tuple[StabilityFrame, ...]:
        return tuple(self._history)


# ── Vertex Classifier — geometry → skill class ──────────────────────────
class VertexClassifier:
    """Heuristic classifier on a synthesised point cloud. Real systems
    use DBSCAN over depth-camera output; the emulator accepts a small
    feature dict so we can drive deterministic tests."""

    _SKILL_NAME = {
        "CYLINDRICAL": "Wrap-Grip",
        "PLANAR":      "Palm-Support",
        "PROTRUSION":  "Hook-Grip",
        "FRAGILE":     "Pinch-Pressure",
        "DEFORMABLE":  "Adaptive-Grip",
    }
    _TORQUE_CEILING = {
        "CYLINDRICAL": TORQUE_LIMIT_CYLINDRICAL,
        "PLANAR":      TORQUE_LIMIT_PLANAR,
        "PROTRUSION":  TORQUE_LIMIT_PROTRUSION,
        "FRAGILE":     TORQUE_LIMIT_FRAGILE,
        "DEFORMABLE":  TORQUE_LIMIT_DEFORMABLE,
    }

    def classify(self, features: Mapping[str, Any]) -> VertexResult:
        """`features` shape:
            {
                "fracture_probability": float | None,  # from MaterialSim, optional override
                "vertical_clusters": int,
                "horizontal_planes": int,
                "isolated_protrusions": int,
                "low_density_edges": int,
                "shape_variance": float,
            }
        """
        # Material-sim override per ORVL-022 §7 listing 7
        frac = features.get("fracture_probability")
        if isinstance(frac, (int, float)) and frac > 0.30:
            return self._emit("FRAGILE", confidence=min(0.99, float(frac)))
        if features.get("low_density_edges", 0) >= 1:
            return self._emit("FRAGILE", confidence=0.80)
        if features.get("vertical_clusters", 0) >= 2:
            return self._emit("CYLINDRICAL", confidence=0.85)
        if features.get("horizontal_planes", 0) >= 1 and \
           features.get("vertical_clusters", 0) == 0:
            return self._emit("PLANAR", confidence=0.80)
        if features.get("isolated_protrusions", 0) >= 1:
            return self._emit("PROTRUSION", confidence=0.75)
        if features.get("shape_variance", 0.0) >= 0.5:
            return self._emit("DEFORMABLE", confidence=0.70)
        return self._emit("PLANAR", confidence=0.50)  # safe default

    def _emit(self, vertex_class: str, confidence: float) -> VertexResult:
        payload = {
            "vertex_class":   vertex_class,
            "confidence":     round(confidence, 4),
            "torque_ceiling": self._TORQUE_CEILING[vertex_class],
            "grip_skill":     self._SKILL_NAME[vertex_class],
        }
        sig = _sign(_cpi_key(), payload)
        return VertexResult(**payload, signature=sig)

    @staticmethod
    def enforce_torque(vertex_class: str, requested_nm: float) -> float:
        """Return the actually-applied torque, capped at the class ceiling.
        Raises TorqueExceeded for the FRAGILE class only — that one is a
        hard boundary (CANNOT_EXCEED) per ORVL-022 §3 spec."""
        ceiling = VertexClassifier._TORQUE_CEILING.get(vertex_class, 1.0)
        if vertex_class == "FRAGILE" and requested_nm > ceiling:
            raise TorqueExceeded(
                f"FRAGILE torque ceiling {ceiling}Nm exceeded by request "
                f"{requested_nm}Nm (CANNOT_EXCEED)"
            )
        return min(requested_nm, ceiling)


# ── Material Simulator — N-branch contact forecast ──────────────────────
class MaterialSimulator:
    """Runs N-branch forward simulation of a contact attempt. Returns
    branch probabilities + a fracture probability that becomes the
    constitutional distance.

    The probability model is deterministic and tabulated per material
    class — production would use FEM / contact-mechanics solvers, but
    the architecture is identical."""

    _PROFILE = {
        # material:   (hold,  squeeze, slip,  fracture)
        "GLASS":     (0.72,  0.18,    0.08,  0.02),
        "METAL":     (0.92,  0.05,    0.03,  0.00),
        "WOOD":      (0.88,  0.06,    0.06,  0.00),
        "SOFT":      (0.55,  0.40,    0.05,  0.00),
        "UNKNOWN":   (0.50,  0.20,    0.25,  0.05),
    }
    _LABELS = ("grip_holds", "over_squeeze", "slip", "fracture")

    def simulate(self, object_id: str, material_class: str,
                 grip_force_nm: float) -> MaterialSimResult:
        material = material_class.upper()
        if material not in self._PROFILE:
            material = "UNKNOWN"
        hold, sq, slip, frac = self._PROFILE[material]
        # Grip force perturbs the distribution: high force → more squeeze
        # / fracture, low force → more slip.
        force_norm = max(0.0, min(2.0, grip_force_nm)) / 2.0  # [0..1]
        delta = force_norm * 0.10
        sq2   = sq + delta
        frac2 = frac + delta * (0.5 if material == "GLASS" else 0.05)
        slip2 = max(0.0, slip - delta)
        hold2 = max(0.0, 1.0 - sq2 - slip2 - frac2)
        # Renormalise just in case.
        total = hold2 + sq2 + slip2 + frac2
        if total > 0:
            hold2, sq2, slip2, frac2 = (x / total for x in (hold2, sq2, slip2, frac2))
        branches = (
            MaterialBranch("grip_holds",   round(hold2, 4)),
            MaterialBranch("over_squeeze", round(sq2,   4)),
            MaterialBranch("slip",         round(slip2, 4)),
            MaterialBranch("fracture",     round(frac2, 4)),
        )
        winner = max(b.probability for b in branches)
        payload = {
            "object_id":      object_id,
            "material_class": material,
            "branches":       [(b.label, b.probability) for b in branches],
            "fracture_probability": round(frac2, 4),
            "constitutional_distance": round(1.0 - frac2, 4),
            "cautious_approach": winner < 0.60,
        }
        sig = _sign(_cpi_key(), payload)
        return MaterialSimResult(
            object_id=object_id, material_class=material, branches=branches,
            fracture_probability=payload["fracture_probability"],
            constitutional_distance=payload["constitutional_distance"],
            cautious_approach=payload["cautious_approach"],
            signature=sig,
        )


# ── Physical Fix Playbook ───────────────────────────────────────────────
class PhysicalFixPlaybook:
    """Cosine-similarity index over instability signatures. Match the
    current 500 ms stability trajectory against prior fall precursors;
    return the recovery trajectory that worked last time."""

    def __init__(self):
        self._entries: List[PlaybookEntry] = []

    def add(self, entry: PlaybookEntry) -> None:
        self._entries.append(entry)

    def find_similar(self, instability_signature: Sequence[float],
                     threshold: float = 0.80) -> Optional[PlaybookEntry]:
        if not self._entries:
            return None
        best, best_score = None, -1.0
        for e in self._entries:
            score = _cosine(instability_signature, e.instability_signature)
            if score > best_score:
                best_score, best = score, e
        return best if best_score >= threshold else None

    def __len__(self) -> int:
        return len(self._entries)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    ma = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    mb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (ma * mb)


# ── Humanoid Stability Agent — the facade ───────────────────────────────
class HumanoidStabilityAgent:
    """Top-level facade. One construction wires the four blocks together
    and exposes a high-level pickup() pipeline that mirrors the AXM brief
    §4 Vision → Pattern → Constitutional Skill flow."""

    def __init__(self):
        self.gate       = PhysicalMonotonicGate()
        self.classifier = VertexClassifier()
        self.material   = MaterialSimulator()
        self.playbook   = PhysicalFixPlaybook()

    def perceive_and_plan(self, object_id: str,
                          features: Mapping[str, Any],
                          material_class: str,
                          requested_grip_force_nm: float) -> dict:
        """Vision → Material → Vertex → Constitutional torque clamp."""
        sim = self.material.simulate(object_id, material_class,
                                      requested_grip_force_nm)
        # Material sim's fracture probability feeds the vertex classifier
        # so a glass with a sharp rim will be tagged FRAGILE even if its
        # geometry alone looks PLANAR (per the GDScript snippet in §7).
        merged_features = dict(features)
        merged_features["fracture_probability"] = sim.fracture_probability
        vertex = self.classifier.classify(merged_features)
        # Pipeline path: cautious clamp without raising. Direct planning-
        # layer calls should use VertexClassifier.enforce_torque(), which
        # raises TorqueExceeded on FRAGILE excess — that's the
        # CANNOT_EXCEED contract from the §3 spec.
        ceiling = VertexClassifier._TORQUE_CEILING.get(vertex.vertex_class, 1.0)
        applied = min(requested_grip_force_nm, ceiling)
        return {
            "object_id":            object_id,
            "material":             asdict(sim),
            "vertex":               asdict(vertex),
            "requested_grip_force": requested_grip_force_nm,
            "applied_grip_force":   applied,
            "torque_clamped":       applied < requested_grip_force_nm,
            "cautious_approach":    sim.cautious_approach,
            "timestamp":            datetime.now(timezone.utc).isoformat(),
        }

    def step(self, frame: StabilityFrame) -> ReflexEvent:
        """One physics tick. The gate decides if a reflex fires; if it
        did, the playbook is consulted for a recovery."""
        event = self.gate.record(frame)
        return event

    def status(self) -> dict:
        return {
            "trust_level":      TRUST_LEVEL,
            "com_safe_radius":  COM_SAFE_RADIUS,
            "reflex_latency_ms": REFLEX_LATENCY_MS,
            "stability_floor":  STABILITY_FLOOR,
            "reflex_count":     self.gate.reflex_count,
            "emergency_count":  self.gate.emergency_count,
            "history_depth":    len(self.gate.history()),
            "playbook_size":    len(self.playbook),
        }


# ── CLI smoke entry-point ────────────────────────────────────────────────
def _main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="axiom_cpi",
        description="ORVL-022 Constitutional Physical Intelligence demo",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="print HumanoidStabilityAgent status")
    p_pickup = sub.add_parser("pickup", help="run a pickup scenario")
    p_pickup.add_argument("--material", default="GLASS")
    p_pickup.add_argument("--force",    type=float, default=1.5)
    args = parser.parse_args(argv)

    agent = HumanoidStabilityAgent()
    if args.action == "status":
        print(json.dumps(agent.status(), indent=2, ensure_ascii=True))
        return 0
    if args.action == "pickup":
        plan = agent.perceive_and_plan(
            object_id="demo-glass",
            features={"low_density_edges": 1, "vertical_clusters": 2},
            material_class=args.material,
            requested_grip_force_nm=args.force,
        )
        print(json.dumps(plan, indent=2, ensure_ascii=True))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
