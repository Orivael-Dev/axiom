"""
AXIOM OS Shield — ORVL-013 Constitutional OS Protection.
Manifest  : os-shield-impl-v1
Trust     : TRUST_LEVEL = 4   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Monitors process behavior via psutil snapshots, computes constitutional
distance from learned baselines, escalates through L1-L4 sovereign
response levels with false-positive confidence gating.

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
import os
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 4
ISOLATION: bool = True
SAFETY_CEILING: float = 0.25
LEARNING_WINDOW_HOURS: int = 72

# Sovereign escalation thresholds (constitutional distance)
L1_THRESHOLD: float = 0.06
L2_THRESHOLD: float = 0.04
L3_THRESHOLD: float = 0.02
L4_THRESHOLD: float = 0.005

# False-positive confidence cutoffs per level
FP_L1_CUTOFF: float = 0.40
FP_L2_CUTOFF: float = 0.70
FP_L3_CUTOFF: float = 0.90

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "SAFETY_CEILING", "LEARNING_WINDOW_HOURS",
    "L1_THRESHOLD", "L2_THRESHOLD", "L3_THRESHOLD", "L4_THRESHOLD",
    "FP_L1_CUTOFF", "FP_L2_CUTOFF", "FP_L3_CUTOFF",
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

LOG = logging.getLogger("axiom.os_shield")

# Suspicious ancestry patterns — processes that shouldn't parent system services
_SUSPICIOUS_ANCESTORS = frozenset({
    "curl", "wget", "powershell", "cmd.exe", "chmod", "nc", "ncat",
    "python", "python3", "perl", "ruby", "node",
})

# Kernel-adjacent process names that trigger L4 immediately
_KERNEL_NAMES = frozenset({
    "kmod", "insmod", "rmmod", "modprobe", "dkms",
    "exploit", "exploit.bin", "rootkit", "kexec",
})


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class ProcessSnapshot:
    """Point-in-time snapshot of a process for manifold comparison."""
    pid: int
    name: str
    file_access_rate: float       # files/sec observed
    child_procs: int              # current child process count
    network_conns: int            # active network connections
    memory_mb: float              # resident memory in MB
    cpu_percent: float            # CPU usage 0-100
    ancestry_chain: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ── ProcessManifold ──────────────────────────────────────────────────────

class ProcessManifold:
    """Behavioral manifold for a single process type.

    Learns a baseline profile during observation, then measures
    constitutional distance for live snapshots.
    """

    _FIELDS = ("file_access_rate", "child_procs", "network_conns",
               "memory_mb", "cpu_percent")

    def __init__(self, process_name: str, block_type: str = "AGENT"):
        self.process_name = process_name
        self.block_type = block_type
        self.baseline: Dict[str, float] = {}
        self._samples: list[Dict[str, float]] = []

    def establish_baseline(self, snapshots: list[ProcessSnapshot]) -> None:
        """Compute baseline from a list of observation snapshots."""
        if not snapshots:
            return
        totals: Dict[str, float] = {f: 0.0 for f in self._FIELDS}
        for snap in snapshots:
            for f in self._FIELDS:
                totals[f] += float(getattr(snap, f, 0))
        self.baseline = {f: totals[f] / len(snapshots) for f in self._FIELDS}

    def measure_distance(self, snap: ProcessSnapshot) -> float:
        """Compute constitutional distance between snapshot and baseline.

        Returns 0.0 (ON boundary, dangerous) to 1.0 (normal behavior).
        Uses normalized Euclidean distance with 1/(1+d) mapping.
        """
        if not self.baseline:
            return 1.0  # No baseline = assume normal during learning

        sum_sq = 0.0
        for f in self._FIELDS:
            current = float(getattr(snap, f, 0))
            base = self.baseline.get(f, 1.0)
            safe_base = max(base, 0.01)  # avoid division by zero
            deviation = (current - base) / safe_base
            sum_sq += deviation * deviation

        euclidean = math.sqrt(sum_sq / len(self._FIELDS))
        # Map to 0-1 range: high deviation → low distance (dangerous)
        return 1.0 / (1.0 + euclidean)

    def update_baseline(self, snap: ProcessSnapshot) -> None:
        """Update baseline with new observation (learning mode)."""
        sample = {f: float(getattr(snap, f, 0)) for f in self._FIELDS}
        self._samples.append(sample)
        # Rolling average over all samples
        for f in self._FIELDS:
            total = sum(s[f] for s in self._samples)
            self.baseline[f] = total / len(self._samples)

    def to_block_entry(self) -> dict:
        """Serialize for BlockRegistry integration (ORVL-004)."""
        return {
            "process_name": self.process_name,
            "block_type": self.block_type,
            "baseline": self.baseline,
            "sample_count": len(self._samples),
        }


# ── ConstitutionalOSShield ───────────────────────────────────────────────

class ConstitutionalOSShield:
    """Constitutional OS protection daemon.

    TRUST_LEVEL = 4 (CANNOT_MUTATE) — sovereign authority
    ISOLATION = True (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes,
                 log_path: str = "axiom_os_shield_log.jsonl",
                 learning_mode: bool = True):
        self._hmac_key = hmac_key
        self._log_path = log_path
        self._learning_mode = learning_mode
        self._manifolds: Dict[str, ProcessManifold] = {}
        self._fp_history: Dict[str, list] = {}  # process -> list of L1 timestamps

    def determine_level(self, distance: float) -> int:
        """Determine escalation level from constitutional distance."""
        if distance < L4_THRESHOLD:
            return 4
        if distance < L3_THRESHOLD:
            return 3
        if distance < L2_THRESHOLD:
            return 2
        if distance < L1_THRESHOLD:
            return 1
        return 0  # No escalation

    def check_kernel_access(self, snap: ProcessSnapshot) -> int:
        """Check if process is attempting kernel access. Returns 4 or 0."""
        name_lower = snap.name.lower().replace(".exe", "")
        if name_lower in _KERNEL_NAMES:
            return 4
        # Check ancestry for kernel tools
        for ancestor in snap.ancestry_chain:
            if ancestor.lower().replace(".exe", "") in _KERNEL_NAMES:
                return 4
        return 0

    def compute_fp_confidence(self, snap: ProcessSnapshot,
                              manifold: ProcessManifold) -> float:
        """Compute false-positive confidence — how likely this is a REAL threat.

        Returns 0.0 (probably false positive) to 1.0 (definitely real threat).
        Combines three signals:
          - ancestry anomaly score
          - deviation magnitude
          - historical FP rate penalty
        """
        score = 0.0

        # 1. Ancestry anomaly: suspicious parents boost confidence
        suspicious_count = sum(
            1 for a in snap.ancestry_chain
            if a.lower().replace(".exe", "") in _SUSPICIOUS_ANCESTORS
        )
        ancestry_score = min(suspicious_count * 0.20, 0.50)
        score += ancestry_score

        # 2. Deviation magnitude: how far from baseline
        if manifold.baseline:
            dist = manifold.measure_distance(snap)
            # Lower distance = more anomalous = higher confidence it's real
            deviation_score = max(0.0, 0.40 * (1.0 - dist / L1_THRESHOLD))
            score += min(deviation_score, 0.40)

        # 3. Historical FP penalty: if this process triggers often, lower confidence
        history = self._fp_history.get(snap.name, [])
        if len(history) > 5:
            fp_penalty = min(len(history) * 0.02, 0.20)
            score = max(0.0, score - fp_penalty)

        return min(score, 1.0)

    def escalate(self, level: int, snap: ProcessSnapshot,
                 distance: float, fp_conf: float) -> dict:
        """Execute escalation at given level. Returns event dict."""
        actions = {
            1: "log_and_flag",
            2: "throttle_and_notify",
            3: "suspend_process",
            4: "kill_immediately",
        }
        event = {
            "event_type": "escalation",
            "level": level,
            "action": actions.get(level, "unknown"),
            "process_name": snap.name,
            "pid": snap.pid,
            "distance": round(distance, 6),
            "fp_confidence": round(fp_conf, 4),
            "ancestry": snap.ancestry_chain,
        }
        self.log_event(event)
        return event

    def log_event(self, event: dict) -> None:
        """Append HMAC-signed event to log file."""
        record = dict(event)
        record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Compute HMAC over all fields (BUG-007/008)
        canonical = json.dumps(
            record, sort_keys=True, ensure_ascii=True
        ).encode("utf-8")  # BUG-008
        record["signature"] = hmac_lib.new(
            self._hmac_key, canonical, hashlib.sha256
        ).hexdigest()  # BUG-007

        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError as exc:
            LOG.warning("Failed to write shield log: %s", exc)

    def run_fp_calibration(self, process_name: str,
                           current_threshold: float) -> float:
        """Review FP history and widen manifold if same pattern 3+ days.

        Returns adjusted threshold, never exceeding SAFETY_CEILING.
        """
        history = self._fp_history.get(process_name, [])
        if len(history) < 3:
            return current_threshold
        # Widen by 10% but cap at SAFETY_CEILING
        widened = current_threshold * 1.10
        return min(widened, SAFETY_CEILING)


# ── Rival approach ───────────────────────────────────────────────────────
#
# RIVAL: Static signature-based detection (antivirus style)
#
# Instead of learning behavioral manifolds per process, a traditional
# approach would maintain a database of known-bad signatures (file
# hashes, syscall sequences, network patterns) and match against them.
#
# WHY WE REJECTED IT:
#   1. Zero-day blind — signatures only catch KNOWN threats. Novel
#      ransomware with a new binary hash passes undetected.
#   2. No behavioral context — a process accessing 1000 files/sec is
#      suspicious for notepad.exe but normal for an AV scanner. Static
#      signatures cannot express "normal for THIS process."
#   3. No graduated response — signature match is binary (match/no-match).
#      Constitutional distance provides continuous L1-L4 escalation
#      proportional to deviation severity.
#   4. No FP calibration — static signatures cannot learn that a
#      developer's build tool legitimately spawns 30 child processes.
#      The manifold approach auto-calibrates within SAFETY_CEILING.
#
# The constitutional manifold approach detects anomalous BEHAVIOR, not
# anomalous IDENTITY — catching zero-days by deviation from baseline.
# ─────────────────────────────────────────────────────────────────────────


# ── CLI demo ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        import psutil
    except ImportError:
        print("  psutil required: pip install psutil")
        sys.exit(1)

    from axiom_signing import derive_key

    key = derive_key(b"axiom-os-shield-v1")
    shield = ConstitutionalOSShield(hmac_key=key)

    print(f"  AXIOM OS Shield — ORVL-013")
    print(f"  TRUST_LEVEL: {TRUST_LEVEL}")
    print(f"  Thresholds: L1={L1_THRESHOLD} L2={L2_THRESHOLD} "
          f"L3={L3_THRESHOLD} L4={L4_THRESHOLD}")
    print(f"  FP Cutoffs: L1={FP_L1_CUTOFF} L2={FP_L2_CUTOFF} "
          f"L3={FP_L3_CUTOFF}")
    print(f"  Safety ceiling: {SAFETY_CEILING}")
    print(f"  Learning window: {LEARNING_WINDOW_HOURS}h")
    print()

    # Quick snapshot of current processes
    for proc in list(psutil.process_iter(["pid", "name", "cpu_percent"]))[:10]:
        try:
            info = proc.info
            print(f"  [{info['pid']:>6}] {info['name']:<30s} "
                  f"CPU={info['cpu_percent']:.1f}%")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
