"""Tests for `axiom_event_token.chain.EventTokenChain` — parent-hash
chaining for conversation reasoning history (briefing § 5).
"""
from __future__ import annotations

import json
import sys

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing", "axiom_intent_classifier",
        )):
            sys.modules.pop(mod, None)
    yield


# ─── 1. Root token has empty parent_signature ───────────────────────────


def test_root_token_has_empty_parent_signature(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    t0 = chain.append(coord.compose(text="hi", activate=("text",)))

    assert t0.parent_signature == ""
    assert chain.verify_chain() is True


# ─── 2. Second token's parent_signature == prior token's signature ─────


def test_chained_tokens_link_via_parent_signature(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    t0 = chain.append(coord.compose(text="hi", activate=("text",)))
    t1 = chain.append(coord.compose(
        text="follow up", activate=("text",), parent=t0,
    ))

    assert t1.parent_signature == t0.signature
    assert t1.parent_signature != ""
    assert chain.verify_chain() is True


# ─── 3. append_via convenience routes parent automatically ──────────────


def test_append_via_routes_parent_for_caller(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    t0 = chain.append_via(coord.compose, text="hi", activate=("text",))
    t1 = chain.append_via(coord.compose, text="next", activate=("text",))
    t2 = chain.append_via(coord.compose, text="third", activate=("text",))

    assert t0.parent_signature == ""
    assert t1.parent_signature == t0.signature
    assert t2.parent_signature == t1.signature
    assert chain.verify_chain() is True
    assert len(chain) == 3


# ─── 4. append rejects inconsistent parent_signature ────────────────────


def test_append_rejects_inconsistent_parent_signature(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    chain.append(coord.compose(text="root", activate=("text",)))

    # Build a token whose parent_signature is wrong (no parent= passed)
    orphan = coord.compose(text="orphan", activate=("text",))
    assert orphan.parent_signature == ""
    with pytest.raises(ValueError, match="parent_signature"):
        chain.append(orphan)


def test_append_rejects_nonempty_parent_on_root(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    real = coord.compose(text="real root", activate=("text",))
    fake = coord.compose(text="fake follower", activate=("text",), parent=real)

    chain = EventTokenChain()
    with pytest.raises(ValueError, match="root"):
        chain.append(fake)


# ─── 5. Tampering a single token in the middle breaks the chain ────────


def test_tampering_a_middle_token_breaks_chain(isolated):
    from axiom_event_token import Coordinator, EventTokenChain
    from axiom_event_token.models import EventToken

    coord = Coordinator()
    chain = EventTokenChain()
    t0 = chain.append_via(coord.compose, text="a", activate=("text",))
    t1 = chain.append_via(coord.compose, text="b", activate=("text",))
    t2 = chain.append_via(coord.compose, text="c", activate=("text",))
    assert chain.verify_chain()

    # Mutate t1's parent_signature to point at something fake
    bogus_t1 = EventToken(
        **{**{f.name: getattr(t1, f.name)
              for f in t1.__dataclass_fields__.values()},
           "parent_signature": "ff" * 32},
    )
    chain._tokens[1] = bogus_t1

    assert chain.verify_chain() is False
    assert chain.first_broken_link() == 1


# ─── 6. Round-trip via JSONL preserves chain integrity ─────────────────


def test_jsonl_roundtrip_preserves_chain_integrity(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    for word in ("alpha", "beta", "gamma", "delta"):
        chain.append_via(coord.compose, text=word, activate=("text",))

    serialised = chain.to_jsonl()
    restored = EventTokenChain.from_jsonl(serialised)

    assert len(restored) == 4
    assert restored.verify_chain() is True
    # Same signatures end-to-end
    for orig, rest in zip(chain, restored):
        assert orig.signature == rest.signature
        assert orig.parent_signature == rest.parent_signature


def test_to_list_roundtrip_preserves_chain_integrity(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    for word in ("one", "two", "three"):
        chain.append_via(coord.compose, text=word, activate=("text",))

    dicts = chain.to_list()
    restored = EventTokenChain.from_list(dicts)

    assert len(restored) == 3
    assert restored.verify_chain() is True


# ─── 7. The outer signature COVERS parent_signature (tamper detection) ─


def test_tampering_parent_signature_breaks_outer_signature(isolated):
    """Per the briefing § 5: parent_signature must be cryptographically
    bound. If a verifier just iterates parent links without re-checking
    outer signatures, an attacker who flips parent_signature would go
    undetected. We rely on the outer sig covering it.
    """
    from axiom_event_token import Coordinator
    from axiom_event_token.models import EventToken

    coord = Coordinator()
    root = coord.compose(text="root", activate=("text",))
    child = coord.compose(text="child", activate=("text",), parent=root)
    assert child.verify() is True

    # Mutate parent_signature in isolation — outer sig should fail
    tampered = EventToken(
        **{**{f.name: getattr(child, f.name)
              for f in child.__dataclass_fields__.values()},
           "parent_signature": "00" * 32},
    )
    assert tampered.verify() is False


# ─── 8. Pre-chain tokens (no parent_signature in JSON) still verify ────


def test_legacy_tokens_without_parent_signature_field_still_verify(isolated):
    """BC contract: a token serialised by pre-chaining code (which
    didn't include the `parent_signature` key at all) must verify
    under the new code. The `omit-when-empty` to_dict() rule makes
    this byte-identical.
    """
    from axiom_event_token import Coordinator
    from axiom_event_token.models import EventToken

    coord = Coordinator()
    token = coord.compose(text="legacy", activate=("text",))
    serialised = token.to_json()

    # Verify the on-wire JSON contains NO parent_signature key for a
    # token that isn't part of a chain — that's the BC guarantee.
    payload = json.loads(serialised)
    assert "parent_signature" not in payload

    restored = EventToken.from_dict(payload)
    assert restored.verify() is True
    assert restored.parent_signature == ""


# ─── 9. tail + len + iteration sanity ──────────────────────────────────


def test_chain_tail_and_indexing(isolated):
    from axiom_event_token import Coordinator, EventTokenChain

    coord = Coordinator()
    chain = EventTokenChain()
    assert chain.tail is None
    assert len(chain) == 0

    a = chain.append_via(coord.compose, text="a", activate=("text",))
    assert chain.tail is a
    assert chain[0] is a
    assert list(chain) == [a]

    b = chain.append_via(coord.compose, text="b", activate=("text",))
    assert chain.tail is b
    assert chain[-1] is b
    assert len(chain) == 2


# ─── 10. Two siblings with the same parent both verify individually ────


def test_branching_does_not_break_individual_verify(isolated):
    """Branching (one parent, two children) isn't supported by the chain
    helper (which is linear), but each child token individually verifies.
    Documents the "EventTokenChain is linear; siblings need separate
    chains" design decision.
    """
    from axiom_event_token import Coordinator

    coord = Coordinator()
    root = coord.compose(text="root", activate=("text",))
    childA = coord.compose(text="a", activate=("text",), parent=root)
    childB = coord.compose(text="b", activate=("text",), parent=root)

    assert root.verify()
    assert childA.verify()
    assert childB.verify()
    assert childA.parent_signature == root.signature
    assert childB.parent_signature == root.signature
    assert childA.signature != childB.signature
