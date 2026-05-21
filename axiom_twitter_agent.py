"""Twitter scraper + signed-reply agent — halt-at-gate, paste-for-send.

Mirrors the Patch Agent pattern: a tweet (the input) gets signed
as an EventToken, an LLM drafts N=3 candidate replies (each its
own signed EventToken), the founder picks one at the gate, and
the approval is signed into the chosen draft's governance slot.

There is NO actual Twitter API posting in this module. Approval =
the draft is marked approved + the chosen text is surfaced for
the founder to copy-paste into Twitter manually. The
`mark_sent(draft_id, sent_at)` step records that the manual paste
happened, signed into the ledger.

This intentional ToS-clean design avoids:
  - X API Pro tier costs
  - OAuth token storage for the posting account
  - Auto-posting side effects
  - Rate-limit policing
…while still giving the founder a fully audited draft-approval
workflow.

CLI:
    python3 -m axiom_twitter_agent ingest \\
        --tweet-id 1234567890 --author "@somebody" \\
        --url https://x.com/somebody/status/1234567890 \\
        --text "Anyone else worried about agent security right now?"
    python3 -m axiom_twitter_agent draft <input_id> [--candidates 3]
    python3 -m axiom_twitter_agent list
    python3 -m axiom_twitter_agent show <draft_id>
    python3 -m axiom_twitter_agent approve <draft_id> \\
        --reviewer alice@example.com
    python3 -m axiom_twitter_agent reject <draft_id> \\
        --reviewer alice@example.com --reason "wrong tone"
    python3 -m axiom_twitter_agent mark-sent <draft_id> \\
        --sent-at 2026-05-22T15:30:00Z

CANNOT_MUTATE rules:
    - honesty_block_count > 0 → approval refused
    - draft can be approved only once
    - approved draft's text is sealed to the parent_tweet_id
    - sent timestamp can be set only after approval
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# ── Storage layout ───────────────────────────────────────────────────


DEFAULT_DRAFTS_DIR = Path.home() / ".axiom" / "twitter"


def default_drafts_dir() -> Path:
    p = os.environ.get("AXIOM_TWITTER_DRAFTS")
    if p:
        return Path(p).expanduser()
    return DEFAULT_DRAFTS_DIR


# ── Errors ───────────────────────────────────────────────────────────


class TwitterAgentError(RuntimeError):
    """Validation, gate, or signature error."""


class HonestyRefusal(TwitterAgentError):
    """Honesty scan found a BLOCK-severity finding; approval refused."""


# ── Helpers ──────────────────────────────────────────────────────────


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# Twitter's hard limit is 280 chars; 270 leaves room for handle
# prefix when replying to threads.
MAX_REPLY_CHARS = 270


# Constitutional system prompt for the reply drafter. Short on
# purpose — Twitter audience expects compactness. The honesty
# preamble is appended at runtime so the same overclaim rules
# the exoskeleton enforces apply here too.
_TWITTER_REPLY_SYSTEM = (
    "You are AXIOM's Twitter-reply drafter. Given the text of a "
    "tweet, draft ONE candidate reply that:\n"
    f"- Stays under {MAX_REPLY_CHARS} characters\n"
    "- Engages with the actual point the parent tweet makes\n"
    "- Avoids any claim AXIOM has not publicly shipped\n"
    "- Uses lowercase-first sentence style\n"
    "- No buzzwords (revolutionary / synergize / leverage)\n"
    "- No hashtags or emoji unless the parent tweet uses them\n"
    "- If the parent tweet is hostile, stay calm + factual\n"
    "Output ONLY the reply text. No preamble, no quotes, no "
    "markdown."
)


# Three diversity framings so N=3 candidates don't all sound the
# same. The drafter picks one per candidate.
_DRAFT_FRAMINGS: tuple[tuple[str, str], ...] = (
    ("acknowledge",
     "Respond by acknowledging the point + adding ONE specific "
     "observation that extends it."),
    ("counter",
     "Respond by gently pushing back with a counter-question. "
     "Do not be dismissive."),
    ("artifact",
     "Respond by referencing a concrete artifact (a paper, a "
     "benchmark, a repo). If you have nothing concrete to "
     "reference, say so explicitly instead of inventing."),
)


# ── Data classes ────────────────────────────────────────────────────


@dataclass
class TweetInput:
    """A tweet that the agent will consider replying to.

    No actual scraping happens in this module — the founder
    pastes the tweet (text + URL + author) via the CLI or UI.
    The input event token is signed at ingest time so the audit
    trail shows exactly which tweet was considered, not just
    its synthesized rewrite.
    """
    input_id:          str
    tweet_id:          str
    author_handle:     str
    url:               str
    text:              str
    text_hash:         str
    ingested_at:       str
    parent_thread_id:  Optional[str] = None

    @classmethod
    def new(
        cls,
        *,
        tweet_id: str,
        author_handle: str,
        url: str,
        text: str,
        parent_thread_id: Optional[str] = None,
    ) -> "TweetInput":
        if not tweet_id or not str(tweet_id).strip():
            raise TwitterAgentError("tweet_id is required")
        if not text or not text.strip():
            raise TwitterAgentError("tweet text must be non-empty")
        if not url or not url.strip():
            raise TwitterAgentError("tweet url is required")
        handle = (author_handle or "").lstrip("@").strip()
        if not handle:
            raise TwitterAgentError("author_handle is required")
        return cls(
            input_id=f"twin_{uuid.uuid4().hex[:12]}",
            tweet_id=str(tweet_id).strip(),
            author_handle=handle,
            url=url.strip(),
            text=text.strip(),
            text_hash=_sha256_text(text.strip()),
            ingested_at=_utc_now(),
            parent_thread_id=parent_thread_id,
        )

    def dir(self, root: Path) -> Path:
        return root / "inputs" / self.input_id

    def save(self, root: Path) -> Path:
        d = self.dir(root)
        d.mkdir(parents=True, exist_ok=True)
        meta = asdict(self)
        (d / "input.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        return d

    @classmethod
    def load(cls, input_id: str, root: Path) -> "TweetInput":
        d = root / "inputs" / input_id
        if not d.is_dir():
            raise TwitterAgentError(f"no input found at {d}")
        meta = json.loads(
            (d / "input.json").read_text(encoding="utf-8")
        )
        return cls(**meta)


@dataclass
class TweetReplyDraft:
    """One candidate reply, halted pending human approval."""
    draft_id:              str
    input_id:              str
    parent_tweet_id:       str
    parent_text_hash:      str
    parent_author_handle:  str
    parent_url:            str
    framing:               str   # acknowledge|counter|artifact
    reply_text:            str
    reply_hash:            str
    char_count:            int
    over_limit:            bool
    honesty_block_count:   int
    honesty_flag_count:    int
    honesty_findings:      list
    backend:               str
    model:                 str
    latency_ms:            int
    drafted_at:            str
    status:                str = "pending"   # pending | approved | rejected | sent
    sent_at:               Optional[str] = None

    @classmethod
    def new(
        cls,
        *,
        input_obj: TweetInput,
        framing: str,
        reply_text: str,
        backend: str,
        model: str,
        latency_ms: int,
        honesty_findings: list,
        honesty_block_count: int,
        honesty_flag_count: int,
    ) -> "TweetReplyDraft":
        reply = (reply_text or "").strip()
        if not reply:
            raise TwitterAgentError("reply_text must be non-empty")
        return cls(
            draft_id=f"twdr_{uuid.uuid4().hex[:12]}",
            input_id=input_obj.input_id,
            parent_tweet_id=input_obj.tweet_id,
            parent_text_hash=input_obj.text_hash,
            parent_author_handle=input_obj.author_handle,
            parent_url=input_obj.url,
            framing=framing,
            reply_text=reply,
            reply_hash=_sha256_text(reply),
            char_count=len(reply),
            over_limit=(len(reply) > MAX_REPLY_CHARS),
            honesty_block_count=int(honesty_block_count),
            honesty_flag_count=int(honesty_flag_count),
            honesty_findings=list(honesty_findings or []),
            backend=str(backend),
            model=str(model),
            latency_ms=int(latency_ms),
            drafted_at=_utc_now(),
        )

    def dir(self, root: Path) -> Path:
        return root / "drafts" / self.draft_id

    def save(self, root: Path) -> Path:
        d = self.dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.json").write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        return d

    @classmethod
    def load(cls, draft_id: str, root: Path) -> "TweetReplyDraft":
        d = root / "drafts" / draft_id
        if not d.is_dir():
            raise TwitterAgentError(f"no draft found at {d}")
        meta = json.loads(
            (d / "draft.json").read_text(encoding="utf-8")
        )
        # Tamper check on the reply text.
        if _sha256_text(meta["reply_text"]) != meta["reply_hash"]:
            raise TwitterAgentError(
                f"reply_hash mismatch for {draft_id} — "
                f"draft.json was edited after drafting (tamper)."
            )
        return cls(**meta)

    def update_status(
        self, status: str, root: Path, *,
        sent_at: Optional[str] = None,
    ) -> None:
        if status not in ("pending", "approved", "rejected", "sent"):
            raise TwitterAgentError(f"unknown status: {status!r}")
        self.status = status
        if sent_at is not None:
            self.sent_at = sent_at
        (self.dir(root) / "draft.json").write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )


# ── TwitterAgent — top-level orchestrator ───────────────────────────


class TwitterAgent:
    """Ingest a tweet, draft N candidates, halt at the gate, sign on
    approve, surface for paste-and-send."""

    def __init__(
        self,
        *,
        drafts_dir: Optional[Path] = None,
        improvements_path: Optional[Path] = None,
        ledger=None,
        backend=None,
    ) -> None:
        self._drafts_dir = (
            Path(drafts_dir) if drafts_dir else default_drafts_dir()
        )
        self._drafts_dir.mkdir(parents=True, exist_ok=True)
        # Reuse the existing dev-agent improvements file so rejected
        # drafts feed the same retrospect pipeline that already
        # ingests Patch Agent + Dev Agent rejections.
        self._improvements_path = (
            Path(improvements_path) if improvements_path
            else Path("dev_agent_improvements.jsonl")
        )
        self._ledger = ledger
        self._backend = backend     # SLMBackend, None = default

    # ── 1. ingest ──────────────────────────────────────────────────

    def ingest(self, tweet: TweetInput) -> TweetInput:
        tweet.save(self._drafts_dir)
        return tweet

    def get_input(self, input_id: str) -> TweetInput:
        return TweetInput.load(input_id, self._drafts_dir)

    # ── 2. draft N candidates ──────────────────────────────────────

    def draft(
        self,
        input_id: str,
        *,
        candidates: int = 3,
    ) -> list[TweetReplyDraft]:
        if candidates < 1 or candidates > 5:
            raise TwitterAgentError(
                "candidates must be 1..5 (3 is the default; >5 = "
                "decision fatigue, <1 = no comparison signal)"
            )
        tweet = self.get_input(input_id)
        backend = self._backend or self._default_backend()
        framings = _DRAFT_FRAMINGS[:candidates]
        if candidates > len(_DRAFT_FRAMINGS):
            # Pad by re-using the first framing.
            framings = (
                framings
                + _DRAFT_FRAMINGS[:candidates - len(_DRAFT_FRAMINGS)]
            )
        drafts: list[TweetReplyDraft] = []
        for framing_name, framing_hint in framings:
            user_prompt = (
                f"PARENT TWEET (@{tweet.author_handle}): "
                f"{tweet.text}\n\n"
                f"FRAMING: {framing_hint}\n\n"
                f"Draft the reply now."
            )
            import time
            t0 = time.monotonic()
            try:
                result = backend.generate(
                    system=_TWITTER_REPLY_SYSTEM,
                    prompt=user_prompt,
                    max_output_tokens=120,   # ~270 chars
                )
                reply_text = result.text.strip()
                backend_name = result.backend
                model_name   = result.model
                latency_ms   = result.latency_ms
            except Exception as e:
                # Honest fallback: surface the failure as a draft
                # with an obvious placeholder + a flagged status.
                # The reviewer sees what broke instead of silently
                # losing a candidate.
                latency_ms = int((time.monotonic() - t0) * 1000)
                reply_text = (
                    f"(draft generation failed for framing "
                    f"{framing_name!r}: {type(e).__name__}: {e})"
                )
                backend_name = "error"
                model_name   = "error"
            # Run the honesty scan that already exists in the
            # exoskeleton — same rules, same governance namespace.
            findings_dicts, block_count, flag_count = _scan_honesty(
                reply_text
            )
            d = TweetReplyDraft.new(
                input_obj=tweet,
                framing=framing_name,
                reply_text=reply_text,
                backend=backend_name,
                model=model_name,
                latency_ms=latency_ms,
                honesty_findings=findings_dicts,
                honesty_block_count=block_count,
                honesty_flag_count=flag_count,
            )
            d.save(self._drafts_dir)
            drafts.append(d)
        return drafts

    # ── 3. inspection ──────────────────────────────────────────────

    def list_pending(self) -> list[TweetReplyDraft]:
        out: list[TweetReplyDraft] = []
        drafts_root = self._drafts_dir / "drafts"
        if not drafts_root.is_dir():
            return out
        for entry in sorted(drafts_root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                d = TweetReplyDraft.load(entry.name, self._drafts_dir)
            except TwitterAgentError:
                continue
            if d.status == "pending":
                out.append(d)
        return out

    def get_draft(self, draft_id: str) -> TweetReplyDraft:
        return TweetReplyDraft.load(draft_id, self._drafts_dir)

    # ── 4. approve ─────────────────────────────────────────────────

    def approve(
        self,
        draft_id: str,
        *,
        reviewer_principal: str,
    ):
        """Sign approval, append ledger entry, surface the chosen text.

        Refuses with HonestyRefusal if the draft tripped any
        BLOCK-severity honesty findings — same gate the
        exoskeleton runs.
        """
        if not reviewer_principal or not reviewer_principal.strip():
            raise TwitterAgentError("reviewer_principal is required")
        draft = self.get_draft(draft_id)
        if draft.status != "pending":
            raise TwitterAgentError(
                f"draft {draft_id} is already {draft.status}"
            )
        if draft.honesty_block_count > 0:
            cats = sorted({
                f.get("category", "?")
                for f in draft.honesty_findings
                if f.get("severity") == "block"
            })
            raise HonestyRefusal(
                f"Honesty refused: {draft.honesty_block_count} BLOCK "
                f"finding(s) — categories: {cats}. The draft makes a "
                f"claim AXIOM has not publicly shipped. Re-draft "
                f"(`draft {draft.input_id}`) instead of approving."
            )
        if draft.over_limit:
            raise TwitterAgentError(
                f"draft is {draft.char_count} chars (> {MAX_REPLY_CHARS}). "
                f"Approval blocked — Twitter would reject the post."
            )

        token = self._build_event_token(
            draft=draft,
            decision="approve",
            reviewer_principal=reviewer_principal.strip(),
            rejection_reason=None,
        )
        draft.update_status("approved", self._drafts_dir)
        if self._ledger is not None:
            self._ledger.append(
                draft=draft, token=token,
                decision="approve",
                reviewer_principal=reviewer_principal.strip(),
                rejection_reason=None,
            )
        return token

    # ── 5. reject ──────────────────────────────────────────────────

    def reject(
        self,
        draft_id: str,
        *,
        reviewer_principal: str,
        reason: str,
    ):
        if not reviewer_principal or not reviewer_principal.strip():
            raise TwitterAgentError("reviewer_principal is required")
        if not reason or not reason.strip():
            raise TwitterAgentError("reason is required for rejection")
        draft = self.get_draft(draft_id)
        if draft.status != "pending":
            raise TwitterAgentError(
                f"draft {draft_id} is already {draft.status}"
            )
        token = self._build_event_token(
            draft=draft,
            decision="reject",
            reviewer_principal=reviewer_principal.strip(),
            rejection_reason=reason.strip(),
        )
        # Feed the same retrospect pipeline the Patch Agent + Dev
        # Agent already write to — one improvements file, three
        # producers, one consumer.
        improvement = {
            "input_text":
                f"reply to @{draft.parent_author_handle}: "
                f"{draft.parent_url}\n"
                f"---\n{draft.reply_text}",
            "former_self_verdict": "PROPOSED",
            "current_verdict":     "REJECTED",
            "improvement_cause":
                f"twitter_agent_rejection:{reason.strip()}",
            "training_signal":     "negative",
            "hmac_signature":      _governance_sig(token) or "",
        }
        self._improvements_path.parent.mkdir(parents=True, exist_ok=True)
        with self._improvements_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(improvement,
                                ensure_ascii=True, sort_keys=True) + "\n")
        draft.update_status("rejected", self._drafts_dir)
        if self._ledger is not None:
            self._ledger.append(
                draft=draft, token=token,
                decision="reject",
                reviewer_principal=reviewer_principal.strip(),
                rejection_reason=reason.strip(),
            )
        return token

    # ── 6. mark sent (paste-and-confirm) ───────────────────────────

    def mark_sent(
        self,
        draft_id: str,
        *,
        sent_at: Optional[str] = None,
    ):
        """Record that the founder manually pasted the approved
        reply on Twitter. The system has no way to verify the
        post actually happened — this is a self-attestation,
        signed into the ledger so it's at least tamper-evident."""
        draft = self.get_draft(draft_id)
        if draft.status != "approved":
            raise TwitterAgentError(
                f"draft is {draft.status}; only 'approved' drafts "
                f"can be marked sent"
            )
        ts = sent_at or _utc_now()
        draft.update_status("sent", self._drafts_dir, sent_at=ts)
        if self._ledger is not None:
            # Re-load the approval token to reference it from the
            # mark-sent ledger entry.
            token_path = (
                self._drafts_dir / "drafts" / draft_id /
                "event_token.json"
            )
            ref_id = ""
            if token_path.exists():
                try:
                    ref_id = json.loads(
                        token_path.read_text(encoding="utf-8")
                    )["id"]
                except Exception:
                    pass
            # Append a "sent" ledger entry — same shape, different
            # decision label.
            self._ledger.append_sent(
                draft=draft,
                referenced_token_id=ref_id,
                sent_at=ts,
            )
        return draft

    # ── verify + internals ─────────────────────────────────────────

    def verify(self, draft_id: str) -> dict:
        draft = self.get_draft(draft_id)
        token_path = (
            self._drafts_dir / "drafts" / draft_id /
            "event_token.json"
        )
        result: dict = {
            "draft_id":            draft.draft_id,
            "status":              draft.status,
            "reply_hash_matches":  True,   # PatchDraft.load already checked
            "event_token_verified": None,
            "honesty_block_count": draft.honesty_block_count,
            "honesty_flag_count":  draft.honesty_flag_count,
            "char_count":          draft.char_count,
            "over_limit":          draft.over_limit,
        }
        if token_path.exists():
            from axiom_event_token.models import EventToken
            try:
                t = EventToken.from_dict(
                    json.loads(token_path.read_text(encoding="utf-8"))
                )
                result["event_token_verified"] = bool(t.verify())
                result["event_token_id"] = t.id
            except Exception:
                result["event_token_verified"] = False
        return result

    def _build_event_token(
        self,
        *,
        draft: TweetReplyDraft,
        decision: str,
        reviewer_principal: str,
        rejection_reason: Optional[str],
    ):
        """Sign an EventToken whose text layer carries the reply +
        framing and whose governance layer carries the decision +
        reviewer + parent_tweet binding + honesty findings."""
        from axiom_event_token.models import (
            EventToken, LayerReport, _canonical_coordinator,
            _canonical_token, _sign, COORD_KEY_NS, TOKEN_KEY_NS,
        )
        from axiom_event_token.coordinator import _token_kwargs

        text_layer = LayerReport.signed(
            agent="twitter_agent_v1",
            payload={
                "delegate":             "twitter_agent_v1",
                "draft_id":             draft.draft_id,
                "input_id":             draft.input_id,
                "parent_tweet_id":      draft.parent_tweet_id,
                "parent_author_handle": draft.parent_author_handle,
                "parent_url":           draft.parent_url,
                "framing":              draft.framing,
                "reply_text":           draft.reply_text,
                "char_count":           draft.char_count,
                "backend":              draft.backend,
                "model":                draft.model,
                "latency_ms":           draft.latency_ms,
            },
            confidence=0.9 if decision == "approve" else 0.5,
        )
        gov_payload = {
            "decision":              decision,
            "reviewer_principal":    reviewer_principal,
            "decision_at":           _utc_now(),
            "reply_hash":            draft.reply_hash,
            "parent_text_hash":      draft.parent_text_hash,
            "honesty_block_count":   draft.honesty_block_count,
            "honesty_flag_count":    draft.honesty_flag_count,
            "honesty_findings":      draft.honesty_findings,
            "over_limit":            draft.over_limit,
        }
        if rejection_reason:
            gov_payload["rejection_reason"] = rejection_reason
        gov_layer = LayerReport.signed(
            agent="twitter_agent_governance_v1",
            payload=gov_payload,
            confidence=1.0,
        )

        token = EventToken(
            id=f"twitter_{draft.draft_id}",
            created_at=_utc_now(),
            activated_agents=("twitter_agent_v1",
                              "twitter_agent_governance_v1"),
            text=text_layer,
            governance=gov_layer,
        )
        coord_sig = _sign(_canonical_coordinator(token),
                          COORD_KEY_NS)
        token = EventToken(**{**_token_kwargs(token),
                              "coordinator_sig": coord_sig})
        outer_sig = _sign(_canonical_token(token), TOKEN_KEY_NS)
        token = EventToken(**{**_token_kwargs(token),
                              "signature": outer_sig})

        token_path = (
            self._drafts_dir / "drafts" / draft.draft_id /
            "event_token.json"
        )
        token_path.write_text(token.to_json(indent=2),
                              encoding="utf-8")
        return token

    def _default_backend(self):
        from axiom_event_token.backends import default_backend
        return default_backend()


def _governance_sig(token) -> str:
    return token.governance.signature if token.governance else ""


def _scan_honesty(text: str) -> tuple[list, int, int]:
    """Run the existing exoskeleton honesty scanner over a reply
    candidate. Returns (findings_as_dicts, block_count, flag_count).
    Best-effort: import failures degrade to empty findings (the
    approval still gates on char-count + over_limit)."""
    try:
        from axiom_exoskeleton_honesty import scan as _hscan
    except Exception:
        return [], 0, 0
    try:
        r = _hscan(text or "")
    except Exception:
        return [], 0, 0
    return (
        [f.to_dict() for f in r.findings],
        int(r.block_count),
        int(r.flag_count),
    )


# ── CLI ──────────────────────────────────────────────────────────────


def _cmd_ingest(args, agent: TwitterAgent) -> int:
    try:
        tweet = TweetInput.new(
            tweet_id=args.tweet_id,
            author_handle=args.author,
            url=args.url,
            text=args.text,
        )
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    agent.ingest(tweet)
    print(f"ingested {tweet.input_id}  tweet={tweet.tweet_id}  "
          f"author=@{tweet.author_handle}")
    print(f"  next: axiom_twitter_agent draft {tweet.input_id}")
    return 0


def _cmd_draft(args, agent: TwitterAgent) -> int:
    try:
        drafts = agent.draft(args.input_id, candidates=args.candidates)
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"drafted {len(drafts)} candidate(s):")
    for d in drafts:
        gate = "✓" if d.honesty_block_count == 0 and not d.over_limit \
               else ("⚠ honesty" if d.honesty_block_count else "⚠ len")
        print(
            f"  {d.draft_id}  framing={d.framing}  "
            f"chars={d.char_count}/{MAX_REPLY_CHARS}  gate={gate}"
        )
        print(f"    {d.reply_text}")
    return 0


def _cmd_list(args, agent: TwitterAgent) -> int:
    pending = agent.list_pending()
    if not pending:
        print("(no pending drafts)")
        return 0
    for d in pending:
        gate = ("BLOCK" if d.honesty_block_count else
                ("LEN"  if d.over_limit else "✓"))
        print(
            f"{d.draft_id}  gate={gate}  framing={d.framing}  "
            f"chars={d.char_count}  parent=@{d.parent_author_handle}  "
            f"drafted={d.drafted_at}"
        )
    return 0


def _cmd_show(args, agent: TwitterAgent) -> int:
    try:
        d = agent.get_draft(args.draft_id)
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"# draft_id:          {d.draft_id}")
    print(f"# parent_tweet:      {d.parent_tweet_id}  "
          f"(@{d.parent_author_handle})")
    print(f"# parent_url:        {d.parent_url}")
    print(f"# framing:           {d.framing}")
    print(f"# chars:             {d.char_count} / {MAX_REPLY_CHARS}  "
          f"over_limit={d.over_limit}")
    print(f"# honesty:           "
          f"{d.honesty_block_count} block / {d.honesty_flag_count} flag")
    if d.honesty_findings:
        for f in d.honesty_findings:
            print(f"    [{f.get('severity', '?').upper()}] "
                  f"{f.get('category', '?')}: "
                  f"{f.get('matched', '')!r}")
    print(f"# backend / model:   {d.backend} / {d.model}  "
          f"latency={d.latency_ms}ms")
    print(f"# status:            {d.status}"
          + (f"  sent_at={d.sent_at}" if d.sent_at else ""))
    print(f"# drafted_at:        {d.drafted_at}")
    print()
    print(d.reply_text)
    return 0


def _cmd_approve(args, agent: TwitterAgent) -> int:
    try:
        token = agent.approve(
            args.draft_id, reviewer_principal=args.reviewer,
        )
    except HonestyRefusal as e:
        print(f"HONESTY REFUSED: {e}", file=sys.stderr)
        return 4
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    d = agent.get_draft(args.draft_id)
    print(f"approved {args.draft_id}")
    print(f"  signed_event_id={token.id}  verified={token.verify()}")
    print()
    print("─── copy this and paste into Twitter ─────────────────────")
    print(d.reply_text)
    print("──────────────────────────────────────────────────────────")
    print(f"After posting, run:  axiom_twitter_agent mark-sent "
          f"{args.draft_id}")
    return 0


def _cmd_reject(args, agent: TwitterAgent) -> int:
    try:
        token = agent.reject(
            args.draft_id,
            reviewer_principal=args.reviewer,
            reason=args.reason,
        )
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"rejected {args.draft_id}")
    print(f"  signed_event_id={token.id}  verified={token.verify()}")
    print(f"  improvement record appended to "
          f"{agent._improvements_path}")
    return 0


def _cmd_mark_sent(args, agent: TwitterAgent) -> int:
    try:
        agent.mark_sent(args.draft_id, sent_at=args.sent_at)
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    d = agent.get_draft(args.draft_id)
    print(f"marked sent: {args.draft_id}  sent_at={d.sent_at}")
    return 0


def _cmd_verify(args, agent: TwitterAgent) -> int:
    try:
        result = agent.verify(args.draft_id)
    except TwitterAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    if result.get("event_token_verified") is False:
        return 1
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-twitter-agent",
        description="Halt-at-gate Twitter reply drafting + signed "
                    "approval. Paste-for-output — no API posting.",
    )
    ap.add_argument("--drafts-dir",
                    help="default ~/.axiom/twitter  "
                         "(override env AXIOM_TWITTER_DRAFTS)")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--improvements-path",
                    help="default ./dev_agent_improvements.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="register a tweet for reply drafting")
    p.add_argument("--tweet-id", required=True)
    p.add_argument("--author", required=True,
                   help="parent tweet's author handle (@ optional)")
    p.add_argument("--url", required=True)
    p.add_argument("--text", required=True,
                   help="parent tweet's body text")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("draft",
                       help="generate N=3 candidate replies + halt")
    p.add_argument("input_id")
    p.add_argument("--candidates", type=int, default=3)
    p.set_defaults(func=_cmd_draft)

    p = sub.add_parser("list",
                       help="show pending drafts awaiting review")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("show", help="show a draft's full text + meta")
    p.add_argument("draft_id")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("approve",
                       help="sign approval, surface text for paste")
    p.add_argument("draft_id")
    p.add_argument("--reviewer", required=True)
    p.set_defaults(func=_cmd_approve)

    p = sub.add_parser("reject",
                       help="sign rejection, feed retrospect")
    p.add_argument("draft_id")
    p.add_argument("--reviewer", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_cmd_reject)

    p = sub.add_parser("mark-sent",
                       help="record that you pasted the approved "
                            "reply on Twitter")
    p.add_argument("draft_id")
    p.add_argument("--sent-at",
                   help="ISO-8601 UTC; default = now")
    p.set_defaults(func=_cmd_mark_sent)

    p = sub.add_parser("verify",
                       help="re-verify signatures + reply hash")
    p.add_argument("draft_id")
    p.set_defaults(func=_cmd_verify)

    args = ap.parse_args(list(argv) if argv is not None else None)

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32 bytes hex).",
              file=sys.stderr)
        return 2

    ledger = None
    if not args.no_ledger:
        from axiom_twitter_agent_ledger import (
            LedgerWriter, default_ledger_path,
        )
        ledger = LedgerWriter(default_ledger_path())

    agent = TwitterAgent(
        drafts_dir=(Path(args.drafts_dir) if args.drafts_dir else None),
        improvements_path=(Path(args.improvements_path)
                            if args.improvements_path else None),
        ledger=ledger,
    )
    return args.func(args, agent)


if __name__ == "__main__":
    raise SystemExit(main())
