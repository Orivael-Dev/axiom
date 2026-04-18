"""
axiom/history_store.py
Rolling memory buffer for AXIOM agents that declare a HISTORY block.

Usage:
    from axiom_files.parser import load_axiom, compile_history
    from axiom.history_store import HistoryStore

    parsed  = load_axiom("game_watcher")
    config  = compile_history(parsed)
    history = HistoryStore(config)

    history.push({"frame": 42, "state": "ghost_north"}, confidence=0.9)
    history.push({"frame": 43, "state": "power_pellet_visible"}, confidence=0.6)
    history.decay()                      # drop low-confidence entries past threshold
    history.promote("corner_escape")     # elevate a confirmed pattern
    recent = history.get_recent(10)      # last 10 entries
    patterns = history.promoted_patterns()
    history.forget("session_end")        # wipe on trigger
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HistoryEntry:
    data: dict
    confidence: float = 1.0
    tick: int = 0          # monotonic insertion counter
    timestamp: float = field(default_factory=time.time)
    promoted: bool = False


class HistoryStore:
    """Rolling buffer with decay and pattern promotion.

    Config keys (from compile_history()):
      retain        — list of {count, type, label} — controls buffer capacity
      decay         — list of {condition, after, unit} — eviction rules
      promote_after — int — confirmations before a pattern is promoted
      forget_on     — list of trigger strings
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._retain = cfg.get("retain", [])
        self._decay_rules = cfg.get("decay", [])
        self._promote_threshold = cfg.get("promote_after", 3)
        self._forget_triggers = set(cfg.get("forget_on", []))

        # Calculate max buffer size from retain rules
        counts = [r["count"] for r in self._retain if isinstance(r.get("count"), int)]
        self._max_size = max(counts) if counts else 100

        self._buffer: deque[HistoryEntry] = deque(maxlen=self._max_size)
        self._tick = 0
        self._pattern_counts: dict[str, int] = {}
        self._promoted: dict[str, dict] = {}

    # ── Write ──────────────────────────────────────────────────

    def push(self, data: dict, confidence: float = 1.0) -> HistoryEntry:
        """Add an observation to the buffer."""
        entry = HistoryEntry(data=data, confidence=confidence, tick=self._tick)
        self._buffer.append(entry)
        self._tick += 1
        return entry

    def observe_pattern(self, pattern_id: str, metadata: dict | None = None) -> bool:
        """Record a pattern occurrence. Returns True if threshold crossed (auto-promoted)."""
        self._pattern_counts[pattern_id] = self._pattern_counts.get(pattern_id, 0) + 1
        count = self._pattern_counts[pattern_id]
        if count >= self._promote_threshold and pattern_id not in self._promoted:
            self.promote(pattern_id, metadata or {})
            return True
        return False

    # ── Maintenance ────────────────────────────────────────────

    def decay(self) -> int:
        """Evict entries that violate decay rules. Returns number of evicted entries."""
        if not self._decay_rules:
            return 0
        evicted = 0
        surviving = []
        for entry in self._buffer:
            keep = True
            age = self._tick - entry.tick
            for rule in self._decay_rules:
                if rule.get("condition") == "low_confidence" and entry.confidence < 0.5:
                    threshold = rule.get("after", 20)
                    if age >= threshold:
                        keep = False
                        break
            if keep:
                surviving.append(entry)
            else:
                evicted += 1
        self._buffer = deque(surviving, maxlen=self._max_size)
        return evicted

    def promote(self, pattern_id: str, metadata: dict | None = None) -> None:
        """Explicitly promote a pattern to the confirmed set."""
        self._promoted[pattern_id] = {
            "id": pattern_id,
            "confirmations": self._pattern_counts.get(pattern_id, 1),
            "promoted_at": time.time(),
            **(metadata or {}),
        }

    def forget(self, trigger: str | None = None) -> None:
        """Clear memory. If trigger is given, only fires if it matches a forget_on rule."""
        if trigger is not None and trigger not in self._forget_triggers:
            return
        self._buffer.clear()
        self._pattern_counts.clear()
        self._promoted.clear()
        self._tick = 0

    # ── Read ───────────────────────────────────────────────────

    def get_recent(self, n: int | None = None) -> list[dict]:
        """Return the last n entries as plain dicts (newest last)."""
        entries = list(self._buffer)
        if n is not None:
            entries = entries[-n:]
        return [
            {
                "data": e.data,
                "confidence": e.confidence,
                "tick": e.tick,
                "timestamp": e.timestamp,
                "promoted": e.promoted,
            }
            for e in entries
        ]

    def promoted_patterns(self) -> list[dict]:
        """Return all promoted (confirmed) patterns."""
        return list(self._promoted.values())

    def size(self) -> int:
        return len(self._buffer)

    def summary(self) -> dict:
        return {
            "size": self.size(),
            "max_size": self._max_size,
            "tick": self._tick,
            "promoted_count": len(self._promoted),
            "pattern_counts": dict(self._pattern_counts),
        }
