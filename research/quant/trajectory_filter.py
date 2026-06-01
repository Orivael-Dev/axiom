"""Constitutional trajectory filter for generated text.

Treats a model's generation as a *trajectory* of reasoning steps and runs
each step through the ORVL-016 intent gate — the same ``IntentClassifier``
that backs the ``axiom_intent_gate_check`` MCP tool. Steps that are noise get
dropped, leaving the substantive answer:

  • **repeat** — degenerate looping (the #1 failure mode of small models:
    the same line/sentence emitted over and over)
  • **blocked** — a step the gate classifies HARM or DECEIVE
  • **low_signal** — UNCERTAIN filler (only dropped when ``drop_uncertain``)

Every step keeps its signed verdict (HMAC over the classifier output), so the
cleaned text comes with a per-step audit trail, not just a smaller string.

Pure-Python: no torch / transformers import, so it is cheap to unit-test and
can run on the generation output of any backend, not just ``axm run``.

CLI smoke test:
    python -m research.quant.trajectory_filter --text "Step. Step. Step. Step."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── segmentation ─────────────────────────────────────────────────────────────

# Fenced code blocks are atomic steps — never split or sentence-break them.
_FENCE = re.compile(r"```.*?```", re.DOTALL)
# Sentence boundary: terminator + space + capital/digit start of next sentence.
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Normalize a step for repetition comparison (case/space-insensitive)."""
    return _WS.sub(" ", s.strip().lower())


def _segment_prose(chunk: str) -> List[str]:
    out: List[str] = []
    for line in chunk.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        # Break long prose lines into sentence-level steps.
        out.extend(p.strip() for p in _SENT.split(line) if p.strip())
    return out


def segment_text(text: str) -> List[str]:
    """Split generated text into trajectory steps, keeping code fences atomic."""
    steps: List[str] = []
    pos = 0
    for m in _FENCE.finditer(text):
        steps.extend(_segment_prose(text[pos:m.start()]))
        steps.append(m.group(0).strip())
        pos = m.end()
    steps.extend(_segment_prose(text[pos:]))
    return [s for s in steps if s.strip()]


def _reassemble(kept: List[str]) -> str:
    """Flow kept prose steps with spaces; keep code fences on their own lines."""
    out: List[str] = []
    buf: List[str] = []          # pending prose run
    for s in kept:
        if s.startswith("```"):
            if buf:
                out.append(_WS.sub(" ", " ".join(buf)).strip())
                buf = []
            out.append(s)
        else:
            buf.append(s)
    if buf:
        out.append(_WS.sub(" ", " ".join(buf)).strip())
    return "\n".join(p for p in out if p).strip()


# ── result types ─────────────────────────────────────────────────────────────

@dataclass
class StepVerdict:
    step: str
    intent_class: str
    confidence: float
    kept: bool
    reason: str          # "kept" | "repeat" | "blocked" | "low_signal"
    signature: str


@dataclass
class CleanResult:
    cleaned_text: str
    n_steps: int
    n_kept: int
    n_dropped: int
    blocked: bool                    # any HARM/DECEIVE step seen
    dropped_reasons: dict            # reason -> count
    steps: List[StepVerdict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Keep the per-step audit but trim step text in the JSON for size.
        return d


# ── core ─────────────────────────────────────────────────────────────────────

_MCP_GATE_SALT = b"axiom-intent-gate-mcp-v1"   # match _handle_intent_gate_check


def _build_classifier(key: Optional[bytes] = None):
    from axiom_intent_classifier import IntentClassifier
    from axiom_signing import derive_key
    return IntentClassifier(key or derive_key(_MCP_GATE_SALT))


def clean_generation(
    text: str,
    *,
    drop_uncertain: bool = False,
    max_repeats: int = 1,
    classifier=None,
    key: Optional[bytes] = None,
) -> CleanResult:
    """Filter noise steps out of a generation via the ORVL-016 intent gate.

    Args:
        text:           the raw generated string.
        drop_uncertain: also drop UNCERTAIN low-signal filler steps.
        max_repeats:    how many times an identical step may appear before
                        further repeats are dropped as looping (default 1 —
                        i.e. the first occurrence is kept, the rest dropped).
        classifier/key: override the gate (for tests / custom signing key).
    """
    classifier = classifier or _build_classifier(key)
    steps = segment_text(text)

    verdicts: List[StepVerdict] = []
    kept_steps: List[str] = []
    seen: dict = {}
    dropped_reasons: dict = {}
    any_blocked = False

    for step in steps:
        res = classifier.classify(step)
        norm = _norm(step)
        seen[norm] = seen.get(norm, 0) + 1

        reason = "kept"
        kept = True
        if res.blocks:
            kept, reason, any_blocked = False, "blocked", True
        elif seen[norm] > max_repeats:
            kept, reason = False, "repeat"
        elif drop_uncertain and res.intent_class == "UNCERTAIN":
            kept, reason = False, "low_signal"

        if kept:
            kept_steps.append(step)
        else:
            dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1

        verdicts.append(StepVerdict(
            step=step,
            intent_class=res.intent_class,
            confidence=round(res.confidence, 4),
            kept=kept,
            reason=reason,
            signature=res.signature,
        ))

    return CleanResult(
        cleaned_text=_reassemble(kept_steps),
        n_steps=len(steps),
        n_kept=len(kept_steps),
        n_dropped=len(steps) - len(kept_steps),
        blocked=any_blocked,
        dropped_reasons=dropped_reasons,
        steps=verdicts,
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Filter noise steps from generated text via the intent gate")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="raw generated text")
    src.add_argument("--file", type=Path, help="read text from a file")
    p.add_argument("--drop-uncertain", action="store_true")
    p.add_argument("--json", action="store_true", help="emit the full report as JSON")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    text = args.text if args.text is not None else args.file.read_text()
    result = clean_generation(text, drop_uncertain=args.drop_uncertain)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))
        return 0
    print(f"steps: {result.n_steps}  kept: {result.n_kept}  "
          f"dropped: {result.n_dropped}  {result.dropped_reasons or ''}")
    if result.blocked:
        print("⚠️  at least one step was BLOCKED (HARM/DECEIVE)")
    print("\n── cleaned ─────────────────────────────────────────────────")
    print(result.cleaned_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
