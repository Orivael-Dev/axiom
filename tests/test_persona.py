# -*- coding: utf-8 -*-
"""
PersonaToken — two-tier signing (soul vs outfit), history/lineage, MET genesis.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from aui.persona import PersonaToken, PersonaStore, mint_default  # noqa: E402
from aui.master_token import MasterEventToken  # noqa: E402
from aui.companion import Companion  # noqa: E402


def test_two_tier_signing_and_verify():
    tok = mint_default()
    assert tok.verify() is True
    assert len(tok.identity_signature) == 64 and len(tok.token_signature) == 64


def test_outfit_change_keeps_identity_but_changes_token():
    tok = mint_default()
    outfit = tok.__class__(**{**tok.to_dict(), "base_model": "qwen2.5:1b"}).signed()
    assert outfit.identity_signature == tok.identity_signature   # soul unchanged
    assert outfit.token_signature != tok.token_signature         # outfit changed
    assert outfit.verify() is True


def test_identity_change_moves_both_signatures():
    tok = mint_default()
    soul = tok.__class__(**{**tok.to_dict(), "name": "Nova"}).signed()
    assert soul.identity_signature != tok.identity_signature
    assert soul.token_signature != tok.token_signature


def test_persona_text_folds_in_caption():
    tok = mint_default().__class__(image_caption="a calm woman with violet eyes").signed()
    assert "You appear as: a calm woman with violet eyes" in tok.persona_text()


def test_persona_text_default_has_no_caption_line():
    assert "You appear as" not in mint_default().persona_text()


def test_store_history_and_lineage(tmp_path):
    store = PersonaStore(str(tmp_path))
    first = store.load_or_mint()
    # outfit change → new token, prior appended to history
    store.save({"base_model": "qwen2.5:1b"})
    # identity change → another history entry
    store.save({"name": "Nova"})
    lineage = store.lineage()
    assert len(lineage) >= 3                      # two priors + current
    assert lineage[-1]["current"] is True
    assert sum(1 for h in (tmp_path / "persona.history").glob("*.json")) == 2


def test_store_noop_when_nothing_changes(tmp_path):
    store = PersonaStore(str(tmp_path))
    a = store.load_or_mint()
    b = store.save({})                            # no edits
    assert a.token_signature == b.token_signature
    assert list((tmp_path / "persona.history").glob("*.json")) == []


# ── MET genesis = identity_signature ────────────────────────────────────────

def test_met_genesis_is_identity_signature():
    tok = mint_default()
    c = Companion(generate=lambda m: "ok", genesis=tok.identity_signature)
    c.say("hello")
    mt = c.master_token
    assert mt.genesis == tok.identity_signature
    assert mt.links[0].parent == tok.identity_signature   # first link parents off the soul
    assert mt.verify() is True


def test_met_chain_tamper_breaks_verify():
    c = Companion(generate=lambda m: "ok", genesis="seed-identity")
    c.say("one")
    c.say("two")
    c.master_token.links[0].intent_class = "HARM"   # tamper
    assert c.master_token.verify() is False


def test_apply_persona_identity_change_resets_chain():
    c = Companion(generate=lambda m: "ok", genesis="ident-A")
    c.say("hello")
    assert len(c.master_token.links) == 1
    new = mint_default().__class__(name="Nova").signed()
    c.apply_persona(new)                            # identity changed → fresh root
    assert c.master_token.genesis == new.identity_signature
    assert len(c.master_token.links) == 0
    assert new.persona_text().split(".")[0] in c.persona  # persona re-grounded


# ── MET turns are stamped with the persona (soul + brain) that spoke ─────────

def test_met_turn_stamped_with_persona_sig():
    tok = mint_default()
    c = Companion(generate=lambda m: "ok", genesis=tok.identity_signature,
                  persona_sig=tok.token_signature)
    c.say("hello")
    link = c.master_token.links[0]
    assert link.persona_sig == tok.token_signature   # the exact Aria that spoke
    assert c.master_token.verify() is True
    assert "persona_sig" in link.to_dict()


def test_met_persona_sig_is_in_the_chain_hash():
    # tampering the persona stamp must break verify — it's bound into the hash
    c = Companion(generate=lambda m: "ok", genesis="ident-A", persona_sig="aria-v1")
    c.say("hello")
    c.master_token.links[0].persona_sig = "someone-else"   # tamper
    assert c.master_token.verify() is False


def test_brain_swap_updates_stamp_without_rerooting():
    # outfit change (same soul, new base_model = mistral) keeps the conversation
    # but stamps later turns with the new persona token — still pointing to Aria.
    tok = mint_default()
    c = Companion(generate=lambda m: "ok", genesis=tok.identity_signature,
                  persona_sig=tok.token_signature)
    c.say("before swap")
    swapped = replace_base_model(tok, "mistral:7b")
    c.apply_persona(swapped)
    assert len(c.master_token.links) == 1                  # NOT re-rooted
    assert c.master_token.genesis == tok.identity_signature  # same soul
    c.say("after swap")
    assert c.master_token.links[0].persona_sig == tok.token_signature
    assert c.master_token.links[1].persona_sig == swapped.token_signature
    assert swapped.token_signature != tok.token_signature  # the brain change is recorded
    assert c.master_token.verify() is True


def replace_base_model(tok, model):
    from dataclasses import replace
    from aui.persona import _now
    return replace(tok, base_model=model, updated_at=_now()).signed()
