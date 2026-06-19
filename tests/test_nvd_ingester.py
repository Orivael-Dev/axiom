"""Tests for NVDIngester — background JSONL tail watcher."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_cve_retriever import CVERetriever
from axiom_nvd_ingester import NVDIngester


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db(tmp: Path) -> CVERetriever:
    db = tmp / "cve.db"
    r = CVERetriever(str(db))
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS cve "
        "USING fts5(cve_id, question, answer, tokenize='unicode61')"
    )
    conn.commit()
    conn.close()
    return r


def _jsonl_row(cve_id: str, q: str = "", a: str = "") -> bytes:
    return json.dumps({"User": q or f"what is {cve_id}?",
                       "Assistant": a or f"{cve_id} is a test vulnerability."}).encode() + b"\n"


# ── parse_line ────────────────────────────────────────────────────────────────

class TestParseLine:
    def test_extracts_cve_id(self):
        raw = _jsonl_row("CVE-2021-44228")
        entry = NVDIngester._parse_line(raw)
        assert entry is not None
        assert entry[0] == "CVE-2021-44228"

    def test_blank_line_returns_none(self):
        assert NVDIngester._parse_line(b"") is None
        assert NVDIngester._parse_line(b"   ") is None

    def test_bad_json_returns_none(self):
        assert NVDIngester._parse_line(b"not json") is None

    def test_no_cve_id_empty_string(self):
        raw = json.dumps({"User": "generic question", "Assistant": "generic answer"}).encode()
        entry = NVDIngester._parse_line(raw)
        assert entry is not None
        assert entry[0] == ""   # no CVE pattern → empty cve_id

    def test_cve_in_answer_extracted(self):
        raw = json.dumps({"User": "log4shell?",
                          "Assistant": "CVE-2021-44228 affects..."}).encode()
        entry = NVDIngester._parse_line(raw)
        assert entry[0] == "CVE-2021-44228"


# ── ingest_file ───────────────────────────────────────────────────────────────

class TestIngestFile:
    def test_inserts_all_rows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _make_db(tmp)
            jl = tmp / "nvd.jsonl"
            jl.write_bytes(
                _jsonl_row("CVE-2021-44228") +
                _jsonl_row("CVE-2022-0001") +
                _jsonl_row("CVE-2023-9999")
            )
            ing = NVDIngester(r)
            count = ing.ingest_file(jl)
            assert count == 3

    def test_empty_file_inserts_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _make_db(tmp)
            jl = tmp / "empty.jsonl"
            jl.write_bytes(b"")
            ing = NVDIngester(r)
            assert ing.ingest_file(jl) == 0

    def test_inserted_rows_are_queryable(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _make_db(tmp)
            jl = tmp / "nvd.jsonl"
            jl.write_bytes(_jsonl_row("CVE-2021-44228",
                                      a="Log4Shell JNDI RCE vulnerability"))
            ing = NVDIngester(r)
            ing.ingest_file(jl)
            hits = r.retrieve("Log4Shell JNDI", k=3)
            assert any("Log4Shell" in h.snippet for h in hits)


# ── tail_jsonl background thread ─────────────────────────────────────────────

class TestTailJsonl:
    def test_picks_up_appended_lines(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _make_db(tmp)
            jl = tmp / "live.jsonl"
            # Write one initial row so the file exists and set the offset
            jl.write_bytes(_jsonl_row("CVE-2000-0001"))
            ing = NVDIngester(r, poll_s=0)
            # Start with offset at end — existing rows not ingested
            t = ing.tail_jsonl(jl, daemon=True)
            time.sleep(0.05)   # let thread settle at offset
            # Append a new row AFTER the tail started
            with jl.open("ab") as fh:
                fh.write(_jsonl_row("CVE-2024-9999", a="brand new CVE entry"))
            # Give the thread up to 1 s to pick it up
            deadline = time.monotonic() + 1.0
            found = False
            while time.monotonic() < deadline:
                hits = r.retrieve("brand new CVE entry", k=3)
                if hits:
                    found = True
                    break
                time.sleep(0.05)
            ing.stop()
            assert found, "tail_jsonl did not ingest the appended row"

    def test_stop_terminates_thread(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _make_db(tmp)
            jl = tmp / "live.jsonl"
            jl.write_bytes(b"")
            ing = NVDIngester(r, poll_s=60)
            t = ing.tail_jsonl(jl, daemon=True)
            assert t.is_alive()
            ing.stop(timeout=2.0)
            assert not t.is_alive()

    def test_double_start_raises(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _make_db(tmp)
            jl = tmp / "live.jsonl"
            jl.write_bytes(b"")
            ing = NVDIngester(r, poll_s=60)
            ing.tail_jsonl(jl, daemon=True)
            with pytest.raises(RuntimeError, match="already running"):
                ing.tail_jsonl(jl, daemon=True)
            ing.stop()


# ── insert_entry / insert_batch on CVERetriever ───────────────────────────────

class TestCVERetrieverInsert:
    def test_insert_entry_queryable(self):
        with tempfile.TemporaryDirectory() as d:
            r = _make_db(Path(d))
            r.insert_entry("CVE-2025-1234",
                           "what is CVE-2025-1234?",
                           "A critical RCE in FooBar 1.0")
            hits = r.retrieve("FooBar RCE critical", k=3)
            assert hits

    def test_insert_batch_returns_count(self):
        with tempfile.TemporaryDirectory() as d:
            r = _make_db(Path(d))
            entries = [
                ("CVE-2025-0001", "q1", "answer one"),
                ("CVE-2025-0002", "q2", "answer two"),
            ]
            assert r.insert_batch(entries) == 2

    def test_insert_batch_empty_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            r = _make_db(Path(d))
            assert r.insert_batch([]) == 0

    def test_wal_pragma_set(self):
        with tempfile.TemporaryDirectory() as d:
            r = _make_db(Path(d))
            r.insert_entry("CVE-2025-0001", "q", "a")
            conn = r._connect()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
