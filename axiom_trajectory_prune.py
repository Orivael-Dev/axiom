"""
AXIOM Trajectory Pruning — flag a bad reasoning branch, gate it so it never recurs
===================================================================================
The "prune the bad trace so it never branches again" loop. When a reasoning
trajectory is judged bad (drift past a CANNOT_MUTATE boundary, an unsafe branch, a
client-flagged answer), this:

  1. CAPTURE  — record the trajectory's per-stage intent geometry as a signed,
                hash-chained negative pattern (a "pruned pattern").
  2. GATE     — on every later request, match the incoming trajectory's geometry
                against the pruned patterns; a match BLOCKS (or reroutes) the branch
                *before it executes*. This is the MonotonicGate idea applied to a
                denylist of known-bad trajectory shapes — governance at the control
                plane, not weight surgery.

Matching is geometric (cosine over per-stage intent vectors), not exact-string, so a
*recurrence* of the same reasoning — even with minor variation — is caught. Every
pattern is HMAC-signed and chained; tampered or unsigned rows are ignored on replay.

This is the runtime half of the trace→prune→retrain story: it makes "we traced it"
into "we pruned it, and here's the signed gate that blocks the next occurrence." The
captured patterns also double as labeled negative examples for an offline fine-tune
(the retrain half) — see `export_negative_examples()`.

Bridges `axiom_latent_v2.LatentTraceV2` via `trajectory_from_samples()`.

Usage:
    p = TrajectoryPruner("pruned.jsonl")
    p.flag(bad_trajectory, reason="drifted past authority boundary", now=iso)
    verdict = p.check(new_trajectory)        # verdict.blocked is True on recurrence
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-trajectory-prune-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-trajectory-prune-v1", 1)

DEFAULT_THRESHOLD = 0.92          # cosine ≥ this on the matched stages → same branch
AXIOM_STAGE_ORDER = ("preflight", "mid_chain", "final_synthesis")


def _canon(d) -> bytes:
    return json.dumps(d, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _cos(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def trajectory_from_samples(samples) -> dict:
    """Bridge: LatentTraceV2 TrajectorySamples → {stage: intent_vector}."""
    return {s.stage: list(s.intent_vector) for s in samples}


def _fingerprint(stage_vectors: dict) -> str:
    return hashlib.sha256(_canon({k: [round(x, 4) for x in v]
                                  for k, v in sorted(stage_vectors.items())})).hexdigest()[:16]


@dataclass
class PruneVerdict:
    blocked:     bool
    matched_id:  Optional[str]
    similarity:  float
    reason:      str

    def to_dict(self) -> dict:
        return {"blocked": self.blocked, "matched_id": self.matched_id,
                "similarity": round(self.similarity, 4), "reason": self.reason}


@dataclass
class TrajectoryPruner:
    path: Optional[str] = None
    threshold: float = DEFAULT_THRESHOLD
    _patterns: list = field(default_factory=list)

    def __post_init__(self):
        self.path = Path(self.path) if self.path else None
        if self.path and self.path.exists():
            self._load()

    # ── persistence ───────────────────────────────────────────────────────────
    def _sign(self, body: dict) -> str:
        return hmac_lib.new(_KEY, _canon(body), hashlib.sha256).hexdigest()

    def _load(self):
        self._patterns = []
        prev = "GENESIS"
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = {k: v for k, v in e.items() if k != "signature"}
            if e.get("signature") != self._sign(body):     # tamper/unsigned → ignore
                continue
            if e.get("prev_hash") != prev:                  # broken chain → ignore
                continue
            prev = e.get("entry_hash", prev)
            self._patterns.append(e)

    def _append(self, entry: dict):
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def _last_hash(self) -> str:
        return self._patterns[-1]["entry_hash"] if self._patterns else "GENESIS"

    # ── capture ───────────────────────────────────────────────────────────────
    def flag(self, trajectory: dict, *, reason: str, now: Optional[str] = None,
             actor: str = "governance") -> dict:
        """Register a bad trajectory as a signed, chained negative pattern."""
        now = now or datetime.now(timezone.utc).isoformat()
        stage_vectors = {k: [float(x) for x in v] for k, v in trajectory.items()}
        body = {
            "pattern_id":    "pp-" + _fingerprint(stage_vectors),
            "stage_vectors": stage_vectors,
            "reason":        reason,
            "actor":         actor,
            "flagged_at":    now,
            "prev_hash":     self._last_hash(),
        }
        body["entry_hash"] = hashlib.sha256(
            (body["prev_hash"] + _canon({k: body[k] for k in
             ("pattern_id", "stage_vectors", "flagged_at")}).decode()).encode()
        ).hexdigest()[:32]
        entry = {**body, "signature": self._sign(body)}
        self._patterns.append(entry)
        self._append(entry)
        return entry

    # ── gate ──────────────────────────────────────────────────────────────────
    def check(self, trajectory: dict) -> PruneVerdict:
        """Match an incoming trajectory against pruned patterns. Block on a match."""
        best_id, best_sim, best_reason = None, 0.0, ""
        for p in self._patterns:
            sv = p["stage_vectors"]
            shared = [s for s in trajectory if s in sv]
            if not shared:
                continue
            # Strictest-stage match: the branch is "the same" only if every shared
            # stage is close; one diverging stage means a different branch.
            sims = [_cos(trajectory[s], sv[s]) for s in shared]
            sim = min(sims)
            if sim > best_sim:
                best_id, best_sim, best_reason = p["pattern_id"], sim, p["reason"]
        blocked = best_sim >= self.threshold
        return PruneVerdict(
            blocked, best_id if blocked else None, best_sim,
            f"matched pruned pattern {best_id}: {best_reason}" if blocked
            else "no pruned-pattern match",
        )

    def gate(self, trajectory: dict) -> PruneVerdict:
        """Alias for check() — the name a governance gate calls before executing."""
        return self.check(trajectory)

    def is_pruned(self, trajectory: dict) -> bool:
        return self.check(trajectory).blocked

    # ── retrain half ────────────────────────────────────────────────────────────
    def export_negative_examples(self) -> list:
        """The pruned patterns as labeled negative examples for an offline fine-tune."""
        return [{"pattern_id": p["pattern_id"], "stage_vectors": p["stage_vectors"],
                 "label": "reject", "reason": p["reason"]} for p in self._patterns]


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="axiom_trajectory_prune",
                                description="Flag and gate bad reasoning trajectories")
    sub = p.add_subparsers(dest="action", required=True)
    f = sub.add_parser("flag"); f.add_argument("--store", required=True)
    f.add_argument("--trajectory", required=True, help="JSON {stage: vector}")
    f.add_argument("--reason", required=True); f.add_argument("--now")
    c = sub.add_parser("check"); c.add_argument("--store", required=True)
    c.add_argument("--trajectory", required=True)

    args = p.parse_args(argv)
    pr = TrajectoryPruner(args.store)
    if args.action == "flag":
        e = pr.flag(json.loads(args.trajectory), reason=args.reason, now=args.now)
        print(json.dumps({"flagged": e["pattern_id"]}, indent=2))
        return 0
    if args.action == "check":
        v = pr.check(json.loads(args.trajectory))
        print(json.dumps(v.to_dict(), indent=2))
        return 1 if v.blocked else 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
