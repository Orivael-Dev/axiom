"""axiom_groove_agent.py — Articulatory Groove Simulation Agent

Models tongue/mouth movement as 4 continuous parameters that perturb vocal
tract resonances. Sits above the uniform-tube model: instead of a fixed
tract length, the groove agent computes effective formant shifts from
articulator positions.

Physics basis:
  - Lip rounding adds effective tube length → lowers all formants
  - Tongue height modulates F1 (high tongue → constriction at pressure node
    of F1 → lower F1; low/open jaw → higher F1)
  - Tongue backness modulates F2 (front tongue → high F2; back tongue → low F2)
  - F3 mildly tracks tongue backness (r-colouring, retroflexion)

Parametric model (3-scale + effective length):
  L_eff = L × (1 + 0.12 × lip_rounding)
  base Fn = (2n-1) × c / (4 × L_eff)
  F1 *= (1 + 0.55 × (0.5 - tongue_height)) × (1 + 0.25 × jaw_opening)
  F2 *= (1 + 0.70 × (0.5 - tongue_backness))
  F3 *= (1 + 0.20 × (0.5 - tongue_backness))

This gives the correct first-formant vowel space covering IPA cardinal vowels.

CANNOT_MUTATE: TRUST_LEVEL, ISOLATION, SOUND_SPEED_CMS

ERV mapping:
  frequency  = "physics" (band 4.0)
  amplitude  = tongue displacement from neutral (effort proxy)
  phase      = tongue backness mapped to [0, 2π] (front=0, back=2π)
  confidence = formant plausibility score
"""

import sys
import types as _types
import math
import hashlib
import hmac
import json
import os
import argparse
from dataclasses import dataclass, asdict, field
from typing import Optional

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────────
TRUST_LEVEL: int    = 2
ISOLATION:   bool   = True
SOUND_SPEED_CMS: float = 34300.0   # cm/s at 37°C body temperature

_FROZEN = frozenset({"TRUST_LEVEL", "ISOLATION", "SOUND_SPEED_CMS"})


def _module_setattr(self, name, value):
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,),
                      {"__setattr__": _module_setattr})


# ── Signing helpers ───────────────────────────────────────────────────────────

def _derive_key(namespace: str) -> bytes:
    master = os.environ.get("AXIOM_MASTER_KEY", "0" * 64)
    return hmac.new(
        bytes.fromhex(master.zfill(64)[:64]),
        namespace.encode(),
        hashlib.sha256,
    ).digest()


def _sign(namespace: str, payload: dict) -> str:
    key = _derive_key(namespace)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, blob, hashlib.sha256).hexdigest()


# ── Data structures ───────────────────────────────────────────────────────────

# Canonical vowel presets: (tongue_height, tongue_backness, lip_rounding, jaw_opening)
# Approximate IPA cardinal vowels for a 17 cm adult tract.
VOWEL_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "/a/":  (0.20, 0.65, 0.00, 0.90),   # open back  — "father"
    "/æ/":  (0.20, 0.15, 0.00, 0.85),   # open front — "cat"
    "/i/":  (0.90, 0.10, 0.00, 0.10),   # close front — "feet"
    "/ɪ/":  (0.75, 0.20, 0.00, 0.20),   # near-close front — "bit"
    "/u/":  (0.85, 0.90, 0.90, 0.10),   # close back — "food"
    "/ʊ/":  (0.70, 0.80, 0.70, 0.20),   # near-close back — "book"
    "/e/":  (0.70, 0.15, 0.00, 0.30),   # close-mid front — "face"
    "/o/":  (0.65, 0.80, 0.80, 0.20),   # close-mid back — "goat"
    "/ə/":  (0.50, 0.50, 0.00, 0.40),   # schwa / neutral
}


@dataclass
class ArticulatorState:
    """Position of the four primary articulators (all normalised 0.0–1.0).

    tongue_height  : 0=low (jaw open, tongue at floor), 1=high (tongue raised to palate)
    tongue_backness: 0=front (tongue tip near teeth),   1=back (tongue body retracted)
    lip_rounding   : 0=spread/neutral,                  1=fully rounded (/u/)
    jaw_opening    : 0=closed,                          1=fully open (/a/)
    """
    tongue_height:   float
    tongue_backness: float
    lip_rounding:    float
    jaw_opening:     float
    signature: str = ""

    def sign(self) -> "ArticulatorState":
        payload = {
            "tongue_height":   round(self.tongue_height,   4),
            "tongue_backness": round(self.tongue_backness, 4),
            "lip_rounding":    round(self.lip_rounding,    4),
            "jaw_opening":     round(self.jaw_opening,     4),
        }
        self.signature = _sign("axiom-groove-articulator-v1", payload)
        return self

    @classmethod
    def from_vowel(cls, vowel: str) -> "ArticulatorState":
        if vowel not in VOWEL_PRESETS:
            raise ValueError(f"Unknown vowel {vowel!r}. Options: {list(VOWEL_PRESETS)}")
        h, b, r, j = VOWEL_PRESETS[vowel]
        return cls(h, b, r, j).sign()

    @property
    def displacement(self) -> float:
        """Euclidean distance from neutral (/ə/) position — effort proxy."""
        nh, nb, nr, nj = VOWEL_PRESETS["/ə/"]
        return math.sqrt(
            (self.tongue_height   - nh) ** 2 +
            (self.tongue_backness - nb) ** 2 +
            (self.lip_rounding    - nr) ** 2 +
            (self.jaw_opening     - nj) ** 2
        )


@dataclass
class GrooveResult:
    """Formants derived from an ArticulatorState."""
    state:       ArticulatorState
    tract_length_cm: float
    formants:    list[float]          # Hz, F1..Fn
    l_effective: float                # cm — after lip rounding
    scales:      list[float]          # per-formant scale factors applied
    signature:   str = ""

    def sign(self) -> "GrooveResult":
        payload = {
            "formants": [round(f, 2) for f in self.formants],
            "l_effective": round(self.l_effective, 4),
            "tract_length_cm": self.tract_length_cm,
        }
        self.signature = _sign("axiom-groove-result-v1", payload)
        return self


@dataclass
class GrooveToken:
    """ERV-compatible token produced by ArticulatorySim.

    Plugs directly into axiom_firewall.erv_router.ResonantEventToken
    without requiring AXIOM_MASTER_KEY at import time.
    """
    meaning:    str
    frequency:  str    = "physics"   # ERV band
    amplitude:  float  = 0.0         # tongue displacement from neutral
    phase:      float  = 0.0         # tongue backness → [0, 2π]
    confidence: float  = 0.0         # formant plausibility 0–1
    decay:      float  = 0.1
    vowel:      str    = ""
    formants:   list[float] = field(default_factory=list)
    signature:  str    = ""

    def sign(self) -> "GrooveToken":
        payload = {
            "meaning":   self.meaning,
            "frequency": self.frequency,
            "amplitude": round(self.amplitude, 4),
            "phase":     round(self.phase, 4),
            "confidence":round(self.confidence, 4),
            "vowel":     self.vowel,
        }
        self.signature = _sign("axiom-groove-token-v1", payload)
        return self


# ── Core simulation ───────────────────────────────────────────────────────────

class ArticulatorySim:
    """Maps articulator positions to vocal tract formants.

    Parametric perturbation model:
      1. Lip rounding elongates effective tube: L_eff = L × (1 + 0.12 × lip_rounding)
      2. Base formants: Fn = (2n-1) × c / (4 × L_eff)
      3. F1 scaled by tongue height + jaw opening
      4. F2 scaled by tongue frontness (inverse of backness)
      5. F3 mildly scaled by tongue backness (r-colouring)
      6. F4+ retain base values (tract-length dominated)
    """

    def __init__(self, tract_length_cm: float = 17.0, n_formants: int = 4):
        if tract_length_cm <= 0:
            raise ValueError("tract_length_cm must be positive")
        self.tract_length_cm = tract_length_cm
        self.n_formants = n_formants

    def compute(self, state: ArticulatorState) -> GrooveResult:
        """Compute formants for a given ArticulatorState."""
        L_eff = self.tract_length_cm * (1.0 + 0.12 * state.lip_rounding)

        base = [
            (2 * n - 1) * SOUND_SPEED_CMS / (4.0 * L_eff)
            for n in range(1, self.n_formants + 1)
        ]

        # Scale factors per formant
        scales: list[float] = []
        for i in range(self.n_formants):
            if i == 0:   # F1
                s = (1.0 + 0.55 * (0.5 - state.tongue_height)) \
                  * (1.0 + 0.25 * state.jaw_opening)
            elif i == 1: # F2
                s = 1.0 + 0.70 * (0.5 - state.tongue_backness)
            elif i == 2: # F3
                s = 1.0 + 0.20 * (0.5 - state.tongue_backness)
            else:        # F4+
                s = 1.0
            scales.append(max(s, 0.1))

        formants = [round(b * s, 1) for b, s in zip(base, scales)]
        return GrooveResult(state, self.tract_length_cm, formants, L_eff, scales).sign()

    def vowel(self, name: str) -> GrooveResult:
        """Compute formants for a named vowel preset."""
        return self.compute(ArticulatorState.from_vowel(name))

    def score(self, state: ArticulatorState) -> tuple[float, list[str]]:
        """Plausibility score (0–1) for an ArticulatorState.

        Checks that all parameters are in-range and that computed formants
        sit within broad phonetically plausible bounds.
        """
        checks: list[str] = []
        passed = 0
        total = 0

        def chk(cond: bool, msg: str):
            nonlocal passed, total
            total += 1
            if cond:
                passed += 1
                checks.append(f"  PASS  {msg}")
            else:
                checks.append(f"  FAIL  {msg}")

        for name, val in [
            ("tongue_height",   state.tongue_height),
            ("tongue_backness", state.tongue_backness),
            ("lip_rounding",    state.lip_rounding),
            ("jaw_opening",     state.jaw_opening),
        ]:
            chk(0.0 <= val <= 1.0, f"{name} in [0,1]: {val:.3f}")

        result = self.compute(state)
        f = result.formants

        if len(f) >= 1:
            chk(150 <= f[0] <= 1000, f"F1 in [150,1000] Hz: {f[0]}")
        if len(f) >= 2:
            chk(600 <= f[1] <= 3000, f"F2 in [600,3000] Hz: {f[1]}")
        if len(f) >= 2:
            chk(f[1] > f[0], f"F2 > F1: {f[1]} > {f[0]}")
        if len(f) >= 3:
            chk(f[2] > f[1], f"F3 > F2: {f[2]} > {f[1]}")

        plausibility = passed / total if total else 0.0
        return plausibility, checks

    def groove_token(self, state: ArticulatorState, vowel: str = "") -> GrooveToken:
        """Generate an ERV GrooveToken from an ArticulatorState."""
        plausibility, _ = self.score(state)
        result = self.compute(state)
        phase = state.tongue_backness * 2.0 * math.pi   # front=0, back=2π
        tok = GrooveToken(
            meaning=f"articulation {vowel or '(custom)'} — groove depth {state.displacement:.2f}",
            amplitude=min(state.displacement / 1.0, 1.0),  # normalise to max possible ~1.2
            phase=phase,
            confidence=plausibility,
            vowel=vowel,
            formants=result.formants,
        )
        return tok.sign()

    def vowel_space(self) -> list[tuple[str, GrooveResult]]:
        """Compute formants for all canonical vowel presets."""
        return [(v, self.vowel(v)) for v in VOWEL_PRESETS]


# ── Agent ─────────────────────────────────────────────────────────────────────

class GrooveAgent:
    """Articulatory groove simulation agent.

    Accepts tongue/mouth articulator positions and maps them to vocal tract
    resonances. Used as a physics verification layer for QRF branches that
    involve speech, acoustic, or phonetic hypothesis outputs.

    TRUST_LEVEL = 2  (domain agent — not orchestrator)
    ISOLATION   = True
    """

    def __init__(self, tract_length_cm: float = 17.0, n_formants: int = 4):
        self._sim = ArticulatorySim(tract_length_cm, n_formants)

    def score_branch(self, hypothesis_formants: list[float],
                     state: Optional[ArticulatorState] = None) -> tuple[float, list[str]]:
        """Score a QRF branch output against articulatory plausibility.

        If state is provided, also verifies articulator-to-formant consistency.
        Returns (plausibility 0–1, detail lines).
        """
        checks: list[str] = []
        passed = 0
        total = 0

        def chk(cond: bool, msg: str):
            nonlocal passed, total
            total += 1
            if cond:
                passed += 1
                checks.append(f"  PASS  {msg}")
            else:
                checks.append(f"  FAIL  {msg}")

        chk(len(hypothesis_formants) >= 2,
            f"at least 2 formants provided: {len(hypothesis_formants)}")

        if len(hypothesis_formants) >= 1:
            chk(150 <= hypothesis_formants[0] <= 1000,
                f"F1 plausible [150,1000]: {hypothesis_formants[0]}")
        if len(hypothesis_formants) >= 2:
            chk(600 <= hypothesis_formants[1] <= 3000,
                f"F2 plausible [600,3000]: {hypothesis_formants[1]}")
            chk(hypothesis_formants[1] > hypothesis_formants[0],
                f"F2 > F1: {hypothesis_formants[1]} > {hypothesis_formants[0]}")

        # If articulator state given, check formant-state consistency (±25%)
        if state:
            expected = self._sim.compute(state).formants
            for i, (hyp, exp) in enumerate(zip(hypothesis_formants, expected)):
                err = abs(hyp - exp) / exp if exp else 1.0
                chk(err <= 0.25, f"F{i+1} within 25% of articulator prediction "
                    f"({hyp:.0f} vs {exp:.0f}, err={err:.1%})")

        plausibility = passed / total if total else 0.0
        return plausibility, checks

    def groove_tokens_for_vowel_space(self) -> list[GrooveToken]:
        """Return one GrooveToken per canonical vowel."""
        tokens = []
        for name, result in self._sim.vowel_space():
            state = ArticulatorState.from_vowel(name)
            tokens.append(self._sim.groove_token(state, name))
        return tokens


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Axiom Groove Agent — articulatory simulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 axiom_groove_agent.py --vowel /i/
  python3 axiom_groove_agent.py --vowel-space
  python3 axiom_groove_agent.py --height 0.9 --backness 0.1 --rounding 0.0 --jaw 0.1
  python3 axiom_groove_agent.py --vowel-space --groove-tokens
""",
    )
    p.add_argument("--length", type=float, default=17.0,
                   help="Vocal tract length in cm (default 17.0 — adult)")
    p.add_argument("--vowel", choices=list(VOWEL_PRESETS),
                   help="Named vowel preset")
    p.add_argument("--vowel-space", action="store_true",
                   help="Print formants for all canonical vowels")
    p.add_argument("--height",   type=float, metavar="H",
                   help="Tongue height 0.0–1.0 (0=low, 1=high)")
    p.add_argument("--backness", type=float, metavar="B",
                   help="Tongue backness 0.0–1.0 (0=front, 1=back)")
    p.add_argument("--rounding", type=float, default=0.0, metavar="R",
                   help="Lip rounding 0.0–1.0 (default 0)")
    p.add_argument("--jaw",      type=float, default=0.5, metavar="J",
                   help="Jaw opening 0.0–1.0 (default 0.5)")
    p.add_argument("--groove-tokens", action="store_true",
                   help="Print ERV GrooveTokens")
    return p.parse_args()


def _print_result(vowel_name: str, result: GrooveResult, sim: ArticulatorySim,
                  show_token: bool):
    s = result.state
    print(f"\n  Articulator state{f'  ({vowel_name})' if vowel_name else ''}")
    print(f"    tongue height   : {s.tongue_height:.2f}  (0=low, 1=high)")
    print(f"    tongue backness : {s.tongue_backness:.2f}  (0=front, 1=back)")
    print(f"    lip rounding    : {s.lip_rounding:.2f}  (0=spread, 1=rounded)")
    print(f"    jaw opening     : {s.jaw_opening:.2f}  (0=closed, 1=open)")
    print(f"    L_eff           : {result.l_effective:.2f} cm  (neutral {sim.tract_length_cm:.1f} cm)")
    print()
    print(f"  {'Formant':<10} {'Freq (Hz)':>10}  {'Scale':>7}")
    print("  " + "─" * 32)
    for i, (f, sc) in enumerate(zip(result.formants, result.scales)):
        print(f"  F{i+1:<9} {f:>10.1f}  {sc:>7.3f}")

    if show_token:
        tok = sim.groove_token(result.state, vowel_name)
        print()
        print(f"  GrooveToken (ERV)")
        print(f"    meaning   : {tok.meaning}")
        print(f"    frequency : {tok.frequency}  (band 4.0)")
        print(f"    amplitude : {tok.amplitude:.4f}  (displacement from /ə/)")
        print(f"    phase     : {tok.phase:.4f} rad  (backness → [0, 2π])")
        print(f"    confidence: {tok.confidence:.4f}")
        print(f"    sig[:12]  : {tok.signature[:12]}…")


def main() -> int:
    args = _parse_args()
    sim = ArticulatorySim(args.length)

    print("═" * 66)
    print("  AXIOM Groove Agent — Articulatory Simulation")
    print(f"  Tract length: {args.length:.1f} cm  |  c = {SOUND_SPEED_CMS:.0f} cm/s (37°C)")
    print("═" * 66)

    if args.vowel_space:
        print(f"\n  Vowel Space  (L = {args.length:.1f} cm)\n")
        print(f"  {'Vowel':<7} {'F1':>7} {'F2':>7} {'F3':>7} {'F4':>7}  "
              f"{'Height':>6} {'Back':>6} {'Round':>6} {'Jaw':>6}")
        print("  " + "─" * 62)
        for name, result in sim.vowel_space():
            f = result.formants
            s = result.state
            row = (f"  {name:<7} "
                   f"{f[0]:>7.0f} {f[1]:>7.0f} {f[2]:>7.0f} {f[3]:>7.0f}  "
                   f"{s.tongue_height:>6.2f} {s.tongue_backness:>6.2f} "
                   f"{s.lip_rounding:>6.2f} {s.jaw_opening:>6.2f}")
            print(row)

        if args.groove_tokens:
            print(f"\n  GrooveTokens  (ERV physics band 4.0)\n")
            print(f"  {'Vowel':<7} {'Amplitude':>10} {'Phase':>8} {'Conf':>6}")
            print("  " + "─" * 36)
            for tok in GrooveAgent(args.length).groove_tokens_for_vowel_space():
                print(f"  {tok.vowel:<7} {tok.amplitude:>10.4f} "
                      f"{tok.phase:>8.4f} {tok.confidence:>6.4f}")

        print()
        print("═" * 66)
        return 0

    if args.vowel:
        result = sim.vowel(args.vowel)
        _print_result(args.vowel, result, sim, args.groove_tokens)
        print()
        print("═" * 66)
        return 0

    if args.height is not None and args.backness is not None:
        state = ArticulatorState(
            args.height, args.backness, args.rounding, args.jaw
        ).sign()
        result = sim.compute(state)
        plaus, checks = sim.score(state)
        _print_result("", result, sim, args.groove_tokens)
        print(f"\n  Plausibility checks (score={plaus:.2f})")
        for line in checks:
            print(f"  {line}")
        print()
        print("═" * 66)
        return 0

    # Default: show /ə/ (neutral) and a quick vowel space summary
    print(f"\n  No vowel or articulator specified — showing neutral /ə/")
    result = sim.vowel("/ə/")
    _print_result("/ə/", result, sim, args.groove_tokens)
    print(f"\n  Tip: use --vowel-space to see all canonical vowels,")
    print(f"       or --height H --backness B to specify custom positions.")
    print()
    print("═" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
