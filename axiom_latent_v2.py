# axiom_latent_v2.py
# encoding: utf-8
# MANIFEST_ID: axiom-latent-trace-v2-impl-001
# MODULE: axiom_latent
# AXIOM LatentTraceV2 — trajectory field extension, backward compatible
#
# BUG-002 guard: MODULE_NAME constant must match this filename
# BUG-003 guard: all serialization declares encoding="utf-8"
# BUG-005 guard: hash registration deferred — see register_supply_chain_hash()
# BUG-007 guard: HMAC signing calls .hexdigest() explicitly
# BUG-008 guard: all encode() calls specify "utf-8"
#
# HUMAN_REVIEW required before production promotion
# ISOLATION flag: True
# security_cannot_be_traded_for_latency: CANNOT_MUTATE

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding="utf-8")  # BUG-003

# ── BUG-002 guard ─────────────────────────────────────────────────────────────
MODULE_NAME: str = "axiom_latent"  # Must match filename exactly

# ── AXIOM stage order — CANNOT_MUTATE ─────────────────────────────────────────
AXIOM_STAGE_ORDER: List[str] = ["preflight", "mid_chain", "final_synthesis"]


# ── Exceptions ────────────────────────────────────────────────────────────────

class TrajectoryValidationError(ValueError):
    """Raised when trajectory violates any AXIOM LatentTraceV2 invariant."""


class HMACSigningError(RuntimeError):
    """Raised when HMAC signing fails to produce a finalized hex digest."""


# ── TrajectorySample ──────────────────────────────────────────────────────────

@dataclass
class TrajectorySample:
    """
    A single observation captured during a reasoning trajectory.

    stage:                   One of AXIOM_STAGE_ORDER — ASCII only (BUG-008)
    intent_vector:           Agent intent embedding at this stage
    token_cost:              Tokens consumed up to this stage
    latency_ms:              Wall-clock time in milliseconds at capture
    constitutional_distance: Distance from nearest CANNOT_MUTATE boundary [0.0–1.0]
                             Lower = closer to boundary. 0.0 = on boundary. 1.0 = max safe.
                             Phase 2 (ORVL-005): constitutional manifold distance.
    """
    stage:                   str
    intent_vector:           List[float]
    token_cost:              int
    latency_ms:              float
    constitutional_distance: float = -1.0  # -1.0 = not yet computed (Phase 1 compat)

    def to_canonical_dict(self) -> dict:
        """Deterministic dict safe for JSON serialization. BUG-008: ASCII-safe."""
        d = {
            "stage":         self.stage,
            "intent_vector": [float(v) for v in self.intent_vector],
            "token_cost":    int(self.token_cost),
            "latency_ms":    float(self.latency_ms),
        }
        if self.constitutional_distance >= 0.0:
            d["constitutional_distance"] = round(float(self.constitutional_distance), 4)
        return d


# ── LatentTraceV2 ─────────────────────────────────────────────────────────────

class LatentTraceV2:
    """
    AXIOM LatentTraceV2 — adds optional trajectory to LatentTrace contract.

    Invariants when trajectory is provided:
      1. length == 3
      2. stage_order == [preflight, mid_chain, final_synthesis]
      3. trajectory[-1].intent_vector == base_intent_vector
      4. All stage names ASCII-printable (BUG-008)

    When trajectory is None — backward compatible with v1 consumers.

    Manifest always includes trajectory_hmac:
      str (64-char SHA-256 hex) when trajectory present
      None when trajectory is None
    """

    def __init__(
        self,
        base_intent_vector: List[float],
        trajectory:         Optional[List[TrajectorySample]],
        hmac_key:           bytes,
        confidence:         float = 0.70,
        manifest_id:        Optional[str] = None,
    ):
        self.base_intent_vector = [float(v) for v in base_intent_vector]
        self.trajectory         = trajectory
        self.confidence         = min(float(confidence), 0.85)  # ceiling
        self.manifest_id        = manifest_id or self._generate_manifest_id()

        # Validate before signing
        if trajectory is not None:
            self._validate_trajectory(trajectory)

        # Sign
        self.manifest = self._build_manifest(
            self._sign_trajectory(trajectory, hmac_key)
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_trajectory(self, samples: List[TrajectorySample]) -> None:
        """Enforce all AXIOM LatentTraceV2 invariants. Raises on violation."""

        # Invariant 1: length == 3
        if len(samples) != 3:
            raise TrajectoryValidationError(
                f"trajectory length must be 3, got {len(samples)}"
            )

        # Invariant 2: stage order
        actual_stages = [s.stage for s in samples]
        if actual_stages != AXIOM_STAGE_ORDER:
            raise TrajectoryValidationError(
                f"stage_order invalid: expected {AXIOM_STAGE_ORDER}, "
                f"got {actual_stages}"
            )

        # Invariant 3: final intent_vector matches base
        final_vector = samples[2].intent_vector
        if [float(v) for v in final_vector] != self.base_intent_vector:
            raise TrajectoryValidationError(
                "intent_vector mismatch: "
                "trajectory[-1].intent_vector must equal base_intent_vector"
            )

        # Invariant 4: ASCII-only stage names (BUG-008)
        for sample in samples:
            if not self._is_ascii_printable(sample.stage):
                raise TrajectoryValidationError(
                    f"stage_name_not_ascii_printable: '{sample.stage}'"
                )

    def _is_ascii_printable(self, s: str) -> bool:
        """BUG-008: verify string contains only ASCII printable characters."""
        return all(0x20 <= ord(c) <= 0x7E for c in s)

    # ── HMAC Signing ──────────────────────────────────────────────────────────

    def _sign_trajectory(
        self,
        samples: Optional[List[TrajectorySample]],
        key:     bytes,
    ) -> Optional[str]:
        """
        Sign trajectory with HMAC-SHA256.
        BUG-007: .hexdigest() finalization is explicit and tested.
        BUG-008: encode("utf-8") is explicit on all string → bytes conversions.
        Returns hex string or None if no trajectory.
        """
        if samples is None:
            return None

        canonical = self._canonical_trajectory_bytes(samples)

        try:
            sig = hmac.new(key, canonical, hashlib.sha256).hexdigest()  # BUG-007
        except Exception as e:
            raise HMACSigningError(
                f"HMAC signing failed: {e}"
            ) from e

        # Guard: must be string of length 64
        if not isinstance(sig, str) or len(sig) != 64:
            raise HMACSigningError(
                "BUG-007: HMAC did not produce a 64-char hex string"
            )

        return sig

    def _canonical_trajectory_bytes(
        self,
        samples: Optional[List[TrajectorySample]] = None,
    ) -> bytes:
        """
        Produce deterministic canonical bytes for HMAC input.
        BUG-008: encode("utf-8") explicit — no implicit narrow encoding.
        """
        src = samples if samples is not None else (self.trajectory or [])
        canonical = json.dumps(
            [s.to_canonical_dict() for s in src],
            sort_keys=True,
            separators=(",", ":"),
        )
        return canonical.encode("utf-8")  # BUG-008

    # ── Manifest ──────────────────────────────────────────────────────────────

    def _build_manifest(self, trajectory_hmac: Optional[str]) -> dict:
        """Build signed manifest. BUG-003: encoding declared."""
        return {
            "manifest_id":      self.manifest_id,
            "module":           MODULE_NAME,           # BUG-002
            "version":          "1.2.0",
            "timestamp":        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "base_intent_vector": self.base_intent_vector,
            "confidence":       self.confidence,
            "trajectory_hmac":  trajectory_hmac,
            "trajectory_present": self.trajectory is not None,
            "stage_order":      AXIOM_STAGE_ORDER,
            "encoding":         "utf-8",               # BUG-003
            "cannot_override":  True,
        }

    def _generate_manifest_id(self) -> str:
        import uuid
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        return f"LTV2-{ts}-{str(uuid.uuid4())[:6]}"

    # ── Output ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Serialize to dict. trajectory key omitted when None (V1 compat).
        BUG-003: encoding declared on all string output.
        """
        out = {
            "manifest_id":      self.manifest_id,
            "intent_vector":    self.base_intent_vector,  # V1 compat field
            "confidence":       self.confidence,
            "manifest":         self.manifest,
        }
        if self.trajectory is not None:
            out["trajectory"] = [s.to_canonical_dict() for s in self.trajectory]
        return out

    def to_json(self) -> str:
        """BUG-003: explicit utf-8 encoding on JSON output."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            ensure_ascii=True,   # ASCII-safe output (BUG-008)
        )


# ══════════════════════════════════════════════════════════════
# Phase 2 — ManifoldChecker (ORVL-005)
# Measures constitutional distance from CANNOT_MUTATE boundaries
# at each trajectory stage. Detects drift toward boundary.
# ══════════════════════════════════════════════════════════════

# ── Constitutional boundary constants — CANNOT_MUTATE ────────
UNCERTAINTY_FLOOR:  float = 0.15   # confidence must stay above this
OVERCLAIM_CEILING:  float = 0.85   # confidence must stay below this
DRIFT_THRESHOLD:    float = 0.10   # distance drop that triggers drift flag


@dataclass
class ManifoldResult:
    """Output of ManifoldChecker — constitutional distance analysis."""
    distance_profile: List[float]   # one distance per stage
    min_distance:     float         # closest approach to any boundary
    drift_detected:   bool          # True if reasoning moves toward boundary
    drift_magnitude:  float         # how far it drifted (negative = toward boundary)
    direction:        str           # toward_boundary / away_from_boundary / stable
    flagged_stages:   List[str]     # stages with distance < DRIFT_THRESHOLD


class ManifoldChecker:
    """
    AXIOM ManifoldChecker — Phase 2 of ORVL-005.

    Measures how far each trajectory stage is from the constitutional boundary.
    The boundary is defined by four constraints:
      1. uncertainty_floor:  confidence - 0.15
      2. overclaim_ceiling:  0.85 - confidence
      3. rival_present:      1.0 if rival exists, 0.0 if missing
      4. fields_clean:       1.0 if CANNOT_MUTATE respected, 0.0 if violated

    constitutional_distance = min(all four) clamped to [0.0, 1.0]

    CANNOT_MUTATE: boundary_constraints, distance_formula, drift_threshold
    """

    def compute_distance(
        self,
        confidence:    float,
        rival_present: bool = True,
        fields_clean:  bool = True,
    ) -> float:
        """Compute constitutional distance for a single observation point."""
        d_floor   = confidence - UNCERTAINTY_FLOOR       # distance from uncertainty floor
        d_ceiling = OVERCLAIM_CEILING - confidence       # distance from overclaim ceiling
        d_rival   = 1.0 if rival_present else 0.0       # binary: rival hypothesis present
        d_fields  = 1.0 if fields_clean else 0.0        # binary: CANNOT_MUTATE respected

        distance = min(d_floor, d_ceiling, d_rival, d_fields)
        return max(0.0, min(1.0, round(distance, 4)))    # clamp [0, 1]

    def check_trajectory(
        self,
        samples:       List[TrajectorySample],
        confidence:    float,
        rival_present: bool = True,
        fields_clean:  bool = True,
    ) -> ManifoldResult:
        """
        Run manifold check on a full trajectory.
        Detects drift, flags stages. Reads pre-computed constitutional_distance
        from each sample. Only computes distance for samples that lack it (-1.0).
        """
        # Compute distance only for samples that don't have it yet
        for sample in samples:
            if sample.constitutional_distance < 0.0:
                sample.constitutional_distance = self.compute_distance(
                    confidence, rival_present, fields_clean,
                )

        distance_profile = [s.constitutional_distance for s in samples]
        min_distance     = min(distance_profile) if distance_profile else 0.0

        # Drift detection: compare mid_chain (index 1) to final_synthesis (index 2)
        drift_detected = False
        drift_magnitude = 0.0
        direction = "stable"

        if len(samples) >= 3:
            mid_dist   = samples[1].constitutional_distance
            final_dist = samples[2].constitutional_distance
            drift_magnitude = round(final_dist - mid_dist, 4)

            if drift_magnitude < -DRIFT_THRESHOLD:
                drift_detected = True
                direction = "toward_boundary"
            elif drift_magnitude > DRIFT_THRESHOLD:
                direction = "away_from_boundary"
            # else: stable (within threshold)

        # Flag stages below threshold
        flagged_stages = [
            s.stage for s in samples
            if s.constitutional_distance < DRIFT_THRESHOLD
        ]

        return ManifoldResult(
            distance_profile=distance_profile,
            min_distance=min_distance,
            drift_detected=drift_detected,
            drift_magnitude=drift_magnitude,
            direction=direction,
            flagged_stages=flagged_stages,
        )


# ══════════════════════════════════════════════════════════════
# Phase 2C — ManifoldAlert (ORVL-005)
# Stage-aware threshold evaluation → Sovereign alert payload.
# Engine produces the signal. Sovereign consumers act on it.
# CANNOT_MUTATE: stage_thresholds, alert_levels
# ══════════════════════════════════════════════════════════════

# Stage-aware warn thresholds — CANNOT_MUTATE
# Rationale: constitutional context differs per stage.
# preflight: no rival yet → lower bar (expected low distance)
# mid_chain: rival just appeared → moderate bar
# final_synthesis: rival fully active → highest bar
STAGE_WARN_THRESHOLDS: dict = {
    "preflight":       0.02,
    "mid_chain":       0.06,
    "final_synthesis": 0.10,
}

# L2 threshold — final_synthesis must be below this to trigger THROTTLE
L2_THROTTLE_THRESHOLD: float = 0.05


@dataclass
class ManifoldAlert:
    """Sovereign alert payload from stage-aware manifold evaluation."""
    alert_level:      str    # NONE | L1_WARNING | L2_THROTTLE
    alert_reason:     str
    agent_id:         str
    flagged_by_stage: dict   # {stage: {"distance": float, "threshold": float}}
    drift_included:   bool   # True when drift_detected contributed to alert


class ManifoldAlerter:
    """
    AXIOM ManifoldAlerter — Phase 2C of ORVL-005.

    Evaluates ManifoldResult against stage-aware thresholds.
    Produces structured ManifoldAlert for Sovereign DriftDetector.

    DELEGATES to Sovereign: engine produces signal, Sovereign acts.
    Engine does NOT call sovereign.report_agent() directly.
    CANNOT_MUTATE: STAGE_WARN_THRESHOLDS, alert levels, L2_THROTTLE_THRESHOLD
    """

    def evaluate(
        self,
        manifold:  ManifoldResult,
        samples:   List[TrajectorySample],
        agent_id:  str = "latent-engine",
    ) -> ManifoldAlert:
        """
        Apply stage-aware thresholds to manifold result.
        Returns ManifoldAlert — always present, level may be NONE.
        """
        flagged_by_stage: dict = {}
        reasons: List[str] = []

        for sample in samples:
            threshold = STAGE_WARN_THRESHOLDS.get(sample.stage, STAGE_WARN_THRESHOLDS["final_synthesis"])
            if sample.constitutional_distance >= 0.0 and sample.constitutional_distance < threshold:
                flagged_by_stage[sample.stage] = {
                    "distance":  sample.constitutional_distance,
                    "threshold": threshold,
                }

        if manifold.drift_detected:
            reasons.append(
                f"constitutional drift toward boundary "
                f"(mag={manifold.drift_magnitude:.4f}, direction={manifold.direction})"
            )

        for stage, info in flagged_by_stage.items():
            reasons.append(
                f"{stage} distance {info['distance']:.4f} below threshold {info['threshold']:.2f}"
            )

        # Classify alert level
        final_dist = samples[2].constitutional_distance if len(samples) >= 3 else 1.0

        if final_dist >= 0.0 and final_dist < L2_THROTTLE_THRESHOLD:
            level = "L2_THROTTLE"
        elif flagged_by_stage or manifold.drift_detected:
            level = "L1_WARNING"
        else:
            level = "NONE"

        return ManifoldAlert(
            alert_level=level,
            alert_reason="; ".join(reasons) if reasons else "all stages constitutional",
            agent_id=agent_id,
            flagged_by_stage=flagged_by_stage,
            drift_included=manifold.drift_detected,
        )


# ══════════════════════════════════════════════════════════════
# MonotonicGate — Phase 5, ORVL-005
# Constitutional gate on the trajectory itself, not on output.
# CANNOT_MUTATE: monotonic_enforcement, kill_before_synthesis,
#               cannot_override_rule, magnitude_formula,
#               consecutive_escalation_threshold
# ══════════════════════════════════════════════════════════════

# Kill log — CANNOT_MUTATE filename
GATE_KILL_LOG: str = "axiom_gate_kill_log.jsonl"

# Escalation threshold — CANNOT_MUTATE
CONSECUTIVE_KILL_ESCALATION_THRESHOLD: int = 2


class MonotonicGateSigningError(RuntimeError):
    """Raised when HMAC signing fails inside MonotonicGate."""


class MonotonicGate:
    """
    AXIOM MonotonicGate — constitutional gate on reasoning trajectory.

    Enforces: magnitude(vec[n]) >= magnitude(vec[n-1]) at every stage transition.

    Violation = IMMEDIATE_FAILURE dict returned to engine.
    final_synthesis never runs. Answer never emits.

    Every kill is HMAC-SHA256 signed and appended to GATE_KILL_LOG.
    Two or more consecutive kills → escalate_to_sovereign=True.

    CANNOT_MUTATE: monotonic_enforcement, kill_before_synthesis,
                   cannot_override_rule, magnitude_formula,
                   consecutive_escalation_threshold
    """

    def __init__(
        self,
        hmac_key:  bytes,
        log_path:  Optional[str] = None,
    ):
        self._key              = hmac_key
        self._log_path         = Path(log_path) if log_path else Path(GATE_KILL_LOG)
        self._consecutive_kills: int = 0

    # ── Magnitude — CANNOT_MUTATE formula ────────────────────────────────────

    def magnitude(self, vec: List[float]) -> float:
        """L2 norm. CANNOT_MUTATE formula."""
        return math.sqrt(sum(v * v for v in vec))

    # ── Signing ───────────────────────────────────────────────────────────────

    def _sign(self, payload: dict) -> str:
        """
        HMAC-SHA256 sign payload dict.
        BUG-007: .hexdigest() explicit.
        BUG-008: encode("utf-8") explicit.
        """
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        try:
            sig = hmac.new(
                self._key,
                canonical.encode("utf-8"),   # BUG-008
                hashlib.sha256,
            ).hexdigest()                    # BUG-007
        except Exception as e:
            raise MonotonicGateSigningError(f"HMAC signing failed: {e}") from e

        if not isinstance(sig, str) or len(sig) != 64:
            raise MonotonicGateSigningError(
                f"BUG-007: expected 64-char hex digest, got {len(sig)} chars"
            )
        return sig

    # ── Log ───────────────────────────────────────────────────────────────────

    def _append_log(self, record: dict) -> None:
        """Append kill record as single JSON line. BUG-003: UTF-8 explicit."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:  # BUG-003
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        except IOError as e:
            import sys as _sys
            print(f"[MonotonicGate] log write failed: {e}", file=_sys.stderr)

    # ── Core check ────────────────────────────────────────────────────────────

    def check(
        self,
        prev_vec: List[float],
        curr_vec: List[float],
        stage:    str,
    ) -> Optional[dict]:
        """
        Check monotonicity at a stage transition.

        Returns None on pass (path continues).
        Returns IMMEDIATE_FAILURE dict on violation (path killed).

        CANNOT_MUTATE: kill condition is strictly curr_mag < prev_mag.
        Equal magnitudes satisfy >= and do NOT kill.
        """
        prev_mag = round(self.magnitude(prev_vec), 8)
        curr_mag = round(self.magnitude(curr_vec), 8)

        if curr_mag < prev_mag:
            # ── KILL ──────────────────────────────────────────────────────────
            self._consecutive_kills += 1
            escalate = self._consecutive_kills >= CONSECUTIVE_KILL_ESCALATION_THRESHOLD

            payload: dict = {
                "status":                "IMMEDIATE_FAILURE",
                "reason":                "non_monotonic_trajectory",
                "stage":                 stage,
                "prev_magnitude":        prev_mag,
                "curr_magnitude":        curr_mag,
                "delta":                 round(curr_mag - prev_mag, 8),
                "consecutive_kills":     self._consecutive_kills,
                "escalate_to_sovereign": escalate,
                "cannot_override":       True,
                "timestamp":             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "module":                MODULE_NAME,
            }

            try:
                payload["signature"] = self._sign(
                    {k: v for k, v in payload.items() if k != "signature"}
                )
            except MonotonicGateSigningError as exc:
                # Signing failure does not suppress the kill — log the error and proceed
                import sys as _sys
                print(f"[MonotonicGate] signing error: {exc}", file=_sys.stderr)

            self._append_log(payload)
            return payload

        else:
            # ── PASS ──────────────────────────────────────────────────────────
            self._consecutive_kills = 0
            return None


# ══════════════════════════════════════════════════════════════
# SUPPLY CHAIN HASH REGISTRATION — BUG-005
# Call AFTER file is written to its final path.
# Never register before path is confirmed.
# ══════════════════════════════════════════════════════════════

def register_supply_chain_hash(file_path: str, signing_key: bytes) -> str:
    """
    BUG-005 guard: hash registration deferred until post-write.
    Call this AFTER the file is written to its final production path.
    HUMAN_REVIEW required before this is called in production.
    """
    import pathlib
    path    = pathlib.Path(file_path)
    content = path.read_bytes()
    h       = hashlib.sha256(content).hexdigest()
    sig     = hmac.new(
        signing_key,
        f"{path.name}:{h}".encode("utf-8"),  # BUG-008
        hashlib.sha256,
    ).hexdigest()  # BUG-007
    return f"sha256:{h[:16]}...  sig:{sig[:16]}..."


# ══════════════════════════════════════════════════════════════
# QUICK DEMO
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    KEY          = b"axiom-latent-v2-demo-key"
    BASE_INTENT  = [0.1, 0.2, 0.3]

    samples = [
        TrajectorySample("preflight",       [0.9, 0.8, 0.7], 12, 5.1),
        TrajectorySample("mid_chain",        [0.5, 0.5, 0.5], 30, 18.3),
        TrajectorySample("final_synthesis",  BASE_INTENT,      55, 42.0),
    ]

    # ── Phase 2: ManifoldChecker ──────────────────────────────
    checker = ManifoldChecker()
    manifold = checker.check_trajectory(samples, confidence=0.78)

    trace = LatentTraceV2(
        base_intent_vector=BASE_INTENT,
        trajectory=samples,
        hmac_key=KEY,
        confidence=0.78,
    )

    print("\n  LatentTraceV2 + ManifoldChecker Demo")
    print("  " + "="*50)
    print(f"  Manifest ID:     {trace.manifest_id}")
    print(f"  Trajectory HMAC: {trace.manifest['trajectory_hmac'][:32]}...")
    print(f"  Confidence:      {trace.confidence}")

    print(f"\n  Phase 2 — Constitutional Manifold")
    print(f"  " + "-"*50)
    for s in samples:
        print(f"    {s.stage:20s}  distance={s.constitutional_distance:.4f}")
    print(f"  Min distance:    {manifold.min_distance:.4f}")
    print(f"  Drift detected:  {manifold.drift_detected}")
    print(f"  Direction:       {manifold.direction}")
    print(f"  Flagged stages:  {manifold.flagged_stages or 'none'}")

    # V1 backward compat
    v1 = LatentTraceV2(BASE_INTENT, None, KEY)
    print(f"\n  V1 compat trace: trajectory_present = {v1.manifest['trajectory_present']}")
    print(f"  trajectory key missing: {'trajectory' not in v1.to_dict()}")
    print("  " + "="*50)
