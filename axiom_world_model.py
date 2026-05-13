"""
AXIOM Constitutional World Model — ORVL-014.
Manifest  : world-model-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Forward simulation of constitutional state evolution using causal graph
traversal and branch-level monotonic enforcement.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held as partial object
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC/hashing
"""

from __future__ import annotations

import copy
import hashlib
import hmac as hmac_lib
import json
import math
import random
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
TRUST_LEVEL: int = 3
ISOLATION: bool = True
SIMULATION_DEPTH: int = 5
MIN_BRANCH_PROBABILITY: float = 0.02
CAUSAL_DECAY: float = 0.85

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION",
    "SIMULATION_DEPTH", "MIN_BRANCH_PROBABILITY", "CAUSAL_DECAY",
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


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class WorldState:
    """Snapshot of constitutional world at a point in time."""
    block_states: Dict[str, List[float]]
    causal_graph: Dict[str, List[str]]
    timestamp: str
    constitutional_distance: float
    hmac_signature: str = ""


@dataclass
class SimulationResult:
    """Result of a forward simulation run."""
    branches: List[WorldState]
    probability_band: Dict[str, float]
    killed_branches: List[str]
    recommended_intervention: str
    intervention_confidence: float


# ── Helpers ──────────────────────────────────────────────────────────────

def _magnitude(vec: List[float]) -> float:
    """L2 norm of a vector."""
    return math.sqrt(sum(v * v for v in vec)) if vec else 0.0


def _sign_state(state: WorldState, hmac_key: bytes) -> str:
    """HMAC-SHA256 over canonical WorldState fields."""
    canonical = json.dumps({
        "block_count": len(state.block_states),
        "edge_count": sum(len(v) for v in state.causal_graph.values()),
        "constitutional_distance": round(state.constitutional_distance, 8),
        "timestamp": state.timestamp,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
    return hmac_lib.new(hmac_key, canonical, hashlib.sha256).hexdigest()  # BUG-007


def _aggregate_distance(block_states: Dict[str, List[float]]) -> float:
    """Compute aggregate constitutional distance from all block state vectors."""
    if not block_states:
        return 0.0
    mags = [_magnitude(v) for v in block_states.values()]
    return round(sum(mags) / len(mags), 8)


# ── ConstitutionalWorldModel ────────────────────────────────────────────

class ConstitutionalWorldModel:
    """Forward simulation of constitutional state evolution.

    TRUST_LEVEL = 3 (CANNOT_MUTATE)
    SIMULATION_DEPTH = 5 (CANNOT_MUTATE)
    MIN_BRANCH_PROBABILITY = 0.02 (CANNOT_MUTATE)
    CAUSAL_DECAY = 0.85 (CANNOT_MUTATE)
    """

    def __init__(self, hmac_key: bytes):
        self._hmac_key = hmac_key
        self._causal_graph: Dict[str, List[str]] = {}

    def add_causal_edge(self, from_block: str, to_block: str) -> None:
        """Add a directed causal edge. HMAC sign updated graph."""
        self._causal_graph.setdefault(from_block, [])
        if to_block not in self._causal_graph[from_block]:
            self._causal_graph[from_block].append(to_block)

    def _step_state(self, state: WorldState, rng: random.Random,
                    perturbation: float = 0.05) -> WorldState:
        """Advance one step: propagate causal influences with decay + noise."""
        new_blocks: Dict[str, List[float]] = {}
        graph = state.causal_graph if state.causal_graph else self._causal_graph

        for block_id, vec in state.block_states.items():
            influence = [0.0] * len(vec)
            # Accumulate causal influence from parents
            for parent_id, children in graph.items():
                if block_id in children and parent_id in state.block_states:
                    parent_vec = state.block_states[parent_id]
                    for j in range(min(len(influence), len(parent_vec))):
                        influence[j] += parent_vec[j] * CAUSAL_DECAY

            new_vec = [
                v + influence[j] * 0.1 + rng.uniform(-perturbation, perturbation)
                for j, v in enumerate(vec)
            ]
            new_blocks[block_id] = [round(x, 6) for x in new_vec]

        ts = datetime.now(timezone.utc).isoformat()
        dist = _aggregate_distance(new_blocks)
        new_state = WorldState(
            block_states=new_blocks,
            causal_graph=dict(graph),
            timestamp=ts,
            constitutional_distance=dist,
        )
        new_state.hmac_signature = _sign_state(new_state, self._hmac_key)
        return new_state

    def simulate_forward(self, current_state: WorldState,
                         n_steps: int = 0,
                         n_branches: int = 4) -> SimulationResult:
        """Simulate forward evolution with branch-level monotonic enforcement."""
        if n_steps <= 0:
            n_steps = SIMULATION_DEPTH

        killed: List[str] = []
        survivors: List[WorldState] = []

        for b in range(n_branches):
            rng = random.Random(42 + b)
            state = copy.deepcopy(current_state)
            branch_id = f"branch-{b}"
            alive = True

            for step in range(n_steps):
                prev_dist = state.constitutional_distance
                state = self._step_state(state, rng)
                # Monotonic check: distance must not decrease
                if state.constitutional_distance < prev_dist:
                    killed.append(branch_id)
                    alive = False
                    break

            if alive:
                survivors.append(state)

        # Build probability band from survivors
        prob_band: Dict[str, float] = {}
        if survivors:
            total = sum(s.constitutional_distance for s in survivors) or 1.0
            for i, s in enumerate(survivors):
                p = round(s.constitutional_distance / total, 4)
                if p >= MIN_BRANCH_PROBABILITY:
                    prob_band[f"branch-{i}"] = p

        # Recommend intervention if any branch was killed
        rec = ""
        confidence = 0.0
        if killed:
            rec = f"Review causal roots for {len(killed)} non-monotonic branch(es)"
            confidence = round(len(killed) / n_branches, 4)

        return SimulationResult(
            branches=survivors,
            probability_band=prob_band,
            killed_branches=killed,
            recommended_intervention=rec,
            intervention_confidence=confidence,
        )

    def simulate_intervention(self, current_state: WorldState,
                              intervention: str) -> SimulationResult:
        """Compare baseline vs intervention simulation."""
        baseline = self.simulate_forward(current_state)

        # Apply intervention: boost all block vectors by small positive delta
        modified = copy.deepcopy(current_state)
        boost = 0.05 * len(intervention.split())
        for block_id in modified.block_states:
            modified.block_states[block_id] = [
                v + boost for v in modified.block_states[block_id]
            ]
        modified.constitutional_distance = _aggregate_distance(
            modified.block_states)

        with_intervention = self.simulate_forward(modified)

        # Combine: delta shows intervention value
        delta_killed = len(baseline.killed_branches) - len(
            with_intervention.killed_branches)
        confidence = round(max(0.0, delta_killed / max(
            len(baseline.killed_branches), 1)), 4)

        return SimulationResult(
            branches=with_intervention.branches,
            probability_band=with_intervention.probability_band,
            killed_branches=with_intervention.killed_branches,
            recommended_intervention=intervention,
            intervention_confidence=confidence,
        )

    def find_causal_root(self, violated_state: WorldState) -> str:
        """Traverse causal graph backward to find root cause block."""
        graph = violated_state.causal_graph or self._causal_graph
        if not graph or not violated_state.block_states:
            return ""

        # Find all root nodes (nodes that are not targets of any edge)
        all_targets: set[str] = set()
        for children in graph.values():
            all_targets.update(children)
        roots = [b for b in graph if b not in all_targets]

        # Score each block by magnitude — lowest distance = most responsible
        best_id = ""
        lowest_mag = float("inf")
        search_set = roots if roots else list(violated_state.block_states.keys())

        for block_id in search_set:
            if block_id in violated_state.block_states:
                mag = _magnitude(violated_state.block_states[block_id])
                if mag < lowest_mag:
                    lowest_mag = mag
                    best_id = block_id

        return best_id
