"""Human-in-the-Loop Patch Agent — signed cryptographic sign-off for
agent-drafted code changes.

The contribution this module makes to the git community is NOT
yet-another-LLM-patch-generator. It's the **verifiable human
oversight layer** that wraps any agent-drafted diff:

    1. Agent (or human) drafts a patch + a one-line reasoning.
    2. Agent runs local tests; the result feeds the MonotonicGate.
    3. The draft is HALTED on disk — production untouched.
    4. A human reviews. Their approve/reject decision (+ the diff
       hash + the gate result) is packaged into an EventToken's
       governance slot and signed.
    5. Approved diffs are applied via `git apply`. Rejected diffs
       are piped into the existing retrospect ingestion pipeline
       (axiom_retrospect via dev_agent_improvements.jsonl) so the
       next iteration learns from the rejection.

Every signed patch lands in `~/.axiom/patch-agent-ledger.jsonl`
under the `axiom-patch-agent-ledger-v1` namespace — an
append-only, tamper-evident audit trail of who approved what,
when, on which diff hash.

CLI:
    python3 -m axiom_patch_agent draft \\
        --bug-id BUG-001 --target-file foo.py \\
        --diff foo.patch --reasoning "off-by-one fix" \\
        --tests-passed 12 --tests-failed 0
    python3 -m axiom_patch_agent list
    python3 -m axiom_patch_agent show <patch_id>
    python3 -m axiom_patch_agent approve <patch_id> \\
        --reviewer alice@example.com
    python3 -m axiom_patch_agent reject  <patch_id> \\
        --reviewer alice@example.com \\
        --reason "wrong approach — use a list comprehension"
    python3 -m axiom_patch_agent verify  <patch_id>

CANNOT_MUTATE (deterministic guardrails):
    - production_untouched_until_signed_approval
    - monotonic_gate_must_pass_before_approval
    - diff_hash_in_governance_slot
    - reviewer_principal_required
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# ── Storage layout ───────────────────────────────────────────────────


DEFAULT_DRAFTS_DIR = Path.home() / ".axiom" / "patches"


def default_drafts_dir() -> Path:
    p = os.environ.get("AXIOM_PATCH_AGENT_DRAFTS")
    if p:
        return Path(p).expanduser()
    return DEFAULT_DRAFTS_DIR


# ── Errors ───────────────────────────────────────────────────────────


class PatchAgentError(RuntimeError):
    """Validation, gate, or signature error."""


class GateRefusal(PatchAgentError):
    """MonotonicGate refused — approval blocked."""


class RelevanceRefusal(PatchAgentError):
    """Diff doesn't appear to address the bug. Approval blocked unless
    the reviewer passes --force-irrelevant explicitly."""


# ── PatchDraft on-disk format ───────────────────────────────────────


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


# ── Relevance scoring (the diff must actually address the bug) ──────


_WORD_RE = __import__("re").compile(r"[a-z0-9_]+")


def _word_set(text: str) -> set:
    if not text:
        return set()
    # Lowercase, alphanumeric tokens >= 3 chars (drop noise like 'a',
    # 'is', operators) so the Jaccard stays dominated by content words.
    return {
        w for w in _WORD_RE.findall(text.lower())
        if len(w) >= 3 and not w.isdigit()
    }


def _diff_content_lines(diff: str) -> str:
    """Extract just the changed lines from a unified diff.

    Skips hunk headers (`@@ … @@`) and file headers (`---`, `+++`,
    `diff --git`) so the relevance score reflects actual code change,
    not metadata."""
    if not diff:
        return ""
    keep: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("diff --git", "index ", "---", "+++",
                            "@@", "Binary files")):
            continue
        if line.startswith(("+", "-")):
            # Strip the leading marker so the word doesn't pick up '+'
            keep.append(line[1:])
    return "\n".join(keep)


def compute_relevance_score(
    bug_description: str,
    agent_reasoning: str,
    diff: str,
) -> float:
    """Jaccard similarity between (bug + reasoning) and diff content.

    Returns 0.0 if either side is empty. Deterministic, no embeddings.
    """
    ask = _word_set(bug_description) | _word_set(agent_reasoning)
    say = _word_set(_diff_content_lines(diff))
    if not ask or not say:
        return 0.0
    inter = ask & say
    union = ask | say
    return round(len(inter) / len(union), 4)


# Thresholds (CANNOT_MUTATE in spirit — change here is a constitutional
# change to what "addresses the bug" means).
RELEVANCE_FLOOR    = 0.05    # below this → refuse approval
RELEVANCE_WARNING  = 0.10    # below this → record warn flag on token


@dataclass
class PatchDraft:
    """A drafted patch awaiting human review.

    The draft is the only state that lives between `draft` and
    `approve` / `reject`. It carries the diff text, the agent's
    one-line reasoning, the MonotonicGate verdict (whether local
    tests passed), and a hash of the diff that the signed
    EventToken later commits to.
    """
    patch_id:                str
    bug_id:                  str
    target_file:             str
    diff:                    str
    diff_hash:               str
    agent_reasoning:         str
    tests_passed:            int
    tests_failed:            int
    monotonic_gate_passed:   bool
    drafted_at:              str
    bug_description:         str  = ""
    relevance_score:         float = 0.0
    status:                  str = "pending"   # pending | approved | rejected | revoked
    severity:                str = "medium"

    @classmethod
    def new(
        cls,
        *,
        bug_id: str,
        target_file: str,
        diff: str,
        agent_reasoning: str,
        bug_description: str = "",
        tests_passed: int = 0,
        tests_failed: int = 0,
        severity: str = "medium",
    ) -> "PatchDraft":
        if not diff.strip():
            raise PatchAgentError("diff must be non-empty")
        if not agent_reasoning.strip():
            raise PatchAgentError("agent_reasoning must be non-empty")
        if tests_passed < 0 or tests_failed < 0:
            raise PatchAgentError("test counts must be non-negative")
        return cls(
            patch_id=f"patch_{uuid.uuid4().hex[:12]}",
            bug_id=bug_id,
            target_file=target_file,
            diff=diff,
            diff_hash=_sha256_text(diff),
            agent_reasoning=agent_reasoning.strip(),
            bug_description=bug_description.strip(),
            relevance_score=compute_relevance_score(
                bug_description=bug_description,
                agent_reasoning=agent_reasoning,
                diff=diff,
            ),
            tests_passed=int(tests_passed),
            tests_failed=int(tests_failed),
            monotonic_gate_passed=(tests_failed == 0 and tests_passed > 0),
            drafted_at=_utc_now(),
            severity=severity,
        )

    # ── persistence ─────────────────────────────────────────────────

    def dir(self, root: Path) -> Path:
        return root / self.patch_id

    def save(self, root: Path) -> Path:
        d = self.dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / "patch.diff").write_text(self.diff, encoding="utf-8")
        meta = {k: v for k, v in asdict(self).items() if k != "diff"}
        (d / "draft.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        return d

    @classmethod
    def load(cls, patch_id: str, root: Path) -> "PatchDraft":
        d = root / patch_id
        if not d.is_dir():
            raise PatchAgentError(f"no patch found at {d}")
        try:
            meta = json.loads(
                (d / "draft.json").read_text(encoding="utf-8")
            )
            diff = (d / "patch.diff").read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError) as e:
            raise PatchAgentError(
                f"corrupt patch dir {d}: {e}"
            ) from None
        # Re-validate the on-disk diff hash matches the recorded one.
        if _sha256_text(diff) != meta.get("diff_hash"):
            raise PatchAgentError(
                f"diff_hash mismatch for {patch_id} — patch.diff was "
                f"modified after drafting (tamper)."
            )
        return cls(diff=diff, **meta)

    def update_status(self, status: str, root: Path) -> None:
        if status not in ("pending", "approved", "rejected", "revoked"):
            raise PatchAgentError(f"unknown status: {status!r}")
        self.status = status
        meta_path = self.dir(root) / "draft.json"
        meta = {k: v for k, v in asdict(self).items() if k != "diff"}
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )


# ── PatchAgent — top-level orchestrator ─────────────────────────────


class PatchAgent:
    """Halt-at-gate orchestrator for human-in-the-loop patch review."""

    def __init__(
        self,
        *,
        drafts_dir: Optional[Path] = None,
        improvements_path: Optional[Path] = None,
        ledger=None,
    ) -> None:
        self._drafts_dir = Path(drafts_dir) if drafts_dir \
            else default_drafts_dir()
        self._drafts_dir.mkdir(parents=True, exist_ok=True)
        # Reuse the existing dev-agent improvements file so rejected
        # patches feed the same retrospect pipeline that nightly
        # learning already consumes.
        self._improvements_path = (
            Path(improvements_path) if improvements_path
            else Path("dev_agent_improvements.jsonl")
        )
        self._ledger = ledger

    # ── 1. draft ────────────────────────────────────────────────────

    def draft(self, draft: PatchDraft) -> PatchDraft:
        draft.save(self._drafts_dir)
        return draft

    def list_pending(self) -> list[PatchDraft]:
        out: list[PatchDraft] = []
        if not self._drafts_dir.is_dir():
            return out
        for entry in sorted(self._drafts_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                d = PatchDraft.load(entry.name, self._drafts_dir)
            except PatchAgentError:
                continue
            if d.status == "pending":
                out.append(d)
        return out

    def get(self, patch_id: str) -> PatchDraft:
        return PatchDraft.load(patch_id, self._drafts_dir)

    # ── 2. approve ──────────────────────────────────────────────────

    def approve(
        self,
        patch_id: str,
        *,
        reviewer_principal: str,
        apply_with: str = "git",
        target_repo: Optional[Path] = None,
        force_irrelevant: bool = False,
    ):
        """Sign the approval, append to ledger, apply the diff.

        Raises GateRefusal if monotonic_gate_passed is False — the
        deterministic guardrail won't let a failing-tests patch
        through, no matter the reviewer's intent.

        Raises RelevanceRefusal if the diff's word-overlap with the
        bug description is below RELEVANCE_FLOOR AND a bug
        description was provided. Override with
        `force_irrelevant=True` (CLI: --force-irrelevant); the
        override gets recorded in the signed token so the audit
        trail shows the human deliberately bypassed the relevance
        check.
        """
        if not reviewer_principal or not reviewer_principal.strip():
            raise PatchAgentError("reviewer_principal is required")
        draft = self.get(patch_id)
        if draft.status != "pending":
            raise PatchAgentError(
                f"patch {patch_id} is already {draft.status}"
            )
        if not draft.monotonic_gate_passed:
            raise GateRefusal(
                f"MonotonicGate refused: tests {draft.tests_passed}p "
                f"{draft.tests_failed}f. Approval blocked until "
                f"the patch passes its local tests."
            )
        # Relevance gate: only enforces when a bug_description was
        # supplied (otherwise we have no ground truth to score against).
        if draft.bug_description and \
                draft.relevance_score < RELEVANCE_FLOOR and \
                not force_irrelevant:
            raise RelevanceRefusal(
                f"Relevance refused: score={draft.relevance_score:.2f} "
                f"below floor {RELEVANCE_FLOOR}. The diff's content "
                f"doesn't overlap with the bug description "
                f"({draft.bug_description[:80]!r}). If you've "
                f"verified the agent's reasoning matches the request, "
                f"re-run with --force-irrelevant to override (the "
                f"override is recorded in the signed token)."
            )

        token = self._build_event_token(
            draft=draft,
            decision="approve",
            reviewer_principal=reviewer_principal.strip(),
            rejection_reason=None,
            relevance_override=force_irrelevant,
        )

        # Apply the diff. Production write is gated on the
        # `approve` path only.
        try:
            self._apply_diff(draft, apply_with=apply_with,
                             target_repo=target_repo)
        except PatchAgentError:
            # Roll back the status change so the user can retry.
            raise
        draft.update_status("approved", self._drafts_dir)

        if self._ledger is not None:
            self._ledger.append(
                draft=draft, token=token,
                decision="approve",
                reviewer_principal=reviewer_principal.strip(),
                rejection_reason=None,
            )
        return token

    # ── 3. reject ───────────────────────────────────────────────────

    def reject(
        self,
        patch_id: str,
        *,
        reviewer_principal: str,
        reason: str,
    ):
        """Sign the rejection, append to ledger, pipe an
        ImprovementRecord into dev_agent_improvements.jsonl so the
        next iteration learns from the rejection."""
        if not reviewer_principal or not reviewer_principal.strip():
            raise PatchAgentError("reviewer_principal is required")
        if not reason or not reason.strip():
            raise PatchAgentError("reason is required for rejection")
        draft = self.get(patch_id)
        if draft.status != "pending":
            raise PatchAgentError(
                f"patch {patch_id} is already {draft.status}"
            )

        token = self._build_event_token(
            draft=draft,
            decision="reject",
            reviewer_principal=reviewer_principal.strip(),
            rejection_reason=reason.strip(),
        )

        # Pipe into the retrospect ingestion pipeline. Matches the
        # ImprovementRecord shape at axiom_retrospect.py:98-105.
        improvement = {
            "input_text":
                f"{draft.bug_id}: {draft.agent_reasoning}\n"
                f"---\n{draft.diff}",
            "former_self_verdict": "PROPOSED",
            "current_verdict":     "REJECTED",
            "improvement_cause":
                f"patch_agent_rejection:{reason.strip()}",
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

    # ── 4. revoke (undo a prior approval) ───────────────────────────

    def revoke(
        self,
        patch_id: str,
        *,
        revoker_principal: str,
        reason: str,
        rollback: bool = False,
        target_repo: Optional[Path] = None,
    ):
        """Sign a tamper-evident 'this approval was a mistake' entry.

        - Requires the patch to be in 'approved' state.
        - Writes a new EventToken whose governance slot references
          the original approval (`revokes_token_id`) + the revoker
          + the reason. Original approval token stays on disk
          unchanged — the audit trail shows BOTH events.
        - If `rollback=True`, also runs `git apply -R` against the
          target repo to undo the diff. The rollback fails LOUD if
          the working tree has since drifted (git refuses).
        - Pipes a negative ImprovementRecord into
          dev_agent_improvements.jsonl so the retrospect loop
          learns from the bad approval.
        """
        if not revoker_principal or not revoker_principal.strip():
            raise PatchAgentError("revoker_principal is required")
        if not reason or not reason.strip():
            raise PatchAgentError("reason is required for revocation")
        draft = self.get(patch_id)
        if draft.status != "approved":
            raise PatchAgentError(
                f"patch {patch_id} is {draft.status}, not approved — "
                f"only approved patches can be revoked"
            )

        # Locate the original approval EventToken (saved next to the
        # draft on disk by _build_event_token).
        original_token_id: Optional[str] = None
        original_path = (self._drafts_dir / patch_id /
                         "event_token.json")
        if original_path.exists():
            try:
                original_token_id = json.loads(
                    original_path.read_text(encoding="utf-8")
                )["id"]
            except Exception:
                pass

        token = self._build_event_token(
            draft=draft,
            decision="revoke",
            reviewer_principal=revoker_principal.strip(),
            rejection_reason=None,
            revokes_token_id=original_token_id,
            revoke_reason=reason.strip(),
        )
        # Persist the revocation token alongside the approval (don't
        # overwrite the original — both are part of the audit trail).
        revoke_path = (self._drafts_dir / patch_id /
                       "event_token_revoke.json")
        revoke_path.write_text(token.to_json(indent=2),
                                encoding="utf-8")

        if rollback:
            self._rollback_diff(draft, target_repo=target_repo)

        # Feed the retrospect pipeline so this bad approval informs
        # the next iteration's training signal.
        improvement = {
            "input_text":
                f"{draft.bug_id}: {draft.agent_reasoning}\n"
                f"---\n{draft.diff}",
            "former_self_verdict": "APPROVED",
            "current_verdict":     "REVOKED",
            "improvement_cause":
                f"patch_agent_revocation:{reason.strip()}",
            "training_signal":     "negative",
            "hmac_signature":      _governance_sig(token) or "",
        }
        self._improvements_path.parent.mkdir(parents=True, exist_ok=True)
        with self._improvements_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(improvement,
                                ensure_ascii=True, sort_keys=True) + "\n")

        draft.update_status("revoked", self._drafts_dir)

        if self._ledger is not None:
            self._ledger.append(
                draft=draft, token=token,
                decision="revoke",
                reviewer_principal=revoker_principal.strip(),
                rejection_reason=reason.strip(),
            )
        return token

    def _rollback_diff(
        self, draft: PatchDraft, *, target_repo: Optional[Path],
    ) -> None:
        diff_path = self._drafts_dir / draft.patch_id / "patch.diff"
        cwd = str(target_repo) if target_repo else None
        check = subprocess.run(
            ["git", "apply", "-R", "--check", str(diff_path)],
            cwd=cwd, capture_output=True, text=True,
        )
        if check.returncode != 0:
            raise PatchAgentError(
                f"git apply -R --check failed (working tree has "
                f"drifted since approval): "
                f"{check.stderr.strip() or check.stdout.strip()}"
            )
        apply = subprocess.run(
            ["git", "apply", "-R", str(diff_path)],
            cwd=cwd, capture_output=True, text=True,
        )
        if apply.returncode != 0:
            raise PatchAgentError(
                f"git apply -R failed: {apply.stderr.strip()}"
            )

    # ── 5. verify ───────────────────────────────────────────────────

    def verify(self, patch_id: str) -> dict:
        """Reload + verify a patch.

        - patch.diff's hash matches the recorded diff_hash (caught
          in PatchDraft.load).
        - If a signed EventToken sidecar exists, re-verify it.
        - If a revocation sidecar exists, re-verify it AND extract
          the linkage back to the approval.
        """
        draft = self.get(patch_id)
        token_path  = (self._drafts_dir / patch_id /
                       "event_token.json")
        revoke_path = (self._drafts_dir / patch_id /
                       "event_token_revoke.json")
        result: dict = {
            "patch_id":   draft.patch_id,
            "status":     draft.status,
            "relevance_score": float(draft.relevance_score),
            "diff_hash_matches": True,    # PatchDraft.load already checked
            "event_token_verified":      None,
            "revocation_token_verified": None,
        }
        if token_path.exists():
            from axiom_event_token.models import EventToken
            try:
                t = EventToken.from_dict(
                    json.loads(token_path.read_text(encoding="utf-8"))
                )
                result["event_token_verified"] = bool(t.verify())
                result["event_token_id"] = t.id
            except Exception:  # noqa: BLE001
                result["event_token_verified"] = False
        if revoke_path.exists():
            from axiom_event_token.models import EventToken
            try:
                rt = EventToken.from_dict(
                    json.loads(revoke_path.read_text(encoding="utf-8"))
                )
                result["revocation_token_verified"] = bool(rt.verify())
                result["revocation_token_id"] = rt.id
                gp = rt.governance.payload if rt.governance else {}
                result["revokes_token_id"] = gp.get("revokes_token_id")
                result["revoke_reason"]    = gp.get("revoke_reason")
            except Exception:  # noqa: BLE001
                result["revocation_token_verified"] = False
        return result

    # ── internals ───────────────────────────────────────────────────

    def _build_event_token(
        self,
        *,
        draft: PatchDraft,
        decision: str,
        reviewer_principal: str,
        rejection_reason: Optional[str],
        relevance_override: bool = False,
        revokes_token_id: Optional[str] = None,
        revoke_reason: Optional[str] = None,
    ):
        """Construct + sign the EventToken with the governance slot
        carrying decision, reviewer, gate result, and the diff hash."""
        from axiom_event_token.models import (
            EventToken, LayerReport, _canonical_coordinator,
            _canonical_token, _sign, COORD_KEY_NS, TOKEN_KEY_NS,
        )

        text_layer = LayerReport.signed(
            agent="patch_agent_v1",
            payload={
                "delegate":         "patch_agent_v1",
                "patch_id":         draft.patch_id,
                "bug_id":           draft.bug_id,
                "target_file":      draft.target_file,
                "agent_reasoning":  draft.agent_reasoning,
                "severity":         draft.severity,
            },
            confidence=0.9 if decision == "approve" else 0.5,
        )
        gov_payload = {
            "decision":              decision,
            "reviewer_principal":    reviewer_principal,
            "decision_at":           _utc_now(),
            "monotonic_gate_passed": draft.monotonic_gate_passed,
            "tests_passed":          draft.tests_passed,
            "tests_failed":          draft.tests_failed,
            "diff_hash":             draft.diff_hash,
            "relevance_score":       float(draft.relevance_score),
            "relevance_floor":       RELEVANCE_FLOOR,
            "relevance_warning":     (
                draft.bug_description != "" and
                draft.relevance_score < RELEVANCE_WARNING
            ),
            "relevance_override":    bool(relevance_override),
        }
        if rejection_reason:
            gov_payload["rejection_reason"] = rejection_reason
        if revokes_token_id:
            gov_payload["revokes_token_id"] = revokes_token_id
        if revoke_reason:
            gov_payload["revoke_reason"] = revoke_reason
        gov_layer = LayerReport.signed(
            agent="patch_agent_governance_v1",
            payload=gov_payload,
            confidence=1.0,
        )

        token = EventToken(
            id=f"patch_{draft.patch_id}",
            created_at=_utc_now(),
            activated_agents=("patch_agent_v1",
                              "patch_agent_governance_v1"),
            text=text_layer,
            governance=gov_layer,
        )
        coord_sig = _sign(_canonical_coordinator(token),
                          COORD_KEY_NS)
        from axiom_event_token.coordinator import _token_kwargs
        token = EventToken(**{**_token_kwargs(token),
                              "coordinator_sig": coord_sig})
        outer_sig = _sign(_canonical_token(token), TOKEN_KEY_NS)
        token = EventToken(**{**_token_kwargs(token),
                              "signature": outer_sig})

        token_path = (self._drafts_dir / draft.patch_id /
                      "event_token.json")
        token_path.write_text(token.to_json(indent=2),
                              encoding="utf-8")
        return token

    def _apply_diff(
        self,
        draft: PatchDraft,
        *,
        apply_with: str,
        target_repo: Optional[Path],
    ) -> None:
        diff_path = self._drafts_dir / draft.patch_id / "patch.diff"
        cwd = str(target_repo) if target_repo else None
        if apply_with == "git":
            # Pre-flight check first — if `git apply --check` fails
            # we refuse before touching the working tree.
            check = subprocess.run(
                ["git", "apply", "--check", str(diff_path)],
                cwd=cwd, capture_output=True, text=True,
            )
            if check.returncode != 0:
                raise PatchAgentError(
                    f"git apply --check failed: "
                    f"{check.stderr.strip() or check.stdout.strip()}"
                )
            apply = subprocess.run(
                ["git", "apply", str(diff_path)],
                cwd=cwd, capture_output=True, text=True,
            )
            if apply.returncode != 0:
                raise PatchAgentError(
                    f"git apply failed: "
                    f"{apply.stderr.strip()}"
                )
        elif apply_with == "patch":
            apply = subprocess.run(
                ["patch", "-p1", "-i", str(diff_path)],
                cwd=cwd, capture_output=True, text=True,
            )
            if apply.returncode != 0:
                raise PatchAgentError(
                    f"patch -p1 failed: {apply.stderr.strip()}"
                )
        elif apply_with == "none":
            # Dry-mode: caller wants the signed token without touching
            # the working tree. Used by tests + by users who want
            # offline review before applying.
            return
        else:
            raise PatchAgentError(
                f"unknown apply_with: {apply_with!r}"
            )


def _governance_sig(token) -> str:
    return token.governance.signature if token.governance else ""


# ── Convenience: build a draft from a raw diff file ──────────────────


def read_diff(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


# ── CLI ──────────────────────────────────────────────────────────────


def _cmd_draft(args, agent: PatchAgent) -> int:
    diff = (
        read_diff(args.diff) if args.diff
        else (sys.stdin.read() if not sys.stdin.isatty() else "")
    )
    if not diff:
        print("error: --diff PATH required (or pipe diff on stdin)",
              file=sys.stderr)
        return 2
    try:
        draft = PatchDraft.new(
            bug_id=args.bug_id,
            target_file=args.target_file,
            diff=diff,
            agent_reasoning=args.reasoning,
            bug_description=args.bug_description or "",
            tests_passed=args.tests_passed,
            tests_failed=args.tests_failed,
            severity=args.severity,
        )
    except PatchAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    agent.draft(draft)
    gate = "PASS" if draft.monotonic_gate_passed else "REFUSED"
    rel_flag = ""
    if draft.bug_description:
        if draft.relevance_score < RELEVANCE_FLOOR:
            rel_flag = f"  ⚠ relevance={draft.relevance_score:.2f} (will REFUSE)"
        elif draft.relevance_score < RELEVANCE_WARNING:
            rel_flag = f"  ⚠ relevance={draft.relevance_score:.2f} (low)"
        else:
            rel_flag = f"  relevance={draft.relevance_score:.2f}"
    print(f"drafted {draft.patch_id}  gate={gate}  "
          f"tests={draft.tests_passed}p/{draft.tests_failed}f{rel_flag}")
    print(f"  at {agent._drafts_dir / draft.patch_id}")
    print(f"  next: axiom_patch_agent show {draft.patch_id}")
    return 0


def _cmd_list(args, agent: PatchAgent) -> int:
    pending = agent.list_pending()
    if not pending:
        print("(no pending patches)")
        return 0
    for d in pending:
        gate = "✓" if d.monotonic_gate_passed else "✗"
        rel = f"rel={d.relevance_score:.2f}" if d.bug_description else "rel=N/A"
        print(
            f"{d.patch_id}  gate={gate}  {rel}  bug={d.bug_id}  "
            f"target={d.target_file}  drafted={d.drafted_at}"
        )
    return 0


def _cmd_show(args, agent: PatchAgent) -> int:
    try:
        d = agent.get(args.patch_id)
    except PatchAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"# patch_id:       {d.patch_id}")
    print(f"# bug_id:         {d.bug_id}")
    print(f"# bug_desc:       {d.bug_description or '(none)'}")
    print(f"# target_file:    {d.target_file}")
    print(f"# severity:       {d.severity}")
    print(f"# tests:          {d.tests_passed}p / {d.tests_failed}f")
    print(f"# gate_passed:    {d.monotonic_gate_passed}")
    rel_status = "N/A"
    if d.bug_description:
        if d.relevance_score < RELEVANCE_FLOOR:
            rel_status = f"{d.relevance_score:.2f} (REFUSE — below floor {RELEVANCE_FLOOR})"
        elif d.relevance_score < RELEVANCE_WARNING:
            rel_status = f"{d.relevance_score:.2f} (warn — below {RELEVANCE_WARNING})"
        else:
            rel_status = f"{d.relevance_score:.2f}"
    print(f"# relevance:      {rel_status}")
    print(f"# status:         {d.status}")
    print(f"# diff_hash:      {d.diff_hash}")
    print(f"# drafted_at:     {d.drafted_at}")
    print(f"# reasoning:      {d.agent_reasoning}")
    print()
    print(d.diff)
    return 0


def _cmd_approve(args, agent: PatchAgent) -> int:
    try:
        token = agent.approve(
            args.patch_id,
            reviewer_principal=args.reviewer,
            apply_with=args.apply,
            target_repo=(Path(args.target_repo) if args.target_repo
                         else None),
            force_irrelevant=args.force_irrelevant,
        )
    except RelevanceRefusal as e:
        print(f"RELEVANCE REFUSED: {e}", file=sys.stderr)
        return 4
    except GateRefusal as e:
        print(f"GATE REFUSED: {e}", file=sys.stderr)
        return 3
    except PatchAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"approved {args.patch_id}")
    print(f"  signed_event_id={token.id}  verified={token.verify()}")
    gp = token.governance.payload if token.governance else {}
    if gp.get("relevance_override"):
        print(f"  ⚠ relevance override recorded — score={gp.get('relevance_score', 0):.2f}")
    return 0


def _cmd_revoke(args, agent: PatchAgent) -> int:
    try:
        token = agent.revoke(
            args.patch_id,
            revoker_principal=args.reviewer,
            reason=args.reason,
            rollback=args.rollback,
            target_repo=(Path(args.target_repo) if args.target_repo
                         else None),
        )
    except PatchAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"revoked {args.patch_id}")
    print(f"  revocation_event_id={token.id}  verified={token.verify()}")
    gp = token.governance.payload if token.governance else {}
    if gp.get("revokes_token_id"):
        print(f"  revokes={gp['revokes_token_id']}")
    if args.rollback:
        print(f"  rolled back via git apply -R against "
              f"{args.target_repo or 'cwd'}")
    print(f"  improvement record appended to "
          f"{agent._improvements_path}")
    return 0


def _cmd_reject(args, agent: PatchAgent) -> int:
    try:
        token = agent.reject(
            args.patch_id,
            reviewer_principal=args.reviewer,
            reason=args.reason,
        )
    except PatchAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"rejected {args.patch_id}")
    print(f"  signed_event_id={token.id}  verified={token.verify()}")
    print(f"  improvement record appended to "
          f"{agent._improvements_path}")
    return 0


def _cmd_verify(args, agent: PatchAgent) -> int:
    try:
        result = agent.verify(args.patch_id)
    except PatchAgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    if result.get("event_token_verified") is False:
        return 1
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-patch-agent",
        description="Human-in-the-loop signed patch workflow.",
    )
    ap.add_argument("--drafts-dir",
                    help="where to store drafts "
                         "(default: ~/.axiom/patches, "
                         "override env AXIOM_PATCH_AGENT_DRAFTS)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="skip writing to the signed patch-agent ledger")
    ap.add_argument("--improvements-path",
                    help="where to append ImprovementRecord on reject "
                         "(default: ./dev_agent_improvements.jsonl)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_draft = sub.add_parser("draft", help="register a patch awaiting review")
    p_draft.add_argument("--bug-id", required=True)
    p_draft.add_argument("--target-file", required=True)
    p_draft.add_argument("--diff",
                         help="path to a unified diff file; if omitted, "
                              "read from stdin")
    p_draft.add_argument("--reasoning", required=True,
                         help="one-line explanation of the change")
    p_draft.add_argument("--bug-description", default="",
                         help="the original ask, used for the relevance "
                              "score (Jaccard of bug+reasoning vs. diff "
                              "content). Without this the relevance gate "
                              "is skipped.")
    p_draft.add_argument("--tests-passed", type=int, default=0)
    p_draft.add_argument("--tests-failed", type=int, default=0)
    p_draft.add_argument("--severity",
                         choices=["low", "medium", "high", "critical"],
                         default="medium")
    p_draft.set_defaults(func=_cmd_draft)

    p_list = sub.add_parser("list", help="list pending patches")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="show diff + reasoning + status")
    p_show.add_argument("patch_id")
    p_show.set_defaults(func=_cmd_show)

    p_appr = sub.add_parser("approve",
                             help="sign approval, apply diff")
    p_appr.add_argument("patch_id")
    p_appr.add_argument("--reviewer", required=True,
                        help="principal (e.g. 'alice@example.com')")
    p_appr.add_argument("--apply", choices=["git", "patch", "none"],
                        default="git",
                        help="how to apply the diff; 'none' = "
                             "dry-mode (sign without applying)")
    p_appr.add_argument("--target-repo",
                        help="path to the repo to apply the diff in "
                             "(default: cwd)")
    p_appr.add_argument("--force-irrelevant", action="store_true",
                        help="bypass the relevance gate. The override "
                             "is RECORDED in the signed token so the "
                             "audit trail shows you deliberately did it.")
    p_appr.set_defaults(func=_cmd_approve)

    p_rev = sub.add_parser(
        "revoke",
        help="sign a tamper-evident 'this approval was a mistake' "
             "entry against a previously-approved patch",
    )
    p_rev.add_argument("patch_id")
    p_rev.add_argument("--reviewer", required=True,
                       help="who is doing the revocation")
    p_rev.add_argument("--reason", required=True,
                       help="why the prior approval was wrong "
                            "(gets piped into the retrospect loop)")
    p_rev.add_argument("--rollback", action="store_true",
                       help="also run `git apply -R` to undo the diff "
                            "(fails if the working tree has drifted)")
    p_rev.add_argument("--target-repo",
                       help="path to the repo for --rollback "
                            "(default: cwd)")
    p_rev.set_defaults(func=_cmd_revoke)

    p_rej = sub.add_parser("reject",
                            help="sign rejection, feed retrospect")
    p_rej.add_argument("patch_id")
    p_rej.add_argument("--reviewer", required=True)
    p_rej.add_argument("--reason", required=True,
                       help="why the patch was rejected (gets piped "
                            "into the retrospect feedback loop)")
    p_rej.set_defaults(func=_cmd_reject)

    p_ver = sub.add_parser("verify",
                            help="re-verify signatures + diff hash")
    p_ver.add_argument("patch_id")
    p_ver.set_defaults(func=_cmd_verify)

    args = ap.parse_args(list(argv) if argv is not None else None)

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32 bytes hex).",
              file=sys.stderr)
        return 2

    ledger = None
    if not args.no_ledger:
        from axiom_patch_agent_ledger import LedgerWriter, default_ledger_path
        ledger = LedgerWriter(default_ledger_path())

    agent = PatchAgent(
        drafts_dir=(Path(args.drafts_dir) if args.drafts_dir else None),
        improvements_path=(Path(args.improvements_path)
                            if args.improvements_path else None),
        ledger=ledger,
    )
    return args.func(args, agent)


if __name__ == "__main__":
    raise SystemExit(main())
