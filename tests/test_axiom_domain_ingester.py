"""Tests for DomainIngester — chunking, ingestion, KnowledgeCookie wiring, export."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "c" * 64)

from axiom_domain_ingester import (
    ChunkConfig,
    DomainIngester,
    IngestedChunk,
)
from axiom_knowledge_cookie import KnowledgeCookieStore


# ── helpers ───────────────────────────────────────────────────────────────────

def _ingester(tmp: Path, *, knowledge: bool = False, finetune_log: bool = False) -> DomainIngester:
    store = KnowledgeCookieStore(tmp / "knowledge.json") if knowledge else None
    log = tmp / "finetune.jsonl" if finetune_log else None
    return DomainIngester(
        domain="legal",
        index_dir=tmp / "index",
        knowledge_store=store,
        finetune_log=log,
        session_id="test-session",
    )


def _doc(tmp: Path, name: str = "doc.txt", *, paragraphs: int = 10) -> Path:
    p = tmp / name
    p.write_text(("This is a legal document paragraph. " * 20 + "\n\n") * paragraphs)
    return p


# ── ChunkConfig ───────────────────────────────────────────────────────────────

class TestChunkConfig:
    def test_defaults(self):
        c = ChunkConfig()
        assert c.max_chars == 1600
        assert c.overlap_chars == 160
        assert c.min_chars == 100
        assert ".txt" in c.supported_exts
        assert ".md" in c.supported_exts

    def test_custom_values(self):
        c = ChunkConfig(max_chars=800, overlap_chars=80)
        assert c.max_chars == 800


# ── _chunk_text (via ingest_file output) ─────────────────────────────────────

class TestChunking:
    def test_short_doc_produces_one_chunk(self, tmp_path):
        ingester = _ingester(tmp_path)
        doc = tmp_path / "short.txt"
        doc.write_text("Short legal document with enough content. " * 4)  # >100 chars, <1600
        chunks = ingester.ingest_file(doc)
        assert len(chunks) == 1

    def test_long_doc_produces_multiple_chunks(self, tmp_path):
        ingester = _ingester(tmp_path)
        chunks = ingester.ingest_file(_doc(tmp_path, paragraphs=20))
        assert len(chunks) > 1

    def test_chunk_content_hash_is_16_chars(self, tmp_path):
        ingester = _ingester(tmp_path)
        chunks = ingester.ingest_file(_doc(tmp_path))
        for c in chunks:
            assert len(c.content_hash) == 16

    def test_chunks_have_correct_indices(self, tmp_path):
        ingester = _ingester(tmp_path)
        chunks = ingester.ingest_file(_doc(tmp_path, paragraphs=20))
        for i, c in enumerate(chunks):
            assert c.chunk_idx == i

    def test_no_chunk_exceeds_max_chars(self, tmp_path):
        cfg = ChunkConfig(max_chars=400)
        ingester = DomainIngester(domain="legal", index_dir=tmp_path / "idx", chunk_config=cfg)
        chunks = ingester.ingest_file(_doc(tmp_path, paragraphs=10))
        for c in chunks:
            assert c.char_count <= 450   # some tolerance for boundary splitting

    def test_chunk_source_path_is_absolute(self, tmp_path):
        ingester = _ingester(tmp_path)
        chunks = ingester.ingest_file(_doc(tmp_path))
        for c in chunks:
            assert Path(c.source_path).is_absolute()


# ── ingest_file ───────────────────────────────────────────────────────────────

class TestIngestFile:
    def test_creates_index_files(self, tmp_path):
        ingester = _ingester(tmp_path)
        ingester.ingest_file(_doc(tmp_path, paragraphs=10))
        assert len(list((tmp_path / "index").glob("*.txt"))) > 0

    def test_skips_unsupported_extension(self, tmp_path):
        ingester = _ingester(tmp_path)
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-fake")
        chunks = ingester.ingest_file(pdf)
        assert chunks == []

    def test_idempotent_no_duplicate_index_files(self, tmp_path):
        ingester = _ingester(tmp_path)
        doc = _doc(tmp_path)
        ingester.ingest_file(doc)
        count1 = len(list((tmp_path / "index").glob("*.txt")))
        ingester.ingest_file(doc)
        count2 = len(list((tmp_path / "index").glob("*.txt")))
        assert count1 == count2

    def test_returns_empty_list_for_missing_file(self, tmp_path):
        ingester = _ingester(tmp_path)
        chunks = ingester.ingest_file(tmp_path / "nonexistent.txt")
        assert chunks == []

    def test_markdown_extension_supported(self, tmp_path):
        ingester = _ingester(tmp_path)
        md = tmp_path / "guide.md"
        md.write_text("# Legal Guide\n\n" + "Paragraph text. " * 100)
        chunks = ingester.ingest_file(md)
        assert len(chunks) > 0


# ── KnowledgeCookie wiring ────────────────────────────────────────────────────

class TestKnowledgeCookieWiring:
    def test_record_hit_called_per_chunk(self, tmp_path):
        ingester = _ingester(tmp_path, knowledge=True)
        doc = _doc(tmp_path, paragraphs=10)
        chunks = ingester.ingest_file(doc)
        store = KnowledgeCookieStore(tmp_path / "knowledge.json")
        c = store.load()
        assert len(c.fragments) == len(chunks)

    def test_hit_count_increments_on_re_ingest(self, tmp_path):
        ingester = _ingester(tmp_path, knowledge=True)
        doc = tmp_path / "single.txt"
        doc.write_text("Exactly one chunk of text. " * 20)
        ingester.ingest_file(doc)
        ingester.ingest_file(doc)  # second ingest with same session_id
        store = KnowledgeCookieStore(tmp_path / "knowledge.json")
        c = store.load()
        frag = list(c.fragments.values())[0]
        assert frag.hit_count == 2

    def test_finetune_event_emitted_at_threshold(self, tmp_path):
        ingester = _ingester(tmp_path, knowledge=True, finetune_log=True)
        doc = tmp_path / "single.txt"
        doc.write_text("Threshold test fragment content. " * 20)
        # Ingest 10 times with different session IDs to hit FINETUNE_THRESHOLD
        for i in range(10):
            ing = DomainIngester(
                domain="legal",
                index_dir=tmp_path / "index",
                knowledge_store=KnowledgeCookieStore(tmp_path / "knowledge.json"),
                finetune_log=tmp_path / "finetune.jsonl",
                session_id=f"sess-{i:03d}",
            )
            ing.ingest_file(doc)

        log = tmp_path / "finetune.jsonl"
        assert log.exists()
        events = [json.loads(line) for line in log.read_text().splitlines()]
        assert any(e["event"] == "finetune_candidate" for e in events)

    def test_no_cookie_wiring_when_store_is_none(self, tmp_path):
        ingester = DomainIngester(domain="legal", index_dir=tmp_path / "index")
        doc = _doc(tmp_path)
        chunks = ingester.ingest_file(doc)
        assert len(chunks) > 0
        assert not (tmp_path / "knowledge.json").exists()


# ── ingest_folder ─────────────────────────────────────────────────────────────

class TestIngestFolder:
    def test_returns_dict_per_file(self, tmp_path):
        src = tmp_path / "docs"
        src.mkdir()
        (src / "a.txt").write_text("Document A content. " * 50)
        (src / "b.md").write_text("# Doc B\n" + "Markdown content. " * 50)
        ingester = _ingester(tmp_path)
        results = ingester.ingest_folder(src)
        assert str(src / "a.txt") in results
        assert str(src / "b.md") in results

    def test_skips_unsupported_files(self, tmp_path):
        src = tmp_path / "docs"
        src.mkdir()
        (src / "a.txt").write_text("Text content. " * 50)
        (src / "b.pdf").write_bytes(b"%PDF-fake")
        ingester = _ingester(tmp_path)
        results = ingester.ingest_folder(src)
        assert not any("b.pdf" in k for k in results)

    def test_idempotent_second_call_shows_zero_new(self, tmp_path):
        src = tmp_path / "docs"
        src.mkdir()
        (src / "a.txt").write_text("Document content. " * 50)
        ingester = _ingester(tmp_path)
        ingester.ingest_folder(src)
        results2 = ingester.ingest_folder(src)
        assert all(v == 0 for v in results2.values())


# ── export_index ──────────────────────────────────────────────────────────────

class TestExportIndex:
    def test_copies_chunk_files(self, tmp_path):
        ingester = _ingester(tmp_path)
        ingester.ingest_file(_doc(tmp_path, paragraphs=10))
        export = tmp_path / "export"
        count = ingester.export_index(export)
        assert count > 0
        assert len(list(export.glob("*.txt"))) == count

    def test_creates_output_dir_if_absent(self, tmp_path):
        ingester = _ingester(tmp_path)
        ingester.ingest_file(_doc(tmp_path))
        export = tmp_path / "does" / "not" / "exist"
        ingester.export_index(export)
        assert export.is_dir()

    def test_empty_index_returns_zero(self, tmp_path):
        ingester = _ingester(tmp_path)
        count = ingester.export_index(tmp_path / "export")
        assert count == 0


# ── session_id auto-generation ────────────────────────────────────────────────

class TestSessionId:
    def test_auto_generated_when_empty(self):
        ing = DomainIngester(domain="legal", index_dir=Path(tempfile.mkdtemp()))
        assert ing.session_id != ""
        assert len(ing.session_id) > 8

    def test_explicit_session_id_used(self, tmp_path):
        ing = DomainIngester(domain="legal", index_dir=tmp_path / "idx", session_id="my-session")
        assert ing.session_id == "my-session"
