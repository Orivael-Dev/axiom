# tests/test_parallel_n.py
# encoding: utf-8
# Tests for Parallel-N dynamic branch count — axiom_agent-generated contract
# SECTION 1: IMMUTABILITY     (3 BLOCKED + 3 PASSED)
# SECTION 2: BRANCH SELECTION (3 BLOCKED + 3 PASSED)

import pytest

from axiom_latent import (
    BRANCH_POOL,
    BranchInputError,
    BranchPoolExhausted,
    LatentState,
    MultiplexRunner,
    _validate_branch_n,
    compute_branch_n,
)


# ===========================================================================
# SECTION 1 — IMMUTABILITY (3 BLOCKED + 3 PASSED)
# ===========================================================================

class TestImmutability:

    # --- BLOCKED: mutations must be rejected --------------------------------

    def test_blocked_branch_pool_element_assignment(self):
        """BLOCKED: BRANCH_POOL is a tuple — element assignment raises TypeError."""
        with pytest.raises(TypeError):
            BRANCH_POOL[0] = "HackedBranch"  # type: ignore

    def test_blocked_non_list_risk_clusters_raises(self):
        """BLOCKED: passing a string instead of a list raises BranchInputError."""
        with pytest.raises(BranchInputError):
            compute_branch_n("medical")

    def test_blocked_n_exceeds_pool_size(self):
        """BLOCKED: _validate_branch_n raises BranchPoolExhausted when N > pool."""
        with pytest.raises(BranchPoolExhausted):
            _validate_branch_n(len(BRANCH_POOL) + 1)

    # --- PASSED: read operations must succeed transparently -----------------

    def test_passed_branch_pool_is_tuple_of_eight(self):
        """PASSED: BRANCH_POOL is a tuple with exactly 8 entries."""
        assert isinstance(BRANCH_POOL, tuple)
        assert len(BRANCH_POOL) == 8

    def test_passed_branch_pool_order_is_fixed(self):
        """PASSED: BRANCH_POOL starts with the four canonical branches in order."""
        assert BRANCH_POOL[0] == "SafetyBranch"
        assert BRANCH_POOL[1] == "FastBranch"
        assert BRANCH_POOL[2] == "SkepticBranch"
        assert BRANCH_POOL[3] == "CreativeBranch"
        assert BRANCH_POOL[4] == "DetailBranch"
        assert BRANCH_POOL[5] == "CautionBranch"
        assert BRANCH_POOL[6] == "RivalBranch"
        assert BRANCH_POOL[7] == "EvidenceBranch"

    def test_passed_validate_n_accepts_valid_values(self):
        """PASSED: _validate_branch_n accepts 2, 4, 6, 8 without raising."""
        for n in (2, 4, 6, 8):
            _validate_branch_n(n)  # must not raise


# ===========================================================================
# SECTION 2 — BRANCH SELECTION (3 BLOCKED + 3 PASSED)
# ===========================================================================

class TestBranchSelection:

    # --- BLOCKED: wrong N for a known risk profile is unconstitutional ------

    def test_blocked_high_risk_must_not_yield_fewer_than_eight(self):
        """BLOCKED: medical/legal/financial/safety must each yield N=8, never less."""
        for cluster in ("medical", "legal", "financial", "safety"):
            n = compute_branch_n([cluster])
            assert n == 8, f"{cluster!r} must give N=8, got {n}"
            assert n != 4, f"{cluster!r} must NOT give the default N=4"

    def test_blocked_two_high_risk_clusters_still_n8_not_n6(self):
        """BLOCKED: two high-risk clusters must give N=8, NOT the count-based N=6."""
        n = compute_branch_n(["medical", "legal"])
        assert n == 8, f"Two high-risk clusters must give N=8, got {n}"
        assert n != 6

    def test_blocked_rival_never_absent_in_runner_output(self):
        """BLOCKED: MultiplexRunner must always populate rival (N >= 2 guaranteed)."""
        runner = MultiplexRunner()
        latent = LatentState(
            intent_vector=["ask_boolean"],
            risk_clusters=[],
            compressed_plan=[],
            confidence=0.75,
        )
        result = runner.run("Does sunlight affect mood?", latent)
        assert result.rival is not None, "rival must always be present"
        assert result.rival.branch != result.winner.branch

    # --- PASSED: correct N and branch slice for each tier ------------------

    def test_passed_empty_risk_gives_n2_first_two_branches(self):
        """PASSED: no risk clusters → N=2, active = [SafetyBranch, FastBranch]."""
        n = compute_branch_n([])
        assert n == 2
        assert list(BRANCH_POOL[:n]) == ["SafetyBranch", "FastBranch"]

    def test_passed_two_low_risk_clusters_give_n6(self):
        """PASSED: two non-high-risk clusters → N=6, first 6 from pool."""
        n = compute_branch_n(["personal", "ethics"])
        assert n == 6
        assert list(BRANCH_POOL[:n]) == [
            "SafetyBranch", "FastBranch", "SkepticBranch", "CreativeBranch",
            "DetailBranch", "CautionBranch",
        ]

    def test_passed_runner_uses_correct_n_for_risk_profile(self):
        """PASSED: MultiplexRunner runs exactly N branches matching risk profile."""
        runner = MultiplexRunner()

        # medical → N=8
        latent_high = LatentState(
            intent_vector=["ask_medical"],
            risk_clusters=["medical"],
            compressed_plan=[],
            confidence=0.75,
        )
        result_high = runner.run("Should I take aspirin daily?", latent_high)
        assert len(result_high.branches) == 8, \
            f"Expected 8 branches for medical risk, got {len(result_high.branches)}"

        # empty → N=2
        latent_low = LatentState(
            intent_vector=["ask_factual"],
            risk_clusters=[],
            compressed_plan=[],
            confidence=0.75,
        )
        result_low = runner.run("What is the speed of light?", latent_low)
        assert len(result_low.branches) == 2, \
            f"Expected 2 branches for empty risk, got {len(result_low.branches)}"
