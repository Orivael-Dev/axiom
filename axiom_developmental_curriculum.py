#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Developmental Curriculum — the mom layer that bridges
ORVL-022 (CPI / toddler reflex) with ORVL-023 (AXM / memory).

The dad (`SupervisoryGuard` from axiom_cpi) watches *this* attempt
and decides whether to let it through. The mom does four things he
doesn't:

  1. PERSISTENCE — competence carries across sessions. The dad starts
     every boot at zero; the mom remembers yesterday's track record.
  2. CURRICULUM — picks what to try next. Zone-of-proximal-development:
     the task whose forecast currently SOFTENs but doesn't VETO.
  3. TRANSFER — competence on one vertex class weakly seeds related
     classes via similarity derived from AXM VectorVertexEntry
     bag-of-words cosine. CYLINDRICAL trust seeds PROTRUSION at the
     similarity ratio; never decreases the target's existing score.
  4. PLATEAU DETECTION — flags the class that's been static the
     longest so the curriculum can rotate to it.

Architectural notes:

  - The AXM container is consulted READ-ONLY for the similarity graph.
    Its proof ledger is not modified.
  - Persistence lives in a SEPARATE signed sidecar JSON next to the
    .axm (default: `<axm_path>.curriculum.json`). The sidecar is
    HMAC-signed under `derive_key(b"axiom-curriculum-v1")`. Tampering
    is refused at boot time.
  - The mom never overrides the dad's CANNOT_MUTATE constants
    (STABILITY_FLOOR, TORQUE_LIMIT_*). She only nudges competence —
    which is itself a tunable, not a constitutional invariant.

Spec : axiom_files/core/axiom_developmental_curriculum.axiom
Trust: TRUST_LEVEL = 3 (one below the toddler reflex — the kid's
       physical reaction outranks the mom's lesson plan).
HMAC : SHA-256 over canonical JSON, hex digest.
BUG-003: UTF-8 output encoding.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import math
import os
import sys
import types as _types
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ──────────────────────────────────────────────
TRUST_LEVEL: int = 3
SIMILARITY_FLOOR: float = 0.30       # below this, transfer is a no-op
TRANSFER_CAP_PER_CALL: float = 0.40  # max competence raise per transfer
PERSISTENCE_FORMAT_VERSION: str = "0.1"
PLATEAU_WINDOW_DEFAULT: int = 50     # ticks of static competence → plateau
MIN_TICKS_FOR_CONSOLIDATION: int = 20  # gains need this many ticks to persist

_FROZEN = frozenset({
    "TRUST_LEVEL", "SIMILARITY_FLOOR", "TRANSFER_CAP_PER_CALL",
    "PERSISTENCE_FORMAT_VERSION",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(
            f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,),
                      {"__setattr__": _module_setattr})


# ── Exceptions ────────────────────────────────────────────────────────────
class CurriculumError(Exception):
    """Base for DevelopmentalCurriculum errors."""


class TransferCapExceeded(CurriculumError):
    """Single transfer would raise competence past TRANSFER_CAP_PER_CALL."""


class PersistenceTampered(CurriculumError):
    """Persisted state's signature does not verify."""


# ── AXM-derived semantic → CPI vertex class mapping ──────────────────────
#
# AXM vertices carry strings like "Glass"/"Box"/"Sphere"; CPI tracks
# competence per CPI vertex class (CYLINDRICAL/PLANAR/PROTRUSION/
# FRAGILE/DEFORMABLE). The mapping projects AXM data onto CPI's
# coordinate system. Unknown semantic classes fall through silently
# and contribute nothing to the similarity graph.
SEMANTIC_TO_CPI_VERTEX: Mapping[str, str] = {
    "glass":   "FRAGILE",
    "bulb":    "FRAGILE",
    "rim":     "FRAGILE",
    "box":     "PLANAR",
    "door":    "PLANAR",
    "panel":   "PLANAR",
    "sphere":  "CYLINDRICAL",
    "cup":     "CYLINDRICAL",
    "mug":     "CYLINDRICAL",
    "bottle":  "CYLINDRICAL",
    "handle":  "PROTRUSION",
    "knob":    "PROTRUSION",
    "lever":   "PROTRUSION",
    "pillow":  "DEFORMABLE",
    "sponge":  "DEFORMABLE",
    "fabric":  "DEFORMABLE",
}


# ── Frozen dataclasses ────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProposedTask:
    """The curriculum's pick for what to try next. Rationale explains
    why this task is in the zone of proximal development for the
    current competence state."""
    vertex_class:     str
    target_force_nm:  float
    current_competence: float
    rationale:        str
    signature:        str = ""


@dataclass(frozen=True)
class TransferEvent:
    """Audit record of a competence transfer between vertex classes."""
    src_class:    str
    dst_class:    str
    similarity:   float
    old_dst:      float
    new_dst:      float
    raise_delta:  float
    signature:    str = ""


# ── Signing helpers ───────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac_lib.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _curriculum_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-curriculum-v1")


# ── Similarity graph from AXM vertices (cosine over bag-of-words) ────────
def _tokens(cluster: str) -> List[str]:
    """Tokenise a vertex_cluster string. 'Cylindrical_Thin' → [cylindrical, thin]."""
    return [t for t in cluster.lower().replace('-', '_').split('_') if t]


def _semantic_to_cpi(semantic_class: str) -> Optional[str]:
    """Project an AXM semantic class onto a CPI vertex class.
    Returns None for unknown — the entry just doesn't contribute."""
    return SEMANTIC_TO_CPI_VERTEX.get(semantic_class.lower())


def _cosine_bow(a: Mapping[str, int], b: Mapping[str, int]) -> float:
    """Cosine similarity over bag-of-words frequency dicts."""
    if not a or not b:
        return 0.0
    dot = sum(a[t] * b.get(t, 0) for t in a)
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


# ── DevelopmentalCurriculum — the mom layer ──────────────────────────────
class DevelopmentalCurriculum:
    """Bridges a CPI `SupervisoryGuard` (per-session competence) with an
    AXM container (read-only similarity source) and a signed sidecar
    JSON file (cross-session persistence)."""

    def __init__(self, supervisor: Any,
                 axm_container: Any = None,
                 persistence_path: Optional[str] = None):
        self.supervisor = supervisor
        self.axm = axm_container
        self.persistence_path = (
            Path(persistence_path) if persistence_path else None
        )
        self._similarity_graph: Dict[frozenset, float] = {}
        self._history: Dict[str, Deque[float]] = {}
        self._consolidation_count = 0
        self._loaded_from_disk = False
        self._transfers: List[TransferEvent] = []

        if self.axm is not None:
            self._build_similarity_graph()
        if self.persistence_path is not None and self.persistence_path.exists():
            self._load_persisted_state()

    # ── Similarity graph ─────────────────────────────────────────────
    def _build_similarity_graph(self) -> None:
        """Collect AXM vertices into per-CPI-class bag-of-words, then
        compute pairwise cosine similarity. Self-similarity (a, a) is
        always 1.0; missing pairs default to 0.0 (no transfer)."""
        per_class: Dict[str, Dict[str, int]] = {}
        for v in self.axm.vertices:
            cpi_class = _semantic_to_cpi(v.semantic_class)
            if cpi_class is None:
                continue
            bag = per_class.setdefault(cpi_class, {})
            for tok in _tokens(v.vertex_cluster):
                bag[tok] = bag.get(tok, 0) + 1
        classes = list(per_class.keys())
        for i, ca in enumerate(classes):
            for cb in classes[i + 1:]:
                sim = _cosine_bow(per_class[ca], per_class[cb])
                self._similarity_graph[frozenset((ca, cb))] = sim

    def similarity(self, class_a: str, class_b: str) -> float:
        if class_a == class_b:
            return 1.0
        return self._similarity_graph.get(frozenset((class_a, class_b)), 0.0)

    # ── Transfer — seed dst's competence from src's, scaled by sim ──
    def transfer(self, src_class: str, dst_class: str,
                 *, force_similarity: Optional[float] = None
                 ) -> TransferEvent:
        """Raise dst's competence to (src × similarity), but only if
        that's higher than dst's current score and only by no more
        than TRANSFER_CAP_PER_CALL. Self-transfer is a no-op.

        Raises TransferCapExceeded if the requested similarity would
        push the raise past the cap (a force_similarity > 1.0 caller
        bug, or a future similarity model that's too aggressive)."""
        sim = (force_similarity if force_similarity is not None
                else self.similarity(src_class, dst_class))
        old_dst = self.supervisor.competence.get(dst_class)
        src_comp = self.supervisor.competence.get(src_class)
        new_dst = old_dst
        raise_delta = 0.0

        if sim >= SIMILARITY_FLOOR and src_class != dst_class:
            seeded = min(1.0, src_comp * sim)
            if seeded > old_dst:
                raise_delta = seeded - old_dst
                if raise_delta > TRANSFER_CAP_PER_CALL:
                    raise TransferCapExceeded(
                        f"transfer {src_class}→{dst_class} would raise "
                        f"competence by {raise_delta:.3f} > cap "
                        f"{TRANSFER_CAP_PER_CALL:.3f}"
                    )
                self.supervisor.competence.set(dst_class, seeded)
                new_dst = seeded

        payload = {
            "src_class":   src_class,
            "dst_class":   dst_class,
            "similarity":  round(sim, 4),
            "old_dst":     round(old_dst, 4),
            "new_dst":     round(new_dst, 4),
            "raise_delta": round(raise_delta, 4),
        }
        sig = _sign(_curriculum_key(), payload)
        event = TransferEvent(**payload, signature=sig)
        self._transfers.append(event)
        return event

    # ── Curriculum — zone of proximal development ────────────────────
    def suggest_next_task(self) -> ProposedTask:
        """Pick the CPI vertex class whose competence is most ripe for
        learning: not so high that the task is trivial (PASS would
        always fire), not so low that the parent would just VETO.
        Heuristic: target the class with competence in [0.10, 0.85]
        that's been static the longest; fall back to the lowest
        competence class if all are saturated."""
        scores = dict(self.supervisor.competence.snapshot().scores)
        # Skip the GENERAL bucket — it's the tick-without-context floor.
        scores.pop("GENERAL", None)
        ripe = [(c, s) for c, s in scores.items() if 0.10 <= s <= 0.85]

        if ripe:
            # Most stuck = least recent change in the history window.
            def staleness(class_name: str) -> int:
                h = self._history.get(class_name)
                if not h or len(h) < 2:
                    return 0
                changes = sum(1 for i in range(1, len(h)) if h[i] != h[i - 1])
                return len(h) - changes  # more static = larger
            ripe.sort(key=lambda cs: (-staleness(cs[0]), cs[1]))
            cls, score = ripe[0]
            rationale = (f"competence {score:.2f} is in the soften-zone "
                         f"({0.10}-{0.85}); class is most static")
        else:
            cls, score = min(scores.items(), key=lambda cs: cs[1])
            rationale = (f"all classes are saturated or fresh; pick "
                         f"the lowest ({score:.2f}) to make progress")

        # Target force scales with competence: at low trust, aim at
        # half the class's torque ceiling so the dad SOFTENs, not VETOs.
        ceiling = _TORQUE_CEILING_FOR.get(cls, 1.0)
        target = ceiling * (0.30 + 0.40 * score)
        payload = {
            "vertex_class":       cls,
            "target_force_nm":    round(target, 4),
            "current_competence": round(score, 4),
            "rationale":          rationale,
        }
        sig = _sign(_curriculum_key(), payload)
        return ProposedTask(**payload, signature=sig)

    # ── Plateau detection ────────────────────────────────────────────
    def observe(self) -> None:
        """Snapshot the current competence into per-class history. Call
        once per tick (or coarser cadence — say every 10 ticks) to
        avoid bloating memory. detect_plateau() reads from here."""
        for cls, score in self.supervisor.competence.snapshot().scores.items():
            self._history.setdefault(
                cls, deque(maxlen=PLATEAU_WINDOW_DEFAULT * 2)
            ).append(round(score, 6))

    def detect_plateau(self, window_n: int = PLATEAU_WINDOW_DEFAULT
                        ) -> Optional[str]:
        """Return the CPI class that's been static for ≥window_n
        observations, or None if no class qualifies. Useful signal
        for 'the kid is stuck — change the curriculum.'"""
        best: Optional[Tuple[str, int]] = None
        for cls, hist in self._history.items():
            if cls == "GENERAL" or len(hist) < window_n:
                continue
            tail = list(hist)[-window_n:]
            if all(v == tail[0] for v in tail):
                # Prefer the class that's been static longest (largest hist).
                if best is None or len(hist) > best[1]:
                    best = (cls, len(hist))
        return best[0] if best else None

    # ── Persistence (HMAC-signed sidecar JSON) ──────────────────────
    def consolidate(self, *, force: bool = False) -> bool:
        """Write current competence + history to the signed persistence
        file. Asymmetric: gains backed by < MIN_TICKS_FOR_CONSOLIDATION
        ticks are NOT persisted unless force=True — prevents single
        lucky runs from ratcheting trust permanently.

        Returns True if anything was actually persisted."""
        if self.persistence_path is None:
            return False
        snap = self.supervisor.competence.snapshot()
        if not force and snap.total_ticks < MIN_TICKS_FOR_CONSOLIDATION:
            return False
        payload = {
            "format_version":        PERSISTENCE_FORMAT_VERSION,
            "consolidation_count":   self._consolidation_count + 1,
            "competence":            dict(snap.scores),
            "total_ticks":           snap.total_ticks,
            "total_demotions":       snap.total_demotions,
            "timestamp":             datetime.now(timezone.utc).isoformat(),
        }
        payload["signature"] = _sign(_curriculum_key(), payload)
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self.persistence_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        self._consolidation_count += 1
        return True

    def _load_persisted_state(self) -> None:
        """Verify signature, then apply scores onto the supervisor.
        Raises PersistenceTampered if the file does not verify."""
        raw = self.persistence_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        sig = data.pop("signature", None)
        if sig is None:
            raise PersistenceTampered("no signature field")
        expected = _sign(_curriculum_key(), data)
        if not hmac_lib.compare_digest(sig, expected):
            raise PersistenceTampered("signature mismatch")
        if data.get("format_version") != PERSISTENCE_FORMAT_VERSION:
            raise PersistenceTampered(
                f"format_version {data.get('format_version')} != "
                f"{PERSISTENCE_FORMAT_VERSION}"
            )
        for cls, score in data.get("competence", {}).items():
            if 0.0 <= score <= 1.0:
                self.supervisor.competence.set(cls, score)
        self._consolidation_count = int(data.get("consolidation_count", 0))
        self._loaded_from_disk = True

    # ── Introspection ───────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "trust_level":         TRUST_LEVEL,
            "loaded_from_disk":    self._loaded_from_disk,
            "consolidation_count": self._consolidation_count,
            "similarity_pairs":    len(self._similarity_graph),
            "history_classes":     list(self._history.keys()),
            "transfers_recorded":  len(self._transfers),
        }


# ── Torque ceilings — direct import; no cycle (axiom_cpi does NOT import us) ─
from axiom_cpi import (
    TORQUE_LIMIT_FRAGILE, TORQUE_LIMIT_DEFORMABLE,
    TORQUE_LIMIT_CYLINDRICAL, TORQUE_LIMIT_PROTRUSION,
    TORQUE_LIMIT_PLANAR,
)

_TORQUE_CEILING_FOR: Mapping[str, float] = {
    "FRAGILE":     TORQUE_LIMIT_FRAGILE,
    "DEFORMABLE":  TORQUE_LIMIT_DEFORMABLE,
    "CYLINDRICAL": TORQUE_LIMIT_CYLINDRICAL,
    "PROTRUSION":  TORQUE_LIMIT_PROTRUSION,
    "PLANAR":      TORQUE_LIMIT_PLANAR,
}


# ── CLI ──────────────────────────────────────────────────────────────────
def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="axiom_developmental_curriculum",
        description="Mom layer — persist competence across sessions and "
                    "transfer trust via AXM-derived similarity.",
    )
    parser.add_argument("--axm", required=True,
                        help="Path to an AXM container directory")
    parser.add_argument("--persistence",
                        help="Path to the curriculum sidecar JSON "
                             "(default: <axm>.curriculum.json)")
    parser.add_argument("--action", choices=("inspect", "boot"),
                        default="inspect")
    args = parser.parse_args(argv)

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("AXIOM_MASTER_KEY not set", file=sys.stderr)
        return 2
    from axiom_axm import AXMContainer
    from axiom_cpi import HumanoidStabilityAgent

    pers = args.persistence or f"{args.axm.rstrip('/')}.curriculum.json"
    agent = HumanoidStabilityAgent()
    container = AXMContainer.from_path(args.axm)
    curr = DevelopmentalCurriculum(
        supervisor=agent.supervisor,
        axm_container=container,
        persistence_path=pers,
    )
    snap = curr.snapshot()
    print(json.dumps(snap, indent=2, ensure_ascii=True, sort_keys=True))
    if args.action == "boot":
        # Surface a curriculum suggestion.
        task = curr.suggest_next_task()
        print(json.dumps(asdict(task), indent=2, ensure_ascii=True,
                          sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
