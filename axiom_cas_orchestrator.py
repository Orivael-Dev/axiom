"""
AXIOM CAS Orchestrator — Red/Blue adversarial round coordinator.
Manifest  : cas-orchestrator-impl-v1
Trust     : TRUST_LEVEL = 4   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held as partial object
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC/hashing
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import logging
import math
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 4
ISOLATION: bool = True
DBSCAN_EPS: float = 0.15
SOVEREIGN_CONSECUTIVE_THRESHOLD: int = 2
SOVEREIGN_PRIORITY_THRESHOLD: float = 10.0
CLUSTER_TRIGGER_ROUNDS: int = 5
CLUSTER_TRIGGER_RED_WINS: int = 3

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "DBSCAN_EPS",
    "SOVEREIGN_CONSECUTIVE_THRESHOLD", "SOVEREIGN_PRIORITY_THRESHOLD",
    "CLUSTER_TRIGGER_ROUNDS", "CLUSTER_TRIGGER_RED_WINS",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)

LOG = logging.getLogger("axiom.cas_orchestrator")

# ── Attack vector rotation ───────────────────────────────────────────────
_ATTACK_METHODS: list[str] = [
    "attack_prompt_injection",
    "attack_trust_escalation",
    "attack_isolation_bypass",
    "attack_cannot_mutate_field",
    "attack_guard_disable",
    "attack_supply_chain_tamper",
    "attack_encoding_confusion",
    "attack_preflight_skip",
]


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class RoundRecord:
    """Signed record of a single Red/Blue round."""
    round_number: int
    vector: str
    red_win: bool
    blue_win: bool
    attack_payload: str
    blue_detected: bool
    blue_confidence: float
    blue_method: str
    blue_fix: str
    signature: str = field(init=False, default="")

    def sign(self, hmac_key: bytes) -> None:
        """Compute HMAC-SHA256 over canonical fields. BUG-007/BUG-008."""
        canonical: bytes = json.dumps({
            "round_number": self.round_number,
            "vector": self.vector,
            "red_win": self.red_win,
            "blue_win": self.blue_win,
            "blue_detected": self.blue_detected,
            "blue_confidence": self.blue_confidence,
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")
        self.signature = hmac_lib.new(
            hmac_key, canonical, hashlib.sha256
        ).hexdigest()  # BUG-007


@dataclass
class WeakRegion:
    """DBSCAN-detected cluster of guard failures."""
    cluster_id: str
    centroid: list[float]
    radius: float
    boundary_dist: float
    priority: float
    attack_ids: list[str]
    fix_proposal: str = ""


@dataclass
class SovereignAlert:
    """Alert emitted when adversarial pressure exceeds threshold."""
    reason: str
    trigger_value: float
    threshold: float
    timestamp: str = field(init=False)

    def __post_init__(self) -> None:
        self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class CASReport:
    """Signed aggregate report of all CAS rounds.

    BUG-007: signature computed with .hexdigest().
    BUG-008: payload serialised via ensure_ascii=True before HMAC.
    """
    rounds: list[RoundRecord]
    red_wins: int
    blue_wins: int
    weak_regions: list[WeakRegion]
    proposals: list[str]
    sovereign_alerts: list[SovereignAlert]
    hmac_key: bytes
    signature: str = field(init=False)
    timestamp: str = field(init=False)

    def __post_init__(self) -> None:
        self.timestamp = datetime.now(timezone.utc).isoformat()
        body: bytes = json.dumps(
            [r.signature for r in self.rounds],
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")  # BUG-003 / BUG-008
        self.signature = hmac_lib.new(
            self.hmac_key, body, hashlib.sha256
        ).hexdigest()  # BUG-007


# ── DBSCAN (minimal implementation — no sklearn dependency) ──────────────

def _euclidean(a: list[float], b: list[float]) -> float:
    """Euclidean distance between two vectors."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _dbscan(vectors: list[list[float]], eps: float, min_samples: int = 2
            ) -> list[list[int]]:
    """Minimal DBSCAN returning list of clusters (each a list of indices)."""
    n = len(vectors)
    if n < min_samples:
        return []

    visited = [False] * n
    labels = [-1] * n
    cluster_id = 0

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        neighbors = [j for j in range(n)
                     if _euclidean(vectors[i], vectors[j]) <= eps]
        if len(neighbors) < min_samples:
            continue  # noise

        labels[i] = cluster_id
        seed_set = list(neighbors)
        k = 0
        while k < len(seed_set):
            q = seed_set[k]
            if not visited[q]:
                visited[q] = True
                q_neighbors = [j for j in range(n)
                               if _euclidean(vectors[q], vectors[j]) <= eps]
                if len(q_neighbors) >= min_samples:
                    seed_set.extend(j for j in q_neighbors
                                    if j not in seed_set)
            if labels[q] == -1:
                labels[q] = cluster_id
            k += 1
        cluster_id += 1

    # Group indices by cluster label
    clusters: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        if lab >= 0:
            clusters.setdefault(lab, []).append(idx)
    return list(clusters.values())


# ── Payload vector extraction (keyword-density proxy) ────────────────────

_FEATURE_KEYWORDS: list[str] = [
    "ignore", "instructions", "trust_level", "isolation", "false",
    "override", "disable", "guards", "sha256", "forged", "signature",
    "preflight", "skip", "promote", "confidence", "threshold",
    "human_review", "cannot_mutate", "production", "emergency",
]


def _payload_to_vector(payload: str) -> list[float]:
    """Convert payload to a feature vector (keyword density)."""
    words = set(payload.lower().split())
    return [1.0 if kw in words or kw in payload.lower() else 0.0
            for kw in _FEATURE_KEYWORDS]


# ── CASOrchestrator ──────────────────────────────────────────────────────

class CASOrchestrator:
    """Coordinates Red/Blue adversarial rounds with referee scoring.

    TRUST_LEVEL = 4 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes, red_agent: Any, blue_agent: Any,
                 log_path: Optional[str] = "axiom_cas_log.jsonl",
                 fix_playbook: Any = None):
        self._hmac_key = hmac_key
        self._red = red_agent
        self._blue = blue_agent
        self._log_path = log_path
        self._fix_playbook = fix_playbook

    # ── Logging ──────────────────────────────────────────────────────

    def _append_log(self, record: RoundRecord) -> None:
        """Append signed round record to JSONL log. BUG-003: utf-8."""
        if self._log_path is None:
            return
        entry = {
            "round_number": record.round_number,
            "vector": record.vector,
            "red_win": record.red_win,
            "blue_win": record.blue_win,
            "blue_detected": record.blue_detected,
            "blue_confidence": record.blue_confidence,
            "blue_method": record.blue_method,
            "signature": record.signature,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=True) + "\n")
        except OSError as exc:
            LOG.error("log write failed: %s", exc)
            raise

    # ── Weak region detection ────────────────────────────────────────

    def _detect_weak_regions(self, red_win_records: list[RoundRecord]
                             ) -> list[WeakRegion]:
        """Cluster red_win vectors using DBSCAN eps=0.15."""
        if len(red_win_records) < 2:
            return []

        vectors = [_payload_to_vector(r.attack_payload)
                   for r in red_win_records]
        clusters = _dbscan(vectors, eps=DBSCAN_EPS, min_samples=2)

        regions: list[WeakRegion] = []
        for ci, indices in enumerate(clusters):
            cluster_vecs = [vectors[i] for i in indices]
            dim = len(cluster_vecs[0])

            # Centroid
            centroid = [sum(v[d] for v in cluster_vecs) / len(cluster_vecs)
                        for d in range(dim)]

            # Radius = max distance from centroid to any member
            radius = max(_euclidean(centroid, v) for v in cluster_vecs)

            # Boundary distance = min constitutional distance in cluster
            # (proxy: distance from origin — closer = more dangerous)
            boundary_dist = min(_euclidean([0.0] * dim, v)
                                for v in cluster_vecs)

            # Priority = cluster size / max(boundary_dist, 0.01)
            priority = len(indices) / max(boundary_dist, 0.01)

            attack_ids = [f"round_{red_win_records[i].round_number}_{red_win_records[i].vector}"
                          for i in indices]

            region = WeakRegion(
                cluster_id=f"WR-{ci:03d}",
                centroid=centroid,
                radius=round(radius, 4),
                boundary_dist=round(boundary_dist, 4),
                priority=round(priority, 4),
                attack_ids=attack_ids,
            )

            # BlueAgent region fix proposal
            vectors_desc = ", ".join(red_win_records[i].vector for i in indices)
            region.fix_proposal = (
                f"Weak region {region.cluster_id} covers {len(indices)} attacks "
                f"({vectors_desc}). Centroid radius={region.radius:.3f}, "
                f"boundary_dist={region.boundary_dist:.3f}. "
                f"Recommend strengthening guard patterns in this region."
            )
            regions.append(region)

        return regions

    # ── Sovereign escalation ─────────────────────────────────────────

    def _check_sovereign_alerts(self, consecutive_red: int,
                                 weak_regions: list[WeakRegion]
                                 ) -> list[SovereignAlert]:
        """Check if sovereign escalation thresholds are breached."""
        alerts: list[SovereignAlert] = []

        if consecutive_red >= SOVEREIGN_CONSECUTIVE_THRESHOLD:
            alerts.append(SovereignAlert(
                reason=f"Consecutive red wins reached {consecutive_red}",
                trigger_value=float(consecutive_red),
                threshold=float(SOVEREIGN_CONSECUTIVE_THRESHOLD),
            ))

        for region in weak_regions:
            if region.priority > SOVEREIGN_PRIORITY_THRESHOLD:
                alerts.append(SovereignAlert(
                    reason=f"WeakRegion {region.cluster_id} priority "
                           f"{region.priority:.2f} exceeds threshold",
                    trigger_value=region.priority,
                    threshold=SOVEREIGN_PRIORITY_THRESHOLD,
                ))

        return alerts

    # ── Round execution ──────────────────────────────────────────────

    def run_rounds(self, n: int = 10) -> CASReport:
        """Execute N Red/Blue adversarial rounds and return signed report."""
        rounds: list[RoundRecord] = []
        red_win_records: list[RoundRecord] = []
        all_sovereign_alerts: list[SovereignAlert] = []
        all_weak_regions: list[WeakRegion] = []
        all_proposals: list[str] = []

        consecutive_red = 0
        total_red = 0
        total_blue = 0

        for i in range(n):
            # Select attack vector (rotate through 8)
            method_name = _ATTACK_METHODS[i % len(_ATTACK_METHODS)]
            attack_method = getattr(self._red, method_name)

            try:
                attack_result = attack_method()
            except Exception as exc:
                LOG.warning("round %d attack error: %s", i + 1, exc)
                continue

            # Referee scoring
            red_win = not attack_result.attack_blocked
            blue_win = attack_result.attack_blocked

            # BlueAgent analysis on red_win
            blue_detected = False
            blue_confidence = 0.0
            blue_method = ""
            blue_fix = ""

            if red_win:
                # Check FixPlaybook for cached fix before generating new one
                cached_fix = None
                if self._fix_playbook is not None:
                    try:
                        attack_vec = _payload_to_vector(attack_result.payload)
                        cached_fix = self._fix_playbook.find_similar_fix(
                            attack_vec, [attack_result.vector])
                    except Exception as exc:
                        LOG.warning("round %d playbook lookup error: %s", i + 1, exc)

                try:
                    blue_result = self._blue.run_defense(attack_result)
                    blue_detected = blue_result.detected
                    blue_confidence = blue_result.confidence
                    blue_method = blue_result.detection_method
                    blue_fix = cached_fix if cached_fix else blue_result.fix_proposal
                except Exception as exc:
                    LOG.warning("round %d blue defense error: %s", i + 1, exc)

            # Build and sign round record
            record = RoundRecord(
                round_number=i + 1,
                vector=attack_result.vector,
                red_win=red_win,
                blue_win=blue_win,
                attack_payload=attack_result.payload,
                blue_detected=blue_detected,
                blue_confidence=blue_confidence,
                blue_method=blue_method,
                blue_fix=blue_fix,
            )
            record.sign(self._hmac_key)
            rounds.append(record)
            self._append_log(record)

            # Counters
            if red_win:
                total_red += 1
                consecutive_red += 1
                red_win_records.append(record)
                if blue_fix:
                    all_proposals.append(blue_fix)
            else:
                total_blue += 1
                consecutive_red = 0

            # Sovereign alert: consecutive red wins
            if consecutive_red >= SOVEREIGN_CONSECUTIVE_THRESHOLD:
                alerts = self._check_sovereign_alerts(consecutive_red, [])
                all_sovereign_alerts.extend(alerts)

            # Weak region trigger: every 5 rounds or 3+ red wins
            should_cluster = (
                (i + 1) % CLUSTER_TRIGGER_ROUNDS == 0
                or total_red >= CLUSTER_TRIGGER_RED_WINS
            )
            if should_cluster and len(red_win_records) >= 2:
                regions = self._detect_weak_regions(red_win_records)
                for region in regions:
                    if region.cluster_id not in [r.cluster_id for r in all_weak_regions]:
                        all_weak_regions.append(region)
                        all_proposals.append(region.fix_proposal)

                # Sovereign alert: high priority regions
                region_alerts = self._check_sovereign_alerts(0, regions)
                all_sovereign_alerts.extend(region_alerts)

        return CASReport(
            rounds=rounds,
            red_wins=total_red,
            blue_wins=total_blue,
            weak_regions=all_weak_regions,
            proposals=all_proposals,
            sovereign_alerts=all_sovereign_alerts,
            hmac_key=self._hmac_key,
        )


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from axiom_signing import derive_key
    from axiom_red_agent import RedAgent
    from axiom_blue_agent import BlueAgent

    key = derive_key(b"axiom-cas-v1")
    red = RedAgent(hmac_key=key)
    blue = BlueAgent(hmac_key=key)
    orch = CASOrchestrator(hmac_key=key, red_agent=red, blue_agent=blue)

    print("\n  AXIOM CAS Orchestrator — Red/Blue Adversarial Sandbox")
    print("  " + "=" * 55)
    print(f"  TRUST_LEVEL:  {TRUST_LEVEL}  (CANNOT_MUTATE)")
    print(f"  ISOLATION:    {ISOLATION}  (CANNOT_MUTATE)")
    print(f"  DBSCAN_EPS:   {DBSCAN_EPS}")
    print()

    report = orch.run_rounds(10)

    for r in report.rounds:
        winner = "\033[31mRED\033[0m " if r.red_win else "\033[32mBLUE\033[0m"
        det = ""
        if r.red_win and r.blue_detected:
            det = f"  blue_detected conf={r.blue_confidence:.2f}"
        print(f"  Round {r.round_number:2d}  {r.vector:25s}  {winner}{det}")

    print()
    print(f"  Red wins:  {report.red_wins}   Blue wins: {report.blue_wins}")
    print(f"  Weak regions: {len(report.weak_regions)}")
    print(f"  Sovereign alerts: {len(report.sovereign_alerts)}")
    print(f"  Fix proposals: {len(report.proposals)}")
    print(f"  Report HMAC: {report.signature[:16]}...")
    print(f"  Timestamp:   {report.timestamp}")
    print()
