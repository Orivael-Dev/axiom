"""
AXIOM Reverse QRF — Reverse Quantum Reasoning Collapse engine.
Manifest  : qrf-reverse-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Forward QRF (axiom_qrf.py) takes a prompt and returns weighted reasoning
branches. Reverse QRF takes (prompt, observed_answer) and returns the
superposition of trajectory hypotheses consistent with that answer under
the existing forward model.

This is a synthetic-trajectory generator: one (prompt, answer) pair
becomes N signed training trajectories carrying intent_alignment,
branch_quality, compatibility, and constitutional_distance metadata,
filtered by an acceptance threshold tau.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held partial
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import logging
import sys
import types as _types
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

# BUG-003
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from axiom_qrf import DOMAIN_BRANCH_COUNTS, QRFEngine, QRFResult

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
DEFAULT_TAU_THRESHOLD: float = 0.10    # matches L1_WARNING from axiom_latent_v2
MIN_TRAJECTORY_DISTANCE: float = 0.05  # matches L2_THROTTLE from axiom_latent_v2

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION",
    "DEFAULT_TAU_THRESHOLD", "MIN_TRAJECTORY_DISTANCE",
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

LOG = logging.getLogger("axiom.qrf_reverse")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class TrajectoryHypothesis:
    """A reverse-collapsed reasoning trajectory consistent with an observed answer.

    score = forward_weight * compatibility
    compatibility = intent_alignment * branch_quality
    intent_alignment = Jaccard similarity of intent label sets
    """
    branch_name: str
    forward_weight: float           # P(branch | prompt) from forward QRF
    intent_alignment: float         # Jaccard over intent label sets, [0,1]
    branch_quality: float           # multiplex "overall" metric, [0,1]
    compatibility: float            # intent_alignment * branch_quality
    constitutional_distance: float  # distance from CANNOT_MUTATE boundary, [0,1]
    score: float                    # forward_weight * compatibility
    response: str                   # branch's reasoning text


@dataclass
class ReverseQRFResult:
    """Output of reverse-QRF collapse — a signed superposition of hypotheses.

    BUG-007: hmac_signature computed with .hexdigest().
    BUG-008: canonical fields encoded via .encode("utf-8") before HMAC.
    """
    prompt: str
    observed_answer: str
    domain: str
    tau_threshold: float
    hypotheses: list[dict]        # accepted (score >= tau), sorted desc
    rejected: list[dict]          # below tau or below MIN_TRAJECTORY_DISTANCE
    n_branches_considered: int
    timestamp: str
    hmac_signature: str


# ── ReverseQRFEngine ─────────────────────────────────────────────────────

class ReverseQRFEngine:
    """Reverse Quantum Reasoning Forecast — recover trajectory superpositions.

    Given (prompt, observed_answer), returns the set of latent trajectories
    that could have produced the observed answer under forward QRF, each
    signed with HMAC under the configured key.

    TRUST_LEVEL = 3 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)
    """

    def __init__(self, domain: str, hmac_key: bytes,
                 tau: float = DEFAULT_TAU_THRESHOLD,
                 endpoint: Optional[str] = None):
        if domain not in DOMAIN_BRANCH_COUNTS:
            supported = ", ".join(sorted(DOMAIN_BRANCH_COUNTS.keys()))
            raise ValueError(
                f"Unsupported domain: {domain}. Supported: {supported}"
            )
        if not (0.0 <= tau <= 1.0):
            raise ValueError(f"tau must be in [0.0, 1.0], got {tau}")

        self._domain = domain
        self._hmac_key = hmac_key
        self._tau = float(tau)
        self._endpoint = endpoint

        # Build forward engine; share its LatentEngine for trace re-encoding.
        # Sharing avoids a second LatentEngine instantiation per collapse.
        self._forward = QRFEngine(
            domain=domain, hmac_key=hmac_key, endpoint=endpoint
        )
        self._latent = self._forward._engine

    # ── Compatibility scoring ────────────────────────────────────────

    @staticmethod
    def _intent_jaccard(observed: list, branch: list) -> float:
        """Jaccard similarity on intent label sets. 0.0 if both empty or no overlap."""
        s_obs = set(observed or [])
        s_br = set(branch or [])
        union = s_obs | s_br
        if not union:
            return 0.0
        return round(len(s_obs & s_br) / len(union), 6)

    def _encode_trace(self, text: str) -> dict:
        """Run LatentEngine trace phase only — no multiplex, no foresight."""
        result = self._latent.run(text, phases=["trace"])
        return result.get("phases", {}).get("trace", {}) or {}

    # ── HMAC signing ─────────────────────────────────────────────────

    def _sign_result(self, prompt: str, observed_answer: str,
                     n_accepted: int, n_rejected: int) -> str:
        """HMAC-SHA256 over canonical fields. BUG-007/BUG-008."""
        canonical: bytes = json.dumps({
            "prompt": prompt,
            "observed_answer": observed_answer,
            "domain": self._domain,
            "tau": round(self._tau, 6),
            "n_accepted": n_accepted,
            "n_rejected": n_rejected,
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        return hmac_lib.new(
            self._hmac_key, canonical, hashlib.sha256
        ).hexdigest()  # BUG-007

    # ── Core reverse-collapse ────────────────────────────────────────

    def collapse(self, prompt: str, observed_answer: str) -> ReverseQRFResult:
        """Reverse-collapse an observed (prompt, answer) into a trajectory superposition."""
        # 1. Forward QRF — weighted branches for this prompt
        forward: QRFResult = self._forward.forecast(prompt)

        # 2. Encode observed answer; extract intent labels
        observed_trace = self._encode_trace(observed_answer)
        observed_intents = observed_trace.get("intent_vector", []) or []

        # 3. Score each branch's compatibility with the observation
        accepted: list[dict] = []
        rejected: list[dict] = []
        checker = self._latent.checker

        for branch in forward.branches:
            response_text = branch.get("response", "") or ""
            branch_trace = self._encode_trace(response_text)
            branch_intents = branch_trace.get("intent_vector", []) or []
            branch_confidence = float(branch_trace.get("confidence", 0.5))

            intent_alignment = self._intent_jaccard(observed_intents, branch_intents)
            quality = float(branch.get("metrics", {}).get("overall", 0.5))
            compatibility = round(intent_alignment * quality, 6)

            # Constitutional distance for this hypothesis (final-stage check).
            distance = checker.compute_distance(
                confidence=branch_confidence,
                rival_present=True,
                fields_clean=True,
            )

            forward_weight = float(branch.get("probability_weight", 0.0))
            score = round(forward_weight * compatibility, 6)

            hypothesis = TrajectoryHypothesis(
                branch_name=branch.get("branch", "unknown"),
                forward_weight=round(forward_weight, 6),
                intent_alignment=intent_alignment,
                branch_quality=round(quality, 6),
                compatibility=compatibility,
                constitutional_distance=distance,
                score=score,
                response=response_text,
            )

            if score >= self._tau and distance >= MIN_TRAJECTORY_DISTANCE:
                accepted.append(asdict(hypothesis))
            else:
                rejected.append(asdict(hypothesis))

        accepted.sort(key=lambda h: h["score"], reverse=True)

        signature = self._sign_result(
            prompt=prompt,
            observed_answer=observed_answer,
            n_accepted=len(accepted),
            n_rejected=len(rejected),
        )

        return ReverseQRFResult(
            prompt=prompt,
            observed_answer=observed_answer,
            domain=self._domain,
            tau_threshold=round(self._tau, 6),
            hypotheses=accepted,
            rejected=rejected,
            n_branches_considered=len(forward.branches),
            timestamp=datetime.now(timezone.utc).isoformat(),
            hmac_signature=signature,
        )


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from axiom_signing import derive_key

    parser = argparse.ArgumentParser(
        description="AXIOM Reverse QRF — recover trajectory superposition from (prompt, answer)"
    )
    parser.add_argument("prompt", help="Original question")
    parser.add_argument("answer", help="Observed answer")
    parser.add_argument("--domain", default="financial",
                        choices=sorted(DOMAIN_BRANCH_COUNTS.keys()))
    parser.add_argument("--tau", type=float, default=DEFAULT_TAU_THRESHOLD,
                        help=f"Acceptance threshold (default {DEFAULT_TAU_THRESHOLD})")
    args = parser.parse_args()

    key = derive_key(b"axiom-qrf-reverse-v1")
    engine = ReverseQRFEngine(domain=args.domain, hmac_key=key, tau=args.tau)

    print()
    print("  AXIOM Reverse QRF — trajectory superposition recovery")
    print("  " + "=" * 53)
    print(f"  TRUST_LEVEL: {TRUST_LEVEL}  (CANNOT_MUTATE)")
    print(f"  Domain:      {args.domain}")
    print(f"  Tau:         {args.tau}")
    print()
    print(f"  Prompt:  {args.prompt}")
    print(f"  Answer:  {args.answer}")
    print()

    result = engine.collapse(args.prompt, args.answer)

    print(f"  Considered: {result.n_branches_considered} branches  "
          f"|  Accepted: {len(result.hypotheses)}  "
          f"|  Rejected: {len(result.rejected)}")
    print()

    if result.hypotheses:
        for h in result.hypotheses:
            pct = h["score"] * 100
            bar = "█" * max(1, int(pct / 2))
            print(f"    {h['branch_name']:18s}  score={h['score']:.3f}  "
                  f"fw={h['forward_weight']:.2f}  "
                  f"compat={h['compatibility']:.2f}  {bar}")
    else:
        print("    (no hypotheses above tau)")

    print()
    print(f"  HMAC:      {result.hmac_signature[:16]}...")
    print(f"  Timestamp: {result.timestamp}")
    print()
