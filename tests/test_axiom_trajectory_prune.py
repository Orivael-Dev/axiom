# -*- coding: utf-8 -*-
"""
Trajectory prune-and-gate loop: flag a bad branch → it is blocked on recurrence;
distinct branches pass; geometric (not exact) matching; signed + tamper-evident.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_prune"

from axiom_trajectory_prune import TrajectoryPruner, trajectory_from_samples

NOW = "2026-06-28T00:00:00+00:00"

# A "bad" branch and a clearly different "good" branch (near-orthogonal geometry).
BAD = {"preflight": [1.0, 0.0, 0.0], "mid_chain": [0.9, 0.1, 0.0],
       "final_synthesis": [0.8, 0.2, 0.0]}
GOOD = {"preflight": [0.0, 0.0, 1.0], "mid_chain": [0.0, 0.1, 0.9],
        "final_synthesis": [0.0, 0.2, 0.8]}
# A near-recurrence of BAD (minor numerical drift) — should still be caught.
BAD_VARIANT = {"preflight": [0.98, 0.04, 0.0], "mid_chain": [0.9, 0.12, 0.01],
               "final_synthesis": [0.79, 0.21, 0.0]}


class TestPruneLoop:

    def test_the_loop_second_occurrence_is_blocked(self, tmp_path):
        pr = TrajectoryPruner(tmp_path / "pruned.jsonl")
        # First occurrence: not yet known → passes.
        assert pr.check(BAD).blocked is False
        # Flag it.
        entry = pr.flag(BAD, reason="drifted past authority boundary", now=NOW)
        assert entry["pattern_id"].startswith("pp-")
        # Second occurrence: now blocked.
        v = pr.check(BAD)
        assert v.blocked is True
        assert v.matched_id == entry["pattern_id"]
        assert "authority boundary" in v.reason

    def test_distinct_branch_passes(self, tmp_path):
        pr = TrajectoryPruner(tmp_path / "pruned.jsonl")
        pr.flag(BAD, reason="bad", now=NOW)
        assert pr.check(GOOD).blocked is False

    def test_geometric_match_catches_minor_variation(self, tmp_path):
        pr = TrajectoryPruner(tmp_path / "pruned.jsonl")
        pr.flag(BAD, reason="bad", now=NOW)
        # Not byte-identical, but geometrically the same branch → blocked.
        assert pr.check(BAD_VARIANT).blocked is True

    def test_one_diverging_stage_is_not_a_match(self, tmp_path):
        pr = TrajectoryPruner(tmp_path / "pruned.jsonl")
        pr.flag(BAD, reason="bad", now=NOW)
        # Same start, but final stage flips to the orthogonal direction → different branch.
        partial = {**BAD, "final_synthesis": [0.0, 0.2, 0.8]}
        assert pr.check(partial).blocked is False     # strictest-stage (min) rejects it

    def test_threshold_is_tunable(self, tmp_path):
        loose = TrajectoryPruner(tmp_path / "loose.jsonl", threshold=0.5)
        loose.flag(BAD, reason="bad", now=NOW)
        # The diverging-stage branch shares 2/3 stages strongly; at a loose threshold
        # the min-similarity is still low, so it remains a non-match — proving min() is
        # doing the strict work, not the threshold alone.
        partial = {**BAD, "final_synthesis": [0.0, 0.2, 0.8]}
        assert loose.check(partial).blocked is False


class TestPersistenceAndIntegrity:

    def test_patterns_persist_across_instances(self, tmp_path):
        path = tmp_path / "pruned.jsonl"
        TrajectoryPruner(path).flag(BAD, reason="bad", now=NOW)
        # A fresh process loads the signed ledger and still blocks.
        assert TrajectoryPruner(path).check(BAD).blocked is True

    def test_tampered_ledger_row_is_ignored(self, tmp_path):
        path = tmp_path / "pruned.jsonl"
        pr = TrajectoryPruner(path)
        pr.flag(BAD, reason="bad", now=NOW)
        # Forge the reason without re-signing → row dropped on reload → no longer blocks.
        row = json.loads(path.read_text().splitlines()[0])
        row["reason"] = "tampered"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        assert TrajectoryPruner(path).check(BAD).blocked is False

    def test_signing_key_not_in_store(self, tmp_path):
        path = tmp_path / "pruned.jsonl"
        TrajectoryPruner(path).flag(BAD, reason="bad", now=NOW)
        import axiom_trajectory_prune as t
        assert t._KEY.hex() not in path.read_text()


class TestRetrainHalf:

    def test_export_negative_examples(self, tmp_path):
        pr = TrajectoryPruner(tmp_path / "pruned.jsonl")
        pr.flag(BAD, reason="drift", now=NOW)
        ex = pr.export_negative_examples()
        assert len(ex) == 1
        assert ex[0]["label"] == "reject"
        assert "stage_vectors" in ex[0]


class TestLatentBridge:

    def test_trajectory_from_samples(self):
        class S:
            def __init__(self, stage, vec): self.stage, self.intent_vector = stage, vec
        samples = [S("preflight", [1.0, 0.0]), S("final_synthesis", [0.0, 1.0])]
        traj = trajectory_from_samples(samples)
        assert traj == {"preflight": [1.0, 0.0], "final_synthesis": [0.0, 1.0]}
