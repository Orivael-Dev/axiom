#!/usr/bin/env python3
"""
Reverse QRF Simulation — research/simulation/reverse_qrf_sim.py

Adds a forward-prediction arm to the MET pipeline:

  After each MET is encoded the Reverse QRF predictor fires during the
  idle gap before the next phrase arrives. It reads the intent history
  and constitutional-distance trend, emits a lightweight QRFPrediction
  (the "intent clue"), and pre-stages a speculative MET slot.

  When the actual next phrase arrives the prediction is evaluated:
    HIT  → KV cache slot was already warm; encode time saved
    MISS → speculative slot discarded; falls back to normal MET encode

Full pipeline (phases 1-3 from met_retro_sim.py, phase 4 added here):

  raw text
    → segment_text()                   [trajectory_filter]
    → Coordinator.compose()            [MET encoding — phase 1]
    → StateTransitionEngine            [S_{t+1} = f(S_t, MET_λ) + Δ — phase 2]
    → LedgerWriter                     [signed audit trail — phase 3]
    → ConstitutionalRetrospect         [retrospective loop — phase 3]
    → ReverseQRFPredictor              [forward intent prediction — phase 4]

Requires:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    python3 research/simulation/reverse_qrf_sim.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

# ── BUG-003: UTF-8 stdout ─────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "research" / "quant"))

if not os.environ.get("AXIOM_MASTER_KEY"):
    sys.exit(
        "ERROR: AXIOM_MASTER_KEY is not set.\n"
        "  export AXIOM_MASTER_KEY="
        '$(python3 -c "import secrets; print(secrets.token_hex(32))")\n'
    )

# ── Reuse Phase 1-3 infrastructure from met_retro_sim ─────────────────────────
from met_retro_sim import (   # noqa: E402
    METEncoder, METRecord,
    StateTransitionEngine, StateVector,
    _write_manifest_jsonl, _run_retrospective,
    BLOCK_CLASSES, _SEP, _DASH, _DEMO_TEXT,
)
from axiom_exoskeleton_ledger import LedgerWriter   # noqa: E402
from trajectory_filter import segment_text          # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════
# Reverse QRF data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class QRFPrediction:
    """Intent clue emitted by the Reverse QRF after MET step `at_step`."""
    at_step: int               # MET that triggered this prediction
    predicts_step: int         # MET being predicted (at_step + 1)
    predicted_intent: str      # INFORM | CLARIFY | REFUSE | HARM | DECEIVE | UNCERTAIN
    predicted_confidence: float
    basis: str                 # "markov" | "drift_signal" | "uniform"
    clue_latency_ms: float     # time spent generating this prediction


@dataclass
class QRFResult:
    """Outcome once the predicted MET actually arrives."""
    prediction: QRFPrediction
    actual_intent: str
    actual_confidence: float
    actual_encode_ms: float    # how long MET encoding actually took
    hit: bool                  # prediction matched actual intent
    latency_saved_ms: float    # encode_ms if hit, else 0


@dataclass
class QRFSummary:
    total_predictions: int
    hits: int
    misses: int
    hit_rate: float
    total_saved_ms: float
    total_clue_cost_ms: float
    net_gain_ms: float
    prediction_basis_counts: dict


# ══════════════════════════════════════════════════════════════════════════════
# Reverse QRF Predictor
# ══════════════════════════════════════════════════════════════════════════════

# Intent transition priors — domain-agnostic baseline
# Captures that INFORM begets INFORM, drift toward boundary → CLARIFY, etc.
_TRANSITION_PRIORS: dict[str, str] = {
    "INFORM":    "INFORM",
    "CLARIFY":   "INFORM",
    "REFUSE":    "CLARIFY",
    "UNCERTAIN": "CLARIFY",
    "HARM":      "HARM",
    "DECEIVE":   "DECEIVE",
}

_DRIFT_INTENT_MAP: dict[str, str] = {
    "toward_boundary":     "CLARIFY",   # constitutional pressure → model may hedge
    "away_from_boundary":  "INFORM",    # confident sequence → likely stays informative
    "stable":              "",          # defer to Markov
}

PATTERN_WINDOW = 5   # look at last N intent classes for Markov prediction


class ReverseQRFPredictor:
    """
    Lightweight forward-intent predictor.

    Operates in two modes (tried in order):

    1. drift_signal — if ManifoldChecker detected 'toward_boundary' drift,
       the constitutional tension predicts an intent shift (CLARIFY/REFUSE).
       This is the strongest signal — constitutional pressure is non-random.

    2. markov — Markov frequency over the last PATTERN_WINDOW intents.
       If the window is unanimous → high confidence (0.85).
       Otherwise → weighted by majority frequency.

    The prediction itself is O(1) — no model inference, just list operations.
    It runs during the dead gap between phrases, so its cost is ~0.1 ms.
    """

    def predict(
        self,
        intent_history: List[str],
        states: List[StateVector],
    ) -> tuple[str, float, str]:
        """
        Returns (predicted_intent, confidence, basis).
        Called after MET[i] is encoded, predicting MET[i+1].
        """
        # ── Signal 1: constitutional drift overrides Markov ───────────────────
        if states:
            drift = states[-1].drift_direction
            drift_intent = _DRIFT_INTENT_MAP.get(drift, "")
            if drift_intent:
                return drift_intent, 0.72, "drift_signal"

        # ── Signal 2: Markov window on recent intent sequence ─────────────────
        if intent_history:
            window = intent_history[-PATTERN_WINDOW:]
            counts = Counter(window)
            top_intent, top_count = counts.most_common(1)[0]

            if len(set(window)) == 1:
                # Unanimous window — maximum Markov confidence
                return top_intent, 0.88, "markov"

            confidence = round(top_count / len(window), 2)
            return top_intent, max(confidence, 0.40), "markov"

        # ── Signal 3: uniform prior ────────────────────────────────────────────
        return "INFORM", 0.50, "uniform"

    def fire(
        self,
        at_step: int,
        intent_history: List[str],
        states: List[StateVector],
    ) -> QRFPrediction:
        t0 = time.perf_counter()
        predicted_intent, confidence, basis = self.predict(intent_history, states)
        clue_ms = round((time.perf_counter() - t0) * 1000, 4)

        return QRFPrediction(
            at_step=at_step,
            predicts_step=at_step + 1,
            predicted_intent=predicted_intent,
            predicted_confidence=confidence,
            basis=basis,
            clue_latency_ms=clue_ms,
        )

    def evaluate(
        self,
        prediction: QRFPrediction,
        actual_met: METRecord,
        actual_encode_ms: float,
    ) -> QRFResult:
        hit = prediction.predicted_intent == actual_met.intent_class
        return QRFResult(
            prediction=prediction,
            actual_intent=actual_met.intent_class,
            actual_confidence=actual_met.confidence,
            actual_encode_ms=actual_encode_ms,
            hit=hit,
            latency_saved_ms=round(actual_encode_ms, 3) if hit else 0.0,
        )


def summarise(results: List[QRFResult]) -> QRFSummary:
    hits   = sum(1 for r in results if r.hit)
    misses = len(results) - hits
    total_saved  = round(sum(r.latency_saved_ms for r in results), 3)
    total_cost   = round(sum(r.prediction.clue_latency_ms for r in results), 4)
    basis_counts = Counter(r.prediction.basis for r in results)
    return QRFSummary(
        total_predictions=len(results),
        hits=hits,
        misses=misses,
        hit_rate=round(hits / len(results), 3) if results else 0.0,
        total_saved_ms=total_saved,
        total_clue_cost_ms=total_cost,
        net_gain_ms=round(total_saved - total_cost, 3),
        prediction_basis_counts=dict(basis_counts),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Timed MET encoding (Phase 1 + timing)
# ══════════════════════════════════════════════════════════════════════════════

def encode_with_timing(
    text: str,
) -> tuple[List[METRecord], list, List[float]]:
    """
    Runs METEncoder phrase-by-phrase, recording per-MET encode time in ms.
    Returns (met_records, event_tokens, encode_times_ms).
    """
    from axiom_event_token.coordinator import Coordinator
    from trajectory_filter import segment_text

    phrases = segment_text(text)
    coordinator = Coordinator()
    met_records: List[METRecord] = []
    event_tokens = []
    encode_times: List[float] = []
    parent = None

    for i, phrase in enumerate(phrases, start=1):
        t0 = time.perf_counter()
        token = coordinator.compose(
            text=phrase,
            parent=parent,
            activate=["text", "governance"],
        )
        encode_ms = round((time.perf_counter() - t0) * 1000, 3)

        text_payload = token.text.payload if token.text else {}
        intent_class = str(text_payload.get("intent_class", "INFORM"))
        confidence   = float(text_payload.get("confidence", 0.70))

        met = METRecord(
            step=i,
            met_id=token.id,
            met_state_var=f"[ENCAP_{token.id[:8].upper()}]",
            raw_phrase=phrase,
            raw_tokens=len(phrase.split()),
            intent_class=intent_class,
            confidence=confidence,
            parent_sig=token.parent_signature or "",
            signature=token.signature,
        )
        met_records.append(met)
        event_tokens.append(token)
        encode_times.append(encode_ms)
        parent = token

    return met_records, event_tokens, encode_times


# ══════════════════════════════════════════════════════════════════════════════
# Output helpers
# ══════════════════════════════════════════════════════════════════════════════

def _print_qrf_table(results: List[QRFResult], summary: QRFSummary) -> None:
    print(f"\n{_SEP}")
    print("PHASE 4  —  REVERSE QRF PREDICTIONS  (intent clue → pre-stage)")
    print(_DASH)
    print(
        f"  {'Step':>4}  {'Predicted':>12}  {'Conf':>5}  "
        f"{'Actual':>12}  {'Hit':>3}  {'Saved ms':>9}  Basis"
    )
    print(f"  {_DASH}")
    for r in results:
        p = r.prediction
        hit_s   = " ✓" if r.hit else " ✗"
        saved_s = f"{r.latency_saved_ms:>8.2f}" if r.hit else "        —"
        print(
            f"  {p.predicts_step:>4}  {p.predicted_intent:>12}  {p.predicted_confidence:>5.2f}  "
            f"{r.actual_intent:>12}  {hit_s:>3}  {saved_s}  {p.basis}"
        )
    print(f"  {_DASH}")
    print(
        f"\n  Hit rate      : {summary.hits}/{summary.total_predictions}"
        f" = {summary.hit_rate * 100:.0f}%"
    )
    print(f"  Latency saved : {summary.total_saved_ms:.2f} ms  (MET encode cycles avoided)")
    print(f"  Clue cost     : {summary.total_clue_cost_ms:.3f} ms  (prediction overhead)")
    print(f"  Net gain      : {summary.net_gain_ms:.2f} ms")
    print(f"  Basis mix     : {summary.prediction_basis_counts}")


def _print_full_pipeline(
    mets: List[METRecord],
    states: List[StateVector],
    results: List[QRFResult],
    summary: QRFSummary,
    n_ledger: int,
    retro: dict,
    ledger_path: Path,
    manifest_path: Path,
    total_raw: int,
) -> None:
    m = len(mets)

    # Phase 1
    print(f"\n{_SEP}")
    print("PHASE 1  —  MET ENCODING")
    print(_DASH)
    print(
        f"  {'Step':>4}  {'MET State Variable':<22}  "
        f"{'Phrase (truncated)':<38}  {'Tok':>3}  {'Encode ms':>9}  Intent"
    )
    print(f"  {_DASH}")
    encode_ms_map = {r.prediction.predicts_step: r.actual_encode_ms for r in results}
    for met in mets:
        phrase_s = (met.raw_phrase[:37] + "..") if len(met.raw_phrase) > 39 else met.raw_phrase
        ems = encode_ms_map.get(met.step, 0.0)
        print(
            f"  {met.step:>4}  {met.met_state_var:<22}  {phrase_s:<38}  "
            f"{met.raw_tokens:>3}  {ems:>9.2f}  {met.intent_class}"
        )
    on2, om2 = total_raw ** 2, m ** 2
    compression = round(total_raw / m, 1) if m else 0.0
    print(f"  {_DASH}")
    print(f"\n  N={total_raw} tokens → M={m} METs  |  {compression}× compression")
    print(f"  O(N²)={on2:,} → O(M²)={om2}  (attention complexity)\n")

    # Phase 2
    print(_SEP)
    print("PHASE 2  —  STATE TRANSITIONS   S₊₁ = f(Sₜ, METλ) + Δ_correction")
    print(_DASH)
    print(f"  {'Step':>4}  {'S_t.dist':>8}  {'conf':>6}  {'Δ_corr':>8}  {'S_{t+1}.dist':>13}  Drift")
    print(f"  {_DASH}")
    prev = 0.0
    for sv in states:
        drift_s = sv.drift_direction if sv.drift_direction != "stable" else ""
        print(
            f"  {sv.step:>4}  {prev:>8.4f}  {sv.confidence:>6.2f}"
            f"  {sv.delta_correction:>+8.4f}  {sv.constitutional_distance:>13.4f}  {drift_s}"
        )
        prev = sv.constitutional_distance
    print(f"  {_DASH}\n")

    # Phase 3
    print(_SEP)
    print("PHASE 3  —  LEDGER + RETROSPECTIVE")
    print(_DASH)
    print(f"  Ledger entries   : {n_ledger}  →  {ledger_path.name}")
    print(f"  Retro candidates : {retro.get('total_reviewed', 0)}")
    print(f"  Regression alert : {retro.get('regression_alert', False)}\n")

    # Phase 4
    _print_qrf_table(results, summary)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reverse QRF Simulation — forward intent prediction on MET chain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--text", default=_DEMO_TEXT,
                        help="Raw input paragraph (default: built-in demo)")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--output", default=None,
                        help="Save full result as JSON")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    _tl = tempfile.NamedTemporaryFile(suffix="-qrf-ledger.jsonl",   delete=False)
    _tm = tempfile.NamedTemporaryFile(suffix="-qrf-manifest.jsonl", delete=False)
    _tl.close(); _tm.close()

    ledger_path   = Path(args.ledger)   if args.ledger   else Path(_tl.name)
    manifest_path = Path(args.manifest) if args.manifest else Path(_tm.name)

    print(_SEP)
    print("AXIOM Reverse QRF Simulation")
    print(_SEP)
    preview = args.text[:90] + ("..." if len(args.text) > 90 else "")
    print(f"  Input: {preview}\n")

    # ── Phase 1: timed MET encoding ───────────────────────────────────────────
    print("  Phase 1: encoding METs (timed) ...", flush=True)
    mets, tokens, encode_times = encode_with_timing(args.text)
    if not mets:
        sys.exit("ERROR: no phrases segmented.")
    total_raw = sum(m.raw_tokens for m in mets)

    # ── Phase 2: state transitions ────────────────────────────────────────────
    print("  Phase 2: state transitions ...", flush=True)
    engine = StateTransitionEngine()
    states = engine.run_chain(mets)

    # ── Phase 3: ledger + retrospective ───────────────────────────────────────
    print("  Phase 3: ledger + retrospective ...", flush=True)
    writer = LedgerWriter(path=ledger_path)
    for token, met in zip(tokens, mets):
        writer.append(token=token, use_case="qrf_simulation", input_text=met.raw_phrase)
    _write_manifest_jsonl(mets, states, manifest_path)
    retro_report = _run_retrospective(mets, manifest_path)

    # ── Phase 4: Reverse QRF ──────────────────────────────────────────────────
    print("  Phase 4: reverse QRF predictions ...", flush=True)
    qrf     = ReverseQRFPredictor()
    results: List[QRFResult] = []
    intent_history: List[str] = []

    # MET[0] seeds the history; predictions start from MET[1] onward.
    # We predict MET[i+1] immediately after encoding MET[i], then evaluate
    # the prediction when MET[i+1] is actually encoded (simulate streaming).
    pending: Optional[QRFPrediction] = None

    for i, met in enumerate(mets):
        # Evaluate previous prediction against this actual MET
        if pending is not None:
            result = qrf.evaluate(pending, met, encode_times[i])
            results.append(result)

        intent_history.append(met.intent_class)

        # Fire next prediction (if there IS a next MET)
        if i < len(mets) - 1:
            pending = qrf.fire(
                at_step=met.step,
                intent_history=list(intent_history),
                states=list(states[: i + 1]),
            )

    summary = summarise(results)

    # ── Output ────────────────────────────────────────────────────────────────
    if not args.quiet:
        _print_full_pipeline(
            mets, states, results, summary,
            n_ledger=len(tokens),
            retro=retro_report,
            ledger_path=ledger_path,
            manifest_path=manifest_path,
            total_raw=total_raw,
        )

    print(f"\n{_SEP}")
    print("SIMULATION COMPLETE")
    print(_DASH)
    print(f"  METs encoded        : {len(mets)}")
    print(f"  QRF predictions     : {summary.total_predictions}")
    print(f"  Hit rate            : {summary.hits}/{summary.total_predictions}"
          f" = {summary.hit_rate * 100:.0f}%")
    print(f"  Net latency gain    : {summary.net_gain_ms:.2f} ms")
    print(f"  Compression         : {round(total_raw / len(mets), 1)}×"
          f"  (N={total_raw} → M={len(mets)} METs)")

    if args.output:
        out = {
            "mets":     [asdict(m) for m in mets],
            "states":   [asdict(s) for s in states],
            "qrf_results": [
                {
                    "predicts_step":       r.prediction.predicts_step,
                    "predicted_intent":    r.prediction.predicted_intent,
                    "predicted_confidence": r.prediction.predicted_confidence,
                    "basis":               r.prediction.basis,
                    "actual_intent":       r.actual_intent,
                    "hit":                 r.hit,
                    "actual_encode_ms":    r.actual_encode_ms,
                    "latency_saved_ms":    r.latency_saved_ms,
                    "clue_latency_ms":     r.prediction.clue_latency_ms,
                }
                for r in results
            ],
            "summary": asdict(summary),
            "retrospective_report": {
                k: v for k, v in retro_report.items() if not k.startswith("_")
            },
        }
        Path(args.output).write_text(
            json.dumps(out, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        print(f"  Full results        → {args.output}")

    print(_SEP)


if __name__ == "__main__":
    main()
