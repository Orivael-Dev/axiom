"""
AXIOM Fact-Preservation Loop — verified semantic transformations
================================================================
Turn one verified fact into many safe paraphrases, keeping only the ones that carry the
meaning intact. The unit is an Entity–Relation–Value triple:

    Fact("France", "has capital", "Paris")      # France.capital = Paris

You may reword it freely — "Paris is the capital of France", "France's capital is Paris" —
but you may NOT reverse or mutate the relationship. "France is the capital of Paris" and
"Paris is not the capital of France" must be rejected. The loop is:

    fact → generate paraphrases → VALIDATE each → keep only the clean ones → (signed)

The validator is deterministic and LLM-free (a Layer-0/1 gate): entity present, value
present, relation not reversed, not negated, not topic-drifted. That makes it cheap to run
over thousands of candidates and safe to trust as a training-data filter.

Why it matters (Theme 2 — smaller models / distillation): instead of feeding a small model
a giant ocean of random text, you feed it *verified semantic transformations* — "changing
the wording is allowed; changing the relationship is not." The model learns to carry meaning
without dropping it, which is exactly the behaviour that reduces hallucination.

    loop = TruthPreservationLoop()
    out  = loop.expand(Fact("France", "has capital", "Paris"), n=6)
    out.kept          # clean paraphrases (signed)
    out.rejected      # [(sentence, reasons), ...] — why each was discarded
    out.training_examples()   # jsonl-ready dicts for fine-tuning

Bring your own generator (an LLM) via expand(..., generator=fn); with none, a deterministic
template generator runs so the loop works offline.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-fact-preserve-v1")
except Exception:  # pragma: no cover
    import os
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-fact-preserve-v1", 1)

# Words stripped when deriving the relation's head noun ("has capital" → "capital").
_REL_STOP = {"has", "have", "is", "are", "was", "were", "the", "a", "an", "of", "'s", "its",
             "with", "in", "at", "to", "by", "as"}
# Clear negation cues — a paraphrase asserting the fact must not carry these.
_NEG = [r"\bnot\b", r"n't\b", r"\bnever\b", r"\bno longer\b", r"\bisn't\b", r"\bwasn't\b",
        r"\bdoesn't\b", r"\bdon't\b", r"\bnot the\b", r"\bfalse\b", r"\bincorrect\b"]
_NEG_RE = [re.compile(p, re.IGNORECASE) for p in _NEG]


@dataclass(frozen=True)
class Fact:
    """A verified Entity–Relation–Value triple (France.has_capital = Paris)."""
    entity:   str
    relation: str
    value:    str
    entity_aliases: Tuple[str, ...] = ()
    value_aliases:  Tuple[str, ...] = ()
    # Surface noun forms the relation can take in a paraphrase. Reversal detection keys
    # on these, so supply synonyms when paraphrases won't reuse the relation's own word —
    # e.g. relation "was written by" but paraphrases say "author"/"writer".
    rel_nouns: Tuple[str, ...] = ()

    def rel_noun(self) -> str:
        """Head noun of the relation — 'has capital' → 'capital', 'was written by' → 'written'."""
        toks = [t for t in re.findall(r"[A-Za-z']+", self.relation.lower()) if t not in _REL_STOP]
        return toks[-1] if toks else self.relation.lower().strip()

    def rel_noun_forms(self) -> List[str]:
        """All noun forms to check for role-reversal — explicit rel_nouns, else the derived one."""
        return list(self.rel_nouns) if self.rel_nouns else [self.rel_noun()]

    def canonical(self) -> str:
        return f"{self.entity} {self.relation} {self.value}."

    def entity_terms(self) -> List[str]:
        return [self.entity, *self.entity_aliases]

    def value_terms(self) -> List[str]:
        return [self.value, *self.value_aliases]


@dataclass(frozen=True)
class PreservationVerdict:
    ok:      bool
    reasons: Tuple[str, ...]        # why rejected (empty when ok)
    checks:  dict                   # per-check booleans — the auditable trail


def _present(terms: Sequence[str], text: str) -> bool:
    low = text.lower()
    return any(re.search(rf"\b{re.escape(t.lower())}\b", low) for t in terms if t)


def _reversed_role(fact: Fact, text: str) -> bool:
    """True if the sentence puts the VALUE in the possessor slot of the relation — i.e.
    asserts the reverse fact. Covers the two common directional frames:
        '<rel_noun> of <X>'   and   "<X>'s <rel_noun>"
    Correct: X = entity (capital of France).  Reversed: X = value (capital of Paris)."""
    low = text.lower()
    for rel_form in fact.rel_noun_forms():
        rel = re.escape(rel_form.lower())
        for v in fact.value_terms():
            ve = re.escape(v.lower())
            if re.search(rf"\b{rel}\s+of\s+{ve}\b", low):       # "capital of Paris"
                return True
            if re.search(rf"\b{ve}(?:'s|s')\s+{rel}\b", low):   # "Paris's capital"
                return True
    return False


@dataclass
class FactValidator:
    """Deterministic truth-preservation gate. No LLM — cheap enough to run over every
    candidate. ``semantic_floor`` (optional) rejects paraphrases that drift off-topic."""
    semantic_floor: Optional[float] = None      # e.g. 0.45; None disables the check

    def check(self, fact: Fact, sentence: str) -> PreservationVerdict:
        text = (sentence or "").strip()
        checks, reasons = {}, []

        checks["entity_present"] = _present(fact.entity_terms(), text)
        if not checks["entity_present"]:
            reasons.append(f"dropped the entity ({fact.entity!r})")

        checks["value_present"] = _present(fact.value_terms(), text)
        if not checks["value_present"]:
            reasons.append(f"dropped the value ({fact.value!r})")

        checks["not_reversed"] = not _reversed_role(fact, text)
        if not checks["not_reversed"]:
            reasons.append("reversed the relationship (value put in the possessor role)")

        checks["not_negated"] = not any(r.search(text) for r in _NEG_RE)
        if not checks["not_negated"]:
            reasons.append("negated the fact")

        if self.semantic_floor is not None:
            try:
                from axiom_semantic_embed import similarity
                sim = similarity(text, fact.canonical())
                checks["on_topic"] = sim >= self.semantic_floor
                if not checks["on_topic"]:
                    reasons.append(f"drifted off-topic (sim {sim:.2f} < {self.semantic_floor})")
            except Exception:
                checks["on_topic"] = True     # embedder unavailable → don't over-reject

        return PreservationVerdict(ok=not reasons, reasons=tuple(reasons), checks=checks)


# ── generation ────────────────────────────────────────────────────────────────
_TEMPLATES = [
    "{value} is the {rel} of {entity}.",
    "The {rel} of {entity} is {value}.",
    "{entity}'s {rel} is {value}.",
    "{entity} has {value} as its {rel}.",
    "For {entity}, the {rel} is {value}.",
    "{value} serves as the {rel} of {entity}.",
    "As every atlas notes, the {rel} of {entity} is {value}.",
    "{entity} counts {value} as its {rel}.",
]


def template_generator(fact: Fact, n: int) -> List[str]:
    """Deterministic paraphrases — no API key. Vary the surface, never the roles."""
    rel = fact.rel_noun()
    out = [t.format(value=fact.value, entity=fact.entity, rel=rel) for t in _TEMPLATES]
    if n <= len(out):
        return out[:n]
    reps = -(-n // len(out))            # ceil division — cycle templates to fill n
    return (out * reps)[:n]


# A generator is any callable (fact, n) -> list[str] — e.g. an LLM wrapper.
Generator = Callable[[Fact, int], List[str]]


@dataclass
class ExpansionResult:
    fact:     Fact
    kept:     List[str]
    rejected: List[Tuple[str, Tuple[str, ...]]]      # (sentence, reasons)
    signature: str = ""

    def training_examples(self) -> List[dict]:
        """jsonl-ready fine-tuning rows — only the verified transformations."""
        return [{"entity": self.fact.entity, "relation": self.fact.relation,
                 "value": self.fact.value, "text": s, "label": "truth_preserving"}
                for s in self.kept]

    def to_dict(self) -> dict:
        return {"fact": [self.fact.entity, self.fact.relation, self.fact.value],
                "kept": list(self.kept),
                "rejected": [[s, list(r)] for s, r in self.rejected],
                "signature": self.signature}


@dataclass
class TruthPreservationLoop:
    """generate → validate → keep only clean. The kept set is signed, so a training
    corpus built from it is tamper-evident like the rest of the stack."""
    validator: FactValidator = field(default_factory=FactValidator)

    def expand(self, fact: Fact, n: int = 6, *, generator: Optional[Generator] = None,
               dedup: bool = True) -> ExpansionResult:
        gen = generator or template_generator
        candidates = gen(fact, n)
        seen, kept, rejected = set(), [], []
        for sent in candidates:
            s = (sent or "").strip()
            if dedup and s.lower() in seen:
                continue
            seen.add(s.lower())
            verdict = self.validator.check(fact, s)
            (kept if verdict.ok else rejected).append(s if verdict.ok else (s, verdict.reasons))
        sig = hmac_lib.new(_KEY, json.dumps(
            {"fact": [fact.entity, fact.relation, fact.value], "kept": kept},
            sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(),
            hashlib.sha256).hexdigest()
        return ExpansionResult(fact=fact, kept=kept, rejected=rejected, signature=sig)

    def verify(self, result: ExpansionResult) -> bool:
        want = hmac_lib.new(_KEY, json.dumps(
            {"fact": [result.fact.entity, result.fact.relation, result.fact.value],
             "kept": result.kept}, sort_keys=True, ensure_ascii=True,
            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        return hmac_lib.compare_digest(result.signature, want)


# ── CLI ───────────────────────────────────────────────────────────────────────
def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fact-preservation loop — verified paraphrases")
    p.add_argument("--entity", default="France")
    p.add_argument("--relation", default="has capital")
    p.add_argument("--value", default="Paris")
    p.add_argument("-n", type=int, default=8)
    p.add_argument("--floor", type=float, default=None, help="semantic similarity floor (e.g. 0.45)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    fact = Fact(args.entity, args.relation, args.value)
    loop = TruthPreservationLoop(FactValidator(semantic_floor=args.floor))
    out = loop.expand(fact, args.n)
    if args.json:
        print(json.dumps(out.to_dict(), indent=2))
        return 0
    print(f"Fact: {fact.canonical()}  (relation noun: {fact.rel_noun()})\n")
    print(f"KEPT ({len(out.kept)}) — verified truth-preserving:")
    for s in out.kept:
        print(f"  ✓ {s}")
    print(f"\nREJECTED ({len(out.rejected)}):")
    for s, reasons in out.rejected:
        print(f"  ✗ {s}\n      → {'; '.join(reasons)}")
    print(f"\nsigned {out.signature[:24]}…  verify={loop.verify(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
