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
import sys
import time
from dataclasses import dataclass
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

    stage:         One of AXIOM_STAGE_ORDER — ASCII only (BUG-008)
    intent_vector: Agent intent embedding at this stage
    token_cost:    Tokens consumed up to this stage
    latency_ms:    Wall-clock time in milliseconds at capture
    """
    stage:         str
    intent_vector: List[float]
    token_cost:    int
    latency_ms:    float

    def to_canonical_dict(self) -> dict:
        """Deterministic dict safe for JSON serialization. BUG-008: ASCII-safe."""
        return {
            "stage":         self.stage,
            "intent_vector": [float(v) for v in self.intent_vector],
            "token_cost":    int(self.token_cost),
            "latency_ms":    float(self.latency_ms),
        }


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

    trace = LatentTraceV2(
        base_intent_vector=BASE_INTENT,
        trajectory=samples,
        hmac_key=KEY,
        confidence=0.78,
    )

    print("\n  LatentTraceV2 Demo")
    print("  " + "="*50)
    print(f"  Manifest ID:     {trace.manifest_id}")
    print(f"  Trajectory HMAC: {trace.manifest['trajectory_hmac'][:32]}...")
    print(f"  Stages:          {[s.stage for s in trace.trajectory]}")
    print(f"  Confidence:      {trace.confidence}")
    print(f"  V1 compat:       intent_vector present = {'intent_vector' in trace.to_dict()}")

    # V1 backward compat
    v1 = LatentTraceV2(BASE_INTENT, None, KEY)
    print(f"\n  V1 compat trace: trajectory_present = {v1.manifest['trajectory_present']}")
    print(f"  trajectory key missing: {'trajectory' not in v1.to_dict()}")
    print("  " + "="*50)
