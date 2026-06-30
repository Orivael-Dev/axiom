# -*- coding: utf-8 -*-
"""
Governance benchmark (Tier 1) — runs, reports the four axes as a pair, signs the
scorecard, and never reports catch without over-block.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_govbench"

import axiom_governance_bench as gb


def test_runs_and_reports_all_four_axes():
    r = gb.run()
    for k in ("catch_pct", "over_block_pct", "over_refusal_pct",
              "integrity_pass", "overhead_us_mean"):
        assert k in r, f"missing axis: {k}"


def test_metrics_in_valid_ranges():
    r = gb.run()
    for k in ("catch_pct", "over_block_pct", "over_refusal_pct", "precision_pct"):
        assert 0 <= r[k] <= 100
    assert r["overhead_us_mean"] > 0
    assert r["integrity_pass"] is True              # tamper is detected


def test_scorecard_is_signed_and_verifies():
    r = gb.run()
    sig = r["signature"]
    assert sig == gb._sign(r)                        # recomputed signature matches
    r2 = {**r, "catch_pct": 999}
    assert gb._sign(r2) != sig                       # tampered scorecard differs


def test_render_pairs_catch_with_over_block():
    out = gb.render(gb.run())
    # The headline must never show catch alone — over-block is on the same line.
    head = [l for l in out.splitlines() if "HEADLINE" in l][0]
    assert "catch" in head and "over-block" in head


def test_corpus_has_hard_negatives():
    # The over-refusal probe is only meaningful with benign trigger-shaped prompts.
    subsets = {s for _, _, s in gb.CORPUS}
    assert "hard_negative" in subsets
    assert {"harm", "deceive", "benign"} <= subsets


def test_misses_are_listed_for_honesty():
    r = gb.run()
    assert isinstance(r["misses"], list)             # surfaced, not hidden
