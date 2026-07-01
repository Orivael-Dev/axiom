# -*- coding: utf-8 -*-
"""
GDPR data-subject-rights adapter tests — access, portability, erasure (crypto-shred
of mutable PII; integrity ledger retained hash-only), signed receipts.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_dsr"

from axiom_data_subject import (
    DataSubjectService, JsonlSubjectStore, verify_receipt, TOMBSTONE,
)

NOW = "2026-06-28T00:00:00+00:00"


@pytest.fixture()
def stores(tmp_path):
    # Mutable application store with PII.
    app = tmp_path / "app.jsonl"
    app.write_text("\n".join(json.dumps(r) for r in [
        {"user_id": "u-42", "name": "Alice", "email": "alice@x.io", "note": "vip"},
        {"user_id": "u-99", "name": "Bob", "email": "bob@x.io", "note": "n/a"},
        {"user_id": "u-42", "name": "Alice", "email": "alice@x.io", "note": "second"},
    ]) + "\n", encoding="utf-8")
    # Append-only integrity ledger — hashes only, no raw PII.
    led = tmp_path / "ledger.jsonl"
    led.write_text("\n".join(json.dumps(r) for r in [
        {"subject_id": "u-42", "event": "decision", "content_sha256": "abc123", "sig": "z"},
        {"subject_id": "u-99", "event": "decision", "content_sha256": "def456", "sig": "z"},
    ]) + "\n", encoding="utf-8")
    svc = DataSubjectService()
    svc.register(JsonlSubjectStore(app, subject_key="user_id",
                                   pii_fields=("name", "email"), name="app"))
    svc.register(JsonlSubjectStore(led, subject_key="subject_id",
                                   append_only=True, name="ledger"))
    return svc, app, led


class TestAccessPortability:

    def test_access_finds_all_subject_records(self, stores):
        svc, _, _ = stores
        rep = svc.access("u-42", NOW)
        assert rep["total"] == 3                 # 2 app rows + 1 ledger row
        assert len(rep["records"]["app"]) == 2
        assert len(rep["records"]["ledger"]) == 1
        assert verify_receipt(rep)

    def test_access_unknown_subject_empty(self, stores):
        svc, _, _ = stores
        rep = svc.access("nobody", NOW)
        assert rep["total"] == 0

    def test_portability_is_structured_json(self, stores):
        svc, _, _ = stores
        exp = svc.portability("u-42", NOW)
        assert exp["format"] == "json"
        assert exp["data"]["app"][0]["user_id"] == "u-42"
        assert verify_receipt(exp)


class TestErasure:

    def test_erasure_cryptoshreds_mutable_pii(self, stores):
        svc, app, _ = stores
        receipt = svc.erasure("u-42", NOW)
        rows = [json.loads(l) for l in app.read_text().splitlines() if l.strip()]
        # u-42 PII fields tombstoned; non-PII + other subjects untouched.
        for r in rows:
            if r["user_id"] == "u-42":
                assert r["name"].startswith(TOMBSTONE) and r["email"].startswith(TOMBSTONE)
                assert r["note"] in ("vip", "second")          # non-PII preserved
            else:
                assert r["name"] == "Bob"                        # other subject intact
        assert receipt["redacted_stores"] == 1
        assert verify_receipt(receipt)

    def test_erasure_retains_integrity_ledger_hash_only(self, stores):
        svc, _, led = stores
        before = led.read_text()
        receipt = svc.erasure("u-42", NOW)
        after = led.read_text()
        assert before == after                                  # chain untouched
        led_result = next(r for r in receipt["results"] if r["store"] == "ledger")
        assert led_result["mode"] == "retained_hash_only"
        assert led_result["matched"] == 1

    def test_erasure_idempotent(self, stores):
        svc, app, _ = stores
        svc.erasure("u-42", NOW)
        once = app.read_text()
        svc.erasure("u-42", NOW)                                 # second time = no further change
        assert app.read_text() == once


class TestReceiptIntegrity:

    def test_tampered_receipt_fails_verify(self, stores):
        svc, _, _ = stores
        rep = svc.access("u-42", NOW)
        rep["total"] = 999
        assert verify_receipt(rep) is False

    def test_signing_key_not_in_receipt(self, stores):
        svc, _, _ = stores
        import axiom_data_subject as d
        blob = json.dumps(svc.erasure("u-42", NOW))
        assert d._KEY.hex() not in blob
