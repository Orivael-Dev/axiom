"""Tests for axiom_twitter_agent — halt-at-gate signed reply
drafting + paste-for-send (no API posting)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AXIOM_TWITTER_DRAFTS", str(tmp_path / "twitter"))
    monkeypatch.setenv(
        "AXIOM_TWITTER_AGENT_LEDGER",
        str(tmp_path / "twitter-ledger.jsonl"),
    )
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_twitter_agent", "axiom_exoskeleton_honesty",
        )):
            sys.modules.pop(mod, None)
    yield


# A deterministic fake backend whose .generate() returns a canned reply
# per framing. Avoids any network call + lets us drive honesty + length
# behavior from the test side.

@dataclass
class _FakeResult:
    text:       str
    backend:    str = "fake"
    model:      str = "fake-1"
    latency_ms: int = 12


class _FakeBackend:
    """Returns canned replies per framing. The agent's framing hint
    strings (see _DRAFT_FRAMINGS in axiom_twitter_agent) contain
    distinct unique substrings — we match on those."""

    # The unique fingerprints inside each framing hint that the
    # agent embeds in the prompt. Keep these in sync with
    # _DRAFT_FRAMINGS in axiom_twitter_agent.py.
    _FINGERPRINT = {
        "acknowledge": "acknowledging the point",
        "counter":     "pushing back",
        "artifact":    "concrete artifact",
    }

    def __init__(self, replies: dict | None = None):
        self.replies = replies or {}
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system: str, prompt: str,
                  max_output_tokens: int = 120):
        self.calls.append((system, prompt))
        for framing, fingerprint in self._FINGERPRINT.items():
            if fingerprint in prompt and framing in self.replies:
                return _FakeResult(text=self.replies[framing])
        # Default — a safe, clean reply.
        return _FakeResult(text="agreed, that's a fair point.")


def _make_agent(isolated, backend=None):
    from axiom_twitter_agent import TwitterAgent
    from axiom_twitter_agent_ledger import LedgerWriter, default_ledger_path
    return TwitterAgent(
        ledger=LedgerWriter(default_ledger_path()),
        backend=backend or _FakeBackend(),
    )


# ── Input dataclass validation ──────────────────────────────────────


def test_tweet_input_requires_text(isolated):
    from axiom_twitter_agent import TweetInput, TwitterAgentError
    with pytest.raises(TwitterAgentError):
        TweetInput.new(
            tweet_id="1", author_handle="bob",
            url="https://x.com/bob/status/1", text="",
        )


def test_tweet_input_strips_handle_prefix(isolated):
    from axiom_twitter_agent import TweetInput
    tw = TweetInput.new(
        tweet_id="1", author_handle="@alice",
        url="https://x.com/alice/status/1", text="hello",
    )
    assert tw.author_handle == "alice"


def test_tweet_input_round_trip(isolated, tmp_path):
    from axiom_twitter_agent import TweetInput, default_drafts_dir
    tw = TweetInput.new(
        tweet_id="42", author_handle="bob",
        url="https://x.com/bob/status/42", text="thing",
    )
    root = default_drafts_dir()
    tw.save(root)
    loaded = TweetInput.load(tw.input_id, root)
    assert loaded.text_hash == tw.text_hash
    assert loaded.tweet_id == "42"


# ── ingest + draft happy path ───────────────────────────────────────


def test_draft_creates_n_candidates(isolated):
    from axiom_twitter_agent import TweetInput
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1",
        text="agent security is the bottleneck right now",
    )
    agent.ingest(tw)
    drafts = agent.draft(tw.input_id, candidates=3)
    assert len(drafts) == 3
    framings = {d.framing for d in drafts}
    assert framings == {"acknowledge", "counter", "artifact"}
    for d in drafts:
        assert d.status == "pending"
        assert d.parent_tweet_id == "1"
        assert d.parent_text_hash == tw.text_hash
        assert d.char_count == len(d.reply_text)


def test_draft_candidates_count_validation(isolated):
    from axiom_twitter_agent import TweetInput, TwitterAgentError
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hello",
    )
    agent.ingest(tw)
    with pytest.raises(TwitterAgentError):
        agent.draft(tw.input_id, candidates=0)
    with pytest.raises(TwitterAgentError):
        agent.draft(tw.input_id, candidates=6)


# ── approve path: signs an EventToken + appends ledger ──────────────


def test_approve_signs_event_token_and_ledger(isolated):
    from axiom_twitter_agent import TweetInput
    from axiom_twitter_agent_ledger import read_ledger
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hello world",
    )
    agent.ingest(tw)
    drafts = agent.draft(tw.input_id, candidates=3)
    chosen = drafts[0]
    token = agent.approve(
        chosen.draft_id, reviewer_principal="alice@example.com",
    )
    assert token.verify() is True
    assert token.governance.payload["decision"] == "approve"
    assert token.governance.payload["reviewer_principal"] \
        == "alice@example.com"
    # Status moved to approved.
    after = agent.get_draft(chosen.draft_id)
    assert after.status == "approved"
    # Ledger entry signed under the twitter ledger namespace.
    entries = read_ledger()
    approves = [e for e in entries if e.decision == "approve"]
    assert len(approves) == 1
    assert approves[0].draft_id == chosen.draft_id
    assert approves[0].verify() is True


def test_approve_then_approve_again_refused(isolated):
    from axiom_twitter_agent import TweetInput, TwitterAgentError
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hello",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    agent.approve(d.draft_id, reviewer_principal="alice@example.com")
    with pytest.raises(TwitterAgentError):
        agent.approve(d.draft_id, reviewer_principal="alice@example.com")


# ── honesty gate refuses overclaim drafts ───────────────────────────


def test_honesty_refusal_blocks_approval_on_overclaim(isolated):
    from axiom_twitter_agent import TweetInput, HonestyRefusal
    # The exoskeleton honesty scanner flags invented track-record
    # claims like "AXIOM has helped startups..." — exact phrasing
    # taken from the existing overclaim test suite.
    backend = _FakeBackend(replies={
        "acknowledge":
            "AXIOM has helped startups reduce hallucination by 47%.",
        "counter":
            "have you tried logging?",
        "artifact":
            "we have a paper coming on this.",
    })
    agent = _make_agent(isolated, backend=backend)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1",
        text="how are people solving agent security?",
    )
    agent.ingest(tw)
    drafts = agent.draft(tw.input_id, candidates=3)
    overclaim = [d for d in drafts if d.framing == "acknowledge"][0]
    # The honesty scanner should have flagged the "has helped startups"
    # phrasing as a BLOCK finding.
    assert overclaim.honesty_block_count >= 1, (
        f"expected at least one block finding, got "
        f"{overclaim.honesty_findings}"
    )
    with pytest.raises(HonestyRefusal):
        agent.approve(
            overclaim.draft_id,
            reviewer_principal="alice@example.com",
        )


# ── reject path: feeds retrospect + signs ledger ─────────────────────


def test_reject_writes_improvement_record_and_ledger(isolated, tmp_path):
    from axiom_twitter_agent import TweetInput, TwitterAgent
    from axiom_twitter_agent_ledger import (
        LedgerWriter, default_ledger_path, read_ledger,
    )
    improvements = tmp_path / "improvements.jsonl"
    agent = TwitterAgent(
        ledger=LedgerWriter(default_ledger_path()),
        improvements_path=improvements,
        backend=_FakeBackend(),
    )
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="agent security?",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    token = agent.reject(
        d.draft_id,
        reviewer_principal="alice@example.com",
        reason="wrong tone",
    )
    assert token.verify() is True
    assert token.governance.payload["decision"] == "reject"
    assert token.governance.payload["rejection_reason"] == "wrong tone"
    # Improvement record appended.
    lines = improvements.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["current_verdict"] == "REJECTED"
    assert rec["training_signal"] == "negative"
    assert "twitter_agent_rejection:wrong tone" \
        in rec["improvement_cause"]
    # Ledger entry signed.
    rejects = [e for e in read_ledger() if e.decision == "reject"]
    assert len(rejects) == 1
    assert rejects[0].rejection_reason == "wrong tone"
    assert rejects[0].verify() is True


def test_reject_requires_reason(isolated):
    from axiom_twitter_agent import TweetInput, TwitterAgentError
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    with pytest.raises(TwitterAgentError):
        agent.reject(d.draft_id,
                     reviewer_principal="alice@example.com",
                     reason="")


# ── over-limit refusal ───────────────────────────────────────────────


def test_over_limit_draft_refused_at_approval(isolated):
    from axiom_twitter_agent import (
        TweetInput, TwitterAgentError, MAX_REPLY_CHARS,
    )
    long_text = "x" * (MAX_REPLY_CHARS + 50)
    backend = _FakeBackend(replies={
        "acknowledge": long_text,
        "counter":     long_text,
        "artifact":    long_text,
    })
    agent = _make_agent(isolated, backend=backend)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    drafts = agent.draft(tw.input_id, candidates=1)
    d = drafts[0]
    assert d.over_limit is True
    assert d.char_count > MAX_REPLY_CHARS
    with pytest.raises(TwitterAgentError):
        agent.approve(d.draft_id,
                      reviewer_principal="alice@example.com")


# ── mark-sent: requires approval + signed ────────────────────────────


def test_mark_sent_requires_prior_approval(isolated):
    from axiom_twitter_agent import TweetInput, TwitterAgentError
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    with pytest.raises(TwitterAgentError):
        agent.mark_sent(d.draft_id)


def test_mark_sent_after_approval_signs_ledger(isolated):
    from axiom_twitter_agent import TweetInput
    from axiom_twitter_agent_ledger import read_ledger
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    agent.approve(d.draft_id, reviewer_principal="alice@example.com")
    after_sent = agent.mark_sent(
        d.draft_id, sent_at="2026-05-22T15:30:00Z",
    )
    assert after_sent.status == "sent"
    assert after_sent.sent_at == "2026-05-22T15:30:00Z"
    sent_entries = [
        e for e in read_ledger() if e.decision == "sent"
    ]
    assert len(sent_entries) == 1
    assert sent_entries[0].sent_at == "2026-05-22T15:30:00Z"
    assert sent_entries[0].verify() is True


# ── verify() returns a useful diagnostic dict ────────────────────────


def test_verify_reports_event_token_status(isolated):
    from axiom_twitter_agent import TweetInput
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    # Before approval no event token exists.
    pre = agent.verify(d.draft_id)
    assert pre["status"] == "pending"
    assert pre["event_token_verified"] is None
    agent.approve(d.draft_id, reviewer_principal="alice@example.com")
    post = agent.verify(d.draft_id)
    assert post["status"] == "approved"
    assert post["event_token_verified"] is True


# ── tamper detection on draft.json ───────────────────────────────────


def test_loading_tampered_draft_raises(isolated):
    from axiom_twitter_agent import (
        TweetInput, TweetReplyDraft, TwitterAgentError,
        default_drafts_dir,
    )
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    # Tamper: rewrite reply_text without updating reply_hash.
    p = default_drafts_dir() / "drafts" / d.draft_id / "draft.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["reply_text"] = raw["reply_text"] + " EDITED"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TwitterAgentError):
        TweetReplyDraft.load(d.draft_id, default_drafts_dir())


# ── ledger entry tamper detection ────────────────────────────────────


def test_ledger_signature_detects_tamper(isolated):
    from axiom_twitter_agent import TweetInput
    from axiom_twitter_agent_ledger import (
        default_ledger_path, read_ledger,
    )
    agent = _make_agent(isolated)
    tw = TweetInput.new(
        tweet_id="1", author_handle="bob",
        url="https://x.com/bob/status/1", text="hi",
    )
    agent.ingest(tw)
    d = agent.draft(tw.input_id, candidates=1)[0]
    agent.approve(d.draft_id, reviewer_principal="alice@example.com")
    path = default_ledger_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["reviewer_principal"] = "mallory@evil.com"
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    [entry] = read_ledger()
    # Signature was computed over the original principal; tampered
    # version must fail verification.
    assert entry.verify() is False
