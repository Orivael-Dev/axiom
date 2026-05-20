"""Tests for axiom_patch_agent — human-in-the-loop signed patch
workflow + cryptographic sign-off + MonotonicGate."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_PATCH_AGENT_DRAFTS", raising=False)
    monkeypatch.delenv("AXIOM_PATCH_AGENT_LEDGER", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_patch_agent",
        )):
            sys.modules.pop(mod, None)
    yield


def _git_init(repo: Path, filename: str, content: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "tester"],
                   cwd=repo, check=True, capture_output=True)
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"],
                   cwd=repo, check=True, capture_output=True)


def _git_diff_for_change(repo: Path, filename: str,
                          new_content: str) -> str:
    """Write a new version of `filename`, get the unified diff, then
    restore the original file so the diff can be applied later."""
    original = (repo / filename).read_text(encoding="utf-8")
    (repo / filename).write_text(new_content, encoding="utf-8")
    out = subprocess.run(["git", "diff", filename],
                          cwd=repo, check=True,
                          capture_output=True, text=True)
    (repo / filename).write_text(original, encoding="utf-8")
    return out.stdout


# ─── PatchDraft basics ───────────────────────────────────────────────


def test_draft_new_rejects_empty_diff(isolated):
    from axiom_patch_agent import PatchDraft, PatchAgentError
    with pytest.raises(PatchAgentError, match="diff"):
        PatchDraft.new(
            bug_id="x", target_file="x.py",
            diff="   ", agent_reasoning="r",
        )


def test_draft_new_rejects_empty_reasoning(isolated):
    from axiom_patch_agent import PatchDraft, PatchAgentError
    with pytest.raises(PatchAgentError, match="reasoning"):
        PatchDraft.new(
            bug_id="x", target_file="x.py",
            diff="diff content", agent_reasoning="   ",
        )


def test_gate_passes_when_tests_green(isolated):
    from axiom_patch_agent import PatchDraft
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=5, tests_failed=0,
    )
    assert d.monotonic_gate_passed is True


def test_gate_refused_when_tests_red(isolated):
    from axiom_patch_agent import PatchDraft
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=3, tests_failed=2,
    )
    assert d.monotonic_gate_passed is False


def test_gate_refused_when_no_tests_run(isolated):
    """Zero tests run shouldn't count as passing."""
    from axiom_patch_agent import PatchDraft
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=0, tests_failed=0,
    )
    assert d.monotonic_gate_passed is False


# ─── Persistence + tamper detection ──────────────────────────────────


def test_save_and_load_round_trip(isolated, tmp_path):
    from axiom_patch_agent import PatchDraft
    d = PatchDraft.new(
        bug_id="BUG-001", target_file="foo.py",
        diff="diff --git a/foo.py b/foo.py\n",
        agent_reasoning="fix off-by-one",
        tests_passed=10, tests_failed=0,
    )
    d.save(tmp_path)
    loaded = PatchDraft.load(d.patch_id, tmp_path)
    assert loaded.patch_id == d.patch_id
    assert loaded.diff_hash == d.diff_hash
    assert loaded.monotonic_gate_passed is True


def test_tampered_diff_caught_on_load(isolated, tmp_path):
    from axiom_patch_agent import PatchDraft, PatchAgentError
    d = PatchDraft.new(
        bug_id="BUG-001", target_file="foo.py",
        diff="original diff text",
        agent_reasoning="r",
        tests_passed=1, tests_failed=0,
    )
    d.save(tmp_path)
    # Tamper with the on-disk patch.diff.
    (tmp_path / d.patch_id / "patch.diff").write_text(
        "EVIL DIFF", encoding="utf-8",
    )
    with pytest.raises(PatchAgentError, match="diff_hash mismatch"):
        PatchDraft.load(d.patch_id, tmp_path)


# ─── Approval flow ───────────────────────────────────────────────────


def test_approve_blocked_when_gate_refused(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, GateRefusal,
    )
    drafts = tmp_path / "drafts"
    agent = PatchAgent(drafts_dir=drafts)
    d = PatchDraft.new(
        bug_id="x", target_file="x.py",
        diff="some-diff", agent_reasoning="r",
        tests_passed=0, tests_failed=1,
    )
    agent.draft(d)
    with pytest.raises(GateRefusal, match="MonotonicGate refused"):
        agent.approve(d.patch_id, reviewer_principal="alice@example.com",
                       apply_with="none")


def test_approve_requires_reviewer(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, PatchAgentError,
    )
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    with pytest.raises(PatchAgentError, match="reviewer_principal"):
        agent.approve(d.patch_id, reviewer_principal="",
                       apply_with="none")


def test_approve_dry_mode_signs_without_applying(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py",
        diff="diff --git a/x.py b/x.py\n",
        agent_reasoning="r", tests_passed=3, tests_failed=0,
    )
    agent.draft(d)
    token = agent.approve(
        d.patch_id, reviewer_principal="alice@example.com",
        apply_with="none",
    )
    assert token.verify()
    assert token.governance is not None
    gp = token.governance.payload
    assert gp["decision"] == "approve"
    assert gp["reviewer_principal"] == "alice@example.com"
    assert gp["monotonic_gate_passed"] is True
    assert gp["diff_hash"] == d.diff_hash
    # Status flipped to approved on disk.
    reloaded = agent.get(d.patch_id)
    assert reloaded.status == "approved"


def test_approve_refuses_to_re_approve(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, PatchAgentError,
    )
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=3, tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice",
                   apply_with="none")
    with pytest.raises(PatchAgentError, match="already"):
        agent.approve(d.patch_id, reviewer_principal="alice",
                       apply_with="none")


# ─── Rejection flow ──────────────────────────────────────────────────


def test_reject_signs_and_writes_improvement_record(
    isolated, tmp_path,
):
    from axiom_patch_agent import PatchAgent, PatchDraft
    drafts = tmp_path / "drafts"
    improvements = tmp_path / "improvements.jsonl"
    agent = PatchAgent(
        drafts_dir=drafts, improvements_path=improvements,
    )
    d = PatchDraft.new(
        bug_id="BUG-001", target_file="x.py",
        diff="d", agent_reasoning="fix off-by-one",
        tests_passed=2, tests_failed=0,
    )
    agent.draft(d)
    token = agent.reject(
        d.patch_id,
        reviewer_principal="alice@example.com",
        reason="should use a list comprehension",
    )
    assert token.verify()
    assert token.governance.payload["decision"] == "reject"
    assert "list comprehension" in token.governance.payload[
        "rejection_reason"
    ]
    # ImprovementRecord written for the retrospect pipeline.
    assert improvements.exists()
    lines = improvements.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["former_self_verdict"] == "PROPOSED"
    assert rec["current_verdict"] == "REJECTED"
    assert "list comprehension" in rec["improvement_cause"]
    assert rec["training_signal"] == "negative"
    # Status flipped to rejected.
    assert agent.get(d.patch_id).status == "rejected"


def test_reject_requires_reason(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, PatchAgentError,
    )
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    with pytest.raises(PatchAgentError, match="reason"):
        agent.reject(d.patch_id,
                      reviewer_principal="alice", reason="")


# ─── List / verify ───────────────────────────────────────────────────


def test_list_pending_filters_decided(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(drafts_dir=tmp_path / "drafts",
                        improvements_path=tmp_path / "imp.jsonl")
    pending = PatchDraft.new(
        bug_id="x1", target_file="x.py", diff="d1",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    approved = PatchDraft.new(
        bug_id="x2", target_file="x.py", diff="d2",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    rejected = PatchDraft.new(
        bug_id="x3", target_file="x.py", diff="d3",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(pending)
    agent.draft(approved)
    agent.approve(approved.patch_id, reviewer_principal="alice",
                   apply_with="none")
    agent.draft(rejected)
    agent.reject(rejected.patch_id, reviewer_principal="alice",
                  reason="nope")
    ids = {d.patch_id for d in agent.list_pending()}
    assert ids == {pending.patch_id}


def test_verify_signed_token_round_trip(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice",
                   apply_with="none")
    result = agent.verify(d.patch_id)
    assert result["status"] == "approved"
    assert result["diff_hash_matches"] is True
    assert result["event_token_verified"] is True


# ─── Apply diff to a real git repo ───────────────────────────────────


def test_approve_with_git_apply_modifies_target(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft, read_diff
    repo = tmp_path / "repo"
    _git_init(repo, "foo.py", "def f():\n    return 1\n")
    diff = _git_diff_for_change(
        repo, "foo.py",
        "def f():\n    return 2\n",
    )
    drafts = tmp_path / "drafts"
    agent = PatchAgent(drafts_dir=drafts)
    d = PatchDraft.new(
        bug_id="BUG-001", target_file="foo.py",
        diff=diff, agent_reasoning="bump return value",
        tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    token = agent.approve(
        d.patch_id, reviewer_principal="alice@example.com",
        apply_with="git", target_repo=repo,
    )
    assert token.verify()
    # File on disk reflects the patch.
    assert (repo / "foo.py").read_text(encoding="utf-8") \
        == "def f():\n    return 2\n"


def test_approve_with_failing_git_apply_raises(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, PatchAgentError,
    )
    repo = tmp_path / "repo"
    _git_init(repo, "foo.py", "def f():\n    return 1\n")
    # Bogus diff that won't apply.
    bad_diff = (
        "diff --git a/does-not-exist.py b/does-not-exist.py\n"
        "--- a/does-not-exist.py\n"
        "+++ b/does-not-exist.py\n"
        "@@ -1 +1 @@\n"
        "-not real\n"
        "+also not real\n"
    )
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="does-not-exist.py",
        diff=bad_diff, agent_reasoning="r",
        tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    with pytest.raises(PatchAgentError, match="git apply"):
        agent.approve(d.patch_id, reviewer_principal="alice",
                       apply_with="git", target_repo=repo)


# ─── Ledger integration ──────────────────────────────────────────────


def test_ledger_appends_signed_entry_on_approve(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    from axiom_patch_agent_ledger import LedgerWriter, read_ledger
    ledger_path = tmp_path / "ledger.jsonl"
    agent = PatchAgent(
        drafts_dir=tmp_path / "drafts",
        ledger=LedgerWriter(ledger_path),
    )
    d = PatchDraft.new(
        bug_id="BUG-9", target_file="x.py",
        diff="d", agent_reasoning="r",
        tests_passed=5, tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice@example.com",
                   apply_with="none")
    entries = read_ledger(ledger_path)
    assert len(entries) == 1
    e = entries[0]
    assert e.verify()
    assert e.decision == "approve"
    assert e.reviewer_principal == "alice@example.com"
    assert e.diff_hash == d.diff_hash


def test_ledger_records_rejection_reason(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    from axiom_patch_agent_ledger import LedgerWriter, read_ledger
    ledger_path = tmp_path / "ledger.jsonl"
    agent = PatchAgent(
        drafts_dir=tmp_path / "drafts",
        improvements_path=tmp_path / "imp.jsonl",
        ledger=LedgerWriter(ledger_path),
    )
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=2, tests_failed=0,
    )
    agent.draft(d)
    agent.reject(d.patch_id, reviewer_principal="alice",
                  reason="not the right approach")
    entries = read_ledger(ledger_path)
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].rejection_reason == "not the right approach"
    assert entries[0].verify()


def test_ledger_tamper_detected(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    from axiom_patch_agent_ledger import LedgerWriter, read_ledger
    ledger_path = tmp_path / "ledger.jsonl"
    agent = PatchAgent(
        drafts_dir=tmp_path / "drafts",
        ledger=LedgerWriter(ledger_path),
    )
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice",
                   apply_with="none")
    # Tamper the on-disk ledger entry.
    raw = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    raw["reviewer_principal"] = "mallory"
    ledger_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    entries = read_ledger(ledger_path)
    assert entries[0].verify() is False


# ─── CLI smoke ───────────────────────────────────────────────────────


def test_cli_draft_list_show(isolated, tmp_path, capsys):
    from axiom_patch_agent import main
    diff_path = tmp_path / "p.diff"
    diff_path.write_text("diff content here\n", encoding="utf-8")
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"),
        "--no-ledger",
        "draft",
        "--bug-id", "BUG-001",
        "--target-file", "foo.py",
        "--diff", str(diff_path),
        "--reasoning", "smoke test",
        "--tests-passed", "3",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "drafted patch_" in out
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"),
        "--no-ledger",
        "list",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "BUG-001" in out


def test_cli_approve_dry_mode(isolated, tmp_path, capsys):
    from axiom_patch_agent import main
    diff_path = tmp_path / "p.diff"
    diff_path.write_text("diff stub\n", encoding="utf-8")
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"),
        "--no-ledger",
        "draft", "--bug-id", "x", "--target-file", "x.py",
        "--diff", str(diff_path),
        "--reasoning", "r", "--tests-passed", "1",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    patch_id = next(
        tok.strip() for tok in out.split() if tok.startswith("patch_")
    )
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"),
        "--no-ledger",
        "approve", patch_id,
        "--reviewer", "alice@example.com",
        "--apply", "none",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "approved" in out
    assert "verified=True" in out


def test_cli_gate_refusal_exits_3(isolated, tmp_path, capsys):
    from axiom_patch_agent import main
    diff_path = tmp_path / "p.diff"
    diff_path.write_text("d\n", encoding="utf-8")
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"),
        "--no-ledger",
        "draft", "--bug-id", "x", "--target-file", "x.py",
        "--diff", str(diff_path),
        "--reasoning", "r",
        "--tests-passed", "1", "--tests-failed", "1",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    patch_id = next(
        tok.strip() for tok in out.split() if tok.startswith("patch_")
    )
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"),
        "--no-ledger",
        "approve", patch_id,
        "--reviewer", "alice",
        "--apply", "none",
    ])
    assert rc == 3
    assert "GATE REFUSED" in capsys.readouterr().err


# ─── Relevance scoring + gate ────────────────────────────────────────


def test_relevance_score_high_on_matching_diff(isolated):
    from axiom_patch_agent import compute_relevance_score
    score = compute_relevance_score(
        bug_description="High latency in the request handler — "
                        "needs caching",
        agent_reasoning="add request-cache lookup before db query",
        diff=(
            "diff --git a/handler.py b/handler.py\n"
            "@@ -1 +1,3 @@\n"
            "+def handler(request):\n"
            "+    cached = request_cache.get(request)\n"
            "+    return cached or fetch_from_db(request)\n"
        ),
    )
    # Words like request, cache, handler are present in both.
    assert score > 0.10


def test_relevance_score_low_on_off_topic_diff(isolated):
    from axiom_patch_agent import compute_relevance_score
    score = compute_relevance_score(
        bug_description="High latency in the request handler — "
                        "needs caching",
        agent_reasoning="fix typo in unrelated docs",
        diff=(
            "diff --git a/README.md b/README.md\n"
            "@@ -1 +1 @@\n"
            "-welcom to the project\n"
            "+welcome to the project\n"
        ),
    )
    assert score < 0.10


def test_draft_without_bug_description_skips_relevance_gate(
    isolated, tmp_path,
):
    """No bug_description = no ground truth to score against. The
    relevance gate must NOT block in that case."""
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py",
        diff="diff --git a/x b/x\n@@\n+typo fix\n",
        agent_reasoning="fix typo",
        # no bug_description supplied → gate disabled
        tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    token = agent.approve(d.patch_id, reviewer_principal="alice",
                           apply_with="none")
    assert token.verify()


def test_approve_refuses_when_relevance_below_floor(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, RelevanceRefusal,
    )
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    # Ask: about latency. Reasoning + diff: about README typo.
    # Zero word overlap on content words.
    d = PatchDraft.new(
        bug_id="LAT-1",
        target_file="docs/README.md",
        diff=(
            "diff --git a/docs/README.md b/docs/README.md\n"
            "@@\n"
            "-welcom paragraph header\n"
            "+welcome paragraph header\n"
        ),
        agent_reasoning="correct spelling mistake",
        bug_description="High latency in the request handler — "
                        "needs caching",
        tests_passed=3, tests_failed=0,
    )
    agent.draft(d)
    assert d.relevance_score < 0.05, \
        f"expected score < 0.05, got {d.relevance_score}"
    with pytest.raises(RelevanceRefusal, match="below floor"):
        agent.approve(d.patch_id, reviewer_principal="alice",
                       apply_with="none")


def test_force_irrelevant_bypass_records_override(isolated, tmp_path):
    """Reviewer can deliberately override the relevance gate. The
    override MUST appear in the signed token's governance payload."""
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="LAT-1",
        target_file="docs/README.md",
        diff=(
            "diff --git a/docs/README.md b/docs/README.md\n"
            "@@\n"
            "-welcom paragraph header\n"
            "+welcome paragraph header\n"
        ),
        agent_reasoning="correct spelling mistake",
        bug_description="Latency in request handler caching",
        tests_passed=2, tests_failed=0,
    )
    agent.draft(d)
    assert d.relevance_score < 0.05
    token = agent.approve(
        d.patch_id, reviewer_principal="alice",
        apply_with="none", force_irrelevant=True,
    )
    assert token.verify()
    assert token.governance.payload["relevance_override"] is True
    assert token.governance.payload["relevance_score"] < 0.05


def test_approve_allows_high_relevance(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="LAT-1",
        target_file="handler.py",
        diff=(
            "diff --git a/handler.py b/handler.py\n"
            "@@\n"
            "+def handler(request):\n"
            "+    cached = request_cache.get(request)\n"
            "+    return cached or fetch_from_db(request)\n"
        ),
        agent_reasoning="add request-cache lookup before db query",
        bug_description="High latency in request handler — needs cache",
        tests_passed=5, tests_failed=0,
    )
    agent.draft(d)
    assert d.relevance_score >= 0.10
    token = agent.approve(
        d.patch_id, reviewer_principal="alice", apply_with="none",
    )
    assert token.verify()
    assert token.governance.payload["relevance_override"] is False


# ─── Revocation ──────────────────────────────────────────────────────


def test_revoke_signs_and_appends_improvement(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(
        drafts_dir=tmp_path / "drafts",
        improvements_path=tmp_path / "imp.jsonl",
    )
    d = PatchDraft.new(
        bug_id="LAT-1", target_file="x.py", diff="d",
        agent_reasoning="off-topic fix", tests_passed=1,
        tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice",
                   apply_with="none")
    rev = agent.revoke(
        d.patch_id, revoker_principal="alice",
        reason="approved the wrong thing — was supposed to fix latency",
    )
    assert rev.verify()
    gp = rev.governance.payload
    assert gp["decision"] == "revoke"
    assert "revokes_token_id" in gp
    assert "wrong thing" in gp["revoke_reason"]
    # Status flipped.
    assert agent.get(d.patch_id).status == "revoked"
    # Improvement record appended with REVOKED verdict + negative signal.
    lines = (tmp_path / "imp.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    rec = json.loads(lines[0])
    assert rec["current_verdict"] == "REVOKED"
    assert rec["former_self_verdict"] == "APPROVED"
    assert rec["training_signal"] == "negative"


def test_revoke_requires_approved_status(isolated, tmp_path):
    from axiom_patch_agent import (
        PatchAgent, PatchDraft, PatchAgentError,
    )
    agent = PatchAgent(drafts_dir=tmp_path / "drafts")
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    # Not approved yet.
    with pytest.raises(PatchAgentError, match="not approved"):
        agent.revoke(d.patch_id, revoker_principal="alice",
                      reason="changed my mind")


def test_revoke_with_rollback_undoes_diff(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    repo = tmp_path / "repo"
    _git_init(repo, "foo.py", "def f():\n    return 1\n")
    diff = _git_diff_for_change(
        repo, "foo.py", "def f():\n    return 2\n",
    )
    agent = PatchAgent(
        drafts_dir=tmp_path / "drafts",
        improvements_path=tmp_path / "imp.jsonl",
    )
    d = PatchDraft.new(
        bug_id="x", target_file="foo.py",
        diff=diff, agent_reasoning="bump",
        tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice",
                   apply_with="git", target_repo=repo)
    assert (repo / "foo.py").read_text() == "def f():\n    return 2\n"
    agent.revoke(
        d.patch_id, revoker_principal="alice",
        reason="wrong", rollback=True, target_repo=repo,
    )
    # File restored.
    assert (repo / "foo.py").read_text() == "def f():\n    return 1\n"


def test_verify_after_revoke_shows_both_tokens(isolated, tmp_path):
    from axiom_patch_agent import PatchAgent, PatchDraft
    agent = PatchAgent(
        drafts_dir=tmp_path / "drafts",
        improvements_path=tmp_path / "imp.jsonl",
    )
    d = PatchDraft.new(
        bug_id="x", target_file="x.py", diff="d",
        agent_reasoning="r", tests_passed=1, tests_failed=0,
    )
    agent.draft(d)
    agent.approve(d.patch_id, reviewer_principal="alice",
                   apply_with="none")
    agent.revoke(d.patch_id, revoker_principal="alice",
                  reason="wrong")
    r = agent.verify(d.patch_id)
    assert r["status"] == "revoked"
    assert r["event_token_verified"] is True
    assert r["revocation_token_verified"] is True
    assert r["revokes_token_id"] is not None


# ─── CLI smoke for new commands ──────────────────────────────────────


def test_cli_relevance_refusal_exits_4(isolated, tmp_path, capsys):
    from axiom_patch_agent import main
    diff_path = tmp_path / "p.diff"
    diff_path.write_text(
        "diff --git a/docs/README.md b/docs/README.md\n@@\n"
        "-welcom paragraph header\n"
        "+welcome paragraph header\n",
        encoding="utf-8",
    )
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "draft", "--bug-id", "LAT-1",
        "--target-file", "docs/README.md",
        "--diff", str(diff_path),
        "--reasoning", "correct spelling mistake",
        "--bug-description", "High latency in request handler caching",
        "--tests-passed", "1",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    patch_id = next(
        tok.strip() for tok in out.split() if tok.startswith("patch_")
    )
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "approve", patch_id, "--reviewer", "alice", "--apply", "none",
    ])
    assert rc == 4
    assert "RELEVANCE REFUSED" in capsys.readouterr().err


def test_cli_force_irrelevant_approves(isolated, tmp_path, capsys):
    from axiom_patch_agent import main
    diff_path = tmp_path / "p.diff"
    diff_path.write_text("diff --git a/x b/x\n@@\n+typo fix\n",
                          encoding="utf-8")
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "draft", "--bug-id", "LAT-1",
        "--target-file", "x", "--diff", str(diff_path),
        "--reasoning", "fix typo",
        "--bug-description", "Latency in handler",
        "--tests-passed", "1",
    ])
    out = capsys.readouterr().out
    patch_id = next(
        tok.strip() for tok in out.split() if tok.startswith("patch_")
    )
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "approve", patch_id, "--reviewer", "alice",
        "--apply", "none", "--force-irrelevant",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "relevance override recorded" in out


def test_cli_revoke_smoke(isolated, tmp_path, capsys):
    from axiom_patch_agent import main
    diff_path = tmp_path / "p.diff"
    diff_path.write_text("d\n", encoding="utf-8")
    main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "draft", "--bug-id", "x", "--target-file", "x.py",
        "--diff", str(diff_path), "--reasoning", "r",
        "--tests-passed", "1",
    ])
    out = capsys.readouterr().out
    patch_id = next(
        tok.strip() for tok in out.split() if tok.startswith("patch_")
    )
    main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "approve", patch_id, "--reviewer", "alice", "--apply", "none",
    ])
    capsys.readouterr()
    rc = main([
        "--drafts-dir", str(tmp_path / "drafts"), "--no-ledger",
        "revoke", patch_id,
        "--reviewer", "alice",
        "--reason", "approved the wrong thing",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "revoked" in out
    assert "verified=True" in out
