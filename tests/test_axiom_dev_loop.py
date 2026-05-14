# -*- coding: utf-8 -*-
"""
AXIOM Dev Loop Tests — capture-and-train shim for AxiomDev
============================================================
4 BLOCKED + 4 PASSED + 3 INVARIANTS

BLOCKED:   module CANNOT_MUTATE, failing tests yield rating="bad",
           tampered signature fails verify, empty task is refused.
PASSED:    green cycle writes one line to each sink, recorder fans out
           to all three sinks, dataset_builder ingests the new record,
           verify() round-trips the signature.
INVARIANTS: spec validates without errors,
            AxiomAgent declares AxiomDev as a delegate,
            axiom_dev.axiom declares the four downstream delegates.

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
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
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_dev_loop_tests"

import axiom_dev_loop as devloop
from axiom_dev_loop import DevCycleRecord, DevCycleRecorder, verify


# ── helpers ────────────────────────────────────────────────────────────
def _make_recorder(tmp_path):
    return DevCycleRecorder(repo_root=tmp_path)


def _green_kwargs(**overrides):
    base = dict(
        commit_sha="abcdef0123456789",
        task="add: dev loop recorder",
        changed_files=["axiom_dev_loop.py", "tests/test_axiom_dev_loop.py"],
        # Long enough to clear the 50-char `len(result) > 50` filter in
        # axiom_dataset_builder._process_existing_training (line 577).
        diff_summary=(
            "2 files changed, 250 insertions(+), 0 deletions(-); "
            "new DevCycleRecorder fans out to three sinks."
        ),
        test_pass=233,
        test_fail=0,
        retrospect_signal="green",
    )
    base.update(overrides)
    return base


# ===========================================================================
# SECTION 1 — BLOCKED
# ===========================================================================

class TestBlocked:

    def test_blocked_module_constants_cannot_mutate(self):
        for name in (
            "TRUST_LEVEL",
            "ISOLATION",
            "RATING_BAD_ON_TEST_FAIL",
            "DEFAULT_TRAINING_PATH",
            "DEFAULT_IMPROVEMENTS_PATH",
            "DEFAULT_REWARD_LOG_PATH",
            "MANIFEST_ID",
        ):
            with pytest.raises(AttributeError):
                setattr(devloop, name, "tampered")

    def test_blocked_failing_tests_yield_rating_bad(self, tmp_path):
        rec = _make_recorder(tmp_path).record(
            **_green_kwargs(test_pass=200, test_fail=3)
        )
        assert rec.rating == "bad"

        # The dataset_builder's _process_existing_training filters bad ratings.
        # Confirm the same filter logic rejects this line.
        line = json.loads((tmp_path / "axiom_dev_training.jsonl").read_text(encoding="utf-8").strip())
        assert line["rating"] == "bad"

    def test_blocked_tampered_signature_fails_verify(self, tmp_path):
        rec = _make_recorder(tmp_path).record(**_green_kwargs())
        tampered = DevCycleRecord(
            commit_sha=rec.commit_sha,
            task=rec.task,
            changed_files=rec.changed_files,
            diff_summary=rec.diff_summary + "  # tampered",
            test_pass=rec.test_pass,
            test_fail=rec.test_fail,
            retrospect_signal=rec.retrospect_signal,
            rating=rec.rating,
            timestamp=rec.timestamp,
            signature=rec.signature,  # same sig, different payload
        )
        assert verify(tampered) is False

    def test_blocked_empty_task_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            _make_recorder(tmp_path).record(
                **_green_kwargs(task="")
            )


# ===========================================================================
# SECTION 2 — PASSED
# ===========================================================================

class TestPassed:

    def test_passed_green_cycle_writes_signed_training_line(self, tmp_path):
        rec = _make_recorder(tmp_path).record(**_green_kwargs())
        assert rec.rating == "good"
        assert len(rec.signature) == 64
        assert verify(rec) is True

        training = tmp_path / "axiom_dev_training.jsonl"
        line = json.loads(training.read_text(encoding="utf-8").strip())
        assert line["task"] == rec.task
        assert line["rating"] == "good"
        assert line["signature"] == rec.signature
        assert line["source"] == "axiom-dev-loop-v1"

    def test_passed_recorder_fans_out_to_all_three_sinks(self, tmp_path):
        _make_recorder(tmp_path).record(**_green_kwargs())
        for fname in (
            "axiom_dev_training.jsonl",
            "dev_agent_improvements.jsonl",
            "axiom_crl_reward_log.jsonl",
        ):
            path = tmp_path / fname
            assert path.exists(), f"{fname} not written"
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) >= 1, f"{fname} is empty"

    def test_passed_improvement_record_schema_matches_retrospect(self, tmp_path):
        _make_recorder(tmp_path).record(**_green_kwargs())
        line = json.loads(
            (tmp_path / "dev_agent_improvements.jsonl").read_text(encoding="utf-8").strip()
        )
        # axiom_retrospect.ImprovementRecord fields:
        for f in (
            "input_text", "former_self_verdict", "current_verdict",
            "improvement_cause", "training_signal", "hmac_signature",
        ):
            assert f in line, f"missing {f} from ImprovementRecord"
        assert line["training_signal"] == "positive"

    def test_passed_dataset_builder_accepts_new_line(self, tmp_path, monkeypatch):
        """Prove the schema we write is ingested by
        axiom_dataset_builder._process_existing_training (line 559)."""
        _make_recorder(tmp_path).record(**_green_kwargs())
        from axiom_dataset_builder import DatasetBuilder

        # Point the builder at our tmp tree.
        monkeypatch.chdir(tmp_path)
        b = DatasetBuilder(repo_root=tmp_path)
        b.examples = []
        b._process_existing_training()
        # The good cycle must have produced one ingested example.
        ours = [e for e in b.examples if e.get("source") == "axiom_dev_training.jsonl"]
        assert len(ours) == 1
        assert ours[0]["instruction"] == "add: dev loop recorder"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_invariant_axiom_agent_declares_axiomdev_delegate(self):
        from axiom_files.parser import compile_delegates, load_axiom
        parsed = load_axiom("axiom_agent")
        targets = " ".join(d["target"] for d in compile_delegates(parsed))
        assert "AxiomDev" in targets

    def test_invariant_axiom_dev_declares_downstream_delegates(self):
        text = (Path(__file__).resolve().parents[1] / "axiom_dev.axiom").read_text(
            encoding="utf-8"
        )
        for expected in ("ScoutAgent", "Reasoner", "Teacher", "SkillBuilder"):
            assert f"AxiomDev -> {expected}" in text, (
                f"axiom_dev.axiom does not declare AxiomDev -> {expected}"
            )

    def test_invariant_devloop_concept_is_pure(self):
        """The DevLoop CONCEPT block must contain no procedural Python."""
        import re

        text = (Path(__file__).resolve().parents[1] / "axiom_dev.axiom").read_text(
            encoding="utf-8"
        )
        start = text.index("CONCEPT DevLoop")
        # Block ends at the next CONCEPT / WHEN / DELEGATES / HUMAN_REVIEW header.
        end_indices = [
            text.find(marker, start + 16)
            for marker in ("CONCEPT ", "WHEN\n", "DELEGATES\n", "HUMAN_REVIEW\n")
        ]
        end_indices = [i for i in end_indices if i > 0]
        block = text[start: min(end_indices)] if end_indices else text[start:]

        forbidden = [
            (r"\bdef\s+\w+\s*\(", "def"),
            (r"\bclass\s+\w+[\s:(]", "class"),
            (r"\bfor\s+\w+\s+in\b", "for-in"),
            (r"\bwhile\s+.+:", "while"),
            (r"\bimport\s+\w+", "import"),
            (r"\breturn\b(?!\s+(to|control|from)\b)", "return"),
            (r"\bprint\s*\(", "print("),
            (r":=", "walrus"),
            (r"\blambda\b", "lambda"),
        ]
        hits = [name for pat, name in forbidden if re.search(pat, block)]
        assert not hits, f"DevLoop block has purity violations: {hits}"
