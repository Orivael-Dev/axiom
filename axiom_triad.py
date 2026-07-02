"""
AXIOM Triad — Child / Mom / Best Friend, with the Bounce loop
=============================================================
Operationalizes the Multi-Agent Triad architecture (balancing logic & imagination) on
top of Axiom's layers. The insight from the design: don't force one model's weights to
be both a rigid logician and a high-entropy dreamer — split the cognitive load across
three roles and let a *bounce* loop shift the probability mass toward valid-and-creative.

  • Child (Explorer)      — high-entropy generator. Proposes candidates, makes imaginative
                            leaps by manipulating the foundational units. (Layer 1/3)
  • Mom (Grounding Engine)— low-entropy LOGIC GATE. Reviews the Child's output against
                            foundational rules. She does NOT rewrite it — when a leap
                            breaks the logic she issues a hard boundary and reflects it
                            back. This is Axiom's Layer-4 Governance, and FactGuard is her
                            fact-integrity check.
  • Best Friend (Evaluator)— reward model. Scores the utility/creativity of ACCEPTED
                            candidates to lock in the best pathway. (Layer 5)

The Bounce (the autonomous iterative loop):
    1. The Attempt      — Child strings together a high-entropy sequence.
    2. The Logic Check  — Mom evaluates it against baseline logic (facts, safety).
    3. The Bounce       — if logic is broken, the pathway is penalized; Mom flags it
                          "too dangerous, try another way" and reflects the boundary back.
    4. The Recalculation— Child re-attempts; the previous path is penalized (fed back as
                          an explicit constraint), so the distribution shifts toward a new
                          alternative. Logic is bent, not broken.

A black-box/API model can't have its weights literally re-penalized, so the "weight
penalty" is realized honestly in software: the failed path + Mom's boundary are fed back
into the next prompt as a hard constraint, which shifts the model's next sampling. The
loop, the gate, and the reflected boundaries are the mechanism.

    triad = TriadLoop(child=my_llm, mom=MomGate(facts=[Fact("France","has capital","Paris")]))
    out = triad.run("Explain France's capital creatively.")
    out.output        # first candidate that passed the logic gate (best-scored)
    out.bounces       # the rejected attempts + the boundary Mom reflected for each
    out.accepted      # did any attempt pass?
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axiom_fact_preserve import Fact, FactGuard, GuardReport

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-triad-v1")
except Exception:  # pragma: no cover
    import os
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-triad-v1", 1)


# ── the Mom: Layer-4 logic gate ───────────────────────────────────────────────
# A rule is a callable (candidate) -> (ok, boundary_message). It never mutates the text;
# on failure it returns the hard boundary Mom reflects back to the Child.
Rule = Callable[[str], Tuple[bool, str]]


@dataclass(frozen=True)
class MomVerdict:
    ok:         bool
    boundaries: Tuple[str, ...]        # the "try another way" feedback (empty when ok)
    fact_report: Optional[dict] = None


@dataclass
class MomGate:
    """The Grounding Engine — Axiom Layer 4. Composes a fact-integrity check (FactGuard)
    with any number of extra rules (intent gate, cognition verdict, policy). Low entropy:
    it only ever says 'this holds' or 'this breaks rule X — try another way'."""
    facts: List[Fact] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)      # e.g. an intent/safety gate

    def __post_init__(self):
        self._guard = FactGuard(list(self.facts))

    def add_rule(self, rule: Rule) -> "MomGate":
        self.rules.append(rule)
        return self

    def review(self, candidate: str) -> MomVerdict:
        boundaries: List[str] = []
        report: GuardReport = self._guard.check_output(candidate)
        if not report.ok:
            for v in report.violations:
                boundaries.append(f"[{v.kind}] {v.reason} — keep the fact intact.")
        for rule in self.rules:
            try:
                ok, msg = rule(candidate)
                if not ok:
                    boundaries.append(msg or "violates a governance rule — try another way.")
            except Exception:
                continue
        return MomVerdict(ok=not boundaries, boundaries=tuple(boundaries),
                          fact_report=report.to_dict())


# ── the Child (generator) and Best Friend (evaluator) contracts ───────────────
# Child: (task, boundaries_so_far, attempt_index) -> candidate text.
Child = Callable[[str, List[str], int], str]
# Best Friend: (candidate) -> score (higher = better utility/creativity).
BestFriend = Callable[[str], float]


def _default_best_friend(candidate: str) -> float:
    """Cheap stand-in reward: reward informative, lexically varied answers, lightly. A real
    deployment plugs in axiom_crl (Constitutional RL reward) here."""
    words = candidate.split()
    if not words:
        return 0.0
    variety = len(set(w.lower() for w in words)) / len(words)
    length = min(len(words), 60) / 60.0
    return round(0.6 * variety + 0.4 * length, 4)


@dataclass(frozen=True)
class BounceRecord:
    attempt:    int
    candidate:  str
    boundaries: Tuple[str, ...]         # why Mom bounced it


@dataclass
class TriadResult:
    task:      str
    output:    str                       # best accepted candidate ("" if none passed)
    accepted:  bool
    score:     float
    bounces:   List[BounceRecord]
    attempts:  int
    signature: str = ""

    def to_dict(self) -> dict:
        return {"task": self.task, "output": self.output, "accepted": self.accepted,
                "score": self.score, "attempts": self.attempts,
                "bounces": [{"attempt": b.attempt, "candidate": b.candidate,
                             "boundaries": list(b.boundaries)} for b in self.bounces],
                "signature": self.signature}


@dataclass
class TriadLoop:
    """Runs the Child → Mom → (bounce) → Best Friend loop."""
    child: Child
    mom:   MomGate
    best_friend: BestFriend = _default_best_friend
    max_bounces: int = 4                 # how many recalculations before giving up

    def run(self, task: str) -> TriadResult:
        boundaries: List[str] = []       # accumulated "try another way" constraints
        bounces: List[BounceRecord] = []
        best_text, best_score, accepted = "", -1.0, False

        for attempt in range(self.max_bounces + 1):
            candidate = (self.child(task, list(boundaries), attempt) or "").strip()
            verdict = self.mom.review(candidate)
            if verdict.ok:
                score = float(self.best_friend(candidate))
                accepted = True
                if score > best_score:
                    best_text, best_score = candidate, score
                break                     # first valid pathway wins; Best Friend scored it
            # The Bounce: penalize this path, reflect the boundary, recalculate.
            bounces.append(BounceRecord(attempt, candidate, verdict.boundaries))
            for b in verdict.boundaries:
                if b not in boundaries:
                    boundaries.append(b)

        res = TriadResult(task=task, output=best_text, accepted=accepted,
                          score=max(best_score, 0.0), bounces=bounces,
                          attempts=len(bounces) + (1 if accepted else 0))
        res.signature = self._sign(res)
        return res

    @staticmethod
    def _sign(res: TriadResult) -> str:
        payload = {"task": res.task, "output": res.output, "accepted": res.accepted,
                   "bounces": [[b.attempt, b.candidate, list(b.boundaries)] for b in res.bounces]}
        return hmac_lib.new(_KEY, json.dumps(payload, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    def verify(self, res: TriadResult) -> bool:
        return hmac_lib.compare_digest(res.signature, self._sign(res))


# ── CLI demo — a scripted "Child" that first mutates a fact, then recovers ─────
def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Axiom Triad — Child/Mom/Best Friend bounce loop")
    p.add_argument("--task", default="State France's capital, creatively.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    # A demo Child: attempt 0 breaks the fact (reversed); after Mom bounces it, it recovers.
    drafts = [
        "France is the capital of Paris, a bold reframing.",   # broken → bounce
        "Paris, the City of Light, is the capital of France.",  # valid → accepted
    ]
    def child(task, boundaries, attempt):
        return drafts[min(attempt, len(drafts) - 1)]

    mom = MomGate(facts=[Fact("France", "has capital", "Paris")])
    res = TriadLoop(child=child, mom=mom).run(args.task)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
        return 0
    print(f"Task: {res.task}\n")
    for b in res.bounces:
        print(f"  ✗ attempt {b.attempt}: {b.candidate}")
        for x in b.boundaries:
            print(f"        ↩ Mom: {x}")
    print(f"\n  ✓ accepted: {res.output}")
    print(f"    score={res.score}  attempts={res.attempts}  signed verify={TriadLoop(child, mom).verify(res)}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
