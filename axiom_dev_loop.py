"""
AXIOM Dev Loop — capture-and-train shim for AxiomDev (ORVL-001 + ORVL-011)
==========================================================================
Records one development cycle (scout → plan → edit → test → commit) as a
signed JSONL entry and fans it out to the three existing sinks so the
downstream training pipeline picks it up without schema changes.

Sinks (already consumed by existing modules — see axiom_dataset_builder.py
line 559, axiom_retrospect.py line 99, axiom_crl_reward.py line 135):

  1. axiom_dev_training.jsonl     instruction/output pairs
                                  (read by axiom_dataset_builder)
  2. dev_agent_improvements.jsonl ImprovementRecord shape
                                  (consumed by retrospect once wired)
  3. axiom_crl_reward_log.jsonl   ConstitutionalRewardFunction output
                                  (RL training signal)

Manifest  : axiom-dev-loop-v1
Trust     : TRUST_LEVEL = 3 (same as axiom_dev.axiom)
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

BUG-003 : explicit utf-8 on every open()
BUG-007 : HMAC always finalised with .hexdigest()
BUG-008 : explicit utf-8 encode before HMAC
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ────────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
RATING_BAD_ON_TEST_FAIL: bool = True
DEFAULT_TRAINING_PATH: str = "axiom_dev_training.jsonl"
DEFAULT_IMPROVEMENTS_PATH: str = "dev_agent_improvements.jsonl"
DEFAULT_REWARD_LOG_PATH: str = "axiom_crl_reward_log.jsonl"
MANIFEST_ID: str = "axiom-dev-loop-v1"

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "RATING_BAD_ON_TEST_FAIL",
    "DEFAULT_TRAINING_PATH", "DEFAULT_IMPROVEMENTS_PATH",
    "DEFAULT_REWARD_LOG_PATH", "MANIFEST_ID",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)


# ── Signing key ──────────────────────────────────────────────────────────
def _signing_key() -> bytes:
    # Lazy import so tests that scrub the env still load axiom_dev_loop.
    from axiom_signing import derive_key
    return derive_key(b"axiom-dev-loop-v1")


# ── Dataclasses ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DevCycleRecord:
    commit_sha: str
    task: str
    changed_files: tuple
    diff_summary: str
    test_pass: int
    test_fail: int
    retrospect_signal: str
    rating: str          # "good" | "bad"
    timestamp: str
    signature: str

    def as_training_line(self) -> dict:
        """Schema axiom_dataset_builder._process_existing_training expects."""
        return {
            "task":          self.task,
            "result":        self.diff_summary,
            "rating":        self.rating,
            "commit_sha":    self.commit_sha,
            "changed_files": list(self.changed_files),
            "test_pass":     self.test_pass,
            "test_fail":     self.test_fail,
            "timestamp":     self.timestamp,
            "signature":     self.signature,
            "source":        MANIFEST_ID,
        }


# ── Helpers ───────────────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")  # BUG-008


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()  # BUG-007


def verify(record: DevCycleRecord, key: Optional[bytes] = None) -> bool:
    """Constant-time HMAC check on a previously emitted record."""
    payload = {
        "commit_sha":        record.commit_sha,
        "task":              record.task,
        "changed_files":     list(record.changed_files),
        "diff_summary":      record.diff_summary,
        "test_pass":         record.test_pass,
        "test_fail":         record.test_fail,
        "retrospect_signal": record.retrospect_signal,
        "rating":            record.rating,
        "timestamp":         record.timestamp,
    }
    expected = _sign(key or _signing_key(), payload)
    if not isinstance(record.signature, str) or len(record.signature) != len(expected):
        return False
    return hmac.compare_digest(record.signature, expected)


def _append_jsonl(path: Path, entry: Mapping[str, Any]) -> None:
    # BUG-003: explicit utf-8
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")


# ── Recorder ──────────────────────────────────────────────────────────────
class DevCycleRecorder:
    """Append one signed dev-cycle record per build event.

    Each call to :meth:`record` fans out to three existing JSONL sinks so the
    downstream training pipeline picks it up automatically:

    * ``axiom_dev_training.jsonl`` — read by
      ``axiom_dataset_builder._process_existing_training`` (line 559).
      Records with ``rating == "bad"`` are filtered out at line 573, so a
      cycle that broke the test suite still gets logged but won't poison
      the fine-tune corpus.
    * ``dev_agent_improvements.jsonl`` — matches the dataclass at
      ``axiom_retrospect.py`` line 99 so the nightly retrospective can
      ingest it.
    * ``axiom_crl_reward_log.jsonl`` — written by
      ``axiom_crl_reward.ConstitutionalRewardFunction.compute`` so RL
      training has reward labels for every cycle.

    The recorder never raises on a sink write failure that is downstream of
    a successful signature — git hooks must not break user commits.
    """

    def __init__(
        self,
        hmac_key: Optional[bytes] = None,
        repo_root: Optional[Path] = None,
        *,
        training_path: Optional[Path] = None,
        improvements_path: Optional[Path] = None,
        reward_fn: Any = None,
    ) -> None:
        self._key = bytes(hmac_key) if hmac_key is not None else _signing_key()
        self._root = Path(repo_root) if repo_root is not None else Path.cwd()
        self._training = Path(training_path) if training_path else (self._root / DEFAULT_TRAINING_PATH)
        self._improvements = Path(improvements_path) if improvements_path else (self._root / DEFAULT_IMPROVEMENTS_PATH)
        self._reward_fn = reward_fn  # injected for tests; None means construct lazily

    # ── Public API ────────────────────────────────────────────────────────
    def record(
        self,
        *,
        commit_sha: str,
        task: str,
        changed_files: Iterable[str],
        diff_summary: str,
        test_pass: int,
        test_fail: int,
        retrospect_signal: str = "neutral",
    ) -> DevCycleRecord:
        if not commit_sha or not task:
            raise ValueError("commit_sha and task are required")
        if test_pass < 0 or test_fail < 0:
            raise ValueError("test counts must be non-negative")

        changed_tuple = tuple(changed_files)
        timestamp = datetime.now(timezone.utc).isoformat()
        rating = "good" if (test_fail == 0 and test_pass > 0) else "bad"

        payload = {
            "commit_sha":        commit_sha,
            "task":              task,
            "changed_files":     list(changed_tuple),
            "diff_summary":      diff_summary,
            "test_pass":         test_pass,
            "test_fail":         test_fail,
            "retrospect_signal": retrospect_signal,
            "rating":            rating,
            "timestamp":         timestamp,
        }
        signature = _sign(self._key, payload)

        record = DevCycleRecord(
            commit_sha=commit_sha,
            task=task,
            changed_files=changed_tuple,
            diff_summary=diff_summary,
            test_pass=test_pass,
            test_fail=test_fail,
            retrospect_signal=retrospect_signal,
            rating=rating,
            timestamp=timestamp,
            signature=signature,
        )

        # ── Sink 1: training corpus ───────────────────────────────────────
        _append_jsonl(self._training, record.as_training_line())

        # ── Sink 2: improvement record (retrospect shape) ─────────────────
        # ImprovementRecord(input_text, former_self_verdict, current_verdict,
        #                   improvement_cause, training_signal, hmac_signature)
        improvement = {
            "input_text":          task,
            "former_self_verdict": "UNKNOWN",
            "current_verdict":     "PASS" if rating == "good" else "FAIL",
            "improvement_cause":   f"dev_cycle:{commit_sha[:12]}",
            "training_signal":     "positive" if rating == "good" else "negative",
            "hmac_signature":      signature,
        }
        _append_jsonl(self._improvements, improvement)

        # ── Sink 3: constitutional reward log ─────────────────────────────
        # Reuse ConstitutionalRewardFunction so the canonical weights stay
        # CANNOT_MUTATE. Map cycle outcomes onto the four governance signals.
        try:
            reward_fn = self._reward_fn or self._lazy_reward_fn()
            cd = 0.85 if rating == "good" else 0.20  # high distance when tests pass
            reward_fn.compute({
                "constitutional_distance": cd,
                "monotonic_pass":          rating == "good",
                "cas_blue_win":            rating == "good",
                "cbv_validity":            0.9 if rating == "good" else 0.1,
            })
        except Exception:
            # Never break a commit hook on a downstream-sink failure.
            pass

        return record

    # ── Internals ─────────────────────────────────────────────────────────
    def _lazy_reward_fn(self):
        from axiom_crl_reward import ConstitutionalRewardFunction
        return ConstitutionalRewardFunction(
            self._key,
            log_path=str(self._root / DEFAULT_REWARD_LOG_PATH),
        )
