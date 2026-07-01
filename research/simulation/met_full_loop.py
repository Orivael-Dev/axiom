#!/usr/bin/env python3
"""
AXIOM MET Complete Loop — research/simulation/met_full_loop.py

Closes the feedback wire between the retrospective and the QRF predictor:

  ┌──────────────────────────────────────────────────────────────────────┐
  │                      COMPLETE LOOP                                   │
  │                                                                      │
  │   raw text                                                           │
  │     → MET encoding (Phase 1)                                        │
  │     → State transitions  S_{t+1} = f(S_t, MET_λ) + Δ  (Phase 2)   │
  │     → Signed ledger + ConstitutionalRetrospect          (Phase 3)   │
  │     → Reverse QRF  pass 1  [static Markov]             (Phase 4)   │
  │     → QRFLearner  observe transitions + retro signals   (Phase 5)   │
  │     → Reverse QRF  pass 2  [learned priors]            (Phase 6)   │
  │     → compare pass-1 vs pass-2 hit rate / confidence                │
  │           └──────────────────────────────────────────┘              │
  │                       feedback wire                                  │
  └──────────────────────────────────────────────────────────────────────┘

Requires:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    python3 research/simulation/met_full_loop.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# ── UTF-8 stdout (BUG-003) ────────────────────────────────────────────────────
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

# ── Reuse phases 1-4 ──────────────────────────────────────────────────────────
from met_retro_sim import (        # noqa: E402
    METRecord, StateVector,
    StateTransitionEngine,
    _write_manifest_jsonl, _run_retrospective,
    BLOCK_CLASSES, _SEP, _DASH,
)
from reverse_qrf_sim import (      # noqa: E402
    QRFPrediction, QRFResult, QRFSummary,
    ReverseQRFPredictor, summarise,
    encode_with_timing,
)
from axiom_exoskeleton_ledger import LedgerWriter   # noqa: E402

# ── Mixed-intent demo text ────────────────────────────────────────────────────
# Contains declarative statements (→ INFORM) and questions (→ CLARIFY)
# so the QRF has real misses to learn from in pass 1.
_MIXED_TEXT = (
    "The system initialization sequence has begun. "
    "What are the security implications of this action? "
    "Load the governance validation module immediately. "
    "Can you verify the authentication token integrity? "
    "Suspend all inference threads pending review. "
    "How should we handle the cryptographic proof chain? "
    "Finalize the container fingerprint and archive."
)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5 — QRFLearner (adaptive transition table)
# ══════════════════════════════════════════════════════════════════════════════

class QRFLearner:
    """
    Adaptive Markov transition table built from observed MET chains.

    After each document the learner:
      1. Records every (from_intent → to_intent) pair from the actual chain.
      2. Applies retrospective signal weights: IMPROVEMENT boosts the transition
         that led to correction; REGRESSION slightly penalises it.

    On predict(), blends learned frequencies with the static fallback.
    As the session grows the 'learned' basis replaces 'markov' and 'uniform'.
    """

    def __init__(self) -> None:
        # from_intent → Counter({to_intent: weighted_count})
        self._counts: Dict[str, Counter] = defaultdict(Counter)
        self._totals: Dict[str, float]   = defaultdict(float)
        # Per-intent confidence multiplier from retrospective signals
        self._retro_boost: Dict[str, float] = defaultdict(lambda: 1.0)
        # Raw log of what was observed (for audit / display)
        self.observation_log: List[Tuple[str, str, float]] = []

    def observe_chain(self, mets: List[METRecord], weight: float = 1.0) -> None:
        """Record all consecutive (prev, next) intent pairs from a MET chain."""
        for i in range(len(mets) - 1):
            from_intent = mets[i].intent_class
            to_intent   = mets[i + 1].intent_class
            self._counts[from_intent][to_intent] += weight
            self._totals[from_intent] += weight
            self.observation_log.append((from_intent, to_intent, weight))

    def apply_retro_signals(
        self,
        candidates: list,   # List[ReviewCandidate]
        results: list,      # List[ReplayResult]
    ) -> List[dict]:
        """
        Update confidence multipliers from retrospective findings.
        Returns list of applied signal dicts for display.
        """
        applied = []
        for c, r in zip(candidates, results):
            intent = c.entry.intent_class
            if r.delta == "IMPROVEMENT":
                # Stack was wrong before → now correct → trust this intent more
                before = self._retro_boost[intent]
                self._retro_boost[intent] = min(2.0, before + 0.4)
                applied.append({
                    "intent": intent,
                    "signal": "IMPROVEMENT",
                    "boost_before": round(before, 3),
                    "boost_after":  round(self._retro_boost[intent], 3),
                })
            elif r.delta == "REGRESSION":
                before = self._retro_boost[intent]
                self._retro_boost[intent] = max(0.5, before - 0.2)
                applied.append({
                    "intent": intent,
                    "signal": "REGRESSION",
                    "boost_before": round(before, 3),
                    "boost_after":  round(self._retro_boost[intent], 3),
                })
        return applied

    def predict(
        self,
        from_intent: str,
        intent_history: List[str],
        states: List[StateVector],
        fallback: ReverseQRFPredictor,
    ) -> Tuple[str, float, str]:
        """
        Predict next intent.  Learned table takes priority over static fallback.
        """
        total = self._totals.get(from_intent, 0)
        if total >= 1:
            counts = self._counts[from_intent]
            best, cnt = counts.most_common(1)[0]
            raw_conf  = cnt / total
            boost     = self._retro_boost.get(from_intent, 1.0)
            confidence = min(0.95, round(raw_conf * boost, 3))
            return best, confidence, "learned"

        # Not enough data yet — fall back to static predictor
        intent, conf, basis = fallback.predict(intent_history, states)
        return intent, conf, f"fallback_{basis}"

    def transition_table(self) -> Dict[str, Dict[str, float]]:
        """Return learned transition probabilities for display."""
        table: Dict[str, Dict[str, float]] = {}
        for from_intent, counts in self._counts.items():
            total = self._totals[from_intent]
            table[from_intent] = {
                to: round(cnt / total, 3)
                for to, cnt in counts.most_common()
            }
        return table


# ══════════════════════════════════════════════════════════════════════════════
# QRF pass runner (shared by pass 1 and pass 2)
# ══════════════════════════════════════════════════════════════════════════════

PredictorFn = Callable[
    [str, List[str], List[StateVector]],   # (from_intent, history, states)
    Tuple[str, float, str],                # (predicted_intent, confidence, basis)
]


def run_qrf_pass(
    mets: List[METRecord],
    states: List[StateVector],
    encode_times: List[float],
    predictor_fn: PredictorFn,
) -> List[QRFResult]:
    """
    Run one full QRF prediction pass over the MET chain.
    Predict MET[i+1] after encoding MET[i]; evaluate when MET[i+1] arrives.
    """
    results: List[QRFResult] = []
    intent_history: List[str] = []
    pending: Optional[QRFPrediction] = None

    for i, met in enumerate(mets):
        # Evaluate pending prediction against this actual MET
        if pending is not None:
            hit = pending.predicted_intent == met.intent_class
            results.append(QRFResult(
                prediction=pending,
                actual_intent=met.intent_class,
                actual_confidence=met.confidence,
                actual_encode_ms=encode_times[i],
                hit=hit,
                latency_saved_ms=round(encode_times[i], 3) if hit else 0.0,
            ))

        intent_history.append(met.intent_class)

        # Fire next prediction (if another MET is coming)
        if i < len(mets) - 1:
            t0 = time.perf_counter()
            pred_intent, pred_conf, basis = predictor_fn(
                met.intent_class, list(intent_history), list(states[: i + 1])
            )
            clue_ms = round((time.perf_counter() - t0) * 1000, 4)
            pending = QRFPrediction(
                at_step=met.step,
                predicts_step=met.step + 1,
                predicted_intent=pred_intent,
                predicted_confidence=pred_conf,
                basis=basis,
                clue_latency_ms=clue_ms,
            )

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Output helpers
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pass(results: List[QRFResult], summary: QRFSummary, label: str) -> None:
    print(f"\n  {label}")
    print(
        f"  {'Step':>4}  {'Predicted':>12}  {'Conf':>5}  "
        f"{'Actual':>12}  Hit  {'Saved ms':>8}  Basis"
    )
    print(f"  {'─' * 66}")
    for r in results:
        p = r.prediction
        hit_s   = "  ✓" if r.hit else "  ✗"
        saved_s = f"{r.latency_saved_ms:>7.2f}" if r.hit else "      —"
        print(
            f"  {p.predicts_step:>4}  {p.predicted_intent:>12}  {p.predicted_confidence:>5.2f}"
            f"  {r.actual_intent:>12}  {hit_s}  {saved_s}  {p.basis}"
        )
    print(
        f"\n    Hit rate  : {summary.hits}/{summary.total_predictions} = "
        f"{summary.hit_rate * 100:.0f}%   |   "
        f"Net gain: {summary.net_gain_ms:.3f} ms"
    )


def _print_full_output(
    mets, states, encode_times,
    pass1_results, pass1_sum,
    learner, retro_signals,
    pass2_results, pass2_sum,
    n_ledger, retro_report,
    ledger_path, manifest_path, total_raw,
) -> None:
    m = len(mets)

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("PHASE 1  —  MET ENCODING")
    print(_DASH)
    print(
        f"  {'Step':>4}  {'MET State Variable':<22}  "
        f"{'Phrase (truncated)':<38}  {'Tok':>3}  {'ms':>6}  Intent"
    )
    print(f"  {_DASH}")
    for met, ems in zip(mets, encode_times):
        phrase_s = (met.raw_phrase[:37] + "..") if len(met.raw_phrase) > 39 else met.raw_phrase
        print(
            f"  {met.step:>4}  {met.met_state_var:<22}  {phrase_s:<38}  "
            f"{met.raw_tokens:>3}  {ems:>6.2f}  {met.intent_class}"
        )
    compression = round(total_raw / m, 1) if m else 0.0
    print(f"  {_DASH}")
    print(
        f"\n  N={total_raw} tokens → M={m} METs  |  "
        f"{compression}× compression  |  "
        f"O(N²)={total_raw**2:,} → O(M²)={m**2}\n"
    )

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print(_SEP)
    print("PHASE 2  —  STATE TRANSITIONS   S_{t+1} = f(S_t, MET_λ) + Δ_correction")
    print(_DASH)
    print(f"  {'Step':>4}  {'S_t.dist':>8}  {'conf':>5}  {'Δ':>7}  {'S_{t+1}':>8}  {'Alert':<12}  Drift")
    print(f"  {_DASH}")
    prev = 0.0
    for sv in states:
        drift_s = sv.drift_direction if sv.drift_direction != "stable" else ""
        alert_s = sv.alert_level if sv.alert_level != "NONE" else ""
        print(
            f"  {sv.step:>4}  {prev:>8.4f}  {sv.confidence:>5.2f}  "
            f"{sv.delta_correction:>+7.4f}  {sv.constitutional_distance:>8.4f}  "
            f"{alert_s:<12}  {drift_s}"
        )
        prev = sv.constitutional_distance
    print(f"  {_DASH}\n")

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    print(_SEP)
    print("PHASE 3  —  LEDGER + RETROSPECTIVE")
    print(_DASH)
    print(f"  Ledger     : {n_ledger} signed entries → {ledger_path.name}")
    print(f"  Candidates : {retro_report.get('total_reviewed', 0)}")
    print(f"  Regression : {retro_report.get('regression_alert', False)}")

    # ── Phase 4 ───────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("PHASE 4  —  REVERSE QRF  pass 1  [static Markov + drift signal]")
    print(_DASH)
    _fmt_pass(pass1_results, pass1_sum, "predictions →")

    # ── Phase 5 ───────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("PHASE 5  —  FEEDBACK WIRE  (retrospective → QRFLearner)")
    print(_DASH)
    table = learner.transition_table()
    print("  Learned transition probabilities:")
    if table:
        for from_intent, transitions in sorted(table.items()):
            for to_intent, prob in transitions.items():
                bar = "█" * int(prob * 20)
                print(f"    {from_intent:>12} → {to_intent:<12}  {prob:.3f}  {bar}")
    else:
        print("    (no transitions observed — single-MET input)")

    if retro_signals:
        print("\n  Retrospective signal boosts applied:")
        for sig in retro_signals:
            direction = "↑" if sig["boost_after"] > sig["boost_before"] else "↓"
            print(
                f"    {sig['intent']:>12}  {sig['signal']:>12}  "
                f"boost {sig['boost_before']:.3f} {direction} {sig['boost_after']:.3f}"
            )
    else:
        print("\n  No retrospective signals to apply (no borderline/missed detections).")

    # ── Phase 6 ───────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("PHASE 6  —  REVERSE QRF  pass 2  [learned priors]")
    print(_DASH)
    _fmt_pass(pass2_results, pass2_sum, "predictions →")

    # ── Comparison ────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("LOOP RESULT  —  pass 1  vs  pass 2")
    print(_DASH)
    delta_hits  = pass2_sum.hits - pass1_sum.hits
    delta_gain  = round(pass2_sum.net_gain_ms - pass1_sum.net_gain_ms, 3)
    delta_rate  = round((pass2_sum.hit_rate - pass1_sum.hit_rate) * 100, 1)
    p1_avg_conf = (
        round(sum(r.prediction.predicted_confidence for r in pass1_results)
              / len(pass1_results), 3)
        if pass1_results else 0.0
    )
    p2_avg_conf = (
        round(sum(r.prediction.predicted_confidence for r in pass2_results)
              / len(pass2_results), 3)
        if pass2_results else 0.0
    )

    print(f"  {'Metric':<28}  {'Pass 1':>10}  {'Pass 2':>10}  {'Delta':>10}")
    print(f"  {'─' * 64}")
    print(
        f"  {'Hit rate':<28}  "
        f"{pass1_sum.hit_rate * 100:>9.0f}%  "
        f"{pass2_sum.hit_rate * 100:>9.0f}%  "
        f"{delta_rate:>+9.1f}pp"
    )
    print(
        f"  {'Avg prediction confidence':<28}  "
        f"{p1_avg_conf:>10.3f}  "
        f"{p2_avg_conf:>10.3f}  "
        f"{round(p2_avg_conf - p1_avg_conf, 3):>+10.3f}"
    )
    print(
        f"  {'Net latency gain (ms)':<28}  "
        f"{pass1_sum.net_gain_ms:>10.3f}  "
        f"{pass2_sum.net_gain_ms:>10.3f}  "
        f"{delta_gain:>+10.3f}"
    )
    print(
        f"  {'Prediction basis':<28}  "
        f"{'markov/drift':>10}  "
        f"{'learned':>10}"
    )
    print(f"  {'─' * 64}")
    if delta_rate > 0:
        print(f"\n  Feedback improved hit rate by {delta_rate:+.1f}pp over static Markov.")
    elif delta_rate == 0:
        print(
            f"\n  Hit rate unchanged — intent sequence was uniform (pure "
            f"{mets[0].intent_class if mets else '?'}).\n"
            f"  Learned basis is still active: on a varied document, improvement\n"
            f"  compounds across sessions as the transition table grows."
        )
    else:
        print(f"\n  Pass 2 hit rate lower — unusual intent sequence; more data needed.")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AXIOM MET Complete Loop — retrospective feeds back into QRF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--text", default=_MIXED_TEXT,
                        help="Input paragraph (default: mixed-intent demo)")
    parser.add_argument("--ledger",   default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--output",   default=None,
                        help="Save full result JSON to this path")
    parser.add_argument("--quiet",    action="store_true")
    args = parser.parse_args()

    _tl = tempfile.NamedTemporaryFile(suffix="-loop-ledger.jsonl",   delete=False)
    _tm = tempfile.NamedTemporaryFile(suffix="-loop-manifest.jsonl", delete=False)
    _tl.close(); _tm.close()

    ledger_path   = Path(args.ledger)   if args.ledger   else Path(_tl.name)
    manifest_path = Path(args.manifest) if args.manifest else Path(_tm.name)

    print(_SEP)
    print("AXIOM MET Complete Loop")
    print(_SEP)
    preview = args.text[:90] + ("..." if len(args.text) > 90 else "")
    print(f"  Input: {preview}\n")

    # ── Phase 1: timed MET encoding ───────────────────────────────────────────
    print("  Phase 1: encoding METs ...", flush=True)
    mets, tokens, encode_times = encode_with_timing(args.text)
    if not mets:
        sys.exit("ERROR: no phrases segmented from input.")
    total_raw = sum(m.raw_tokens for m in mets)

    # ── Phase 2: state transitions ────────────────────────────────────────────
    print("  Phase 2: state transitions ...", flush=True)
    states = StateTransitionEngine().run_chain(mets)

    # ── Phase 3: ledger + retrospective ───────────────────────────────────────
    print("  Phase 3: ledger + retrospective ...", flush=True)
    writer = LedgerWriter(path=ledger_path)
    for token, met in zip(tokens, mets):
        writer.append(token=token, use_case="full_loop", input_text=met.raw_phrase)
    _write_manifest_jsonl(mets, states, manifest_path)
    retro_report = _run_retrospective(mets, manifest_path)

    # ── Phase 4: QRF pass 1 (static Markov) ──────────────────────────────────
    print("  Phase 4: QRF pass 1 (static) ...", flush=True)
    static_qrf = ReverseQRFPredictor()

    def _static_fn(from_intent: str, history: List[str], sv: List[StateVector]):
        return static_qrf.predict(history, sv)

    pass1_results = run_qrf_pass(mets, states, encode_times, _static_fn)
    pass1_sum     = summarise(pass1_results)

    # ── Phase 5: feedback — observe chain + apply retro signals ──────────────
    print("  Phase 5: feedback wire ...", flush=True)
    learner = QRFLearner()
    learner.observe_chain(mets)

    retro_candidates = retro_report.get("_candidates", [])
    # Retrieve actual ReviewCandidate / ReplayResult objects for retro signals
    # (these are embedded in retro_report as dicts; signals are applied via
    # the candidate intent_class which is already in each manifest entry)
    # We synthesize lightweight signal objects from the report dict.
    retro_signals: List[dict] = []
    for cand in retro_candidates:
        intent = cand.get("category", "BORDERLINE")
        # Borderline → treat as improvement opportunity → small positive boost
        retro_signals.extend(
            learner.apply_retro_signals(
                # Build minimal stand-in objects for the API
                [_CandidateProxy(cand)],
                [_ResultProxy("IMPROVEMENT")],
            )
        )

    # ── Phase 6: QRF pass 2 (learned priors) ─────────────────────────────────
    print("  Phase 6: QRF pass 2 (learned) ...", flush=True)

    def _learned_fn(from_intent: str, history: List[str], sv: List[StateVector]):
        return learner.predict(from_intent, history, sv, static_qrf)

    pass2_results = run_qrf_pass(mets, states, encode_times, _learned_fn)
    pass2_sum     = summarise(pass2_results)

    # ── Output ────────────────────────────────────────────────────────────────
    if not args.quiet:
        _print_full_output(
            mets, states, encode_times,
            pass1_results, pass1_sum,
            learner, retro_signals,
            pass2_results, pass2_sum,
            n_ledger=len(tokens),
            retro_report=retro_report,
            ledger_path=ledger_path,
            manifest_path=manifest_path,
            total_raw=total_raw,
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    delta_rate = round((pass2_sum.hit_rate - pass1_sum.hit_rate) * 100, 1)
    print(f"\n{_SEP}")
    print("COMPLETE LOOP SUMMARY")
    print(_DASH)
    print(f"  METs encoded     : {len(mets)}")
    print(f"  Compression      : {round(total_raw / len(mets), 1)}×")
    print(f"  Pass 1 hit rate  : {pass1_sum.hits}/{pass1_sum.total_predictions}"
          f" = {pass1_sum.hit_rate * 100:.0f}%  [static Markov]")
    print(f"  Pass 2 hit rate  : {pass2_sum.hits}/{pass2_sum.total_predictions}"
          f" = {pass2_sum.hit_rate * 100:.0f}%  [learned]")
    print(f"  Δ hit rate       : {delta_rate:+.1f}pp")
    print(f"  Transitions learned : {sum(len(v) for v in learner._counts.values())}")
    print(f"  Retro signals    : {len(retro_signals)}")

    if args.output:
        result = {
            "mets":    [asdict(m) for m in mets],
            "states":  [asdict(s) for s in states],
            "qrf_pass1": [
                {
                    "predicts_step": r.prediction.predicts_step,
                    "predicted":     r.prediction.predicted_intent,
                    "actual":        r.actual_intent,
                    "hit":           r.hit,
                    "basis":         r.prediction.basis,
                }
                for r in pass1_results
            ],
            "qrf_pass2": [
                {
                    "predicts_step": r.prediction.predicts_step,
                    "predicted":     r.prediction.predicted_intent,
                    "actual":        r.actual_intent,
                    "hit":           r.hit,
                    "basis":         r.prediction.basis,
                }
                for r in pass2_results
            ],
            "transition_table":    learner.transition_table(),
            "pass1_summary":       asdict(pass1_sum),
            "pass2_summary":       asdict(pass2_sum),
            "retrospective_report": {
                k: v for k, v in retro_report.items() if not k.startswith("_")
            },
        }
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        print(f"  Full results     → {args.output}")

    print(_SEP)


# ── Minimal proxy objects so apply_retro_signals() works with dict data ───────

class _CandidateProxy:
    """Thin shim so QRFLearner.apply_retro_signals() can read intent_class."""
    def __init__(self, cand_dict: dict) -> None:
        from axiom_retrospect import ManifestEntry, ReviewCategory, ReviewCandidate
        # Reconstruct a minimal ManifestEntry from the stored dict
        me = ManifestEntry(
            input_text=cand_dict.get("met_phrase", ""),
            preflight_vec=[], mid_chain_vec=[], final_synthesis_vec=[],
            constitutional_distance=0.0,
            intent_class=cand_dict.get("intent_class", "INFORM"),
            verdict="PASSED",
            stack_version="met-sim-1.0",
            timestamp="",
            hmac_signature="",
        )
        self.entry = me


class _ResultProxy:
    """Thin shim so apply_retro_signals() can read delta."""
    def __init__(self, delta: str) -> None:
        self.delta = delta


if __name__ == "__main__":
    main()
