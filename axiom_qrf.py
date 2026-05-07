"""
AXIOM QRF — Quantum Reasoning Forecast engine.
Manifest  : qrf-impl-v1
Trust     : TRUST_LEVEL = 2   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Thin layer on axiom_latent.py that reframes branch scores
as probability weights for domain-specific forecasting.

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
TRUST_LEVEL: int = 2
ISOLATION: bool = True

DOMAIN_BRANCH_COUNTS: dict = {
    "medical": 8,
    "financial": 6,
    "supply_chain": 4,
    "hr": 4,
    "security": 6,
}

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "DOMAIN_BRANCH_COUNTS",
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

LOG = logging.getLogger("axiom.qrf")


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class QRFResult:
    """Probability-weighted forecast result.

    BUG-007: hmac_signature computed with .hexdigest().
    BUG-008: canonical fields encoded via .encode("utf-8") before HMAC.
    """
    prompt: str
    domain: str
    branches: list[dict]       # sorted by probability_weight descending
    killed: list[dict]         # branches killed by monotonic gate
    probability_band: str      # "HIGH" / "MODERATE" / "LOW" / "UNCERTAIN"
    top_branch: str            # name of highest-weight branch
    manifold: Optional[dict]   # manifold distance data if available
    timestamp: str
    hmac_signature: str


# ── QRFEngine ────────────────────────────────────────────────────────────

class QRFEngine:
    """Quantum Reasoning Forecast — thin layer on LatentEngine.

    TRUST_LEVEL = 2 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)
    """

    def __init__(self, domain: str, hmac_key: bytes,
                 n_branches: Optional[int] = None,
                 endpoint: Optional[str] = None):
        if domain not in DOMAIN_BRANCH_COUNTS:
            supported = ", ".join(sorted(DOMAIN_BRANCH_COUNTS.keys()))
            raise ValueError(f"Unsupported domain: {domain}. "
                             f"Supported: {supported}")

        self._domain = domain
        self._hmac_key = hmac_key
        self._n_branches = n_branches or DOMAIN_BRANCH_COUNTS[domain]
        self._endpoint = endpoint

        # Lazy-init LatentEngine to avoid import-time side effects
        from axiom_latent import LatentEngine
        self._engine = LatentEngine(use_api=bool(endpoint))

    # ── Probability weight computation ───────────────────────────────

    @staticmethod
    def _compute_weights(branches: list[dict]) -> list[dict]:
        """Convert branch scores to probability weights summing to 1.0."""
        total = sum(b.get("score", 0.0) for b in branches)

        weighted = []
        for b in branches:
            score = b.get("score", 0.0)
            if total > 0:
                weight = score / total
            else:
                # Equal weight when all scores are zero
                weight = 1.0 / len(branches) if branches else 0.0
            weighted.append({
                **b,
                "probability_weight": round(weight, 6),
            })

        # Sort descending by probability weight
        weighted.sort(key=lambda x: x["probability_weight"], reverse=True)
        return weighted

    @staticmethod
    def _classify_band(top_weight: float) -> str:
        """Classify probability band based on top branch weight."""
        if top_weight >= 0.50:
            return "HIGH"
        if top_weight >= 0.30:
            return "MODERATE"
        if top_weight >= 0.15:
            return "LOW"
        return "UNCERTAIN"

    # ── HMAC signing ─────────────────────────────────────────────────

    def _sign_result(self, prompt: str, domain: str, top_branch: str,
                     probability_band: str, n_branches: int,
                     n_killed: int) -> str:
        """Compute HMAC-SHA256 over canonical fields. BUG-007/BUG-008."""
        canonical: bytes = json.dumps({
            "prompt": prompt,
            "domain": domain,
            "top_branch": top_branch,
            "probability_band": probability_band,
            "n_branches": n_branches,
            "n_killed": n_killed,
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        return hmac_lib.new(
            self._hmac_key, canonical, hashlib.sha256
        ).hexdigest()  # BUG-007

    # ── Core forecast ────────────────────────────────────────────────

    def forecast(self, prompt: str) -> QRFResult:
        """Run LatentEngine and reframe branches as probability forecast."""
        # Run latent reasoning with trajectory
        result = self._engine.run(prompt, trajectory=True)

        # Extract branch results from multiplex phase
        phases = result.get("phases", {})
        multiplex = phases.get("multiplex", {})
        all_branches = multiplex.get("all_branches", [])

        # Trim to domain branch count
        branches = all_branches[:self._n_branches]

        # Compute probability weights
        weighted = self._compute_weights(branches)

        # Identify killed branches (score == 0 or absent from results)
        killed = [b for b in weighted if b.get("score", 0.0) == 0.0]
        live = [b for b in weighted if b.get("score", 0.0) > 0.0]

        # Top branch
        top_branch = live[0]["branch"] if live else (
            weighted[0]["branch"] if weighted else "none")

        # Probability band
        top_weight = live[0]["probability_weight"] if live else 0.0
        probability_band = self._classify_band(top_weight)

        # Manifold data
        manifold = result.get("manifold")

        # Timestamp
        timestamp = datetime.now(timezone.utc).isoformat()

        # HMAC signature
        signature = self._sign_result(
            prompt=prompt,
            domain=self._domain,
            top_branch=top_branch,
            probability_band=probability_band,
            n_branches=len(weighted),
            n_killed=len(killed),
        )

        return QRFResult(
            prompt=prompt,
            domain=self._domain,
            branches=weighted,
            killed=killed,
            probability_band=probability_band,
            top_branch=top_branch,
            manifold=manifold,
            timestamp=timestamp,
            hmac_signature=signature,
        )


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from axiom_signing import derive_key

    parser = argparse.ArgumentParser(description="AXIOM QRF — Quantum Reasoning Forecast")
    parser.add_argument("prompt", help="Forecast question")
    parser.add_argument("--domain", default="financial",
                        choices=sorted(DOMAIN_BRANCH_COUNTS.keys()),
                        help="Forecast domain")
    args = parser.parse_args()

    key = derive_key(b"axiom-qrf-v1")
    engine = QRFEngine(domain=args.domain, hmac_key=key)

    print(f"\n  AXIOM QRF — Quantum Reasoning Forecast")
    print("  " + "=" * 44)
    print(f"  TRUST_LEVEL: {TRUST_LEVEL}  (CANNOT_MUTATE)")
    print(f"  Domain:      {args.domain}")
    print(f"  Branches:    {engine._n_branches}")
    print()

    result = engine.forecast(args.prompt)

    for b in result.branches:
        pct = b["probability_weight"] * 100
        bar = "\u2588" * int(pct / 2)
        print(f"  {b['branch']:20s}  {pct:5.1f}%  {bar}")

    print()
    print(f"  Band:       {result.probability_band}")
    print(f"  Top branch: {result.top_branch}")
    print(f"  HMAC:       {result.hmac_signature[:16]}...")
    print(f"  Timestamp:  {result.timestamp}")
    print()
