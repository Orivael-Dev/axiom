"""Delta Memory Map — Pillar 2 of the Memory Trifecta.

Inspired by 90s "Dirty Rectangle" rendering and delta-compression patch
routines: instead of resubmitting the full chat history on every turn
(O(N) context growth), the pipeline keeps a flat, structured state
snapshot and updates only the registers that changed.

Context cost is O(1) regardless of session length.

Layer: 2 — Memory + EventToken Cache
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import types as _types
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── CANNOT_MUTATE module freeze ───────────────────────────────────────────────

def _module_setattr(self: object, name: str, value: object) -> None:
    raise AttributeError(f"CANNOT_MUTATE: {name} is immutable in axiom_delta_memory")

_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})

DELTA_MAP_VERSION:    str = "1.0"  # CANNOT_MUTATE
KEY_NS:               bytes = b"axiom-delta-memory-v1"
MAX_QUESTIONS:        int = 5      # cap on unresolved_questions list
MAX_MILESTONES:       int = 20     # cap on completed_milestones list
_DEFAULT_STORE_PATH:  str = "~/.axiom/delta_memory.jsonl"

# Objective extraction: "objective: <text>" in output (case-insensitive)
_OBJECTIVE_RE = re.compile(r"(?i)\bobjective[:\s]+(.+?)(?:\.|$)", re.MULTILINE)
# Milestone resolution: output contains "resolved:" or "done:" prefix
_RESOLVED_RE  = re.compile(r"(?i)\b(?:resolved|done|completed)[:\s]")


# ── DeltaState ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeltaState:
    """Flat session state register — immutable; updated via apply_delta().

    All string fields default to "" / empty tuples so a fresh session can be
    constructed with only ``session_id`` set.
    """
    session_id:           str
    current_objective:    str                = ""
    active_constraints:   Tuple[str, ...]    = ()
    completed_milestones: Tuple[str, ...]    = ()
    unresolved_questions: Tuple[str, ...]    = ()
    turn_count:           int                = 0
    domain:               Optional[str]      = None
    last_updated:         str                = ""   # ISO 8601 UTC
    hmac_signature:       str                = ""   # HMAC-SHA256 of content fields


# ── DeltaMemoryMap ────────────────────────────────────────────────────────────

class DeltaMemoryMap:
    """Dirty-rectangle state updater for session memory.

    All methods are pure functions over DeltaState — no I/O, no LLM calls.
    """

    # ── mutation ──────────────────────────────────────────────────────────────

    def apply_delta(self, state: DeltaState, **dirty_fields) -> DeltaState:
        """Return a new DeltaState with only *dirty_fields* replaced.

        Immutable update: the original state is never modified.
        """
        allowed = {f.name for f in state.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(dirty_fields) - allowed
        if unknown:
            raise ValueError(f"Unknown DeltaState fields: {unknown}")
        return replace(state, **dirty_fields)

    # ── context serialisation ─────────────────────────────────────────────────

    def to_context_string(self, state: DeltaState) -> str:
        """Compact JSON (~80–120 tokens) for LLM injection.

        Returns "" when the state carries no meaningful content (no objective,
        no constraints, no open questions) — callers can skip injection entirely.
        """
        if not (state.current_objective
                or state.active_constraints
                or state.unresolved_questions):
            return ""
        d: dict = {}
        if state.current_objective:
            d["current_objective"] = state.current_objective
        if state.active_constraints:
            d["active_constraints"] = list(state.active_constraints)
        if state.completed_milestones:
            d["completed_milestones"] = list(state.completed_milestones[-3:])  # last 3 only
        if state.unresolved_questions:
            d["unresolved_questions"] = list(state.unresolved_questions)
        d["turn"] = state.turn_count
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))

    # ── delta extraction (no LLM) ─────────────────────────────────────────────

    def extract_delta(
        self,
        output: str,
        query: str,
        state: DeltaState,
    ) -> Dict[str, object]:
        """Extract dirty fields from a single turn — no LLM call.

        Rules applied in order:
        1. Increment turn_count always.
        2. If query ends with '?' → add to unresolved_questions (capped at MAX_QUESTIONS).
        3. If output matches _RESOLVED_RE → promote last unresolved question to
           completed_milestones; remove from unresolved_questions.
        4. If output matches _OBJECTIVE_RE → update current_objective.
        5. Update last_updated timestamp.
        """
        dirty: Dict[str, object] = {
            "turn_count":   state.turn_count + 1,
            "last_updated": _now_iso(),
        }

        # Rule 2: question tracking
        questions = list(state.unresolved_questions)
        if query.rstrip().endswith("?"):
            q = query.strip()
            if q not in questions:
                questions.append(q)
                if len(questions) > MAX_QUESTIONS:
                    questions = questions[-MAX_QUESTIONS:]
                dirty["unresolved_questions"] = tuple(questions)

        # Rule 3: resolution detection
        if _RESOLVED_RE.search(output) and questions:
            resolved   = questions.pop()
            milestones = list(state.completed_milestones) + [resolved]
            if len(milestones) > MAX_MILESTONES:
                milestones = milestones[-MAX_MILESTONES:]
            dirty["unresolved_questions"] = tuple(questions)
            dirty["completed_milestones"] = tuple(milestones)

        # Rule 4: objective update
        m = _OBJECTIVE_RE.search(output)
        if m:
            new_obj = m.group(1).strip()[:200]
            if new_obj and new_obj != state.current_objective:
                dirty["current_objective"] = new_obj

        return dirty

    # ── signing ───────────────────────────────────────────────────────────────

    def sign(self, state: DeltaState, key: bytes) -> DeltaState:
        """Return a new DeltaState with hmac_signature set."""
        payload = _state_payload(state)
        data    = json.dumps(payload, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True).encode()
        sig     = hmac.new(key, data, hashlib.sha256).hexdigest()
        return replace(state, hmac_signature=sig)

    def verify(self, state: DeltaState, key: bytes) -> bool:
        """Constant-time HMAC verification."""
        if not state.hmac_signature:
            return False
        payload  = _state_payload(state)
        data     = json.dumps(payload, sort_keys=True,
                              separators=(",", ":"), ensure_ascii=True).encode()
        expected = hmac.new(key, data, hashlib.sha256).hexdigest()
        return hmac.compare_digest(state.hmac_signature, expected)


# ── DeltaMemoryStore ──────────────────────────────────────────────────────────

class DeltaMemoryStore:
    """Per-session JSONL store — one logical entry per session_id.

    The file is append-only; on ``load()`` the last entry for a given
    session_id wins (deduplication happens at read time, not write time).
    This avoids seek-and-overwrite on a potentially hot file.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        env_path = os.environ.get("AXIOM_DELTA_MEMORY_PATH", "")
        self._path: Path = (
            path if path is not None
            else (Path(env_path) if env_path else Path(_DEFAULT_STORE_PATH).expanduser())
        )

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, session_id: str) -> Optional[DeltaState]:
        """Return the latest DeltaState for *session_id*, or None."""
        if not self._path.exists():
            return None
        last: Optional[DeltaState] = None
        try:
            with self._path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("session_id") == session_id:
                            last = _state_from_dict(d)
                    except (json.JSONDecodeError, TypeError, KeyError):
                        continue
        except OSError:
            return None
        return last

    def save(self, session_id: str, state: DeltaState) -> None:
        """Append *state* to the store. session_id must match state.session_id."""
        if state.session_id != session_id:
            raise ValueError(
                f"session_id mismatch: arg={session_id!r} vs state={state.session_id!r}"
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_state_to_dict(state), ensure_ascii=True) + "\n")

    def forget(self, session_id: str) -> None:
        """Remove all entries for *session_id* from the store."""
        if not self._path.exists():
            return
        keep: List[str] = []
        try:
            with self._path.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        d = json.loads(stripped)
                        if d.get("session_id") != session_id:
                            keep.append(line)
                    except (json.JSONDecodeError, TypeError):
                        keep.append(line)
        except OSError:
            return
        with self._path.open("w", encoding="utf-8") as fh:
            fh.writelines(keep)

    def purge_older_than(self, days: int = 30) -> int:
        """Remove entries with last_updated older than *days* days.

        Returns the number of entries purged.
        """
        if not self._path.exists():
            return 0
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        keep:  List[str] = []
        purged: int = 0
        try:
            with self._path.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        d = json.loads(stripped)
                        ts_str = d.get("last_updated", "")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts < cutoff:
                                purged += 1
                                continue
                        keep.append(line)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        keep.append(line)
        except OSError:
            return 0
        with self._path.open("w", encoding="utf-8") as fh:
            fh.writelines(keep)
        return purged


# ── internal helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _state_payload(state: DeltaState) -> dict:
    """Fields included in the HMAC (excludes hmac_signature itself)."""
    return {
        "session_id":           state.session_id,
        "current_objective":    state.current_objective,
        "active_constraints":   list(state.active_constraints),
        "completed_milestones": list(state.completed_milestones),
        "unresolved_questions": list(state.unresolved_questions),
        "turn_count":           state.turn_count,
        "domain":               state.domain,
        "last_updated":         state.last_updated,
        "version":              DELTA_MAP_VERSION,
    }


def _state_to_dict(state: DeltaState) -> dict:
    d = _state_payload(state)
    d["hmac_signature"] = state.hmac_signature
    return d


def _state_from_dict(d: dict) -> DeltaState:
    return DeltaState(
        session_id           = str(d["session_id"]),
        current_objective    = str(d.get("current_objective", "")),
        active_constraints   = tuple(d.get("active_constraints", [])),
        completed_milestones = tuple(d.get("completed_milestones", [])),
        unresolved_questions = tuple(d.get("unresolved_questions", [])),
        turn_count           = int(d.get("turn_count", 0)),
        domain               = d.get("domain"),
        last_updated         = str(d.get("last_updated", "")),
        hmac_signature       = str(d.get("hmac_signature", "")),
    )
