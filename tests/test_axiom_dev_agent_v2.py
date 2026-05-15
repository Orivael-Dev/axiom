# -*- coding: utf-8 -*-
"""
AxiomDevAgentV2 — four-layer constitutional dev agent tests
============================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

Mirrors the test discipline used in PRs #6/#7/#8/#9 across the four
layers (Reflex / Reviewer / Curriculum / Examiner).

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_dev_agent_v2"

from axiom_dev_agent_v2 import (
    AxiomDevAgentV2, CodeReflex, PullRequestReviewer, DevCurriculum,
    CIExaminer, DevTask, ReflexResult, ReviewVerdict, CIResult,
    CurriculumSuggestion, TransferCapExceeded,
    SUITE_ID, SUITE_VERSION, TASK_CLASSES,
    COMPETENCE_BUILD_PER_SUCCESS, COMPETENCE_DROP_ON_VETO,
    COMPETENCE_DROP_ON_CI_FAIL, TRANSFER_CAP_PER_CALL,
    _reviewer_key, _examiner_key,
)
from axiom_signing import derive_key


@pytest.fixture()
def agent():
    return AxiomDevAgentV2(persistence_path=None)


def _good_doc_task(tid: str = "t1") -> DevTask:
    return DevTask(
        id=tid, description="add docstring",
        task_class="DOCUMENTATION",
        artifact_path="x.py",
        proposed_diff='+ """A short module docstring."""\n',
        cited_patterns=("traj-axiom-agent-language_knowledge",),
    )


# ===========================================================================
# SECTION 1 — BLOCKED (the four layers refuse what they should refuse)
# ===========================================================================

class TestDevAgentBlocked:

    def test_blocked_reflex_refuses_eval_diff(self, agent):
        """Layer 0 must refuse `eval(` outright. The pipeline never
        reaches the reviewer or examiner."""
        task = DevTask(
            id="bad-1", description="add eval", task_class="FEATURE",
            artifact_path="x.py",
            proposed_diff="+ result = eval(user_input)\n",
            cited_patterns=(),
        )
        out = agent.handle_task(task)
        assert out.final_verdict == "REFLEX_REFUSED"
        assert out.review is None
        assert out.ci is None
        assert any("eval" in r for r in out.reflex.reasons)

    def test_blocked_reflex_refuses_master_key_in_diff(self, agent):
        """A 64-hex-char string in a diff looks like a master key —
        sealing this is a constitutional credential-leak rule."""
        hex_blob = "0123456789abcdef" * 4   # exactly 64 hex chars
        task = DevTask(
            id="bad-2", description="paste config", task_class="FEATURE",
            artifact_path="x.py",
            proposed_diff=f"+ key = '{hex_blob}'\n",
            cited_patterns=(),
        )
        out = agent.handle_task(task)
        assert out.final_verdict == "REFLEX_REFUSED"
        assert any("master key" in r.lower() for r in out.reflex.reasons)

    def test_blocked_constants_are_cannot_mutate(self):
        """All sealed module constants must refuse reassignment."""
        import axiom_dev_agent_v2
        for name in ("SUITE_ID", "SUITE_VERSION", "TASK_CLASSES",
                     "TRANSFER_CAP_PER_CALL",
                     "COMPETENCE_DROP_ON_VETO"):
            with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
                setattr(axiom_dev_agent_v2, name, "tampered")


# ===========================================================================
# SECTION 2 — PASSED (the four layers do the right thing on good input)
# ===========================================================================

class TestDevAgentPassed:

    def test_passed_trusted_clean_task_merges(self, agent):
        """A small, well-cited DOCUMENTATION task at full competence
        should clear all four layers and end MERGED."""
        agent.reviewer.set_all(1.0)
        out = agent.handle_task(_good_doc_task())
        assert out.final_verdict == "MERGED"
        assert out.reflex.ok is True
        assert out.review.verdict == "PASS"
        assert out.ci is not None
        assert out.ci.checks_failed == 0

    def test_passed_untrusted_diff_softens_with_advice(self, agent):
        """At competence=0, a small but uncited diff should SOFTEN
        with concrete advice fields the caller can act on."""
        out = agent.handle_task(_good_doc_task("t-untrusted"))
        assert out.final_verdict == "SOFTEN_REQUESTED"
        assert out.review.verdict == "SOFTEN"
        # At least one of the advice strings mentions competence — the
        # reviewer is telling the operator "pair up with someone
        # who's done this before."
        assert any("competence" in s.lower()
                   for s in out.review.softening_advice)

    def test_passed_examiner_flags_oversized_diff(self, agent):
        """The CI suite caps diff size at 500 lines. A bigger diff
        with otherwise-clean reflex / reviewer state must fail CI."""
        agent.reviewer.set_all(1.0)
        big_diff = "\n".join(f"+ pass  # line {i}" for i in range(600))
        task = DevTask(
            id="big", description="rewrite world",
            task_class="FEATURE", artifact_path="x.py",
            proposed_diff=big_diff,
            cited_patterns=("traj-axiom-agent-implementation_pattern",),
        )
        out = agent.handle_task(task)
        # Reviewer might already SOFTEN due to size; force PASS by
        # constructing a CI scenario directly.
        ci = agent.examiner.evaluate(
            task,
            ReflexResult(task_id=task.id, ok=True, reasons=(), signature=""),
            ReviewVerdict(task_id=task.id, verdict="PASS",
                            task_class="FEATURE", competence=1.0,
                            forecast_passing=0.9, min_safe=0.2,
                            softening_advice=(), reasons=(),
                            signature=""),
        )
        assert ci.checks_failed >= 1
        assert any("diff_under_size_ceiling" in s
                   for s in ci.failure_summary)

    def test_passed_competence_updates_asymmetric_on_outcome(self):
        """Clean CI bumps by build_per_success; veto drops by
        drop_on_veto. Asymmetry mirrors CPI's CompetenceTracker."""
        rev = PullRequestReviewer()
        rev.set("FEATURE", 0.50)
        # 1 clean CI pass = +0.05
        rev.on_outcome("FEATURE", ci_passed=True)
        assert rev.get("FEATURE") == pytest.approx(0.55, abs=1e-9)
        # 1 reviewer veto = −0.30
        rev.on_outcome("FEATURE", review_vetoed=True)
        assert rev.get("FEATURE") == pytest.approx(0.25, abs=1e-9)
        # Drop is much larger than build — the asymmetry IS the point.
        assert COMPETENCE_DROP_ON_VETO > 4 * COMPETENCE_BUILD_PER_SUCCESS


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestDevAgentInvariants:

    def test_invariant_layer_keys_are_independent(self, agent):
        """The four derived keys (reflex / reviewer / curriculum /
        examiner) MUST yield distinct bytes — that's the whole basis
        for "no layer can forge another layer's signature." Pinning
        this catches a future refactor that accidentally aliases."""
        from axiom_dev_agent_v2 import (
            _reflex_key, _reviewer_key, _curriculum_key, _examiner_key,
        )
        keys = {
            "reflex":     _reflex_key(),
            "reviewer":   _reviewer_key(),
            "curriculum": _curriculum_key(),
            "examiner":   _examiner_key(),
        }
        # Each key must be unique.
        assert len(set(keys.values())) == len(keys), (
            "two layers share a derived key — independence is broken"
        )

    def test_invariant_examiner_certificate_does_not_verify_under_reviewer_key(self, agent):
        """A passing CI certificate's signature must NOT verify under
        the reviewer's key — proves signing-key separation
        operationally, not just by inspection."""
        agent.reviewer.set_all(1.0)
        out = agent.handle_task(_good_doc_task("inv-1"))
        assert out.ci is not None
        # Re-sign the certificate body under the WRONG (reviewer) key
        # and confirm the result differs from the certificate's
        # examiner-key signature.
        body = {k: v for k, v in asdict(out.ci).items()
                 if k != "signature"}
        body["failure_summary"] = tuple(body.get("failure_summary", ()))
        import hmac as hmac_lib, hashlib, json as _json
        canonical = _json.dumps(body, sort_keys=True, ensure_ascii=True,
                                  separators=(",", ":")).encode("utf-8")
        wrong_sig = hmac_lib.new(
            _reviewer_key(), canonical, hashlib.sha256
        ).hexdigest()
        assert wrong_sig != out.ci.signature
        # And the right key DOES verify.
        assert agent.examiner.verify_certificate(out.ci) is True
