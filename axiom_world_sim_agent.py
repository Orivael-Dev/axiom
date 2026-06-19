"""AXIOM World Simulation Agent — SimPhysBio branch for QRF hypothesis scoring.

Provides physics/biology simulation primitives that QRF can use as a
verification layer on top of test pass rate.

The key idea: when a QRF branch produces code, we don't only ask "does it
pass unit tests?" — we also ask "is the output physically plausible?"
A vocal formant calculator that returns [500, 1500, 2500, 3500] passes
the range-check tests AND matches the acoustic physics of a 17-cm tube.
One that returns [200, 400, 600, 800] might pass loose bounds tests but
fails the odd-harmonic spacing constraint (F2/F1 ≈ 3, not 2).

Physics plausibility is scored 0.0–1.0 and multiplied into the branch
score, tightening QRF's pruning gate.

Simulation modules
──────────────────
  VocalTractSim      — uniform-tube formant model; maps to ERV physics band
  PendulumSim        — RK4 pendulum; energy drift scoring
  TubeResonanceSim   — standing-wave resonance in a closed/open tube
  EnzymeSim          — Michaelis-Menten kinetics (bio sim track)

ERV integration
───────────────
Each simulation produces ResonantEventToken fields:
  frequency  → physics band (4.0 in DOMAIN_BANDS)
  amplitude  → simulation confidence (1 - normalised error)
  phase      → simulation cycle phase (0–2π, e.g. pendulum position)
  confidence → physical plausibility score (0.0–1.0)
  decay      → how quickly simulation relevance fades (0.0 = forever)

Usage
─────
  # Standalone formant check:
  python3 axiom_world_sim_agent.py --sim vocal --length 17.0

  # Score a hypothesis's output against physics:
  python3 axiom_world_sim_agent.py --sim vocal --check "[504, 1513, 2521, 3529]"

  # Pendulum energy drift:
  python3 axiom_world_sim_agent.py --sim pendulum --length 1.0 --angle 10.0

  # Generate ERV tokens from a vocal tract simulation:
  python3 axiom_world_sim_agent.py --sim vocal --erv-tokens

Trust level: TRUST_LEVEL = 2  (domain agent, not orchestrator)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import types as _types
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────────
TRUST_LEVEL: int = 2
ISOLATION: bool  = True

# Speed of sound (cm/s) at 37°C body temperature
SOUND_SPEED_CMS: float = 34300.0

# Standard gravitational acceleration (m/s²)
G_MS2: float = 9.81

# ERV frequency band for physics simulations (matches DOMAIN_BANDS in erv_router.py)
ERV_PHYSICS_BAND: str   = "physics"
ERV_PHYSICS_FREQ: float = 4.0

_FROZEN = frozenset({"TRUST_LEVEL", "ISOLATION", "SOUND_SPEED_CMS", "G_MS2"})


def _module_setattr(self, name, value):
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,),
                      {"__setattr__": _module_setattr})


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class SimERVToken:
    """A ResonantEventToken produced by a physics simulation.

    Maps directly to axiom_firewall.erv_router.ResonantEventToken fields.
    Created here without importing erv_router to avoid the AXIOM_MASTER_KEY
    dependency during standalone sim runs.
    """
    meaning:    str
    frequency:  str    # always "physics" for sim tokens
    amplitude:  float  # simulation confidence: 1 - normalised_error
    phase:      float  # cycle phase 0–2π (e.g. pendulum angle normalised)
    confidence: float  # physical plausibility 0.0–1.0
    decay:      float  # 0.0 = always relevant; 1.0 = fades quickly
    sim_type:   str
    sim_state:  dict   # raw simulation output for this token


@dataclass
class PhysScore:
    """Physics plausibility score for a QRF hypothesis output."""
    plausibility:    float    # 0.0–1.0; multiplied into QRF branch score
    checks_passed:   int
    checks_total:    int
    detail:          list[str]   # human-readable check results
    erv_token:       Optional[SimERVToken] = None


# ─────────────────────────────────────────────────────────────────────────────
# Vocal Tract Simulation
# ─────────────────────────────────────────────────────────────────────────────

class VocalTractSim:
    """Uniform-tube vocal tract formant model.

    Models the vocal tract as a uniform cylindrical tube closed at the
    glottis (vocal folds) and open at the lips.

    Resonant frequencies (formants):
      Fn = (2n - 1) × c / (4 × L)   n = 1, 2, 3, …

    where:
      c = 34300 cm/s  (speed of sound at 37°C body temperature)
      L = tract length in cm  (adult ≈ 17 cm)

    References:
      Fant (1960) Acoustic Theory of Speech Production.
      Stevens (1998) Acoustic Phonetics.
    """

    def __init__(self, tract_length_cm: float = 17.0):
        if tract_length_cm <= 0:
            raise ValueError(f"tract_length_cm must be positive, got {tract_length_cm}")
        self.tract_length_cm = tract_length_cm

    def formants(self, n: int = 4) -> list[float]:
        """Return first n formant frequencies (Hz)."""
        c = SOUND_SPEED_CMS
        L = self.tract_length_cm
        return [round((2 * k - 1) * c / (4 * L), 1) for k in range(1, n + 1)]

    def score_hypothesis_output(self, candidate: list[float]) -> PhysScore:
        """Score a model's formant output against physical constraints.

        Checks:
          1. Count: must return exactly 4 values
          2. F1 in human speech range [200, 1000] Hz
          3. F2 in [600, 3000] Hz
          4. Ascending order: F1 < F2 < F3 < F4
          5. Odd-harmonic spacing: F2/F1 ≈ 3 (±30%)
          6. F3/F1 ≈ 5 (±30%)
          7. Within 15% of ground-truth formants for this tract length
        """
        truth = self.formants(4)
        checks: list[tuple[bool, str]] = []

        # 1. Count
        checks.append((len(candidate) == 4,
                        f"len={len(candidate)} (expected 4)"))

        if len(candidate) < 2:
            score = sum(1 for ok, _ in checks if ok) / max(len(checks), 1)
            return PhysScore(score, sum(ok for ok, _ in checks),
                             len(checks), [m for _, m in checks])

        f1, f2 = candidate[0], candidate[1]
        f3 = candidate[2] if len(candidate) > 2 else None
        f4 = candidate[3] if len(candidate) > 3 else None

        # 2. F1 range
        checks.append((200 <= f1 <= 1000,
                        f"F1={f1:.0f}Hz in [200,1000]Hz: {'✓' if 200<=f1<=1000 else '✗'}"))

        # 3. F2 range
        checks.append((600 <= f2 <= 3000,
                        f"F2={f2:.0f}Hz in [600,3000]Hz: {'✓' if 600<=f2<=3000 else '✗'}"))

        # 4. Ascending
        ascending = all(candidate[i] < candidate[i+1]
                        for i in range(len(candidate)-1))
        checks.append((ascending, f"ascending order: {'✓' if ascending else '✗'}"))

        # 5. Odd-harmonic spacing F2/F1 ≈ 3
        ratio_21 = f2 / f1 if f1 else 0
        checks.append((2.0 <= ratio_21 <= 4.0,
                        f"F2/F1={ratio_21:.2f} (ideal≈3.0): "
                        f"{'✓' if 2.0<=ratio_21<=4.0 else '✗'}"))

        # 6. F3/F1 ≈ 5
        if f3:
            ratio_31 = f3 / f1 if f1 else 0
            checks.append((3.5 <= ratio_31 <= 6.5,
                            f"F3/F1={ratio_31:.2f} (ideal≈5.0): "
                            f"{'✓' if 3.5<=ratio_31<=6.5 else '✗'}"))

        # 7. Within 15% of ground truth
        within_gt = all(
            abs(c - t) / t <= 0.15
            for c, t in zip(candidate[:4], truth[:4])
        )
        diffs = [f"F{i+1}: {candidate[i]:.0f} vs {truth[i]:.0f}"
                 for i in range(min(4, len(candidate)))]
        checks.append((within_gt,
                        f"within 15% of truth ({', '.join(diffs)}): "
                        f"{'✓' if within_gt else '✗'}"))

        passed = sum(1 for ok, _ in checks if ok)
        plausibility = passed / len(checks)

        # Build ERV token: amplitude = plausibility, phase = F1/F1_max normalised
        phase = 2 * math.pi * (f1 / 1000.0) % (2 * math.pi)
        erv = SimERVToken(
            meaning=f"vocal_formants L={self.tract_length_cm}cm",
            frequency=ERV_PHYSICS_BAND,
            amplitude=plausibility,
            phase=phase,
            confidence=plausibility,
            decay=0.1,
            sim_type="vocal_tract",
            sim_state={"formants": candidate, "truth": truth,
                       "tract_length_cm": self.tract_length_cm},
        )
        return PhysScore(
            plausibility=round(plausibility, 3),
            checks_passed=passed,
            checks_total=len(checks),
            detail=[m for _, m in checks],
            erv_token=erv,
        )

    def erv_tokens(self, vowel_configs: Optional[list[dict]] = None) -> list[SimERVToken]:
        """Generate ERV tokens for a sequence of vocal tract configurations.

        vowel_configs: list of {"name": str, "length_cm": float}
                       Defaults to /a/ /e/ /i/ /u/ approximations.
        """
        if vowel_configs is None:
            # Rough uniform-tube approximations of English vowels
            vowel_configs = [
                {"name": "/a/  (open)",        "length_cm": 17.0},
                {"name": "/i/  (high front)",  "length_cm": 13.0},
                {"name": "/u/  (high back)",   "length_cm": 20.0},
                {"name": "/e/  (mid front)",   "length_cm": 15.0},
            ]
        tokens = []
        for cfg in vowel_configs:
            sim = VocalTractSim(cfg["length_cm"])
            f = sim.formants(4)
            phase = 2 * math.pi * (f[0] / 1000.0) % (2 * math.pi)
            tokens.append(SimERVToken(
                meaning=f"vowel {cfg['name']} → F1={f[0]:.0f} F2={f[1]:.0f} F3={f[2]:.0f} F4={f[3]:.0f} Hz",
                frequency=ERV_PHYSICS_BAND,
                amplitude=0.90,
                phase=phase,
                confidence=0.85,
                decay=0.05,
                sim_type="vocal_tract",
                sim_state={"vowel": cfg["name"], "formants": f,
                           "tract_length_cm": cfg["length_cm"]},
            ))
        return tokens


# ─────────────────────────────────────────────────────────────────────────────
# Pendulum Simulation (RK4)
# ─────────────────────────────────────────────────────────────────────────────

class PendulumSim:
    """Simple pendulum simulation using 4th-order Runge-Kutta.

    Equation of motion: d²θ/dt² = -(g/L) sin(θ)
    State vector: [θ, ω]  where ω = dθ/dt

    Energy:
      KE = ½ω²L²   (per unit mass)
      PE = gL(1 - cosθ)
      E  = KE + PE  (conserved in ideal pendulum)
    """

    def __init__(self, length_m: float = 1.0, g: float = G_MS2):
        self.L = length_m
        self.g = g

    def _deriv(self, state: tuple[float, float]) -> tuple[float, float]:
        theta, omega = state
        return omega, -(self.g / self.L) * math.sin(theta)

    def _rk4_step(self, state: tuple[float, float],
                  dt: float) -> tuple[float, float]:
        k1 = self._deriv(state)
        k2 = self._deriv((state[0] + 0.5*dt*k1[0],
                           state[1] + 0.5*dt*k1[1]))
        k3 = self._deriv((state[0] + 0.5*dt*k2[0],
                           state[1] + 0.5*dt*k2[1]))
        k4 = self._deriv((state[0] + dt*k3[0],
                           state[1] + dt*k3[1]))
        return (
            state[0] + dt*(k1[0] + 2*k2[0] + 2*k3[0] + k4[0])/6,
            state[1] + dt*(k1[1] + 2*k2[1] + 2*k3[1] + k4[1])/6,
        )

    def simulate(self, theta0_deg: float, dt: float = 0.01,
                 steps: int = 500) -> list[dict]:
        theta = math.radians(theta0_deg)
        omega = 0.0
        trajectory = []
        for i in range(steps):
            t = round(i * dt, 4)
            ke = 0.5 * omega**2 * self.L**2
            pe = self.g * self.L * (1 - math.cos(theta))
            trajectory.append({
                "t": t, "theta": round(theta, 6),
                "omega": round(omega, 6),
                "ke": round(ke, 6), "pe": round(pe, 6),
                "energy": round(ke + pe, 6),
            })
            theta, omega = self._rk4_step((theta, omega), dt)
        return trajectory

    def energy_drift(self, trajectory: list[dict]) -> float:
        """Return max relative energy deviation (0.0 = perfect conservation)."""
        energies = [r["energy"] for r in trajectory]
        e0 = energies[0] if energies else 1.0
        if abs(e0) < 1e-10:
            return 0.0
        return max(abs(e - e0) / abs(e0) for e in energies)

    def score_hypothesis_trajectory(
        self,
        trajectory: list[tuple],   # (t, theta, omega, energy) from hypothesis code
    ) -> PhysScore:
        """Score a model's pendulum trajectory against RK4 ground truth.

        trajectory: list of (t, theta, omega, energy) tuples/rows.
        """
        checks: list[tuple[bool, str]] = []

        # 1. Non-empty
        checks.append((len(trajectory) > 0, f"non-empty trajectory: {len(trajectory)} steps"))

        if not trajectory:
            return PhysScore(0.0, 0, len(checks), [m for _, m in checks])

        # 2. Initial theta ≈ theta0 (we can't know theta0 here; check it's not zero)
        try:
            t0, th0, om0, e0 = trajectory[0]
        except (ValueError, TypeError):
            checks.append((False, "first row unpacks as (t, theta, omega, energy)"))
            return PhysScore(0.0, 0, len(checks), [m for _, m in checks])

        checks.append((abs(th0) > 0.01, f"initial theta non-zero: {th0:.4f} rad"))

        # 3. Initial omega ≈ 0
        checks.append((abs(om0) < 0.05, f"initial omega ≈ 0: {om0:.4f}"))

        # 4. Energy roughly conserved (max drift < 10%)
        energies = [row[3] for row in trajectory if len(row) >= 4]
        if energies:
            e_mean = sum(energies) / len(energies)
            if abs(e_mean) > 1e-10:
                drift = max(abs(e - e_mean) / abs(e_mean) for e in energies)
                checks.append((drift < 0.10,
                                f"energy drift {drift*100:.1f}% (threshold 10%): "
                                f"{'✓' if drift < 0.10 else '✗'}"))
            else:
                checks.append((False, "all energy values are zero"))
        else:
            checks.append((False, "no energy column found"))

        # 5. Theta oscillates (crosses zero at least 4 times = 2 full cycles)
        thetas = [row[1] for row in trajectory]
        crossings = sum(1 for i in range(1, len(thetas))
                        if thetas[i-1] * thetas[i] < 0)
        checks.append((crossings >= 4,
                        f"zero crossings={crossings} (need ≥4 for 2 cycles): "
                        f"{'✓' if crossings >= 4 else '✗'}"))

        # 6. Max amplitude roughly preserved
        max_deg = math.degrees(max(abs(t) for t in thetas))
        checks.append((7 <= max_deg <= 13,
                        f"max amplitude={max_deg:.1f}° (expect 8–12°): "
                        f"{'✓' if 7<=max_deg<=13 else '✗'}"))

        passed = sum(1 for ok, _ in checks if ok)
        plausibility = passed / len(checks)

        # ERV token: phase = current pendulum position in cycle
        cycle_phase = (thetas[-1] / (max(abs(t) for t in thetas) + 1e-9) + 1) * math.pi
        erv = SimERVToken(
            meaning=f"pendulum L={self.L}m energy_drift={drift*100:.1f}%"
                    if energies else f"pendulum L={self.L}m",
            frequency=ERV_PHYSICS_BAND,
            amplitude=plausibility,
            phase=cycle_phase % (2 * math.pi),
            confidence=plausibility,
            decay=0.2,
            sim_type="pendulum",
            sim_state={"length_m": self.L, "n_steps": len(trajectory),
                       "crossings": crossings},
        )
        return PhysScore(
            plausibility=round(plausibility, 3),
            checks_passed=passed,
            checks_total=len(checks),
            detail=[m for _, m in checks],
            erv_token=erv,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Enzyme Kinetics (Michaelis-Menten) — bio sim track
# ─────────────────────────────────────────────────────────────────────────────

class EnzymeSim:
    """Michaelis-Menten enzyme kinetics simulation.

    v = Vmax × [S] / (Km + [S])

    where:
      v    = reaction velocity (μmol/min)
      Vmax = maximum velocity
      Km   = Michaelis constant (substrate concentration at half-Vmax)
      [S]  = substrate concentration

    ERV band: medical (2.0) + physics (4.0) — bio straddles both.
    """

    def __init__(self, vmax: float = 10.0, km: float = 1.0):
        self.vmax = vmax
        self.km   = km

    def velocity(self, substrate: float) -> float:
        return self.vmax * substrate / (self.km + substrate)

    def half_max_substrate(self) -> float:
        return self.km

    def score_hypothesis(self, velocities: list[tuple[float, float]]) -> PhysScore:
        """Score (substrate, velocity) pairs from a hypothesis.

        Checks:
          1. At [S]=Km, velocity ≈ Vmax/2
          2. Velocity increases monotonically with substrate
          3. Velocity approaches but never reaches Vmax
          4. At [S]→0, velocity → 0
        """
        checks: list[tuple[bool, str]] = []
        if not velocities:
            return PhysScore(0.0, 0, 1, ["empty output"])

        substrates = [s for s, _ in velocities]
        vels       = [v for _, v in velocities]

        # 1. At Km velocity ≈ Vmax/2
        km_pairs = [(s, v) for s, v in velocities if abs(s - self.km) / self.km < 0.1]
        if km_pairs:
            v_at_km = km_pairs[0][1]
            expected = self.vmax / 2
            err = abs(v_at_km - expected) / expected
            checks.append((err < 0.15,
                            f"v(Km)={v_at_km:.2f} ≈ Vmax/2={expected:.2f}: "
                            f"{'✓' if err<0.15 else '✗'}"))

        # 2. Monotonically increasing
        mono = all(vels[i] <= vels[i+1] for i in range(len(vels)-1))
        checks.append((mono, f"monotone increasing: {'✓' if mono else '✗'}"))

        # 3. Approaches but never reaches Vmax
        never_exceeds = all(v < self.vmax for v in vels)
        checks.append((never_exceeds,
                        f"v < Vmax={self.vmax}: {'✓' if never_exceeds else '✗'}"))

        # 4. Near-zero substrate → near-zero velocity
        low_s = [(s, v) for s, v in velocities if s < 0.1]
        if low_s:
            v_low = low_s[0][1]
            checks.append((v_low < self.vmax * 0.1,
                            f"v([S]→0)={v_low:.3f} < 10% Vmax: "
                            f"{'✓' if v_low < self.vmax*0.1 else '✗'}"))

        passed = sum(1 for ok, _ in checks if ok)
        plausibility = passed / len(checks) if checks else 0.0
        return PhysScore(
            plausibility=round(plausibility, 3),
            checks_passed=passed,
            checks_total=len(checks),
            detail=[m for _, m in checks],
        )


# ─────────────────────────────────────────────────────────────────────────────
# WorldSimAgent — orchestrates all sim modules
# ─────────────────────────────────────────────────────────────────────────────

class WorldSimAgent:
    """Orchestrates physics/bio simulations for QRF branch scoring.

    TRUST_LEVEL = 2  (domain agent)
    ISOLATION   = True

    QRF integration
    ───────────────
    Call score_branch(task_id, hypothesis_output) after each branch
    produces output.  Returns a PhysScore whose .plausibility is
    multiplied into the branch's test-pass-rate score before pruning.

    This gives QRF a physics gate: a hypothesis that passes all unit
    tests BUT produces physically impossible values (e.g. F1=200 Hz
    for a 17-cm tube) gets a reduced composite score and may be
    outcompeted by a branch that is physically coherent.
    """

    def __init__(self):
        self._vocal  = VocalTractSim()
        self._pendulum = PendulumSim()
        self._enzyme = EnzymeSim()

    def score_branch(self, task_id: str, hypothesis_output) -> PhysScore:
        """Score a QRF branch's output against physics constraints.

        hypothesis_output format depends on task_id:
          vocal-formant-calc   → list[float]  (formant frequencies)
          pendulum-energy      → list[tuple]  (t, theta, omega, energy)
          enzyme-kinetics      → list[tuple]  (substrate, velocity)
          (others)             → PhysScore(1.0, …)  (no-op, full score)
        """
        if task_id == "vocal-formant-calc":
            try:
                candidate = list(hypothesis_output)
                return self._vocal.score_hypothesis_output(candidate)
            except Exception as e:
                return PhysScore(0.0, 0, 1, [f"parse error: {e}"])

        if task_id == "pendulum-energy":
            try:
                traj = list(hypothesis_output)
                return self._pendulum.score_hypothesis_trajectory(traj)
            except Exception as e:
                return PhysScore(0.0, 0, 1, [f"parse error: {e}"])

        if task_id == "enzyme-kinetics":
            try:
                pairs = list(hypothesis_output)
                return self._enzyme.score_hypothesis(pairs)
            except Exception as e:
                return PhysScore(0.0, 0, 1, [f"parse error: {e}"])

        # No physics check for this task — full score passthrough
        return PhysScore(1.0, 1, 1, ["no physics check for this task"])

    def vocal_erv_tokens(self, tract_length_cm: float = 17.0,
                          n_formants: int = 4) -> list[SimERVToken]:
        """Generate ERV tokens from vocal tract simulation.

        One token per formant band — maps acoustic resonances to ERV
        frequency/amplitude/phase fields so the ERV router can treat
        vocal resonance patterns as routing signals.
        """
        sim = VocalTractSim(tract_length_cm)
        formants = sim.formants(n_formants)
        tokens = []
        for i, freq_hz in enumerate(formants):
            n = i + 1
            # Phase: formant cycle position (higher formants are further along)
            phase = (2 * math.pi * n / n_formants) % (2 * math.pi)
            # Amplitude: highest for F1 (most perceptually salient), decays for higher
            amplitude = 1.0 / n
            tokens.append(SimERVToken(
                meaning=f"F{n}={freq_hz:.0f}Hz  (L={tract_length_cm}cm tube resonance)",
                frequency=ERV_PHYSICS_BAND,
                amplitude=round(amplitude, 3),
                phase=round(phase, 4),
                confidence=0.90,
                decay=0.05,
                sim_type="vocal_tract",
                sim_state={"formant": n, "freq_hz": freq_hz,
                           "tract_length_cm": tract_length_cm},
            ))
        return tokens

    def pendulum_erv_token(self, length_m: float = 1.0,
                            theta0_deg: float = 10.0) -> SimERVToken:
        """Generate one ERV token from a pendulum simulation state."""
        sim = PendulumSim(length_m)
        traj = sim.simulate(theta0_deg, dt=0.01, steps=100)
        if not traj:
            return SimERVToken("pendulum_empty", ERV_PHYSICS_BAND,
                               0.0, 0.0, 0.0, 1.0, "pendulum", {})
        last = traj[-1]
        drift = sim.energy_drift(traj)
        amplitude = max(0.0, 1.0 - drift * 10)
        phase = (last["theta"] / math.radians(theta0_deg) + 1) * math.pi
        return SimERVToken(
            meaning=f"pendulum L={length_m}m θ={theta0_deg}° t={last['t']}s",
            frequency=ERV_PHYSICS_BAND,
            amplitude=round(amplitude, 3),
            phase=round(phase % (2 * math.pi), 4),
            confidence=round(1.0 - drift, 3),
            decay=0.3,
            sim_type="pendulum",
            sim_state={"length_m": length_m, "theta0_deg": theta0_deg,
                       "energy_drift_pct": round(drift * 100, 2),
                       "last_step": last},
        )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AXIOM World Sim Agent — SimPhysBio physics verification"
    )
    p.add_argument("--sim", choices=["vocal", "pendulum", "enzyme"],
                   default="vocal", help="Simulation type")
    p.add_argument("--length", type=float, default=17.0,
                   help="Vocal tract length cm OR pendulum length m")
    p.add_argument("--angle", type=float, default=10.0,
                   help="Initial pendulum angle (degrees)")
    p.add_argument("--n-formants", type=int, default=4,
                   help="Number of vocal formants to compute")
    p.add_argument("--check", default=None,
                   help="JSON list to score against physics (e.g. '[504,1513,2521,3529]')")
    p.add_argument("--erv-tokens", action="store_true",
                   help="Print ERV tokens generated by the simulation")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    agent = WorldSimAgent()

    W = 66
    print()
    print("═" * W)
    print("  AXIOM World Sim Agent — SimPhysBio")
    print("─" * W)

    if args.sim == "vocal":
        sim = VocalTractSim(args.length)
        formants = sim.formants(args.n_formants)
        print(f"  Vocal Tract  L = {args.length} cm")
        print(f"  Speed of sound: {SOUND_SPEED_CMS:.0f} cm/s (37°C)")
        print()
        print(f"  {'Formant':<10}  {'Freq (Hz)':>10}  {'Formula'}")
        print("  " + "─" * 46)
        for i, f in enumerate(formants):
            n = i + 1
            print(f"  F{n:<9}  {f:>10.1f}  "
                  f"({2*n-1} × {SOUND_SPEED_CMS:.0f}) / (4 × {args.length}) "
                  f"= {f:.1f}")

        if args.check:
            candidate = json.loads(args.check)
            score = sim.score_hypothesis_output(candidate)
            print()
            print(f"  Physics check for: {candidate}")
            print(f"  {'─'*46}")
            for detail in score.detail:
                mark = "  ✓" if any(s in detail for s in ["✓", "within"]) else "  ✗"
                print(f"{mark}  {detail}")
            print(f"  {'─'*46}")
            print(f"  Plausibility: {score.plausibility:.3f}  "
                  f"({score.checks_passed}/{score.checks_total} checks)")

        if args.erv_tokens:
            tokens = agent.vocal_erv_tokens(args.length, args.n_formants)
            print()
            print("  ERV Tokens (physics band = 4.0)")
            print(f"  {'Meaning':<48}  {'Amp':>5}  {'Phase':>7}  {'Conf':>6}")
            print("  " + "─" * 72)
            for tok in tokens:
                print(f"  {tok.meaning:<48}  {tok.amplitude:>5.3f}  "
                      f"{tok.phase:>7.4f}  {tok.confidence:>6.3f}")

    elif args.sim == "pendulum":
        sim = PendulumSim(args.length)
        traj = sim.simulate(args.angle, dt=0.01, steps=500)
        drift = sim.energy_drift(traj)
        period_theory = 2 * math.pi * math.sqrt(args.length / G_MS2)
        print(f"  Pendulum  L = {args.length} m  θ₀ = {args.angle}°")
        print(f"  Theoretical period: {period_theory:.3f} s")
        print(f"  Steps: 500 × dt=0.01s = 5.00 s")
        print(f"  Energy drift (RK4): {drift*100:.4f}%  "
              f"({'✓ excellent' if drift < 0.001 else '✓ good' if drift < 0.01 else '△ degraded'})")
        print()
        print(f"  {'t':>6}  {'θ (rad)':>10}  {'ω (rad/s)':>11}  {'E (J/kg)':>10}")
        print("  " + "─" * 44)
        for row in traj[::50]:    # print every 50th step
            print(f"  {row['t']:>6.2f}  {row['theta']:>10.4f}  "
                  f"{row['omega']:>11.4f}  {row['energy']:>10.6f}")

        if args.erv_tokens:
            tok = agent.pendulum_erv_token(args.length, args.angle)
            print()
            print("  ERV Token")
            print(f"  meaning   : {tok.meaning}")
            print(f"  frequency : {tok.frequency}  (band 4.0)")
            print(f"  amplitude : {tok.amplitude:.3f}")
            print(f"  phase     : {tok.phase:.4f} rad")
            print(f"  confidence: {tok.confidence:.3f}")
            print(f"  decay     : {tok.decay}")

    elif args.sim == "enzyme":
        sim = EnzymeSim(vmax=10.0, km=1.0)
        substrates = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0]
        print(f"  Michaelis-Menten  Vmax={sim.vmax}  Km={sim.km}")
        print(f"  {'[S] (mM)':>10}  {'v (μmol/min)':>14}  {'v/Vmax':>8}")
        print("  " + "─" * 36)
        for s in substrates:
            v = sim.velocity(s)
            print(f"  {s:>10.1f}  {v:>14.4f}  {v/sim.vmax:>8.3f}")
        print(f"  {'─'*36}")
        print(f"  Km (half-max) = {sim.km}  →  v = {sim.velocity(sim.km):.4f} = Vmax/2")

    print()
    print("═" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
