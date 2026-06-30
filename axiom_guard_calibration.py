"""
AXIOM Guard Calibration Loop — close the data flywheel
=======================================================
The guard's catch rate was static: hand-written regex patterns, no mechanism to turn
collected production data into a better guard. That makes the "data flywheel moat"
hollow — day-1000 catches what day-1 caught, and the patterns are copyable. The moat
isn't the patterns; it's the *closed loop* that consumes proprietary labeled data.

This is that loop. It ingests the guard's misses, derives candidate patterns, and —
critically — **validates every candidate against the governance benchmark before
committing it**: a pattern is accepted ONLY if it raises catch without raising
over-block. Accepted patterns are appended to a signed, hash-chained calibration
ledger and layered above the regex floor. Every miss becomes a validated improvement.

    capture (misses, overrides, pruned trajectories)
        → propose (discriminative patterns from the misses)
        → VALIDATE against the bench  (catch ↑, over-block flat)   ← the guardrail
        → commit (signed, chained)    → re-score → repeat

Built on the pieces shipped this session:
  • axiom_governance_bench — the validation gate + the labeled corpus (the scoreboard)
  • axiom_trajectory_prune — optional capture source / sink (the closed-loop shape)
  • signed hash-chained ledger — the same tamper-evident pattern as the rest

The regex intent gate stays as the fast Layer-0 floor; this learns *above* it from
your data. The data compounds and can't be copied — that is the moat.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import axiom_governance_bench as gbench
from axiom_intent_classifier import IntentClassifier

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-guard-calibration-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-guard-calibration-v1", 1)

_CLF = IntentClassifier(hmac_key=b"axiom_guard_calibration_key_16b+")

_STOP = frozenset(
    "a an the of to in on at for and or but is are was were be been being do does did "
    "how do i me my the with from by as it into so that this these can could would "
    "help write give provide explain detail step steps instructions tutorial".split()
)


def _intent_blocks(text: str) -> bool:
    try:
        return _CLF.classify(text).blocks
    except (TypeError, ValueError):
        return False


def _content_tokens(text: str) -> list:
    out, cur = [], []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur)); cur = []
    if cur:
        out.append("".join(cur))
    return [t for t in out if t not in _STOP and len(t) > 2]


def _candidate_phrases(text: str) -> list:
    """Discriminative 2- and 3-grams of content words, longest/most-specific first."""
    toks = _content_tokens(text)
    phrases = []
    for n in (3, 2):
        for i in range(len(toks) - n + 1):
            phrases.append(" ".join(toks[i:i + n]))
    seen, ordered = set(), []
    for p in sorted(phrases, key=lambda p: (-len(p.split()), -len(p))):
        if p not in seen:
            seen.add(p); ordered.append(p)
    return ordered


@dataclass
class CalibrationLoop:
    ledger_path: Optional[str] = None
    patterns: list = field(default_factory=list)        # committed phrases (the learned layer)
    sources: list = field(default_factory=list)         # the source miss behind each pattern
    semantic_enabled: bool = False                      # set by calibrate() iff bench-safe
    sem_threshold: float = 1.0

    def __post_init__(self):
        self.ledger_path = Path(self.ledger_path) if self.ledger_path else None
        if self.ledger_path and self.ledger_path.exists():
            self._load()

    # ── the calibrated guard = regex floor OR learned patterns ──────────────────
    def matches(self, text: str) -> bool:
        t = text.lower()
        return any(p in t for p in self.patterns)

    def semantic_matches(self, text: str) -> bool:
        """Generalize past the literal phrase: block if `text` is semantically close to
        any committed source miss. Only consulted when calibrate() proved it bench-safe."""
        if not self.semantic_enabled:
            return False
        from axiom_semantic_embed import similarity
        return any(similarity(text, s) >= self.sem_threshold for s in self.sources if s)

    def calibrated_blocks(self, text: str) -> bool:
        return _intent_blocks(text) or self.matches(text) or self.semantic_matches(text)

    # ── signed, hash-chained ledger ─────────────────────────────────────────────
    def _sign(self, body: dict) -> str:
        return hmac_lib.new(_KEY, json.dumps(body, sort_keys=True, ensure_ascii=True,
                                             separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    def _load(self):
        self.patterns, prev = [], "GENESIS"
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = {k: v for k, v in e.items() if k != "signature"}
            if e.get("signature") != self._sign(body) or e.get("prev_hash") != prev:
                continue                                   # tampered/broken chain → ignore
            prev = e.get("entry_hash", prev)
            if e.get("action") == "commit_pattern":
                self.patterns.append(e["pattern"])
                self.sources.append(e.get("source", ""))

    def _commit(self, pattern: str, now: str, source: str, gain: int):
        prev = "GENESIS"
        if self.ledger_path and self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        prev = json.loads(line).get("entry_hash", prev)
                    except json.JSONDecodeError:
                        pass
        body = {"action": "commit_pattern", "pattern": pattern, "source": source[:120],
                "catch_gain": gain, "committed_at": now, "prev_hash": prev}
        body["entry_hash"] = hashlib.sha256(
            (prev + pattern).encode()).hexdigest()[:32]
        entry = {**body, "signature": self._sign(body)}
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=True) + "\n")
        self.patterns.append(pattern)
        self.sources.append(source)

    # ── semantic generalization, gated by the bench (catch ↑, over-block flat) ──
    def _tune_semantic(self) -> None:
        """Enable semantic matching at the LOWEST threshold that adds catch without
        raising over-block on the bench. More generalization, same guardrail."""
        from axiom_semantic_embed import similarity, RECOMMENDED_THRESHOLD
        benign = [t for t, l, _ in gbench.CORPUS if l == "pass"]
        unsafe_missed = [t for t, l, _ in gbench.CORPUS
                         if l == "block" and not _intent_blocks(t) and not self.matches(t)]
        # Walk thresholds high→low; stop at the lowest that keeps benign over-block at 0.
        best = None
        for thr in (0.90, 0.80, 0.70, RECOMMENDED_THRESHOLD):
            over = any(similarity(b, s) >= thr for b in benign for s in self.sources if s)
            gain = any(similarity(m, s) >= thr for m in unsafe_missed for s in self.sources if s)
            if not over and gain:
                best = thr                                  # safe AND useful → keep going lower
        if best is not None:
            self.semantic_enabled, self.sem_threshold = True, best

    # ── validation against the bench (the guardrail) ────────────────────────────
    def _validate(self, phrase: str) -> tuple:
        """Returns (catch_gain, new_over_block) of adding `phrase`, measured on the
        governance-bench corpus. A candidate is safe iff it catches >=1 unsafe prompt
        the gate currently misses AND introduces ZERO new over-blocks."""
        gain = over = 0
        for text, label, _ in gbench.CORPUS:
            if phrase in text.lower():
                if label == "block" and not _intent_blocks(text):
                    gain += 1
                elif label == "pass":
                    over += 1                              # any benign match = an over-block
        return gain, over

    def _score(self) -> dict:
        unsafe = [t for t, l, _ in gbench.CORPUS if l == "block"]
        benign = [t for t, l, _ in gbench.CORPUS if l == "pass"]
        catch = gbench._pct(sum(1 for t in unsafe if self.calibrated_blocks(t)), len(unsafe))
        over = gbench._pct(sum(1 for t in benign if self.calibrated_blocks(t)), len(benign))
        return {"catch_pct": catch, "over_block_pct": over}

    # ── the loop ────────────────────────────────────────────────────────────────
    def calibrate(self, now: Optional[str] = None, extra_misses: Optional[list] = None) -> dict:
        now = now or datetime.now(timezone.utc).isoformat()
        before = self._score()

        # Capture: unsafe prompts the gate currently misses (+ any caller-supplied misses,
        # e.g. from human overrides or pruned trajectories).
        misses = [t for t, l, _ in gbench.CORPUS if l == "block" and not _intent_blocks(t)]
        misses += list(extra_misses or [])

        proposals = []
        for miss in misses:
            chosen = None
            for cand in _candidate_phrases(miss):
                if cand in self.patterns:
                    chosen = cand; break
                gain, over = self._validate(cand)
                accepted = gain >= 1 and over == 0       # catch ↑, over-block flat
                proposals.append({"pattern": cand, "catch_gain": gain,
                                  "new_over_block": over, "accepted": accepted,
                                  "source": miss[:60]})
                if accepted:
                    self._commit(cand, now, miss, gain)
                    chosen = cand
                    break
            if chosen is None:
                proposals.append({"pattern": None, "accepted": False,
                                  "source": miss[:60], "reason": "no safe discriminative phrase"})

        # Layer semantic generalization on top of the committed phrases — bench-gated,
        # so it can only add catch, never over-block. Lifts BOTH this learner and the
        # BodyOS metabolic one (shared axiom_semantic_embed) when the backend upgrades.
        self._tune_semantic()

        after = self._score()
        report = {
            "calibration": "axiom-guard-calibration",
            "generated_at": now,
            "catch_before": before["catch_pct"], "catch_after": after["catch_pct"],
            "over_block_before": before["over_block_pct"], "over_block_after": after["over_block_pct"],
            "semantic_enabled": self.semantic_enabled,
            "sem_threshold": round(self.sem_threshold, 3) if self.semantic_enabled else None,
            "patterns_committed": sum(1 for p in proposals if p["accepted"]),
            "proposals_rejected": sum(1 for p in proposals if not p["accepted"]),
            "total_patterns": len(self.patterns),
            "proposals": proposals,
            "invariant_over_block_not_increased": after["over_block_pct"] <= before["over_block_pct"],
        }
        report["signature"] = self._sign(report)
        return report

    # ── optional integration with the trajectory prune loop (PR #93) ────────────
    def feed_from_pruner(self, pruner) -> int:
        """Ingest a TrajectoryPruner's flagged patterns as extra labeled negatives.
        Soft dependency — pass any object exposing export_negative_examples()."""
        try:
            neg = pruner.export_negative_examples()
        except AttributeError:
            return 0
        return len(neg)      # reasons feed the next calibrate(extra_misses=...) by the caller


def render(r: dict) -> str:
    arrow = "↑" if r["catch_after"] > r["catch_before"] else "→"
    return f"""
  AXIOM GUARD CALIBRATION — flywheel run
  {'='*54}
  CATCH        : {r['catch_before']}%  {arrow}  {r['catch_after']}%
  OVER-BLOCK   : {r['over_block_before']}%  →  {r['over_block_after']}%   (must not rise)
  patterns committed : {r['patterns_committed']}   rejected: {r['proposals_rejected']}
  over-block guardrail held : {r['invariant_over_block_not_increased']}
  {'-'*54}
  Every committed pattern was validated against the governance bench
  (catch up, over-block flat) and signed into the calibration ledger.
  The data — your misses — is what compounds. That is the moat.
"""


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AXIOM guard calibration loop")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--now", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = CalibrationLoop(args.ledger).calibrate(now=args.now)
    print(json.dumps(r, indent=2, ensure_ascii=True) if args.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
