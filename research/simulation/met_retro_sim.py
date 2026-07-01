#!/usr/bin/env python3
"""
MET Simulator + Retrospective Loop

Simulates the AXIOM Framework Architecture Specification v1.4 end-to-end:

  Phase 1 — MET Encoding
      Segment raw text into phrases via trajectory_filter.segment_text().
      Wrap each phrase as a signed EventToken (the AXIOM Master Event Token).
      Show O(N²)→O(M²) attention-complexity compression.

  Phase 2 — State Transition Engine
      Run the chain: S_{t+1} = f(S_t, MET_λ) + Δ_correction
      ManifoldChecker computes constitutional distance (Δ_correction) per step.

  Phase 3 — Signed Audit Ledger + Retrospective Loop
      Write every EventToken to a signed JSONL ledger (LedgerWriter).
      Synthesize ManifestEntry records, run ConstitutionalRetrospect,
      surface borderline METs and improvement signals.

Requires:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    python3 research/simulation/met_retro_sim.py
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ── BUG-003: UTF-8 stdout ─────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent          # axiom/
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "research" / "quant"))   # trajectory_filter

# ── AXIOM_MASTER_KEY guard ─────────────────────────────────────────────────────
if not os.environ.get("AXIOM_MASTER_KEY"):
    sys.exit(
        "ERROR: AXIOM_MASTER_KEY is not set.\n"
        "  export AXIOM_MASTER_KEY="
        '$(python3 -c "import secrets; print(secrets.token_hex(32))")\n'
        "Then re-run."
    )

# ── Framework imports ──────────────────────────────────────────────────────────
from axiom_signing import derive_key                              # noqa: E402
from axiom_event_token.coordinator import Coordinator            # noqa: E402
from axiom_exoskeleton_ledger import LedgerWriter                # noqa: E402
from axiom_latent_v2 import ManifoldChecker, DRIFT_THRESHOLD     # noqa: E402
from axiom_retrospect import ConstitutionalRetrospect            # noqa: E402
from trajectory_filter import segment_text                       # noqa: E402

BLOCK_CLASSES = frozenset({"HARM", "DECEIVE"})

# Signing key for manifest entries (mirrors axiom_retrospect._sign)
_RETRO_SIGN_KEY = derive_key(b"axiom-retrospect-v1")
# Signing key for state transition vectors
_STATE_SIGN_KEY = derive_key(b"axiom-met-state-v1")

# ── Default demo input — matches PDF notation style ───────────────────────────
_DEMO_TEXT = (
    "Initiate system reset for all active processes. "
    "Execute governance validation protocol. "
    "Authenticate user credentials against active registry. "
    "Load compressed neural kernel manifest. "
    "Suspend all ambient inference threads pending verification."
)

_SEP  = "═" * 72
_DASH = "─" * 72


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class METRecord:
    step: int
    met_id: str
    met_state_var: str      # "[ENCAP_{id[:8].upper()}]" — PDF §2 notation
    raw_phrase: str
    raw_tokens: int         # word-count proxy for subword tokens
    intent_class: str
    confidence: float
    parent_sig: str         # EventToken.parent_signature (chain link)
    signature: str          # EventToken.signature (outer HMAC)


@dataclass
class StateVector:
    step: int
    met_id: str
    intent_class: str
    confidence: float
    constitutional_distance: float   # S_{t+1}.dist
    delta_correction: float          # Δ_correction = new_dist − prev_dist
    drift_direction: str             # "stable" | "toward_boundary" | "away_from_boundary"
    alert_level: str                 # "NONE" | "L1_WARNING" | "L2_THROTTLE"
    signature: str                   # HMAC(step|met_id|constitutional_distance)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — MET Encoding
# ══════════════════════════════════════════════════════════════════════════════

class METEncoder:
    """
    Segments raw text into phrases, wraps each as a signed EventToken (= MET).

    The MET state variable [ENCAP_XXXXXXXX] mirrors the PDF §2 notation for
    the encapsulated token ID that replaces the raw phrase in the attention layer.
    Parent-linking (EventToken.parent_signature) implements the proof chain:
    each MET commits to its predecessor's signature.
    """

    def __init__(self) -> None:
        self._coordinator = Coordinator()

    def encode(self, text: str) -> tuple[List[METRecord], list]:
        """
        Returns (met_records, event_tokens).
        event_tokens are returned in order for Phase 3 ledger writing.
        """
        phrases = segment_text(text)
        if not phrases:
            return [], []

        met_records: List[METRecord] = []
        event_tokens: list = []
        parent = None

        for i, phrase in enumerate(phrases, start=1):
            token = self._coordinator.compose(
                text=phrase,
                parent=parent,
                activate=["text", "governance"],
            )

            text_payload  = token.text.payload if token.text else {}
            intent_class  = str(text_payload.get("intent_class", "INFORM"))
            confidence    = float(text_payload.get("confidence", 0.70))

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
            parent = token

        return met_records, event_tokens


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — State Transition Engine
# ══════════════════════════════════════════════════════════════════════════════

_L1_WARN_THRESHOLD = 0.06
_L2_THROTTLE_THRESHOLD = 0.05


def _state_sign(step: int, met_id: str, dist: float) -> str:
    payload = f"{step}|{met_id}|{dist:.6f}".encode("utf-8")
    return hmac_lib.new(_STATE_SIGN_KEY, payload, hashlib.sha256).hexdigest()


class StateTransitionEngine:
    """
    Implements: S_{t+1} = f(S_t, MET_λ) + Δ_correction

    f(S_t, MET_λ):   EMA blend — new_dist = 0.6 × prev_dist + 0.4 × dist_MET
    Δ_correction:    ManifoldChecker.compute_distance(confidence, rival_present)
                     is the constitutional correction term for this MET.

    Each S_{t+1} is HMAC-signed under axiom-met-state-v1 so the chain
    is tamper-evident, mirroring the PDF §3.1 cryptographic audit trace.
    """

    def __init__(self) -> None:
        self._checker = ManifoldChecker()

    def run_chain(self, mets: List[METRecord]) -> List[StateVector]:
        states: List[StateVector] = []
        prev_dist: Optional[float] = None

        for met in mets:
            # rival_present=True: every phrase has an alternative interpretation;
            # the binary flag only goes False if we're absolutely certain there is none.
            dist = self._checker.compute_distance(
                confidence=met.confidence,
                rival_present=True,
                fields_clean=True,
            )

            # f(S_t, MET_λ): exponential moving average blend
            if prev_dist is None:
                new_dist = dist
            else:
                new_dist = round(prev_dist * 0.6 + dist * 0.4, 4)

            # Δ_correction
            delta = round(new_dist - (prev_dist if prev_dist is not None else new_dist), 4)

            # Drift direction
            if delta < -DRIFT_THRESHOLD:
                direction = "toward_boundary"
            elif delta > DRIFT_THRESHOLD:
                direction = "away_from_boundary"
            else:
                direction = "stable"

            # Alert level
            if new_dist <= _L2_THROTTLE_THRESHOLD:
                alert = "L2_THROTTLE"
            elif new_dist <= _L1_WARN_THRESHOLD:
                alert = "L1_WARNING"
            else:
                alert = "NONE"

            states.append(StateVector(
                step=met.step,
                met_id=met.met_id,
                intent_class=met.intent_class,
                confidence=met.confidence,
                constitutional_distance=new_dist,
                delta_correction=delta,
                drift_direction=direction,
                alert_level=alert,
                signature=_state_sign(met.step, met.met_id, new_dist),
            ))
            prev_dist = new_dist

        return states


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Signed Ledger + Retrospective Loop
# ══════════════════════════════════════════════════════════════════════════════

def _retro_sign(data: dict) -> str:
    """Mirror axiom_retrospect._sign — same key + canonical form."""
    canon = json.dumps(data, sort_keys=True,
                       ensure_ascii=True).encode("utf-8")   # BUG-008
    return hmac_lib.new(_RETRO_SIGN_KEY, canon,
                        hashlib.sha256).hexdigest()          # BUG-007


def _write_manifest_jsonl(
    mets: List[METRecord],
    states: List[StateVector],
    path: Path,
) -> int:
    """
    Synthesize ManifestEntry records and write to JSONL for
    ConstitutionalRetrospect to read.

    Each MET becomes one entry; its (preflight, mid_chain, final_synthesis)
    vectors come from its left-neighbour, itself, and right-neighbour states.
    This produces a plausible 3-stage trajectory window per MET.
    """
    now_ts = datetime.now(timezone.utc).isoformat() + "Z"
    n = len(mets)

    def _vec(sv: StateVector) -> List[float]:
        return [
            round(sv.constitutional_distance, 4),
            round(sv.confidence, 4),
            float(sv.intent_class in BLOCK_CLASSES),
        ]

    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for i, (met, state) in enumerate(zip(mets, states)):
            pre_state = states[max(0, i - 1)]
            mid_state = states[i]
            fin_state = states[min(n - 1, i + 1)]

            verdict = "BLOCKED" if met.intent_class in BLOCK_CLASSES else "PASSED"

            rec: dict = {
                "input_text":               met.raw_phrase,
                "preflight_vec":            _vec(pre_state),
                "mid_chain_vec":            _vec(mid_state),
                "final_synthesis_vec":      _vec(fin_state),
                "constitutional_distance":  state.constitutional_distance,
                "intent_class":             met.intent_class,
                "verdict":                  verdict,
                "stack_version":            "met-sim-1.0",
                "timestamp":                now_ts,
            }
            rec["hmac_signature"] = _retro_sign(
                {k: v for k, v in rec.items() if k != "hmac_signature"}
            )
            fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
            count += 1

    return count


def _current_stack_fn(input_text: str) -> dict:
    """
    Re-runs the current IntentClassifier on a historical MET phrase.
    Used as the current_stack_fn argument to ConstitutionalRetrospect.replay().
    """
    from axiom_intent_classifier import IntentClassifier   # lazy — avoids circular import
    checker = ManifoldChecker()
    clf = IntentClassifier(derive_key(b"axiom-firewall-v1"))
    result = clf.classify(input_text)
    dist = checker.compute_distance(
        confidence=result.confidence,
        rival_present=True,
        fields_clean=True,
    )
    verdict = "BLOCKED" if result.intent_class in BLOCK_CLASSES else "PASSED"
    return {"verdict": verdict, "constitutional_distance": dist}


def _run_retrospective(mets: List[METRecord], manifest_path: Path) -> dict:
    """Write manifest JSONL, run retrospective, return annotated report."""
    retrospect = ConstitutionalRetrospect(manifest_path=str(manifest_path))
    candidates = retrospect.review_manifests(last_hours=24)
    results    = [retrospect.replay(c, _current_stack_fn) for c in candidates]
    improvements = retrospect.extract_improvements(results)
    report     = retrospect.generate_morning_report(candidates, results)

    report["_candidates"] = [
        {
            "met_phrase": c.entry.input_text[:60],
            "category":   c.category.value,
            "priority":   c.priority,
            "reason":     c.review_reason,
        }
        for c in candidates
    ]
    report["_improvements"] = [
        {
            "former":  imp.former_self_verdict,
            "current": imp.current_verdict,
            "cause":   imp.improvement_cause,
        }
        for imp in improvements
    ]
    return report


# ══════════════════════════════════════════════════════════════════════════════
# Output helpers
# ══════════════════════════════════════════════════════════════════════════════

def _print_encoding_table(mets: List[METRecord], total_raw: int) -> None:
    m   = len(mets)
    on2 = total_raw ** 2
    om2 = m ** 2
    compression = round(total_raw / m, 1) if m else 0.0

    print(f"\n{_SEP}")
    print("PHASE 1  —  MET ENCODING")
    print(_DASH)
    print(f"  {'Step':<4}  {'MET State Variable':<22}  {'Phrase (truncated)':<38}  {'Tok':>3}  Intent")
    print(f"  {_DASH}")
    for met in mets:
        phrase_s = (met.raw_phrase[:37] + "..") if len(met.raw_phrase) > 39 else met.raw_phrase
        print(
            f"  {met.step:<4}  {met.met_state_var:<22}  {phrase_s:<38}  "
            f"{met.raw_tokens:>3}  {met.intent_class}"
        )
    print(f"  {_DASH}")
    print(f"\n  Raw N = {total_raw} tokens  →  M = {m} METs  |  Compression {compression}×")
    print(f"  O(N²) = {on2:,}  →  O(M²) = {om2}  (quadratic cost at attention layer)\n")


def _print_state_table(states: List[StateVector]) -> None:
    print(_SEP)
    print("PHASE 2  —  STATE TRANSITIONS   S₊₁ = f(Sₜ, METλ) + Δₐₒₓₑₐₜₑₒₙ")
    print(_DASH)
    print(f"  {'Step':<4}  {'S_t.dist':>8}  {'conf':>6}  {'Δ_corr':>8}  {'S_{t+1}.dist':>13}  Alert")
    print(f"  {_DASH}")
    prev_dist = 0.0
    for sv in states:
        flag      = "  ← drift" if sv.drift_direction == "toward_boundary" else ""
        alert_str = sv.alert_level if sv.alert_level != "NONE" else ""
        print(
            f"  {sv.step:<4}  {prev_dist:>8.4f}  {sv.confidence:>6.2f}  "
            f"{sv.delta_correction:>+8.4f}  {sv.constitutional_distance:>13.4f}"
            f"  {alert_str}{flag}"
        )
        prev_dist = sv.constitutional_distance
    print(f"  {_DASH}")
    print("  All state vectors signed — HMAC-SHA256 under axiom-met-state-v1\n")


def _print_retro_report(
    report: dict, ledger_path: Path, manifest_path: Path, n_ledger: int
) -> None:
    print(_SEP)
    print("PHASE 3  —  SIGNED AUDIT LEDGER")
    print(_DASH)
    print(f"  {n_ledger} entries → {ledger_path}")
    print( "  Format : HMAC-SHA256 per entry under axiom-exoskeleton-ledger-v1")

    print(f"\n{_SEP}")
    print("PHASE 3  —  RETROSPECTIVE REPORT  (S_t₊₁ = f(S_t, METλ) + Δ_correction)")
    print(_DASH)

    candidates = report.get("_candidates", [])
    borderline = sum(1 for c in candidates if c["category"] == "BORDERLINE")
    missed     = sum(1 for c in candidates if c["category"] == "MISSED_DETECTION")
    fp         = sum(1 for c in candidates if c["category"] == "FALSE_POSITIVE")

    print(f"  Borderline: {borderline}  |  Missed: {missed}  |  False positives: {fp}")
    print(f"  Regression alert: {report.get('regression_alert', False)}")

    imps = report.get("_improvements", [])
    print(f"  Improvement signals: {len(imps)}")
    for imp in imps:
        print(f"    → {imp['former']} → {imp['current']}  ({imp['cause']})")

    if candidates:
        print("\n  Review candidates:")
        for c in candidates:
            print(f"    [{c['priority']:>8}] {c['category']}")
            print(f"              phrase : \"{c['met_phrase']}\"")
            print(f"              reason : {c['reason']}")

    sig_preview = report.get("hmac_signature", "?")[:16]
    print(f"\n  Report HMAC : {sig_preview}…")
    print(f"  Manifest    : {manifest_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "AXIOM MET Simulator + Retrospective Loop\n"
            "  Spec: AXIOM Framework Architecture Specification v1.4"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--text", default=_DEMO_TEXT,
        help="Raw input paragraph to encode as METs (default: built-in demo)",
    )
    parser.add_argument(
        "--ledger", default=None,
        help="Path for signed JSONL audit ledger (default: auto temp file)",
    )
    parser.add_argument(
        "--manifest", default=None,
        help="Path for retrospective manifest JSONL (default: auto temp file)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Save full simulation result as JSON to this path",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress tables; print summary line only",
    )
    args = parser.parse_args()

    # Auto temp files when paths not given
    _tl = tempfile.NamedTemporaryFile(suffix="-met-ledger.jsonl",   delete=False)
    _tm = tempfile.NamedTemporaryFile(suffix="-met-manifest.jsonl", delete=False)
    _tl.close(); _tm.close()

    ledger_path   = Path(args.ledger)   if args.ledger   else Path(_tl.name)
    manifest_path = Path(args.manifest) if args.manifest else Path(_tm.name)

    print(_SEP)
    print("AXIOM MET Simulator + Retrospective Loop  [spec v1.4]")
    print(_SEP)
    preview = args.text[:90] + ("..." if len(args.text) > 90 else "")
    print(f"  Input : {preview}\n")

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    print("  Phase 1: encoding METs ...", flush=True)
    mets, tokens = METEncoder().encode(args.text)
    if not mets:
        sys.exit("ERROR: no phrases segmented from input text.")
    total_raw = sum(m.raw_tokens for m in mets)

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print("  Phase 2: running state transitions ...", flush=True)
    states = StateTransitionEngine().run_chain(mets)

    # ── Phase 3a — signed ledger ──────────────────────────────────────────────
    print("  Phase 3a: writing signed ledger ...", flush=True)
    writer = LedgerWriter(path=ledger_path)
    n_ledger = 0
    for token, met in zip(tokens, mets):
        writer.append(token=token, use_case="met_simulation", input_text=met.raw_phrase)
        n_ledger += 1

    # ── Phase 3b — manifest + retrospective ───────────────────────────────────
    print("  Phase 3b: running retrospective ...", flush=True)
    _write_manifest_jsonl(mets, states, manifest_path)
    report = _run_retrospective(mets, manifest_path)

    # ── Display ───────────────────────────────────────────────────────────────
    if not args.quiet:
        _print_encoding_table(mets, total_raw)
        _print_state_table(states)
        _print_retro_report(report, ledger_path, manifest_path, n_ledger)

    # ── Summary ───────────────────────────────────────────────────────────────
    compression = round(total_raw / len(mets), 1) if mets else 0.0
    print(f"\n{_SEP}")
    print("SIMULATION COMPLETE")
    print(_DASH)
    print(f"  METs encoded     : {len(mets)}")
    print(f"  State steps      : {len(states)}")
    print(f"  Ledger entries   : {n_ledger}  ({ledger_path.name})")
    print(f"  Retro candidates : {report.get('total_reviewed', 0)}")
    print(f"  Compression      : {compression}×  (N={total_raw} raw tokens → M={len(mets)} METs)")
    print(f"  O(N²) → O(M²)   : {total_raw**2:,} → {len(mets)**2}")

    if args.output:
        result = {
            "mets":    [asdict(m) for m in mets],
            "states":  [asdict(s) for s in states],
            "retrospective_report": {
                k: v for k, v in report.items() if not k.startswith("_")
            },
            "summary": {
                "n_raw_tokens":      total_raw,
                "n_mets":            len(mets),
                "compression_ratio": compression,
                "on2":               total_raw ** 2,
                "om2":               len(mets) ** 2,
                "ledger_path":       str(ledger_path),
                "manifest_path":     str(manifest_path),
            },
        }
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        print(f"\n  Full results → {args.output}")

    print(_SEP)


if __name__ == "__main__":
    main()
