"""Multi-Resolution Memory — Pillar 3 of the Memory Trifecta.

Inspired by mipmapping and Level-of-Detail (LOD) in 3D engines: textures are
stored at multiple resolutions and the renderer picks the coarsest level that
satisfies the current view distance.  Applied to LLM memory:

  LOD 0 — single bracketed token pointer  e.g. [LEGAL_REVIEW_NDA_CLAUSE]
           Zero parse cost; fed to the router as a routing hint, never
           injected into the LLM prompt.

  LOD 1 — 2–5 sentence natural-language summary: objective, constraints,
           open questions.  Prepended to the user turn for standard tasks
           (INFORM / CLARIFY intent).

  LOD 2 — full JSON structure: complete DeltaState + up to 3 recalled
           ConstitutionalPackets.  Appended to the system prompt only for
           deep code-gen or strict verification passes
           (INFORM / CLARIFY + compliance-heavy domain).

LOD selection is a pure function of intent_class + domain — no I/O.

Layer: 1 (Inference Router — picks LOD before retrieval) /
       2 (Memory — LOD 1/2 injected into context)
"""
from __future__ import annotations

import enum
import json
import re
import sys
import types as _types
from dataclasses import dataclass
from typing import List, Optional

from axiom_delta_memory import DeltaState


# ── CANNOT_MUTATE module freeze ───────────────────────────────────────────────

def _module_setattr(self: object, name: str, value: object) -> None:
    raise AttributeError(
        f"CANNOT_MUTATE: {name} is immutable in axiom_multiresolution_memory"
    )

_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr}
)

# Domains where LOD 2 is activated (compliance-heavy, need full session context)
_LOD2_DOMAINS:    frozenset = frozenset({"legal", "finance"})
# Intent classes that qualify for LOD 1 or LOD 2 injection
_CONTEXT_INTENTS: frozenset = frozenset({"INFORM", "CLARIFY", "REFUSE"})


# ── MemoryLOD ─────────────────────────────────────────────────────────────────

class MemoryLOD(enum.IntEnum):
    LOD0 = 0   # token pointer — routing metadata only, zero prompt tokens
    LOD1 = 1   # text summary — prepended to user turn (~40–80 tokens)
    LOD2 = 2   # full JSON   — appended to system prompt (~200–400 tokens)


# ── MemoryView ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MemoryView:
    """Memory content at a specific resolution level."""
    lod:            MemoryLOD
    content:        str          # rendered text at this LOD
    token_estimate: int          # rough token count (len(content) // 4)
    source:         str          # "delta_state" | "constitutional" | "empty"


# ── MultiResolutionMemory ────────────────────────────────────────────────────

class MultiResolutionMemory:
    """Wraps DeltaState + optional ConstitutionalPackets in three LOD tiers.

    All methods are pure functions — no I/O, no LLM calls.
    """

    # ── LOD 0 ─────────────────────────────────────────────────────────────────

    def to_lod0(self, state: DeltaState, domain: Optional[str]) -> MemoryView:
        """Generate a bracketed semantic token pointer.

        Format: [DOMAIN_WORD1_WORD2_WORD3]
        - domain prefix is uppercased; falls back to GENERAL if None.
        - slug is derived from the first 3 meaningful words of current_objective.
        - Empty objective → [GENERAL_SESSION_ACTIVE].
        """
        domain_tag = (domain or "general").upper().replace("-", "_")[:12]
        obj = state.current_objective.strip()
        if obj:
            words = [_clean_word(w) for w in obj.split() if _clean_word(w)][:3]
            slug  = "_".join(words) if words else "SESSION"
        else:
            slug  = "SESSION_ACTIVE"
        token   = f"[{domain_tag}_{slug}]"
        return MemoryView(
            lod            = MemoryLOD.LOD0,
            content        = token,
            token_estimate = 1,
            source         = "delta_state" if obj else "empty",
        )

    # ── LOD 1 ─────────────────────────────────────────────────────────────────

    def to_lod1(self, state: DeltaState) -> MemoryView:
        """2–4 sentence natural-language summary of the current state.

        Returns a MemoryView with empty content when state has no meaningful
        data (empty objective, no constraints, no questions).
        """
        parts: List[str] = []
        if state.current_objective:
            parts.append(f"Objective: {state.current_objective}.")
        if state.active_constraints:
            cstr = "; ".join(state.active_constraints[:4])
            parts.append(f"Constraints: {cstr}.")
        if state.unresolved_questions:
            q = state.unresolved_questions[-1]   # most recent open question
            parts.append(f"Open: {q}")
        if state.completed_milestones:
            m = state.completed_milestones[-1]
            parts.append(f"Completed: {m}.")

        if not parts:
            return MemoryView(lod=MemoryLOD.LOD1, content="",
                              token_estimate=0, source="empty")

        text = " ".join(parts)
        return MemoryView(
            lod            = MemoryLOD.LOD1,
            content        = text,
            token_estimate = len(text) // 4,
            source         = "delta_state",
        )

    # ── LOD 2 ─────────────────────────────────────────────────────────────────

    def to_lod2(
        self,
        state: DeltaState,
        packets: Optional[list] = None,   # list[ConstitutionalPacket] | None
    ) -> MemoryView:
        """Full JSON: DeltaState dict + up to 3 recalled ConstitutionalPackets.

        The JSON is formatted compactly (no indentation) to minimise tokens.
        """
        session_dict = {
            "current_objective":    state.current_objective,
            "active_constraints":   list(state.active_constraints),
            "completed_milestones": list(state.completed_milestones[-5:]),
            "unresolved_questions": list(state.unresolved_questions),
            "turn_count":           state.turn_count,
            "domain":               state.domain,
        }
        prior: List[dict] = []
        if packets:
            for pkt in packets[:3]:
                try:
                    prior.append({
                        "domain":    getattr(pkt, "domain_cluster", ""),
                        "resolution": getattr(pkt, "resolution", ""),
                        "constraints": list(getattr(pkt, "active_constraints", ())),
                    })
                except Exception:
                    pass

        blob = {"session_state": session_dict}
        if prior:
            blob["prior_packets"] = prior

        text = json.dumps(blob, ensure_ascii=False, separators=(",", ":"))
        return MemoryView(
            lod            = MemoryLOD.LOD2,
            content        = text,
            token_estimate = len(text) // 4,
            source         = "delta_state" if (state.current_objective or prior)
                             else "empty",
        )

    # ── LOD resolution ────────────────────────────────────────────────────────

    def resolve_lod(self, intent_class: str, domain: Optional[str]) -> MemoryLOD:
        """Pick the appropriate LOD — pure function, no I/O.

        Decision table:
          HARM / DECEIVE → LOD 0  (pipeline blocked; minimal memory overhead)
          UNCERTAIN      → LOD 0  (no context reliable enough to inject)
          INFORM/CLARIFY + compliance domain (legal/finance) → LOD 2
          INFORM/CLARIFY/REFUSE → LOD 1
          anything else  → LOD 0
        """
        if intent_class in _CONTEXT_INTENTS:
            if domain in _LOD2_DOMAINS:
                return MemoryLOD.LOD2
            return MemoryLOD.LOD1
        return MemoryLOD.LOD0

    # ── convenience ───────────────────────────────────────────────────────────

    def view(
        self,
        state: DeltaState,
        intent_class: str,
        domain: Optional[str],
        packets: Optional[list] = None,
    ) -> MemoryView:
        """Convenience: resolve_lod() → to_lodN() in one call."""
        lod = self.resolve_lod(intent_class, domain)
        if lod == MemoryLOD.LOD0:
            return self.to_lod0(state, domain)
        if lod == MemoryLOD.LOD1:
            return self.to_lod1(state)
        return self.to_lod2(state, packets)


# ── internal helpers ──────────────────────────────────────────────────────────

_WORD_CLEAN_RE = re.compile(r"[^A-Za-z0-9]+")


def _clean_word(w: str) -> str:
    """Strip non-alphanum and uppercase — 8 char max for pointer readability."""
    cleaned = _WORD_CLEAN_RE.sub("", w).upper()[:8]
    return cleaned
