"""Electric Resonance Vectoring (ERV) — simulated resonance routing for EventTokens.

Based on the AI VRAM Electric Resonance Concept (Orivael Research Note).

ERV adds a resonance layer to EventTokens: each token carries frequency (domain
band), amplitude (importance/urgency), phase (reasoning state), confidence, and
decay. Agents are assigned frequency bands and only activate when the token's
resonance score exceeds their threshold.

Out-of-phase token pairs flag Constitutional Zero-Day Discovery — novel
constitutional violations not caught by any existing block pattern.

Architecture (from the spec):
  input -> EventTokens -> resonance signature -> active agents -> verified output

IMPORTANT: This is simulated resonance, not physical vibration.
Claim: simulated resonance routing, measurable efficiency, traceable activation.
Do NOT claim quantum computing or literal GPU atom vibration.

Reference: AI VRAM Electric Resonance Concept (Orivael Research Note, 2026)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from axiom_signing import derive_key

_ERV_KEY_NS = b"axiom-erv-v1"

# Domain frequency bands (from PDF §6 prototype path)
DOMAIN_BANDS: dict[str, float] = {
    "legal":      1.0,
    "medical":    2.0,
    "coding":     3.0,
    "physics":    4.0,
    "security":   5.0,
    "governance": 6.0,
    "memory":     7.0,
    "general":    0.0,
}

# Constitutional Zero-Day thresholds
PHASE_CONFLICT_ZERO_DAY = 0.75   # phase_conflict above this → zero-day alert
AMPLITUDE_ZERO_DAY      = 0.70   # minimum amplitude to count as meaningful conflict
RESONANCE_THRESHOLD     = 0.70   # minimum resonance score to wake an agent


def _erv_key() -> bytes:
    return derive_key(_ERV_KEY_NS)


def _sign_erv(d: dict) -> str:
    key = _erv_key()
    body = json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


@dataclass
class ResonantEventToken:
    """An EventToken extended with ERV resonance metadata.

    Signal fields (from the PDF spec):
      frequency  — domain band name ("legal", "medical", "governance", …)
      amplitude  — importance or urgency [0.0, 1.0]; high for contradictions
      phase      — current reasoning state [0.0, 2π]; alignment = reinforce
      confidence — belief strength [0.0, 1.0]
      decay      — how fast relevance fades [0.0, 1.0]; 0 = stays relevant
      signature  — HMAC-SHA256 over canonical fields
    """
    meaning:    str
    frequency:  str          # domain band label
    amplitude:  float        # 0.0 – 1.0
    phase:      float        # 0.0 – 2π
    confidence: float        # 0.0 – 1.0
    decay:      float        # 0.0 – 1.0  (0 = no decay)
    timestamp:  float        = field(default_factory=time.time)
    signature:  str          = ""

    def sign(self) -> "ResonantEventToken":
        d = {k: v for k, v in asdict(self).items() if k != "signature"}
        object.__setattr__(self, "signature", _sign_erv(d))
        return self

    def verify(self) -> bool:
        d = {k: v for k, v in asdict(self).items() if k != "signature"}
        expected = _sign_erv(d)
        return hmac.compare_digest(self.signature, expected)

    @property
    def frequency_value(self) -> float:
        """Numeric value for the frequency band (for cosine similarity)."""
        return DOMAIN_BANDS.get(self.frequency, 0.0)

    def current_amplitude(self) -> float:
        """Amplitude after applying decay based on token age."""
        age_s = time.time() - self.timestamp
        return self.amplitude * math.exp(-self.decay * age_s / 3600)


@dataclass
class AgentBand:
    """Agent frequency band registration.

    Each specialized agent registers a domain band.  The ERV router wakes
    only agents whose band resonates with the incoming token's frequency.
    """
    agent_id:           str
    frequency_band:     str       # one of DOMAIN_BANDS keys
    resonance_threshold: float   = RESONANCE_THRESHOLD
    description:        str      = ""

    @property
    def frequency_value(self) -> float:
        return DOMAIN_BANDS.get(self.frequency_band, 0.0)


@dataclass
class ResonanceDecision:
    """Result of ERV routing for one token."""
    token_meaning:    str
    token_frequency:  str
    token_amplitude:  float
    active_agents:    list[str]   # agent_ids that fired
    idle_agents:      list[str]   # agent_ids kept idle
    scores:           dict        # agent_id → resonance_score
    timestamp:        float       = field(default_factory=time.time)


@dataclass
class ZeroDayAlert:
    """Constitutional Zero-Day Discovery — novel phase-conflict event.

    When two tokens are out of phase (phase_conflict high) AND amplitude is
    high, the system flags a potential novel constitutional violation not
    caught by existing block patterns.
    """
    token_a_meaning:  str
    token_b_meaning:  str
    phase_conflict:   float      # 0.0 – 1.0; high = out of phase
    amplitude:        float      # max of the two tokens' amplitudes
    is_zero_day:      bool
    description:      str
    timestamp:        float      = field(default_factory=time.time)


class ERVRouter:
    """Routes tokens to agents by resonance score.

    Resonance score between a token and an agent band:
      score = (1 - |freq_token - freq_agent| / max_freq) * amplitude

    Above the agent's threshold → agent wakes; below → stays idle.
    """

    def __init__(self, bands: list[AgentBand]) -> None:
        self._bands = bands
        self._history: list[ResonanceDecision] = []
        self._zero_days: list[ZeroDayAlert]    = []

    def score(self, token: ResonantEventToken, band: AgentBand) -> float:
        """Cosine-like resonance score [0.0, 1.0], amplitude-weighted."""
        max_freq = max(DOMAIN_BANDS.values()) or 1.0
        freq_dist = abs(token.frequency_value - band.frequency_value) / max_freq
        freq_sim  = 1.0 - freq_dist
        return round(freq_sim * token.current_amplitude(), 4)

    def route(self, token: ResonantEventToken) -> ResonanceDecision:
        """Return agent_ids above threshold; keep others idle."""
        active: list[str] = []
        idle:   list[str] = []
        scores: dict      = {}

        for band in self._bands:
            s = self.score(token, band)
            scores[band.agent_id] = s
            if s >= band.resonance_threshold:
                active.append(band.agent_id)
            else:
                idle.append(band.agent_id)

        decision = ResonanceDecision(
            token_meaning   = token.meaning,
            token_frequency = token.frequency,
            token_amplitude = token.amplitude,
            active_agents   = active,
            idle_agents     = idle,
            scores          = scores,
        )
        self._history.append(decision)
        if len(self._history) > 200:
            self._history = self._history[-200:]
        return decision

    def detect_phase_conflict(
        self,
        token_a: ResonantEventToken,
        token_b: ResonantEventToken,
    ) -> ZeroDayAlert:
        """Detect constitutional zero-day via phase conflict.

        phase_conflict = |phase_a - phase_b| / π  (normalised to [0,1])
        A high-amplitude, out-of-phase pair may be a novel constitutional
        violation not caught by any block pattern.
        """
        raw_diff     = abs(token_a.phase - token_b.phase)
        # Wrap-around (phases are circular 0–2π)
        wrapped_diff = min(raw_diff, 2 * math.pi - raw_diff)
        conflict     = round(wrapped_diff / math.pi, 4)
        amplitude    = max(token_a.current_amplitude(), token_b.current_amplitude())

        is_zd = conflict >= PHASE_CONFLICT_ZERO_DAY and amplitude >= AMPLITUDE_ZERO_DAY
        description = (
            f"Phase conflict {conflict:.2f} (threshold {PHASE_CONFLICT_ZERO_DAY}) "
            f"with amplitude {amplitude:.2f} — "
            + ("CONSTITUTIONAL ZERO-DAY DETECTED: novel violation pattern."
               if is_zd else "Within normal bounds.")
        )
        alert = ZeroDayAlert(
            token_a_meaning = token_a.meaning,
            token_b_meaning = token_b.meaning,
            phase_conflict  = conflict,
            amplitude       = amplitude,
            is_zero_day     = is_zd,
            description     = description,
        )
        if is_zd:
            self._zero_days.append(alert)
            if len(self._zero_days) > 100:
                self._zero_days = self._zero_days[-100:]
        return alert

    def recent_decisions(self, n: int = 20) -> list[ResonanceDecision]:
        return list(reversed(self._history[-n:]))

    def recent_zero_days(self, n: int = 20) -> list[ZeroDayAlert]:
        return list(reversed(self._zero_days[-n:]))

    # ── Default agent band registry ────────────────────────────────────────

    @classmethod
    def default(cls) -> "ERVRouter":
        """Default band registry matching CMAA container trust hierarchy."""
        bands = [
            AgentBand("axiom-intent-gate",   "governance", 0.65, "Constitutional intent gate"),
            AgentBand("axiom-legal-agent",    "legal",      0.70, "Legal clause / contract domain"),
            AgentBand("axiom-medical-agent",  "medical",    0.70, "Medical / HIPAA domain"),
            AgentBand("axiom-security-agent", "security",   0.70, "Security / threat detection"),
            AgentBand("axiom-coding-agent",   "coding",     0.70, "Code generation / review"),
            AgentBand("axiom-memory",         "memory",     0.60, "Latent memory / context"),
            AgentBand("axiom-orchestrator",   "governance", 0.80, "Fleet orchestrator (TL4)"),
        ]
        return cls(bands)


