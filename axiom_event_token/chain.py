"""Append-only signed chain of EventTokens.

Per the AXIOM architecture briefing § 5 ("Reasoning History Layer"),
a conversation is a sequence of EventTokens where each carries a
cryptographic reference to its predecessor. Tampering with any link
breaks chain verification.

No new HMAC namespace — chain integrity rides on the existing
`axiom-event-token-v1` outer signature, which (when `parent_signature`
is non-empty) covers the parent reference.

Usage:

    chain = EventTokenChain()
    t1 = chain.append(coord.compose(text="hi"))
    t2 = chain.append(coord.compose(text="follow up", parent=t1))
    # OR equivalently, let the chain set the parent for you:
    t3 = chain.append_via(coord, text="another", )  # coord.compose
                                                    #   with parent=t2

    assert chain.verify_chain()
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Optional

from .models import EventToken


class EventTokenChain:
    """Ordered sequence of EventTokens linked by `parent_signature`.

    The chain is append-only. `verify_chain()` checks:
      1. every token's full `EventToken.verify()` passes;
      2. token[i].parent_signature == token[i-1].signature for i ≥ 1;
      3. token[0].parent_signature is "" (chain root).
    """

    def __init__(self, tokens: Optional[Iterable[EventToken]] = None) -> None:
        self._tokens: list[EventToken] = list(tokens) if tokens else []

    # ─── Construction ──────────────────────────────────────────────

    def append(self, token: EventToken) -> EventToken:
        """Append a pre-built token. Caller is responsible for setting
        `parent_signature` to the prior token's outer signature; an
        exception is raised if the parent reference is inconsistent.
        """
        if self._tokens:
            expected_parent = self._tokens[-1].signature
            if token.parent_signature != expected_parent:
                raise ValueError(
                    f"token.parent_signature does not match the "
                    f"prior token's signature "
                    f"(got {token.parent_signature[:16]}..., "
                    f"expected {expected_parent[:16]}...)"
                )
        else:
            if token.parent_signature:
                raise ValueError(
                    "first token in a chain must have empty "
                    "parent_signature (it IS the root)"
                )
        self._tokens.append(token)
        return token

    def append_via(
        self,
        compose_fn: Callable[..., EventToken],
        **compose_kwargs,
    ) -> EventToken:
        """Convenience: call `compose_fn(**kwargs, parent=tail)` and
        append. Use with `Coordinator.compose` or `compose_from_delegates`.
        """
        parent = self.tail
        token = compose_fn(parent=parent, **compose_kwargs)
        return self.append(token)

    # ─── Read-only access ──────────────────────────────────────────

    @property
    def tail(self) -> Optional[EventToken]:
        return self._tokens[-1] if self._tokens else None

    @property
    def tokens(self) -> tuple[EventToken, ...]:
        return tuple(self._tokens)

    def __len__(self) -> int:
        return len(self._tokens)

    def __iter__(self):
        return iter(self._tokens)

    def __getitem__(self, idx: int) -> EventToken:
        return self._tokens[idx]

    # ─── Verification ──────────────────────────────────────────────

    def verify_chain(self) -> bool:
        """True iff every token verifies AND parent-references form an
        unbroken chain from root to tail.
        """
        for i, tok in enumerate(self._tokens):
            if not tok.verify():
                return False
            if i == 0:
                if tok.parent_signature:
                    return False
            else:
                if tok.parent_signature != self._tokens[i - 1].signature:
                    return False
        return True

    def first_broken_link(self) -> Optional[int]:
        """Index of the first token whose verification or chain-link
        check fails. Returns None if the chain is intact.
        """
        for i, tok in enumerate(self._tokens):
            if not tok.verify():
                return i
            if i == 0 and tok.parent_signature:
                return i
            if i > 0 and tok.parent_signature != self._tokens[i - 1].signature:
                return i
        return None

    # ─── Serialization ─────────────────────────────────────────────

    @classmethod
    def from_list(cls, dicts: Iterable[dict]) -> "EventTokenChain":
        """Reconstruct a chain from an iterable of token dicts (e.g.
        from a JSON array)."""
        return cls(EventToken.from_dict(d) for d in dicts)

    def to_list(self) -> list[dict]:
        return [t.to_dict() for t in self._tokens]

    def to_jsonl(self) -> str:
        """One token per line — append-friendly format for ledgers."""
        return "\n".join(
            json.dumps(t.to_dict(), ensure_ascii=False, sort_keys=True)
            for t in self._tokens
        )

    @classmethod
    def from_jsonl(cls, jsonl: str) -> "EventTokenChain":
        return cls(
            EventToken.from_dict(json.loads(line))
            for line in jsonl.splitlines() if line.strip()
        )
