"""
AXIOM Inference OS — Layer 1 Adaptive Router
=============================================
Theme 3 (Continuous Evaluation / smart routing). All the signals already existed —
the exoskeleton ledger records per-backend latency + verified-rate, and the cognition
layer emits a metabolic economy hint — but *nothing fed the routing decision*. The OS
router just took ``backend.name`` and a hard-coded 512-token budget.

This closes that gap. ``AdaptiveRouter.decide()`` fuses two learned signals into a
``RouteDirective`` the pipeline acts on **before** generation:

  • RouterPolicy health — an EWMA of latency_ms + verified-rate per (backend, domain),
    read from the exoskeleton ledger the OS already writes. If the primary backend has
    been slow / failing on recent history, the directive flags it degraded and
    recommends the fallback proactively (don't wait for a live failure to fail over).

  • the cognition economy hint (REASON_CHEAPLY / REFUSE_FOR_HEALTH — the metabolic
    "this resembles a learned high-cost path"). It drops the request into the ECONOMY
    tier: a smaller output-token budget, so "reason cheaply" actually spends fewer
    tokens instead of just being recorded.

Deterministic and LLM-free (Layer 1 must be microsecond-fast). With no ledger history
and no cognition verdict it returns a standard, healthy directive — additive and safe.

    router = AdaptiveRouter()                       # reads the default exoskeleton ledger
    d = router.decide("nim", domain="legal", cognition=cog_verdict, base_max_tokens=512)
    d.tier               # "economy"  (cognition said REASON_CHEAPLY)
    d.max_output_tokens  # 160        (economy budget — fewer tokens spent)
    d.backend_healthy    # False      (recent nim/legal history was slow/failing)
    d.prefer_fallback    # True
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

# tiers
STANDARD = "standard"
ECONOMY = "economy"

# cognition actions that mean "reason cheaply" (metabolic economy hints)
_ECONOMY_ACTIONS = {"REASON_CHEAPLY", "REFUSE_FOR_HEALTH"}

# default output-token budgets
STANDARD_MAX_TOKENS = 512
ECONOMY_MAX_TOKENS = 160


@dataclass(frozen=True)
class RouteDirective:
    """The routing decision the pipeline acts on, before it spends a model token."""
    route:            str            # backend the router recommends
    tier:             str            # "standard" | "economy"
    max_output_tokens: int           # generation budget (economy = smaller)
    backend_healthy:  bool           # RouterPolicy EWMA health for (backend, domain)
    prefer_fallback:  bool           # recommend fallback (backend degraded on history)
    reason:           str

    def to_dict(self) -> dict:
        return {"route": self.route, "tier": self.tier,
                "max_output_tokens": self.max_output_tokens,
                "backend_healthy": self.backend_healthy,
                "prefer_fallback": self.prefer_fallback, "reason": self.reason}


class AdaptiveRouter:
    """Fuse ledger health + cognition economy hint into a RouteDirective.

    ``policy`` is a RouterPolicy (event_token) — omit to auto-build one that reads the
    default exoskeleton ledger. ``fallback_route`` is the route label recommended when
    the primary backend is degraded.
    """

    def __init__(self, policy=None, *, fallback_route: str = "fallback",
                 economy_max_tokens: int = ECONOMY_MAX_TOKENS) -> None:
        self._policy = policy if policy is not None else self._build_policy()
        self._fallback_route = fallback_route
        self._economy_max = economy_max_tokens

    @staticmethod
    def _build_policy():
        try:
            from axiom_event_token.router import RouterPolicy
            return RouterPolicy()
        except Exception:
            return None

    def decide(self, backend_name: str, domain: str = "", *,
               cognition: Optional[dict] = None,
               base_max_tokens: int = STANDARD_MAX_TOKENS) -> RouteDirective:
        # ── health from the exoskeleton ledger (EWMA of latency + verified-rate) ──
        healthy = True
        if self._policy is not None:
            try:
                self._policy.refresh()
                healthy = self._policy.score(backend_name, domain or "") > 0.0
            except Exception:
                healthy = True   # unknown history → assume healthy, never over-fire

        # ── economy tier from the cognition metabolic hint ───────────────────────
        action = (cognition or {}).get("action")
        economy = action in _ECONOMY_ACTIONS
        tier = ECONOMY if economy else STANDARD
        max_tokens = self._economy_max if economy else base_max_tokens

        prefer_fallback = not healthy
        route = self._fallback_route if prefer_fallback else backend_name

        bits = []
        bits.append("degraded backend history → prefer fallback" if prefer_fallback
                    else "backend healthy")
        if economy:
            bits.append(f"economy tier ({action}) → {max_tokens}-tok budget")
        reason = "; ".join(bits)

        return RouteDirective(
            route=route, tier=tier, max_output_tokens=max_tokens,
            backend_healthy=healthy, prefer_fallback=prefer_fallback, reason=reason,
        )

    def rank(self, backend_names, domain: str = ""):
        """Partition a chain's member backends into (healthy, degraded) by ledger
        health, preserving configured order within each group. Used for proactive
        failover: try healthy members first, keep degraded ones as last resort.

        Returns ``(healthy, degraded)`` — both lists of names. Unknown backends
        (no history) count as healthy, so a fresh install reorders nothing."""
        if self._policy is not None:
            try:
                self._policy.refresh()
            except Exception:
                pass
        healthy, degraded = [], []
        for n in backend_names:
            ok = True
            if self._policy is not None:
                try:
                    ok = self._policy.score(n, domain or "") > 0.0
                except Exception:
                    ok = True
            (healthy if ok else degraded).append(n)
        return healthy, degraded


# ── CLI — inspect the directive for a (backend, domain) ───────────────────────
def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inference OS Layer-1 adaptive router")
    p.add_argument("backend", nargs="?", default="local")
    p.add_argument("--domain", default="general")
    p.add_argument("--action", default="PROCEED",
                   help="cognition action (PROCEED / REASON_CHEAPLY / REFUSE_FOR_HEALTH)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    d = AdaptiveRouter().decide(args.backend, args.domain,
                                cognition={"action": args.action})
    print(json.dumps(d.to_dict(), indent=2) if args.json
          else f"route={d.route} tier={d.tier} max_tokens={d.max_output_tokens} "
               f"healthy={d.backend_healthy}  ({d.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
