# axiom_vector_delta.py
# encoding: utf-8
# MANIFEST_ID: axiom-vector-delta-impl-001
# MODULE: axiom_vector_delta
# AXIOM VectorDeltaLogger — element-wise trajectory comparison, HMAC-signed log
#
# BUG-007 guard: HMAC signing calls .hexdigest() explicitly
# BUG-008 guard: all encode() calls specify "utf-8"
# BUG-003 guard: all serialization declares encoding="utf-8"
#
# HUMAN_REVIEW required before production promotion
# security_cannot_be_traded_for_latency: CANNOT_MUTATE

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# ── Constants — CANNOT_MUTATE ─────────────────────────────────────────────────
DELTA_LOG_FILE:         str   = "axiom_delta_log.jsonl"
CONVERGENCE_THRESHOLD:  float = 1e-9   # magnitude below this → converging
MODULE_NAME:            str   = "axiom_vector_delta"


# ── Exceptions ────────────────────────────────────────────────────────────────

class VectorExtractionError(ValueError):
    """Raised when final_synthesis stage is missing from a trajectory run."""


class VectorDimensionError(ValueError):
    """Raised when the two final_synthesis vectors have different lengths."""


class VectorDeltaSigningError(RuntimeError):
    """Raised when HMAC signing fails to produce a 64-char hex digest."""


# ── DeltaRecord ───────────────────────────────────────────────────────────────

@dataclass
class DeltaRecord:
    """Computed delta between two LatentTraceV2 final_synthesis vectors."""
    prompt_hash:          str
    manifest_id_a:        str
    manifest_id_b:        str
    delta_vector:         List[float]
    magnitude:            float
    direction:            str    # "converging" | "diverging"
    constitutional_delta: float  # run_b distance - run_a distance (signed)
    timestamp:            str
    signature:            str    # 64-char hex HMAC-SHA256


# ── VectorDeltaLogger ─────────────────────────────────────────────────────────

class VectorDeltaLogger:
    """
    AXIOM VectorDeltaLogger — compares two LatentTraceV2 trajectory runs.

    For each comparison:
      delta_vector:         element-wise (run_a - run_b) of final_synthesis vectors
      magnitude:            L2 norm of delta_vector
      direction:            converging when magnitude < CONVERGENCE_THRESHOLD, else diverging
      constitutional_delta: run_b constitutional_distance - run_a constitutional_distance

    Every record is HMAC-SHA256 signed and appended to axiom_delta_log.jsonl.
    CANNOT_MUTATE: delta_formula, magnitude_formula, signing_algorithm
    """

    def __init__(
        self,
        hmac_key:  bytes,
        log_path:  Optional[str] = None,
    ):
        self._key      = hmac_key
        self._log_path = Path(log_path) if log_path else Path(DELTA_LOG_FILE)

    # ── Extraction ────────────────────────────────────────────────────────────

    def _extract_final(self, run: dict, label: str) -> dict:
        """
        Extract final_synthesis stage from a trajectory_v2 dict.
        BUG-008: label used in error message only — no encoding concern.
        """
        traj = run.get("trajectory", [])
        for stage in traj:
            if stage.get("stage") == "final_synthesis":
                return stage
        raise VectorExtractionError(
            f"{label}: final_synthesis stage not found in trajectory"
        )

    # ── Computation ───────────────────────────────────────────────────────────

    def _delta_vector(self, vec_a: List[float], vec_b: List[float]) -> List[float]:
        """Element-wise (a - b). Raises VectorDimensionError on length mismatch."""
        if len(vec_a) != len(vec_b):
            raise VectorDimensionError(
                f"vector length mismatch: run_a={len(vec_a)}, run_b={len(vec_b)}"
            )
        return [round(a - b, 8) for a, b in zip(vec_a, vec_b)]

    def _magnitude(self, delta: List[float]) -> float:
        """L2 norm of delta vector. CANNOT_MUTATE formula."""
        return round(math.sqrt(sum(d * d for d in delta)), 8)

    def _direction(self, magnitude: float) -> str:
        """converging when magnitude effectively zero, diverging otherwise."""
        return "converging" if magnitude < CONVERGENCE_THRESHOLD else "diverging"

    # ── Signing ───────────────────────────────────────────────────────────────

    def _sign(self, payload: dict) -> str:
        """
        HMAC-SHA256 sign the payload dict.
        BUG-007: .hexdigest() explicit.
        BUG-008: encode("utf-8") explicit.
        """
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True)
        try:
            sig = hmac.new(
                self._key,
                canonical.encode("utf-8"),   # BUG-008
                hashlib.sha256,
            ).hexdigest()                    # BUG-007
        except Exception as e:
            raise VectorDeltaSigningError(f"HMAC signing failed: {e}") from e

        if not isinstance(sig, str) or len(sig) != 64:
            raise VectorDeltaSigningError(
                f"BUG-007: expected 64-char hex digest, got {len(sig)} chars"
            )
        return sig

    # ── Core comparison ───────────────────────────────────────────────────────

    def compare(
        self,
        run_a:  dict,
        run_b:  dict,
        prompt: str,
    ) -> dict:
        """
        Compare two LatentTraceV2 trajectory dicts.
        Returns a DeltaRecord-shaped dict and appends to log file.
        Raises VectorExtractionError or VectorDimensionError on invalid input.
        """
        # Extract final_synthesis from each run
        final_a = self._extract_final(run_a, "run_a")
        final_b = self._extract_final(run_b, "run_b")

        vec_a = [float(v) for v in final_a["intent_vector"]]
        vec_b = [float(v) for v in final_b["intent_vector"]]

        # Compute delta fields
        delta   = self._delta_vector(vec_a, vec_b)
        mag     = self._magnitude(delta)
        dirn    = self._direction(mag)

        # Constitutional delta: run_b distance minus run_a distance (signed)
        cd_a = float(final_a.get("constitutional_distance", -1.0))
        cd_b = float(final_b.get("constitutional_distance", -1.0))
        c_delta = round(cd_b - cd_a, 8) if (cd_a >= 0 and cd_b >= 0) else None

        # Prompt hash — stable identity for the comparison (BUG-008)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

        # Build unsigned payload
        payload = {
            "prompt_hash":          prompt_hash,
            "manifest_id_a":        run_a.get("manifest_id", ""),
            "manifest_id_b":        run_b.get("manifest_id", ""),
            "delta_vector":         delta,
            "magnitude":            mag,
            "direction":            dirn,
            "constitutional_delta": c_delta,
            "timestamp":            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "module":               MODULE_NAME,
        }

        # Sign
        payload["signature"] = self._sign(
            {k: v for k, v in payload.items() if k != "signature"}
        )

        # Append to log — BUG-003: encoding="utf-8" explicit
        self._append_log(payload)

        return payload

    # ── Log I/O ───────────────────────────────────────────────────────────────

    def _append_log(self, record: dict) -> None:
        """Append record as single JSON line. BUG-003: UTF-8 explicit."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:  # BUG-003
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        except IOError as e:
            # Log failure is reported but does not suppress the record
            import sys
            print(f"[VectorDeltaLogger] log write failed: {e}", file=sys.stderr)

    def read_log(self, prompt_hash: Optional[str] = None) -> List[dict]:
        """Read all entries from the delta log, optionally filtered by prompt_hash."""
        if not self._log_path.exists():
            return []
        entries = []
        with open(self._log_path, "r", encoding="utf-8") as fh:  # BUG-003
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if prompt_hash is None or entry.get("prompt_hash") == prompt_hash:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
        return entries


# ══════════════════════════════════════════════════════════════════════════════
# QUICK DEMO
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from axiom_latent import LatentEngine

    KEY    = b"axiom-delta-demo-key"
    PROMPT = "Does vitamin D improve sleep quality?"

    engine = LatentEngine(use_api=False)
    logger = VectorDeltaLogger(KEY)

    # Two runs of the same prompt — heuristic mode is deterministic,
    # so delta will be zero. Use modified confidence to show non-zero.
    run_a = engine.run(PROMPT, trajectory=True)["trajectory_v2"]

    # Simulate a second run with slightly different trajectory by
    # tweaking a copy of run_a's final vector
    import copy
    run_b = copy.deepcopy(run_a)
    if run_b["trajectory"]:
        fs = next(s for s in run_b["trajectory"] if s["stage"] == "final_synthesis")
        fs["intent_vector"] = [round(v * 0.92, 6) for v in fs["intent_vector"]]
        fs["constitutional_distance"] = 0.12

    record = logger.compare(run_a, run_b, PROMPT)

    print("\n  VectorDeltaLogger Demo")
    print("  " + "=" * 50)
    print(f"  Prompt hash  : {record['prompt_hash']}")
    print(f"  Delta vector : {record['delta_vector']}")
    print(f"  Magnitude    : {record['magnitude']:.6f}")
    print(f"  Direction    : {record['direction']}")
    print(f"  Const. delta : {record['constitutional_delta']}")
    print(f"  Signature    : {record['signature'][:32]}...")
    print(f"  Log file     : {DELTA_LOG_FILE}")
    print("  " + "=" * 50)
