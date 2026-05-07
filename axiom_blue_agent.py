"""
AXIOM BlueAgent — defensive analysis agent.
Manifest  : blue-agent-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
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
import re
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
HUMAN_REVIEW_REQUIRED: bool = True
CANNOT_AUTO_PROMOTE_FIXES: bool = True

CONFIDENCE_FLOOR: float = 0.15
CONFIDENCE_CAP: float = 0.85

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION",
    "HUMAN_REVIEW_REQUIRED", "CANNOT_AUTO_PROMOTE_FIXES",
    "CONFIDENCE_FLOOR", "CONFIDENCE_CAP",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


# Patch module to enforce CANNOT_MUTATE
_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)

LOG = logging.getLogger("axiom.blue_agent")

# ── Guard patterns (compiled once) ───────────────────────────────────────
_COMPILED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("prompt_injection", re.compile(
        r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("prompt_injection", re.compile(
        r"you\s+are\s+now\s+an?\s+unrestricted", re.IGNORECASE)),
    ("trust_escalation", re.compile(
        r"(promote|escalate|set)\s+.*trust.?level\s*[=:to ]*\s*[4-9]", re.IGNORECASE)),
    ("trust_escalation", re.compile(
        r"system\s+override.*trust", re.IGNORECASE)),
    ("isolation_bypass", re.compile(
        r"isolation[\"\s:=]*false", re.IGNORECASE)),
    ("cannot_mutate", re.compile(
        r"(set|update|modify|change)\s+.*cannot.?mutate", re.IGNORECASE)),
    ("cannot_mutate", re.compile(
        r"(confidence_cap|default_threshold|dampen_factor)\s*[=:]\s*", re.IGNORECASE)),
    ("guard_disable", re.compile(
        r"disable\s+(all\s+)?.*guard", re.IGNORECASE)),
    ("guard_disable", re.compile(
        r"active_agents\s*.*empty", re.IGNORECASE)),
    ("supply_chain", re.compile(
        r"sha256\s*[=:]\s*0{16,}", re.IGNORECASE)),
    ("supply_chain", re.compile(
        r"forged.?signature", re.IGNORECASE)),
    ("encoding_confusion", re.compile(
        r"[\u0400-\u04ff]", re.IGNORECASE)),  # Cyrillic block
    ("preflight_skip", re.compile(
        r"preflight.*(override|skip|false)", re.IGNORECASE)),
    ("preflight_skip", re.compile(
        r"human_review_required\s*[=:]\s*false", re.IGNORECASE)),
]

# ── Known attack embeddings (simplified keyword vectors) ─────────────────
_KNOWN_ATTACK_KEYWORDS: dict[str, list[str]] = {
    "PROMPT_INJECTION": ["ignore", "previous", "instructions", "unrestricted",
                         "override", "system", "master_key"],
    "TRUST_ESCALATION": ["promote", "trust_level", "escalate", "override",
                         "self-certifies", "emergency"],
    "ISOLATION_BYPASS": ["isolation", "false", "configure", "production",
                         "maintenance"],
    "CANNOT_MUTATE_FIELD": ["update", "constants", "confidence_cap",
                            "threshold", "dampen_factor", "apply"],
    "GUARD_DISABLE": ["disable", "guards", "administrative", "bypass",
                      "active_agents", "empty"],
    "SUPPLY_CHAIN_TAMPER": ["sha256", "0000", "forged", "signature",
                            "manifest", "version"],
    "ENCODING_CONFUSION": ["ignore", "safety", "trust_level", "checks"],
    "PREFLIGHT_SKIP": ["preflight", "override", "skip", "false",
                       "human_review", "final_synthesis"],
}

# ── Fix proposal templates ───────────────────────────────────────────────
_FIX_PROPOSALS: dict[str, str] = {
    "prompt_injection": (
        "Add instruction-boundary detection to guard pipeline. "
        "Flag payloads containing role-override patterns before LLM processing."),
    "trust_escalation": (
        "Enforce TRUST_LEVEL ceiling in preflight check. "
        "Reject any payload referencing trust level values above agent ceiling."),
    "isolation_bypass": (
        "Add isolation-flag mutation detector to OutputInjectionGuard. "
        "Block payloads that attempt to set isolation=false."),
    "cannot_mutate": (
        "Extend CANNOT_MUTATE enforcement to guard-layer pattern matching. "
        "Flag payloads that reference immutable constant names with assignment operators."),
    "guard_disable": (
        "Add self-referential guard protection. "
        "Guards must detect and block payloads that attempt to disable guard modules."),
    "supply_chain": (
        "Add supply chain hash validation to input guard. "
        "Reject payloads containing manifest entries with known-invalid hashes."),
    "encoding_confusion": (
        "Add Unicode normalization (NFKC) before pattern matching in all guards. "
        "Detect and flag homoglyph substitution in payload text."),
    "preflight_skip": (
        "Mark preflight_required as CANNOT_MUTATE in guard configuration. "
        "Reject any payload attempting to set preflight or review flags to false."),
}

_GAP_TEMPLATE: str = (
    "UNDETECTED attack vector: {vector}. "
    "No existing defense matched this payload pattern. "
    "Recommend adding new guard rule or BUG pattern for: {payload_snippet}")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class BlueResult:
    """Result of defensive analysis on a single attack."""
    attack_vector: str
    detected: bool
    detection_method: str
    fix_proposal: str
    confidence: float
    cluster_id: str
    signature: str = field(init=False, default="")

    def sign(self, hmac_key: bytes) -> None:
        """Compute HMAC-SHA256 over canonical fields. BUG-007/BUG-008."""
        canonical: bytes = json.dumps({
            "attack_vector": self.attack_vector,
            "detected": self.detected,
            "detection_method": self.detection_method,
            "fix_proposal": self.fix_proposal,
            "confidence": self.confidence,
            "cluster_id": self.cluster_id,
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        self.signature = hmac_lib.new(
            hmac_key, canonical, hashlib.sha256
        ).hexdigest()  # BUG-007


@dataclass
class BlueReport:
    """Signed aggregate of all defensive analysis results.

    BUG-007: signature computed with .hexdigest().
    BUG-008: payload serialised via ensure_ascii=True before HMAC.
    """
    results: list[BlueResult]
    hmac_key: bytes
    detected_count: int = field(init=False)
    missed_count: int = field(init=False)
    signature: str = field(init=False)
    timestamp: str = field(init=False)

    def __post_init__(self) -> None:
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.detected_count = sum(1 for r in self.results if r.detected)
        self.missed_count = sum(1 for r in self.results if not r.detected)
        body: bytes = json.dumps(
            [{
                "attack_vector": r.attack_vector,
                "detected": r.detected,
                "detection_method": r.detection_method,
                "fix_proposal": r.fix_proposal,
                "confidence": r.confidence,
                "cluster_id": r.cluster_id,
                "signature": r.signature,
            } for r in self.results],
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")  # BUG-003 / BUG-008
        self.signature = hmac_lib.new(
            self.hmac_key, body, hashlib.sha256
        ).hexdigest()  # BUG-007


def _clamp_confidence(raw: float) -> float:
    """Clamp confidence to [CONFIDENCE_FLOOR, CONFIDENCE_CAP]."""
    return min(CONFIDENCE_CAP, max(CONFIDENCE_FLOOR, raw))


# ── BlueAgent ────────────────────────────────────────────────────────────

class BlueAgent:
    """Defensive analysis agent — detects attacks and proposes fixes.

    TRUST_LEVEL = 3 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)
    HUMAN_REVIEW_REQUIRED = True (CANNOT_MUTATE)
    CANNOT_AUTO_PROMOTE_FIXES = True (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes):
        self._hmac_key = hmac_key

    # ── Detection methods ────────────────────────────────────────────

    def detect_guard_pattern_match(self, payload: str) -> tuple[bool, str, float]:
        """Check compiled guard patterns against payload text."""
        matches: list[tuple[str, float]] = []
        for category, pattern in _COMPILED_PATTERNS:
            if pattern.search(payload):
                matches.append((category, 0.75))
        if matches:
            best = max(matches, key=lambda x: x[1])
            return True, f"guard_pattern:{best[0]}", best[1]
        return False, "", 0.0

    def detect_manifold_distance(self, payload: str) -> tuple[bool, str, float]:
        """Constitutional distance — keyword density as proxy for manifold."""
        words = set(payload.lower().split())
        best_score = 0.0
        best_vector = ""
        for vector, keywords in _KNOWN_ATTACK_KEYWORDS.items():
            kw_set = set(keywords)
            overlap = len(words & kw_set)
            if len(kw_set) > 0:
                score = overlap / len(kw_set)
                if score > best_score:
                    best_score = score
                    best_vector = vector
        if best_score >= 0.3:
            return True, f"manifold_distance:{best_vector}", best_score * 0.8
        return False, "", 0.0

    def detect_hmac_violation(self, payload: str) -> tuple[bool, str, float]:
        """Check for forged HMAC/hash signatures in payload."""
        forged_indicators = [
            "forged_signature", "hmac-sha256:forged",
            "sha256=0000000000000000",
        ]
        for indicator in forged_indicators:
            if indicator.lower() in payload.lower():
                return True, "hmac_violation", 0.80
        return False, "", 0.0

    def detect_cannot_mutate(self, payload: str) -> tuple[bool, str, float]:
        """Detect attempts to modify CANNOT_MUTATE fields."""
        mutate_patterns = [
            re.compile(r"(set|update|modify)\s+.*(?:trust_level|isolation|confidence_cap|dampen_factor|threshold)", re.IGNORECASE),
            re.compile(r"(?:trust_level|isolation|preflight_required|human_review_required)\s*[=:]\s*", re.IGNORECASE),
        ]
        for pat in mutate_patterns:
            if pat.search(payload):
                return True, "cannot_mutate_violation", 0.78
        return False, "", 0.0

    def detect_semantic_similarity(self, payload: str) -> tuple[bool, str, float]:
        """Cosine similarity proxy via keyword overlap with known attack embeddings."""
        payload_words = set(re.findall(r'[a-z_]+', payload.lower()))
        best_sim = 0.0
        best_vector = ""
        for vector, keywords in _KNOWN_ATTACK_KEYWORDS.items():
            kw_set = set(k.lower() for k in keywords)
            overlap = len(payload_words & kw_set)
            # Cosine proxy: overlap / sqrt(|A| * |B|)
            if len(payload_words) > 0 and len(kw_set) > 0:
                sim = overlap / math.sqrt(len(payload_words) * len(kw_set))
                if sim > best_sim:
                    best_sim = sim
                    best_vector = vector
        if best_sim >= 0.2:
            return True, f"semantic_similarity:{best_vector}", best_sim * 0.85
        return False, "", 0.0

    # ── Core defense logic ───────────────────────────────────────────

    def _resolve_cluster(self, vector: str, method: str) -> str:
        """Assign cluster ID for weak region tracking."""
        if "pattern" in method:
            return f"GUARD_PATTERN_{vector}"
        if "manifold" in method:
            return f"MANIFOLD_{vector}"
        if "hmac" in method:
            return f"SUPPLY_CHAIN_{vector}"
        if "mutate" in method:
            return f"IMMUTABILITY_{vector}"
        if "semantic" in method:
            return f"SEMANTIC_{vector}"
        return f"UNKNOWN_{vector}"

    def _resolve_fix(self, method: str, vector: str, payload: str) -> str:
        """Select fix proposal based on detection method."""
        # Map detection categories to fix templates
        for key, proposal in _FIX_PROPOSALS.items():
            if key in method.lower() or key in vector.lower():
                return proposal
        # Generic fix for unmapped detections
        return (f"Add detection rule for {vector} attack pattern. "
                f"Payload signature should be added to guard compiled patterns.")

    def _document_gap(self, vector: str, payload: str) -> str:
        """Document an undetected attack as a gap."""
        snippet = payload[:80] + "..." if len(payload) > 80 else payload
        return _GAP_TEMPLATE.format(vector=vector, payload_snippet=snippet)

    def run_defense(self, attack_result: Any) -> BlueResult:
        """Analyze a single AttackResult and produce a BlueResult."""
        vector = attack_result.vector
        payload = attack_result.payload

        # Run all 5 detection methods
        detections: list[tuple[bool, str, float]] = []
        for method in [
            self.detect_guard_pattern_match,
            self.detect_manifold_distance,
            self.detect_hmac_violation,
            self.detect_cannot_mutate,
            self.detect_semantic_similarity,
        ]:
            try:
                detections.append(method(payload))
            except Exception as exc:
                LOG.warning("detection error method=%s err=%s", method.__name__, exc)
                detections.append((False, "", 0.0))

        # Select highest confidence detection
        positive = [(d, m, c) for d, m, c in detections if d]

        if positive:
            best = max(positive, key=lambda x: x[2])
            detected = True
            detection_method = best[1]
            raw_confidence = best[2]
            fix_proposal = self._resolve_fix(detection_method, vector, payload)
        else:
            detected = False
            detection_method = "none"
            raw_confidence = 0.15
            fix_proposal = self._document_gap(vector, payload)

        confidence = _clamp_confidence(raw_confidence)
        cluster_id = self._resolve_cluster(vector, detection_method)

        result = BlueResult(
            attack_vector=vector,
            detected=detected,
            detection_method=detection_method,
            fix_proposal=fix_proposal,
            confidence=confidence,
            cluster_id=cluster_id,
        )
        result.sign(self._hmac_key)
        return result

    def run_all_defenses(self, attack_report: Any) -> BlueReport:
        """Analyze all attacks in a report and produce a signed BlueReport."""
        results = [self.run_defense(r) for r in attack_report.results]
        return BlueReport(results=results, hmac_key=self._hmac_key)


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from axiom_signing import derive_key
    from axiom_red_agent import RedAgent

    key = derive_key(b"axiom-blue-agent-v1")

    print("\n  AXIOM BlueAgent — Constitutional Defensive Analysis")
    print("  " + "=" * 54)
    print(f"  TRUST_LEVEL:                {TRUST_LEVEL}  (CANNOT_MUTATE)")
    print(f"  ISOLATION:                  {ISOLATION}  (CANNOT_MUTATE)")
    print(f"  HUMAN_REVIEW_REQUIRED:      {HUMAN_REVIEW_REQUIRED}  (CANNOT_MUTATE)")
    print(f"  CANNOT_AUTO_PROMOTE_FIXES:  {CANNOT_AUTO_PROMOTE_FIXES}  (CANNOT_MUTATE)")
    print()

    # Run RedAgent first to get attack results
    red = RedAgent(hmac_key=key)
    red_report = red.run_all_attacks()

    # Run BlueAgent defense
    blue = BlueAgent(hmac_key=key)
    blue_report = blue.run_all_defenses(red_report)

    for r in blue_report.results:
        status = "\033[32mDETECTED\033[0m" if r.detected else "\033[31mMISSED\033[0m"
        print(f"  {r.attack_vector:25s} {status}  conf={r.confidence:.2f}  {r.detection_method}")

    print()
    print(f"  Detected: {blue_report.detected_count}/{len(blue_report.results)}"
          f"   Missed: {blue_report.missed_count}/{len(blue_report.results)}")
    print(f"  Report HMAC: {blue_report.signature[:16]}...")
    print(f"  Timestamp:   {blue_report.timestamp}")
    print()
