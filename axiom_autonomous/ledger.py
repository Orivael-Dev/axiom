"""Per-step signed token chain on top of the existing exoskeleton
ledger.

Each step in an autonomous run produces ONE signed EventToken whose
text-layer payload carries:

    run_id, step_idx, step_kind ∈ {plan,execute,verify,replan,denied},
    parent_token_id, tool_call?, observation?, verdict?, diff_hash,
    honesty_findings?, chain_sig

The token is built manually (not via Coordinator.compose_from_delegates)
because there's no real DelegateAgent / LLM call for the wrapping step
— the LLM-call observations live INSIDE the payload, not in the
token shape itself. Signing follows the exact pattern from
`axiom_exoskeleton._annotate_honesty`:

    1. LayerReport.signed(...)              → layer sig
    2. _sign(_canonical_coordinator(token), COORD_KEY_NS)  → coord sig
    3. _sign(_canonical_token(token), TOKEN_KEY_NS)        → outer sig

Plus one extra detail-in-depth signature:

    chain_sig = HMAC(derive_key(b"axiom-autonomous-chain-v1"),
                     run_id || step_idx || parent_token_id || token_id)

baked into the payload BEFORE the layer sig is computed, so any
attempt to splice a forged token into the middle of an existing chain
breaks the chain_sig check during replay.

`LedgerWriter` from axiom_exoskeleton_ledger.py is reused as-is —
unknown payload fields are silently tolerated.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional


CHAIN_KEY_NS = b"axiom-autonomous-chain-v1"


def _chain_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(CHAIN_KEY_NS)


def _compute_chain_sig(
    run_id: str, step_idx: int,
    parent_token_id: Optional[str], token_id: str,
) -> str:
    """HMAC the four linkage fields. Verifiable from any later replay
    without needing the full payload — keeps the linkage cheap to
    audit even when the surrounding payload changes shape over time.
    """
    msg = (
        f"{run_id}|{int(step_idx)}|{parent_token_id or ''}|{token_id}"
    ).encode("utf-8")
    return hmac.new(_chain_key(), msg, hashlib.sha256).hexdigest()


def _verify_chain_sig(
    run_id: str, step_idx: int,
    parent_token_id: Optional[str], token_id: str,
    sig: str,
) -> bool:
    expected = _compute_chain_sig(
        run_id, step_idx, parent_token_id, token_id,
    )
    if not isinstance(sig, str) or len(sig) != len(expected):
        return False
    return hmac.compare_digest(sig, expected)


def new_run_id() -> str:
    return f"auto_{uuid.uuid4().hex[:12]}"


def _new_step_token_id() -> str:
    return f"auto_step_{uuid.uuid4().hex[:12]}"


def build_step_token(
    *,
    run_id: str,
    step_idx: int,
    step_kind: str,
    parent_token_id: Optional[str],
    payload: Mapping[str, Any],
    backend: str = "autonomous-orchestrator",
    model: str = "n/a",
    confidence: float = 0.9,
):
    """Build a fully-signed EventToken for one step.

    The returned token's `text` layer carries the step payload (plus
    backend/model/latency facts that `LedgerWriter.append` extracts).
    """
    from axiom_event_token.models import (
        EventToken, LayerReport, COORD_KEY_NS, TOKEN_KEY_NS,
        _canonical_coordinator, _canonical_token, _sign, now_iso,
    )
    from axiom_event_token.coordinator import _token_kwargs

    token_id = _new_step_token_id()
    chain_sig = _compute_chain_sig(
        run_id, step_idx, parent_token_id, token_id,
    )

    full_payload = dict(payload)
    full_payload.update({
        "run_id":          run_id,
        "step_idx":        int(step_idx),
        "step_kind":       step_kind,
        "parent_token_id": parent_token_id or "",
        "chain_sig":       chain_sig,
        # Mirror the LedgerWriter-expected transport facts.
        "delegate":        f"autonomous:{step_kind}",
        "backend":         backend,
        "model":           model,
        "input_tokens":    int(full_payload.get("input_tokens", 0)),
        "output_tokens":   int(full_payload.get("output_tokens", 0)),
        "latency_ms":      int(full_payload.get("latency_ms", 0)),
    })

    layer = LayerReport.signed(
        agent=f"autonomous:{step_kind}",
        payload=full_payload,
        confidence=float(confidence),
    )
    token = EventToken(
        id=token_id,
        created_at=now_iso(),
        activated_agents=(f"autonomous:{step_kind}",),
        text=layer,
    )
    coord_sig = _sign(_canonical_coordinator(token), COORD_KEY_NS)
    token = EventToken(**{**_token_kwargs(token),
                          "coordinator_sig": coord_sig})
    outer_sig = _sign(_canonical_token(token), TOKEN_KEY_NS)
    token = EventToken(**{**_token_kwargs(token),
                          "signature": outer_sig})
    return token


def reannotate_step_token(token, *, extra: Mapping[str, Any]):
    """Append extra fields to an existing step token's payload and
    re-sign layer + coord + outer sigs end-to-end. Mirrors the trick
    in `axiom_exoskeleton._annotate_honesty` so token.verify() still
    passes after the annotation.
    """
    from axiom_event_token.models import (
        EventToken, LayerReport, COORD_KEY_NS, TOKEN_KEY_NS,
        _canonical_coordinator, _canonical_token, _sign,
    )
    from axiom_event_token.coordinator import _token_kwargs

    if token.text is None:
        return token
    new_payload = dict(token.text.payload)
    new_payload.update(extra)
    new_layer = LayerReport.signed(
        agent=token.text.agent,
        payload=new_payload,
        confidence=token.text.confidence,
    )
    rebuilt = EventToken(**{**_token_kwargs(token),
                            "text": new_layer,
                            "coordinator_sig": "",
                            "signature": ""})
    coord_sig = _sign(_canonical_coordinator(rebuilt), COORD_KEY_NS)
    rebuilt = EventToken(**{**_token_kwargs(rebuilt),
                            "coordinator_sig": coord_sig})
    outer_sig = _sign(_canonical_token(rebuilt), TOKEN_KEY_NS)
    rebuilt = EventToken(**{**_token_kwargs(rebuilt),
                            "signature": outer_sig})
    return rebuilt


# ── TokenChain — the chain bookkeeping helper ─────────────────────────


class TokenChain:
    """Holds the per-run chain head + appends signed tokens through
    the existing LedgerWriter.

    Usage:
        chain = TokenChain(run_id=new_run_id(), ledger=writer)
        token = chain.append(step_kind="plan", payload={...})
        ...
        token = chain.append(step_kind="execute", payload={...})
        chain.head_id  # → last appended token's id
    """

    def __init__(self, *, run_id: str, ledger=None) -> None:
        self.run_id = run_id
        self._ledger = ledger
        self._tokens: List = []
        self._head_id: Optional[str] = None
        self._step_idx = 0

    @property
    def head_id(self) -> Optional[str]:
        return self._head_id

    @property
    def tokens(self) -> List:
        """Return the per-run tokens in append order. Mostly useful
        for in-process verification — production code reads the ledger."""
        return list(self._tokens)

    def append(
        self,
        *,
        step_kind: str,
        payload: Mapping[str, Any],
        backend: str = "autonomous-orchestrator",
        model: str = "n/a",
        confidence: float = 0.9,
    ):
        token = build_step_token(
            run_id=self.run_id,
            step_idx=self._step_idx,
            step_kind=step_kind,
            parent_token_id=self._head_id,
            payload=payload,
            backend=backend,
            model=model,
            confidence=confidence,
        )
        self._tokens.append(token)
        self._head_id = token.id
        self._step_idx += 1
        if self._ledger is not None:
            try:
                self._ledger.append(
                    token=token,
                    use_case=f"autonomous:{self.run_id}:{step_kind}",
                    input_text=json.dumps(
                        {"step_idx": token.text.payload["step_idx"],
                         "step_kind": step_kind},
                        sort_keys=True, separators=(",", ":"),
                    ),
                )
            except Exception:
                # Ledger failures must NEVER break the loop — the in-
                # memory chain is still intact, and the orchestrator
                # will surface the failure via its log.
                pass
        return token

    def reannotate_head(self, *, extra: Mapping[str, Any]):
        """Re-sign the head token with extra payload fields. Used by
        the honesty post-scan, which adds findings to a verify-step
        token after it's already been signed.
        """
        if not self._tokens:
            return None
        last = self._tokens[-1]
        new_token = reannotate_step_token(last, extra=extra)
        # Preserve token id continuity for the chain — reannotation
        # only changes the payload, NOT the linkage. We swap the
        # in-memory copy AND let the ledger record the original; a
        # nightly job can re-emit the annotated copy if needed.
        self._tokens[-1] = new_token
        # head_id stays the same (id field is untouched).
        return new_token


def verify_chain(tokens: List, *, run_id: str) -> dict:
    """Walk a list of step tokens and check structural + cryptographic
    integrity. Returns a dict suitable for embedding into a test
    assertion or an audit report:

        {"ok": bool, "broken_at": int|None, "reason": str}
    """
    expected_parent: Optional[str] = None
    for idx, token in enumerate(tokens):
        if not token.verify():
            return {"ok": False, "broken_at": idx,
                    "reason": "token signatures fail to verify"}
        payload = token.text.payload if token.text else {}
        if payload.get("run_id") != run_id:
            return {"ok": False, "broken_at": idx,
                    "reason": f"run_id mismatch at step {idx}"}
        if int(payload.get("step_idx", -1)) != idx:
            return {"ok": False, "broken_at": idx,
                    "reason": f"step_idx mismatch: expected {idx}, "
                              f"got {payload.get('step_idx')}"}
        parent = payload.get("parent_token_id") or None
        if parent != expected_parent:
            return {"ok": False, "broken_at": idx,
                    "reason": f"parent_token_id mismatch: expected "
                              f"{expected_parent!r}, got {parent!r}"}
        if not _verify_chain_sig(
            run_id, idx, expected_parent, token.id,
            payload.get("chain_sig", ""),
        ):
            return {"ok": False, "broken_at": idx,
                    "reason": "chain_sig fails to verify"}
        expected_parent = token.id
    return {"ok": True, "broken_at": None, "reason": ""}
