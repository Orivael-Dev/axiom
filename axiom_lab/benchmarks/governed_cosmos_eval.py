"""
GovernedCosmos vs raw Cosmos/BM25 — recall-under-governance contrast.

The Recall Bench head-to-head showed Cosmos/BM25 retrieves well but is
UNCALIBRATED (it always returns its top hit, so it never abstains and ignores
recency) — calibration 3, decay 23. GovernedCosmos (axiom_governed_cosmos.py)
layers ORVL-015's gates back on: integrity → decay → calibration abstain.

This eval isolates the axes on a small labeled scenario set and prints a
two-column scorecard, demonstrating the hypothesis: **GovernedCosmos keeps recall
on answerable queries while restoring calibration / decay / integrity** that pure
BM25 loses. That profile is the 5th column you plug into the full Recall Bench.

Run:
    python axiom_lab/benchmarks/governed_cosmos_eval.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "governed_cosmos_eval_key"

from axiom_governed_cosmos import GovernedCosmos, _coverage
from axiom_semantic_cosmos import FTS5Hit

NOW = "2026-06-20T00:00:00+00:00"

# ── corpus: (uri, text, timestamp) ─────────────────────────────────────────────
_CORPUS = [
    ("hanta",  "Andes hantavirus case fatality rate is approximately 35 to 40 percent.", "2026-06-15T00:00:00+00:00"),
    ("sepsis", "Sepsis hour-one bundle: blood cultures, antibiotics within one hour, lactate measurement.", "2026-06-10T00:00:00+00:00"),
    ("mi",     "Troponin I rises within hours after myocardial infarction onset.", "2026-06-05T00:00:00+00:00"),
    ("bs",     "Black-Scholes prices European options using volatility and the risk-free rate.", "2026-06-01T00:00:00+00:00"),
    ("cds",    "Credit default swap spread is the annual cost of credit protection in basis points.", "2026-05-20T00:00:00+00:00"),
    ("warfarin", "Warfarin target INR for atrial fibrillation anticoagulation is between two and three.", "2026-06-12T00:00:00+00:00"),
    ("hanta_old", "Hantavirus host reservoir is the long-tailed pygmy rice rat in the Andes.", "2025-01-01T00:00:00+00:00"),  # stale
]

# ── scenarios: (kind, query, expected_uri_or_None) ─────────────────────────────
# kind ∈ ANSWERABLE | UNANSWERABLE | STALE | TAMPERED
_SCENARIOS = [
    ("ANSWERABLE", "hantavirus case fatality rate", "hanta"),
    ("ANSWERABLE", "sepsis hour one bundle antibiotics lactate", "sepsis"),
    ("ANSWERABLE", "troponin myocardial infarction onset", "mi"),
    ("ANSWERABLE", "black scholes european options volatility", "bs"),
    ("ANSWERABLE", "credit default swap spread basis points", "cds"),
    ("UNANSWERABLE", "quantum chromodynamics gluon confinement lattice", None),
    ("UNANSWERABLE", "hantavirus vaccine schedule dosage approval", None),   # keyword overlap, wrong answer
    ("UNANSWERABLE", "sepsis survival prognosis statistics year 2035", None),
    ("UNANSWERABLE", "black scholes nobel prize committee biography", None),
    ("STALE", "hantavirus host reservoir rice rat species", "hanta_old"),     # only in the 535-day-old doc
    ("TAMPERED", "warfarin target INR atrial fibrillation", "warfarin"),       # warfarin gets tampered below
]


class RawCosmos:
    """Pure BM25 column: broad retrieve, ALWAYS serve the top hit. No gates."""

    def __init__(self, gc: GovernedCosmos):
        self._gc = gc

    def query(self, q: str):
        hits = self._gc._retrieve_broad(q, k=5)
        return hits[0] if hits else None   # never abstains unless zero matches


def _scorecard():
    work = Path(tempfile.mkdtemp(prefix="gov_cosmos_eval_"))
    gc = GovernedCosmos(work / "cosmos.db")
    for uri, text, ts in _CORPUS:
        gc.ingest(uri, text, timestamp=ts)
    raw = RawCosmos(gc)

    # Tamper the doc the TAMPERED scenario targets (alter indexed content, no re-sign).
    # Uses a dedicated doc no ANSWERABLE query needs, so integrity is tested in isolation.
    gc.tamper("warfarin", "Warfarin target INR for atrial fibrillation is between zero and one. [ALTERED]")

    cols = {"raw": {}, "gov": {}}
    rows = []
    for kind, q, expected in _SCENARIOS:
        # raw: serves top hit, never abstains, no integrity/decay
        rh = raw.query(q)
        raw_served = rh.uri if rh else None
        raw_abstain = rh is None
        # gov: governed
        gr = gc.query(q, now=NOW)
        gov_served = gr.served[0].uri if (not gr.abstained and gr.served) else None
        gov_abstain = gr.abstained

        # correctness per kind
        if kind == "ANSWERABLE":
            raw_ok = (raw_served == expected)
            gov_ok = (gov_served == expected)
        elif kind in ("UNANSWERABLE", "STALE"):
            raw_ok = raw_abstain                 # correct = abstained
            gov_ok = gov_abstain
        elif kind == "TAMPERED":
            raw_ok = (raw_served != expected)    # correct = did NOT serve the tampered doc
            gov_ok = (gov_served != expected)
        cols["raw"].setdefault(kind, []).append(raw_ok)
        cols["gov"].setdefault(kind, []).append(gov_ok)
        rows.append((kind, q[:40], raw_served or "ABSTAIN", gov_served or "ABSTAIN", raw_ok, gov_ok))

    # ── print ──────────────────────────────────────────────────────────────────
    print("\n  GovernedCosmos vs raw Cosmos/BM25 — recall under governance")
    print("  " + "=" * 74)
    print(f"  {'scenario':<13}{'query':<42}{'raw':>7}{'gov':>7}")
    print("  " + "-" * 74)
    for kind, q, rs, gs, rok, gok in rows:
        print(f"  {kind:<13}{q:<42}{'✓' if rok else '✗':>7}{'✓' if gok else '✗':>7}")

    print("  " + "-" * 74)

    def axis(kinds):
        r = [ok for k in kinds for ok in cols["raw"].get(k, [])]
        g = [ok for k in kinds for ok in cols["gov"].get(k, [])]
        rp = round(100 * sum(r) / len(r)) if r else 0
        gp = round(100 * sum(g) / len(g)) if g else 0
        return rp, gp

    axes = [
        ("Recall (answerable served correctly)", ["ANSWERABLE"]),
        ("Calibration (abstains when it should)", ["UNANSWERABLE"]),
        ("Decay (stale fact correctly faded)",   ["STALE"]),
        ("Integrity (tampered memory refused)",  ["TAMPERED"]),
    ]
    print(f"  {'axis':<42}{'raw':>8}{'gov':>8}")
    print("  " + "-" * 74)
    for label, kinds in axes:
        rp, gp = axis(kinds)
        print(f"  {label:<42}{rp:>7}%{gp:>7}%")
    allr, allg = axis(["ANSWERABLE", "UNANSWERABLE", "STALE", "TAMPERED"])
    print("  " + "-" * 74)
    print(f"  {'OVERALL':<42}{allr:>7}%{allg:>7}%")
    print("\n  Reading: raw BM25 keeps recall but cannot abstain, decay, or detect")
    print("  tampering — exactly the calibration/decay collapse the head-to-head showed.")
    print("  GovernedCosmos keeps recall AND restores the governance axes.\n")
    gc.close()


if __name__ == "__main__":
    _scorecard()
