"""DelegateRouter — cheap, non-LLM router from event content to AXM delegates.

The router runs the existing IntentClassifier (microseconds, no LLM,
already signed) and returns the ordered list of SkillDelegate names
whose `intent_classes` tuple contains the classified intent. Used by
`Coordinator.compose_from_delegates()` to pick which 1..N delegates
should actually fire — that selectivity is where the per-event token
savings come from.

`LatencyAwareRouter` wraps `DelegateRouter` with ledger-fed backend
health scores (EWMA of latency + verified-rate). Delegates whose
primary backend is unhealthy are moved to the end of the result —
they are never dropped entirely so callers always get a fallback.

`RouterPolicy` is the scoring engine: it reads the last N exoskeleton-
ledger entries, computes per-backend average latency and verified-rate,
and returns a binary healthy/unhealthy score. The score can be extended
to a continuous signal by setting `continuous=True`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from axiom_signing import derive_key


@dataclass(frozen=True)
class RoutingDecision:
    """What the router decided + why. Surfaced into the LayerReport payload."""
    intent_class:  str
    confidence:    float
    delegate_names: tuple[str, ...]
    matched_on:    str   # "text" | "audio_transcript" | "empty"


class DelegateRouter:
    """Pure function: (event content, delegate set) -> ordered delegate names.

    Construction lazily builds an IntentClassifier under the firewall
    key so the router is callable with no further setup. Pass an
    explicit classifier instance to share across components.
    """

    def __init__(self, classifier=None) -> None:
        if classifier is None:
            from axiom_intent_classifier import IntentClassifier
            classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))
        self._clf = classifier

    def route(
        self,
        *,
        delegates: Sequence,        # Sequence[SkillDelegate]
        text:      Optional[str] = None,
        audio_transcript: Optional[str] = None,
    ) -> RoutingDecision:
        """Pick the delegates that should run for this event.

        Classification uses `text` if present, otherwise `audio_transcript`.
        Both empty → no delegates matched (caller falls back to default).
        """
        body = text if text else audio_transcript
        matched_on = "text" if text else (
            "audio_transcript" if audio_transcript else "empty"
        )
        if not body:
            return RoutingDecision(
                intent_class="UNCERTAIN", confidence=0.0,
                delegate_names=(), matched_on="empty",
            )
        result = self._clf.classify(body)
        intent = result.intent_class
        # Map AXIOM intent classes → canonical lowercase tags so AXM
        # delegate manifests can use either case ("harm", "HARM", etc.)
        intent_variants = {intent, intent.lower()}
        picked: List[str] = []
        for d in delegates:
            d_intents = {i for i in d.intent_classes} | {
                i.lower() for i in d.intent_classes
            }
            if intent_variants & d_intents:
                if d.name not in picked:
                    picked.append(d.name)
        return RoutingDecision(
            intent_class=intent,
            confidence=float(result.confidence),
            delegate_names=tuple(picked),
            matched_on=matched_on,
        )


# ── RouterPolicy — ledger-fed backend health scores ───────────────────────────

def _ewma(values: List[float], alpha: float = 0.1) -> float:
    """Exponentially weighted moving average over an ordered sequence.

    Entries are processed oldest-first (values[0] is oldest).
    Recent entries weigh more: each new observation gets weight ``alpha``,
    the accumulated estimate gets ``1 - alpha``.

    Returns 0.0 for an empty sequence.
    """
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


class RouterPolicy:
    """EWMA-backed health score per (backend, domain) from the exoskeleton ledger.

    Reads the last ``max_age_entries`` ledger entries and computes per-
    (backend, domain) EWMA of latency_ms and verified-rate.  Recent entries
    weigh more than older ones (α = ``ewma_alpha``, default 0.1).

    Scoring:
      1.0  when ewma_latency < latency_threshold AND ewma_verified_rate >= verified_floor
      0.0  otherwise

    ``score(backend, domain)`` falls back to the per-backend aggregate (domain="")
    when no domain-specific history exists.  Unknown backends return 1.0 (healthy).

    Call ``refresh()`` before routing to update from disk.  ``LatencyAwareRouter``
    calls this automatically on every ``route()``.
    """

    def __init__(
        self,
        ledger_path=None,
        latency_threshold_ms: int = 1500,
        verified_floor: float = 0.90,
        max_age_entries: int = 200,
        ewma_alpha: float = 0.1,
    ) -> None:
        self._path          = ledger_path
        self._lat_threshold = latency_threshold_ms
        self._ver_floor     = verified_floor
        self._max_entries   = max_age_entries
        self._alpha         = ewma_alpha
        # keyed by (backend, domain); domain="" = aggregate across all domains
        self._scores: Dict[Tuple[str, str], float] = {}

    def refresh(self) -> None:
        """Re-compute per-(backend, domain) EWMA scores from the last N entries."""
        from axiom_exoskeleton_ledger import read_ledger
        entries = read_ledger(self._path)[-self._max_entries:]

        # samples[(backend, domain)] = [(latency_ms, verified), ...]  oldest-first
        samples: Dict[Tuple[str, str], List[Tuple[int, bool]]] = {}
        for e in entries:
            key_domain = (e.backend, e.domain)
            key_agg    = (e.backend, "")
            pair       = (e.latency_ms, e.verified)
            samples.setdefault(key_domain, []).append(pair)
            if key_domain != key_agg:
                samples.setdefault(key_agg, []).append(pair)

        scores: Dict[Tuple[str, str], float] = {}
        for key, slist in samples.items():
            lat_ewma = _ewma([float(s[0]) for s in slist], self._alpha)
            ver_ewma = _ewma([1.0 if s[1] else 0.0 for s in slist], self._alpha)
            healthy  = (lat_ewma < self._lat_threshold
                        and ver_ewma >= self._ver_floor)
            scores[key] = 1.0 if healthy else 0.0
        self._scores = scores

    def score(self, backend: str, domain: str = "") -> float:
        """Return 1.0 (healthy) or 0.0 (unhealthy) for a (backend, domain) pair.

        Lookup order:
          1. (backend, domain)    — specific match
          2. (backend, "")        — per-backend aggregate (all domains)
          3. 1.0                  — unknown backend, assume healthy
        """
        specific = self._scores.get((backend, domain))
        if specific is not None:
            return specific
        return self._scores.get((backend, ""), 1.0)

    @property
    def scores(self) -> Dict[Tuple[str, str], float]:
        """Read-only snapshot of last computed (backend, domain) scores."""
        return dict(self._scores)


# ── LatencyAwareRouter — DelegateRouter + RouterPolicy ───────────────────────

class LatencyAwareRouter:
    """Wraps DelegateRouter, reorders delegates by backend health.

    On each `route()` call:
      1. Refreshes RouterPolicy from the exoskeleton ledger.
      2. Runs the underlying DelegateRouter (intent classification).
      3. Partitions matched delegates into healthy / degraded by their
         primary backend (`backend_chain[0]`).
      4. Returns healthy delegates first, degraded ones last — never
         drops delegates entirely so callers always have a fallback.

    Usage::

        policy = RouterPolicy(latency_threshold_ms=1500, verified_floor=0.90)
        router = LatencyAwareRouter(policy=policy)
        # drop-in for DelegateRouter in coordinator.compose_from_delegates()
    """

    def __init__(
        self,
        policy: RouterPolicy,
        base: Optional[DelegateRouter] = None,
    ) -> None:
        self._policy = policy
        self._base   = base or DelegateRouter()

    def route(
        self,
        *,
        delegates: Sequence,
        text:      Optional[str] = None,
        audio_transcript: Optional[str] = None,
        domain:    Optional[str] = None,
    ) -> RoutingDecision:
        self._policy.refresh()
        decision = self._base.route(
            delegates=delegates, text=text, audio_transcript=audio_transcript,
        )
        if not decision.delegate_names:
            return decision

        by_name = {d.name: d for d in delegates}

        def _primary_backend(dname: str) -> str:
            d = by_name.get(dname)
            if d is not None and hasattr(d, "backend_chain") and d.backend_chain:
                return d.backend_chain[0]
            return "local"

        healthy:  List[str] = []
        degraded: List[str] = []
        for dname in decision.delegate_names:
            b = _primary_backend(dname)
            if self._policy.score(b, domain or "") > 0.0:
                healthy.append(dname)
            else:
                degraded.append(dname)

        return RoutingDecision(
            intent_class=decision.intent_class,
            confidence=decision.confidence,
            delegate_names=tuple(healthy + degraded),
            matched_on=decision.matched_on,
        )
