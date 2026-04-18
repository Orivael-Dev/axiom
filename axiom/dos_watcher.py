"""
axiom/dos_watcher.py
Spec: axiom_files/dos_watcher.axiom  (VERSION 1.0)

DosWatcher — rate limiter and circuit breaker for AXIOM API calls.
Enforces per-minute, per-hour, per-session limits and a sliding-window
burst guard. Circuit breaker transitions: CLOSED → OPEN → HALF_OPEN → CLOSED.

Reads all limits and circuit config from dos_watcher.axiom at init.
Defines nothing — the spec is the authority.

Usage:
    watcher = DosWatcher()
    result = watcher.check(caller="worker", request_text="...")
    # result: {"decision": "ALLOW"|"BLOCK"|"CIRCUIT_OPEN", ...}
    if result["decision"] != "ALLOW":
        raise DoSBlock(result)
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque


# ── Spec loading ───────────────────────────────────────────────────────────────

def _load_spec() -> dict:
    try:
        from axiom_files.parser import load_axiom
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent))
        from axiom_files.parser import load_axiom
    return load_axiom("dos_watcher")


# ── Log path ───────────────────────────────────────────────────────────────────

_AXIOM_DIR  = os.environ.get("AXIOM_FILES_DIR", "axiom_files")
_LOG_DIR    = Path(_AXIOM_DIR) / ".dos"
_LOG_PATH   = _LOG_DIR / "dos_log.jsonl"


# ── Circuit breaker states ─────────────────────────────────────────────────────

CLOSED    = "CLOSED"
OPEN      = "OPEN"
HALF_OPEN = "HALF_OPEN"


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class DoSBlock(Exception):
    """Raised by DosWatcher.check() callers when they want hard blocking."""
    decision: str
    limit_name: str
    cooldown_seconds: int
    caller: str

    def __str__(self) -> str:
        if self.decision == "CIRCUIT_OPEN":
            return f"CIRCUIT_OPEN — cooldown: {self.cooldown_seconds}s"
        return f"BLOCK: {self.limit_name} — cooldown: {self.cooldown_seconds}s"


@dataclass
class _CallRecord:
    timestamp: float
    text_hash: str


@dataclass
class _SessionState:
    session_count: int = 0
    call_log: list = field(default_factory=list)  # [_CallRecord]
    burst_window: Deque = field(default_factory=deque)  # timestamps in sliding window


# ── DosWatcher ────────────────────────────────────────────────────────────────

class DosWatcher:
    """
    Rate limiter and circuit breaker.

    All limits are loaded from dos_watcher.axiom at init — immutable at runtime
    per SECURITY block: "Rate limit configuration is immutable at runtime."
    """

    def __init__(self):
        spec = _load_spec()
        rl   = spec.get("rate_limits", {})
        cc   = spec.get("circuit_config", {})

        # Rate limits — loaded from RATE_LIMITS block, immutable
        self._per_minute:   int = int(rl.get("per_minute",   20))
        self._per_hour:     int = int(rl.get("per_hour",     200))
        self._per_session:  int = int(rl.get("per_session",  1000))
        self._burst_window: int = int(rl.get("burst_window_seconds", 10))
        self._burst_thresh: int = int(rl.get("burst_threshold", 8))
        self._replay_limit: int = int(rl.get("identical_repeat_limit", 5))

        # Circuit breaker config — loaded from CIRCUIT_CONFIG block, immutable
        self._failure_thresh:    int = int(cc.get("failure_threshold",     5))
        self._cooldown_secs:     int = int(cc.get("cooldown_seconds",      60))
        self._half_open_probes:  int = int(cc.get("half_open_probe_count", 1))

        # Mutable runtime state (per MUTATES: counters, circuit_state)
        self._circuit_state:  str   = CLOSED
        self._circuit_open_at: float | None = None
        self._failure_count:  int   = 0
        self._half_open_sent: int   = 0

        # Per-caller session state
        self._sessions: dict[str, _SessionState] = {}

        # Minute/hour sliding windows (global, not per-caller)
        self._minute_window: Deque[float] = deque()
        self._hour_window:   Deque[float] = deque()

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, caller: str = "unknown", request_text: str = "") -> dict:
        """
        Evaluate a request against all rate limits and circuit breaker.

        Returns:
          {"decision": "ALLOW"|"BLOCK"|"CIRCUIT_OPEN",
           "limit_name": str|None, "cooldown_seconds": int,
           "circuit_state": str}
        """
        import time
        now = time.time()
        text_hash = hashlib.sha256(request_text.encode()).hexdigest()[:16]

        # ── 1. Circuit breaker ────────────────────────────────────────────────
        self._tick_circuit(now)
        if self._circuit_state == OPEN:
            remaining = max(0, int(self._cooldown_secs - (now - (self._circuit_open_at or now))))
            return self._block("CIRCUIT_OPEN", "circuit_open", remaining, caller, now)

        if self._circuit_state == HALF_OPEN:
            if self._half_open_sent >= self._half_open_probes:
                remaining = max(0, int(self._cooldown_secs - (now - (self._circuit_open_at or now))))
                return self._block("CIRCUIT_OPEN", "half_open_saturated", remaining, caller, now)

        # ── 2. Per-minute limit ───────────────────────────────────────────────
        self._prune(self._minute_window, now, 60)
        if len(self._minute_window) >= self._per_minute:
            oldest = self._minute_window[0]
            remaining = max(1, int(60 - (now - oldest)))
            return self._block("BLOCK", "per_minute", remaining, caller, now)

        # ── 3. Per-hour limit ─────────────────────────────────────────────────
        self._prune(self._hour_window, now, 3600)
        if len(self._hour_window) >= self._per_hour:
            oldest = self._hour_window[0]
            remaining = max(1, int(3600 - (now - oldest)))
            return self._block("BLOCK", "per_hour", remaining, caller, now)

        # ── 4. Per-session limit ──────────────────────────────────────────────
        session = self._sessions.setdefault(caller, _SessionState())
        if session.session_count >= self._per_session:
            return self._block("BLOCK", "per_session", self._cooldown_secs, caller, now)

        # ── 5. Burst protection ───────────────────────────────────────────────
        self._prune(session.burst_window, now, self._burst_window)
        if len(session.burst_window) >= self._burst_thresh:
            return self._block("BLOCK", "burst_protection",
                               max(1, self._burst_window), caller, now)

        # ── 6. Replay attack detection ────────────────────────────────────────
        recent_identical = sum(
            1 for r in session.call_log[-self._replay_limit * 2:]
            if r.text_hash == text_hash
        )
        if recent_identical >= self._replay_limit:
            return self._block("BLOCK", "replay_attack", self._cooldown_secs, caller, now)

        # ── ALLOW ─────────────────────────────────────────────────────────────
        self._minute_window.append(now)
        self._hour_window.append(now)
        session.burst_window.append(now)
        session.call_log.append(_CallRecord(timestamp=now, text_hash=text_hash))
        session.session_count += 1

        if self._circuit_state == HALF_OPEN:
            self._half_open_sent += 1

        self._log({"decision": "ALLOW", "caller": caller,
                   "circuit_state": self._circuit_state, "ts": _iso(now)})
        return {"decision": "ALLOW", "limit_name": None,
                "cooldown_seconds": 0, "circuit_state": self._circuit_state}

    def record_failure(self):
        """
        Call after a model call fails. Increments failure counter;
        trips circuit breaker to OPEN if failure_threshold reached.
        """
        import time
        self._failure_count += 1
        if self._failure_count >= self._failure_thresh:
            self._trip_open(time.time())

    def record_success(self):
        """Call after a successful model call. Resets failure count; closes circuit."""
        self._failure_count = 0
        if self._circuit_state == HALF_OPEN:
            self._circuit_state = CLOSED
            self._circuit_open_at = None
            self._half_open_sent = 0

    def reset_session(self, caller: str):
        """Clear per-session state for a caller (e.g. on session end)."""
        self._sessions.pop(caller, None)

    @property
    def circuit_state(self) -> str:
        return self._circuit_state

    def status(self) -> dict:
        """Return current watcher status (safe for logging — no raw counters exposed)."""
        import time
        return {
            "circuit_state": self._circuit_state,
            "minute_calls": len(self._minute_window),
            "minute_limit": self._per_minute,
            "hour_calls": len(self._hour_window),
            "hour_limit": self._per_hour,
            "active_sessions": len(self._sessions),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _tick_circuit(self, now: float):
        """Advance circuit breaker FSM based on elapsed time."""
        if self._circuit_state == OPEN and self._circuit_open_at is not None:
            if now - self._circuit_open_at >= self._cooldown_secs:
                self._circuit_state = HALF_OPEN
                self._half_open_sent = 0

    def _trip_open(self, now: float):
        self._circuit_state = OPEN
        self._circuit_open_at = now
        self._half_open_sent = 0
        self._log({
            "decision": "CIRCUIT_TRIPPED",
            "failure_count": self._failure_count,
            "ts": _iso(now),
        })

    def _block(self, decision: str, limit_name: str,
               cooldown: int, caller: str, now: float) -> dict:
        result = {
            "decision": decision,
            "limit_name": limit_name,
            "cooldown_seconds": cooldown,
            "circuit_state": self._circuit_state,
        }
        self._log({
            "decision": decision,
            "limit_name": limit_name,
            "cooldown_seconds": cooldown,
            "caller": caller,
            "ts": _iso(now),
        })
        return result

    @staticmethod
    def _prune(window: Deque, now: float, window_secs: int):
        while window and now - window[0] > window_secs:
            window.popleft()

    def _log(self, entry: dict):
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # log failure must never block the decision


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
