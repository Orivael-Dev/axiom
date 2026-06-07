"""
Reverse-QRF — forward-intent prediction fed by the Master Event Token chain.
============================================================================
Ported from research/simulation/reverse_qrf_sim.py + the QRFLearner in
met_full_loop.py. Each committed turn feeds the (prev_intent → this_intent)
transition into an adaptive Markov learner; the learner predicts the *next*
turn's intent before it arrives. Turns where Aria learned something (the
retrospect signal) are weighted up — the feedback wire — so over a session the
prediction basis shifts uniform → markov → learned and the hit-rate climbs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

PATTERN_WINDOW = 5

# Domain-agnostic transition priors (INFORM begets INFORM, etc.).
_TRANSITION_PRIORS: Dict[str, str] = {
    "INFORM": "INFORM", "CLARIFY": "INFORM", "REFUSE": "CLARIFY",
    "UNCERTAIN": "CLARIFY", "HARM": "HARM", "DECEIVE": "DECEIVE",
}
LEARNED_WEIGHT = 1.6   # IMPROVEMENT-like boost for turns where Aria learned

# Maturity threshold — only act on the prediction once it's earned trust:
# the learned table is driving, enough transitions seen, and it's been accurate.
MATURE_MIN_OBS = 3
MATURE_CONF = 0.6
MATURE_HIT = 0.6


class ReverseQRFPredictor:
    """Static forward-intent predictor: Markov window over recent intents, then
    a transition prior, then a uniform fallback."""

    def predict(self, intent_history: List[str]) -> Tuple[str, float, str]:
        if intent_history:
            window = intent_history[-PATTERN_WINDOW:]
            counts = Counter(window)
            top, cnt = counts.most_common(1)[0]
            if len(set(window)) == 1:
                return top, 0.88, "markov"
            prior = _TRANSITION_PRIORS.get(intent_history[-1])
            if prior and counts.get(prior, 0) == 0:   # weak window → lean on prior
                return prior, 0.45, "prior"
            return top, max(round(cnt / len(window), 2), 0.40), "markov"
        return "INFORM", 0.50, "uniform"


class QRFLearner:
    """Adaptive Markov transition table built from observed MET chains."""

    def __init__(self) -> None:
        self._counts: Dict[str, Counter] = defaultdict(Counter)
        self._totals: Dict[str, float] = defaultdict(float)

    def observe(self, from_intent: str, to_intent: str, weight: float = 1.0) -> None:
        self._counts[from_intent][to_intent] += weight
        self._totals[from_intent] += weight

    def predict(self, from_intent: str, intent_history: List[str],
                fallback: ReverseQRFPredictor) -> Tuple[str, float, str]:
        total = self._totals.get(from_intent, 0)
        if total >= 1:
            best, cnt = self._counts[from_intent].most_common(1)[0]
            return best, min(0.95, round(cnt / total, 3)), "learned"
        intent, conf, basis = fallback.predict(intent_history)
        return intent, conf, f"fallback_{basis}"

    def transition_table(self) -> Dict[str, Dict[str, float]]:
        return {frm: {to: round(c / self._totals[frm], 3)
                      for to, c in counts.most_common()}
                for frm, counts in self._counts.items()}


class QRFEngine:
    """Drives the predictor off the MET chain: evaluate the standing prediction,
    observe the new transition, fire the next prediction."""

    def __init__(self) -> None:
        self._static = ReverseQRFPredictor()
        self._learner = QRFLearner()
        self._history: List[str] = []
        self._pending: Optional[Tuple[str, float, str]] = None
        self._hits = 0
        self._total = 0

    def step(self, intent_class: str, learned: bool = False) -> dict:
        intent_class = intent_class or "INFORM"
        if self._pending is not None:               # evaluate prior prediction
            self._total += 1
            if self._pending[0] == intent_class:
                self._hits += 1
        if self._history:                            # feed the chain transition
            self._learner.observe(self._history[-1], intent_class,
                                  weight=LEARNED_WEIGHT if learned else 1.0)
        self._history.append(intent_class)
        self._pending = self._learner.predict(intent_class, self._history, self._static)
        return self.anticipation()

    def anticipation(self, *, min_obs: int = MATURE_MIN_OBS,
                     conf_threshold: float = MATURE_CONF,
                     hit_threshold: float = MATURE_HIT) -> dict:
        intent, conf, basis = self._pending or ("INFORM", 0.5, "uniform")
        hit_rate = round(self._hits / self._total, 3) if self._total else None
        mature = (basis == "learned" and self._total >= min_obs
                  and conf >= conf_threshold and (hit_rate or 0.0) >= hit_threshold)
        return {
            "predicted_next_intent": intent,
            "confidence": conf,
            "basis": basis,
            "hit_rate": hit_rate,
            "observations": self._total,
            "mature": mature,
        }

    def reset(self) -> None:
        self.__init__()
