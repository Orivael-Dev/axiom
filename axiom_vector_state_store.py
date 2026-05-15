# axiom_vector_state_store.py
# encoding: utf-8
# MANIFEST_ID: axiom-vector-state-store-impl-001
# MODULE: axiom_vector_state_store
# AXIOM VectorStateStore — coordinate-based reasoning restoration
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
import time
from pathlib import Path
from typing import Dict, List, Optional

# ── Constants — CANNOT_MUTATE ─────────────────────────────────────────────────
STORE_FILE:  str = "axiom_state_store.jsonl"
MODULE_NAME: str = "axiom_vector_state_store"


# ── Exceptions ────────────────────────────────────────────────────────────────

class VectorStateNotFoundError(KeyError):
    """Raised when restore() is called for a run_id that does not exist in the store."""


class VectorStateDuplicateError(ValueError):
    """Raised when store() is called with a prompt_hash + run_id that already exists."""


class VectorStateTamperError(RuntimeError):
    """Raised when restore() detects an HMAC signature mismatch on a stored entry."""


class VectorStateSigningError(RuntimeError):
    """Raised when HMAC signing fails to produce a 64-char hex digest."""


# ══════════════════════════════════════════════════════════════════════════════
# VectorStateStore
# ══════════════════════════════════════════════════════════════════════════════

class VectorStateStore:
    """
    AXIOM VectorStateStore — append-only coordinate store for reasoning restoration.

    Each entry is keyed by (prompt_hash, run_id) and stores:
      intent_vector:           final_synthesis vector from a LatentTraceV2 run
      manifest_id:             source trajectory manifest ID
      confidence:              agent confidence at that run
      constitutional_distance: manifold distance at final_synthesis (optional)
      alert_level:             manifold alert level (optional)

    All entries are HMAC-SHA256 signed. restore() verifies before returning.
    Duplicates rejected before write. Tampered entries raise on restore.

    CANNOT_MUTATE: store_filename, signing_algorithm, append_only_contract,
                   tamper_detection, entry_schema
    """

    def __init__(
        self,
        hmac_key:   bytes,
        store_path: Optional[str] = None,
    ):
        self._key        = hmac_key
        self._store_path = Path(store_path) if store_path else Path(STORE_FILE)

    # ── Signing ───────────────────────────────────────────────────────────────

    def _sign(self, payload: dict) -> str:
        """
        HMAC-SHA256 sign a payload dict.
        BUG-007: .hexdigest() explicit.
        BUG-008: encode("utf-8") explicit.
        """
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        try:
            sig = hmac.new(
                self._key,
                canonical.encode("utf-8"),   # BUG-008
                hashlib.sha256,
            ).hexdigest()                    # BUG-007
        except Exception as e:
            raise VectorStateSigningError(f"HMAC signing failed: {e}") from e

        if not isinstance(sig, str) or len(sig) != 64:
            raise VectorStateSigningError(
                f"BUG-007: expected 64-char hex, got {len(sig)} chars"
            )
        return sig

    def _verify(self, entry: dict) -> bool:
        """Verify HMAC signature on a stored entry. Returns True if valid.

        Uses ``hmac.compare_digest`` to avoid early-exit timing leaks that
        ``==`` exposes when comparing per-byte hex strings against a secret.
        """
        stored_sig = entry.get("signature", "")
        payload    = {k: v for k, v in entry.items() if k != "signature"}
        expected   = self._sign(payload)
        if not isinstance(stored_sig, str) or len(stored_sig) != len(expected):
            return False
        return hmac.compare_digest(stored_sig, expected)

    # ── Index ─────────────────────────────────────────────────────────────────

    def _load_all(self) -> List[dict]:
        """Read all entries from store. BUG-003: UTF-8 explicit."""
        if not self._store_path.exists():
            return []
        entries = []
        with open(self._store_path, "r", encoding="utf-8") as fh:  # BUG-003
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def _exists(self, prompt_hash: str, run_id: str) -> bool:
        """Check whether a (prompt_hash, run_id) pair is already stored."""
        for entry in self._load_all():
            if entry.get("prompt_hash") == prompt_hash and entry.get("run_id") == run_id:
                return True
        return False

    # ── Core operations ───────────────────────────────────────────────────────

    def store(
        self,
        prompt_hash:             str,
        run_id:                  str,
        intent_vector:           List[float],
        manifest_id:             str,
        confidence:              float = 0.0,
        constitutional_distance: Optional[float] = None,
        alert_level:             Optional[str]   = None,
    ) -> dict:
        """
        Persist a final_synthesis coordinate.
        Raises VectorStateDuplicateError if (prompt_hash, run_id) already stored.
        Returns the signed entry dict.
        """
        if self._exists(prompt_hash, run_id):
            raise VectorStateDuplicateError(
                f"run_id {run_id!r} already stored for prompt_hash {prompt_hash!r}"
            )

        payload: dict = {
            "prompt_hash":   prompt_hash,
            "run_id":        run_id,
            "intent_vector": [float(v) for v in intent_vector],
            "manifest_id":   manifest_id,
            "confidence":    round(float(confidence), 6),
            "timestamp":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "module":        MODULE_NAME,
        }
        if constitutional_distance is not None:
            payload["constitutional_distance"] = round(float(constitutional_distance), 6)
        if alert_level is not None:
            payload["alert_level"] = alert_level

        payload["signature"] = self._sign(
            {k: v for k, v in payload.items() if k != "signature"}
        )

        # Append — BUG-003: UTF-8 explicit
        with open(self._store_path, "a", encoding="utf-8") as fh:  # BUG-003
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")

        return payload

    def restore(
        self,
        prompt_hash: str,
        run_id:      str,
    ) -> List[float]:
        """
        Load and verify a stored coordinate.
        Raises VectorStateNotFoundError if not found.
        Raises VectorStateTamperError if signature invalid.
        Returns intent_vector as list of floats.
        """
        for entry in self._load_all():
            if entry.get("prompt_hash") == prompt_hash and entry.get("run_id") == run_id:
                if not self._verify(entry):
                    raise VectorStateTamperError(
                        f"HMAC signature mismatch on entry run_id={run_id!r} — "
                        "entry may have been tampered"
                    )
                return [float(v) for v in entry["intent_vector"]]

        raise VectorStateNotFoundError(
            f"No stored entry for prompt_hash={prompt_hash!r}, run_id={run_id!r}"
        )

    def list_runs(self, prompt_hash: str) -> List[dict]:
        """
        List all stored runs for a prompt_hash, sorted by timestamp ascending.
        Each item is a summary dict (excludes signature).
        """
        matches = [
            e for e in self._load_all()
            if e.get("prompt_hash") == prompt_hash
        ]
        matches.sort(key=lambda e: e.get("timestamp", ""))
        return [
            {k: v for k, v in e.items() if k != "signature"}
            for e in matches
        ]


# ══════════════════════════════════════════════════════════════════════════════
# ReasoningHistory — browse and select past runs
# ══════════════════════════════════════════════════════════════════════════════

class ReasoningHistory:
    """
    AXIOM ReasoningHistory — browse, compare, and select past runs for restoration.

    Wraps VectorStateStore.list_runs() with display and comparison helpers.
    The restore path: select a run_id from history → pass to store.restore()
    → use returned vector as base_intent_vector for a new LatentTraceV2.
    """

    def __init__(self, store: VectorStateStore):
        self._store = store

    def show(self, prompt_hash: str) -> None:
        """Print a formatted summary of all stored runs for a prompt."""
        runs = self._store.list_runs(prompt_hash)
        if not runs:
            print(f"  No stored runs for prompt_hash={prompt_hash!r}")
            return

        print(f"\n  ReasoningHistory — {len(runs)} run(s) for {prompt_hash}")
        print("  " + "─" * 58)
        for i, r in enumerate(runs):
            cd  = r.get("constitutional_distance")
            alv = r.get("alert_level", "")
            cd_s = f"  cd={cd:.4f}" if cd is not None else ""
            al_s = f"  [{alv}]" if alv and alv != "NONE" else ""
            vec_preview = ", ".join(f"{v:.4f}" for v in r["intent_vector"][:3])
            if len(r["intent_vector"]) > 3:
                vec_preview += ", ..."
            print(f"  {i+1:2d}. {r['run_id']:20s}  {r['timestamp']}  "
                  f"conf={r['confidence']:.2f}{cd_s}{al_s}")
            print(f"      vec=[{vec_preview}]")
        print()

    def compare(self, prompt_hash: str, run_id_a: str, run_id_b: str) -> dict:
        """
        Compare two stored runs — return element-wise delta and magnitude.
        Does not require VectorDeltaLogger dependency.
        """
        import math
        vec_a = self._store.restore(prompt_hash, run_id_a)
        vec_b = self._store.restore(prompt_hash, run_id_b)

        if len(vec_a) != len(vec_b):
            raise ValueError(
                f"Cannot compare: run_a length {len(vec_a)} != run_b length {len(vec_b)}"
            )
        delta = [round(a - b, 8) for a, b in zip(vec_a, vec_b)]
        mag   = round(math.sqrt(sum(d * d for d in delta)), 8)
        return {
            "run_id_a":    run_id_a,
            "run_id_b":    run_id_b,
            "delta_vector": delta,
            "magnitude":   mag,
            "direction":   "converging" if mag < 1e-9 else "diverging",
        }

    def select(self, prompt_hash: str, run_id: str) -> List[float]:
        """
        Load a stored vector for use as base_intent_vector in a new LatentTraceV2.
        This is the coordinate rewind — no code rewrite needed.
        """
        return self._store.restore(prompt_hash, run_id)


# ══════════════════════════════════════════════════════════════════════════════
# QUICK DEMO
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from axiom_latent import LatentEngine

    KEY    = b"axiom-state-store-demo-key"
    PROMPT = "Does vitamin D improve sleep quality?"
    PHASH  = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:16]

    engine  = LatentEngine(use_api=False)
    store   = VectorStateStore(KEY)
    history = ReasoningHistory(store)

    # Run twice — same prompt, same heuristic engine → same vector in both
    for run_id in ("run-001", "run-002", "run-003"):
        r   = engine.run(PROMPT, trajectory=True)
        tv2 = r["trajectory_v2"]
        fs  = next(s for s in tv2["trajectory"] if s["stage"] == "final_synthesis")
        ma  = r.get("manifold_alert", {})

        try:
            store.store(
                prompt_hash=PHASH,
                run_id=run_id,
                intent_vector=fs["intent_vector"],
                manifest_id=tv2["manifest_id"],
                confidence=tv2["confidence"],
                constitutional_distance=fs.get("constitutional_distance"),
                alert_level=ma.get("alert_level"),
            )
        except VectorStateDuplicateError:
            pass   # demo may re-run on same store file

    # Browse history
    history.show(PHASH)

    # Restore run-001 as base for a new reasoning cycle
    restored_vec = history.select(PHASH, "run-001")
    print(f"  Restored vector (run-001): {restored_vec}")
    print(f"  → Ready as base_intent_vector for new LatentTraceV2")
    print()

    # Compare two runs
    cmp = history.compare(PHASH, "run-001", "run-002")
    print(f"  Compare run-001 vs run-002:")
    print(f"    delta={cmp['delta_vector']}  mag={cmp['magnitude']}  dir={cmp['direction']}")
