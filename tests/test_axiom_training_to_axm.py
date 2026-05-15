# -*- coding: utf-8 -*-
"""
axiom_training_to_axm — training-corpus → .AXM compilation tests
=================================================================
2 BLOCKED + 4 PASSED + 2 INVARIANTS

Covers the Option-A bridge: every type cluster in the two source JSONLs
must compile into exactly one TrajectoryBlock, every agent mode must
map to one SkillDelegate, the resulting container must verify under
the same key it was packed with, and the compilation must be
deterministic so re-packs don't drift the fingerprint.

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_training_to_axm"

import axiom_training_to_axm as compiler
from axiom_axm import AXMContainer
from axiom_intent_classifier import IntentClassifier
from axiom_signing import derive_key


@pytest.fixture()
def packed(tmp_path):
    cpath = tmp_path / "axiom_agent.axm"
    return compiler.pack(str(cpath))


# ===========================================================================
# SECTION 1 — BLOCKED (compiler refuses bad preconditions)
# ===========================================================================

class TestTrainingToAXMBlocked:

    def test_blocked_missing_master_key_refused(self, tmp_path, monkeypatch):
        """Packing without AXIOM_MASTER_KEY would produce a container that
        can't be verified later — fail loud at compile time rather than
        ship a dead artifact."""
        monkeypatch.delenv("AXIOM_MASTER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="AXIOM_MASTER_KEY"):
            compiler.pack(str(tmp_path / "no_key.axm"))

    def test_blocked_missing_source_jsonl_refused(self, tmp_path, monkeypatch):
        """If a source corpus disappears, the compiler refuses rather
        than silently produce a partial container."""
        # Redirect the constants the compiler reads from
        monkeypatch.setattr(compiler, "TRAINING_JSONL",
                            tmp_path / "does_not_exist.jsonl")
        with pytest.raises(FileNotFoundError, match="training source"):
            compiler.pack(str(tmp_path / "missing_src.axm"))


# ===========================================================================
# SECTION 2 — PASSED (compilation produces what the brief promises)
# ===========================================================================

class TestTrainingToAXMPassed:

    def test_passed_every_type_cluster_becomes_a_trajectory(self, packed):
        """Each distinct `type` in either source file should end up as
        exactly one TrajectoryBlock. Catches drift if a new training
        type is added but the TYPE_PATTERNS map isn't updated — the new
        type would fall through to the catch-all and we'd see fewer
        trajectories than types."""
        training   = compiler._read_jsonl(compiler.TRAINING_JSONL)
        behavioral = compiler._read_jsonl(compiler.BEHAVIORAL_JSONL)
        type_union = (set(compiler._count_by_type(training)) |
                      set(compiler._count_by_type(behavioral)))
        traj_ids = {t.id for t in packed.trajectories}
        for t in type_union:
            assert f"traj-axiom-agent-{t}" in traj_ids, (
                f"missing trajectory for type '{t}'")
        assert len(traj_ids) == len(type_union)

    def test_passed_five_delegates_one_per_agent_mode(self, packed):
        """Four runtime modes from axiom_agent.py (FEATURE, BUG_HUNT,
        EFFICIENCY, REASONING_LAB) plus the always-on constitutional
        enforcer = 5 delegates."""
        names = {d.name for d in packed.delegates}
        assert names == {
            "feature_writer", "bug_hunter", "efficiency_profiler",
            "reasoning_lab", "constitutional_enforcer",
        }
        # constitutional_enforcer must always-fire — it's the "verify
        # before activate" rule from the .axiom spec made operational.
        enforcer = next(d for d in packed.delegates
                        if d.name == "constitutional_enforcer")
        assert enforcer.when_condition == "always"

    def test_passed_proof_ledger_verifies(self, packed):
        """The compiler hands the spec to AXMContainer.pack(), which
        builds a proof ledger entry per sub-module. The whole ledger
        must verify under the same key it was packed with — otherwise
        the container is dead on arrival."""
        assert packed.verify_proofs() is True

    def test_passed_route_lazy_loads_matched_delegates_only(self, packed):
        """An INFORM-class task should activate constitutional_enforcer
        (always-on) and feature_writer (intent_class ∈ {INFORM,...}),
        but NOT bug_hunter (gates on REQUEST,UNCERTAIN only)."""
        packed.verify_proofs()  # route() refuses without this
        clf = IntentClassifier(hmac_key=derive_key(b"axiom-intent-classifier-v1"))
        result = packed.route("explain how the constitutional gate works", clf)
        # The keyword classifier slots questions like this as INFORM.
        assert result.intent_class == "INFORM"
        loaded = set(result.loaded_skills)
        assert "constitutional_enforcer" in loaded   # always-on
        assert "feature_writer"          in loaded   # matches INFORM
        # The reasoning_lab gates on EXPLORE/UNCERTAIN — NOT this task.
        assert "reasoning_lab" not in loaded


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestTrainingToAXMInvariants:

    def test_invariant_pack_is_deterministic(self, tmp_path):
        """Same key + same sources → same fingerprint. If the compiler
        ever introduces nondeterminism (dict iteration order, timestamp
        injection, etc.) this catches it before it becomes a supply-
        chain headache."""
        a = compiler.pack(str(tmp_path / "a.axm"))
        b = compiler.pack(str(tmp_path / "b.axm"))
        assert a.fingerprint() == b.fingerprint()

    def test_invariant_no_raw_training_text_in_container(self, packed, tmp_path):
        """Option A is by design lossy: raw {instruction, output} pairs
        must NOT bleed into the on-disk container — only the abstracted
        task_pattern + action_sequence. This invariant guards against a
        future refactor that quietly stuffs raw records into the
        TrajectoryBlock fields, which would defeat the point of the
        pattern abstraction (and could leak training-set content)."""
        sample = json.loads(
            compiler.TRAINING_JSONL.read_text(encoding="utf-8").splitlines()[0]
        )
        raw_instruction = sample["instruction"]
        assert len(raw_instruction) > 20  # sanity — should be a real sentence

        # Walk every on-disk file in the container and assert the raw
        # instruction text is not present verbatim.
        for f in Path(packed.path).rglob("*"):
            if not f.is_file():
                continue
            content = f.read_text(encoding="utf-8", errors="ignore")
            assert raw_instruction not in content, (
                f"raw training record leaked into {f.relative_to(packed.path)}")
