# -*- coding: utf-8 -*-
"""
EU AI Act Art. 50 content-marking tests.
Marked AI text carries a human-readable disclosure + a signed machine-readable tag;
verify() detects content tampering AND tag forgery.
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_provenance"

from axiom_content_provenance import mark, verify, _TAG_RE, _SEP

NOW = "2026-06-28T00:00:00+00:00"
TEXT = "The capital of France is Paris. éè unicode ok."


class TestProvenancePassed:

    def test_passed_mark_then_verify_is_valid(self):
        marked = mark(TEXT, system="Hello Operator", deployer="Acme",
                      model="claude-opus-4-8", now=NOW)
        r = verify(marked)
        assert r.status == "VALID"
        assert r.ai_marked is True
        assert r.content == TEXT                      # exact original recovered
        assert r.record["ai_generated"] is True
        assert r.record["deployer"] == "Acme"

    def test_passed_human_footer_visible_machine_tag_invisible(self):
        marked = mark(TEXT, deployer="Acme", model="m", now=NOW)
        # Human-readable disclosure is present and mentions Art. 50 + deployer.
        assert "AI-generated content" in marked
        assert "Art. 50" in marked
        assert "Acme" in marked
        # Machine tag is an HTML comment (invisible in rendered output).
        assert _TAG_RE.search(marked)
        assert "<!-- AI-PROVENANCE v1" in marked

    def test_passed_no_footer_still_machine_marked(self):
        marked = mark(TEXT, now=NOW, footer=False)
        assert "AI-generated content" not in marked      # no visible footer
        assert verify(marked).status == "VALID"           # still machine-verifiable

    def test_passed_unicode_roundtrip(self):
        t = "Café — 日本語 — emoji 🤖 — math ∑∫"
        assert verify(mark(t, now=NOW)).content == t


class TestProvenanceBlocked:

    def test_blocked_unmarked_text(self):
        r = verify("just some plain text, never marked")
        assert r.status == "UNMARKED"
        assert r.ai_marked is False

    def test_blocked_content_altered_after_marking(self):
        marked = mark("Refund $100 to the customer.", deployer="Acme", now=NOW)
        tampered = marked.replace("$100", "$9000")        # edit the AI content
        r = verify(tampered)
        assert r.status == "CONTENT_ALTERED"
        assert r.ai_marked is True                          # still detectably AI

    def test_blocked_forged_tag_rejected(self):
        # Flip a character inside the base64 provenance payload → signature breaks.
        marked = mark(TEXT, now=NOW)
        b64 = _TAG_RE.search(marked).group(1)
        flipped = ("A" if b64[10] != "A" else "B")
        forged = marked.replace(b64, b64[:10] + flipped + b64[11:])
        r = verify(forged)
        assert r.status in ("SIG_INVALID", "CONTENT_ALTERED")
        assert r.status == "SIG_INVALID"

    def test_blocked_stripped_tag_reads_as_unmarked(self):
        # An adversary removing the marking should at least be detectable as
        # "no longer marked" (not silently pass as clean human content).
        marked = mark(TEXT, now=NOW)
        stripped = marked.split(_SEP, 1)[0]
        assert verify(stripped).status == "UNMARKED"


class TestProvenanceInvariants:

    def test_invariant_signing_key_not_in_output(self):
        marked = mark(TEXT, now=NOW)
        # The HMAC key must never appear in the marked artifact.
        import axiom_content_provenance as acp
        assert acp._KEY.hex() not in marked

    def test_invariant_two_deployers_differ(self):
        a = verify(mark(TEXT, deployer="Acme", now=NOW)).record["sig"]
        b = verify(mark(TEXT, deployer="Globex", now=NOW)).record["sig"]
        assert a != b      # identity is bound into the signature
