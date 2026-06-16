"""Tests for DatasheetIngester and LocalRetriever.add_documents()."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "cc" * 32)
os.environ["AXIOM_EXTERNAL_RETRIEVAL"] = "0"   # no network calls

from axiom_research_retriever import LocalRetriever, _tokenize
from axiom_datasheet_ingester import DatasheetIngester, _chunk_by_tokens


# ── _chunk_by_tokens ──────────────────────────────────────────────────────────

def test_chunk_empty_text():
    assert _chunk_by_tokens("") == []

def test_chunk_short_text_single_chunk():
    chunks = _chunk_by_tokens("hello world test", max_tokens=400)
    assert len(chunks) == 1

def test_chunk_splits_long_text():
    many_words = " ".join([f"word{i}" for i in range(1000)])
    chunks = _chunk_by_tokens(many_words, max_tokens=100, overlap=10)
    assert len(chunks) > 1

def test_chunk_overlap_produces_more_chunks_than_no_overlap():
    words = " ".join([f"w{i}" for i in range(500)])
    chunks_no_overlap  = _chunk_by_tokens(words, max_tokens=100, overlap=0)
    chunks_with_overlap = _chunk_by_tokens(words, max_tokens=100, overlap=20)
    assert len(chunks_with_overlap) >= len(chunks_no_overlap)


# ── LocalRetriever.add_documents() ───────────────────────────────────────────

def _make_retriever(tmp_path: Path) -> LocalRetriever:
    (tmp_path / "existing.txt").write_text(
        "capacitor 100uF 25V KEMET electrolytic component datasheet"
    )
    r = LocalRetriever(roots=[tmp_path])
    r.build()
    return r


def test_add_documents_makes_new_content_retrievable(tmp_path):
    r = _make_retriever(tmp_path)
    new = tmp_path / "new_part.txt"
    # Use full part number as one word so BM25 tokenizes it consistently.
    # Query with tokens that actually exist in the tokenized document.
    new.write_text("microcontroller flash gpio uart cortexm arm processor")
    count = r.add_documents([new])
    assert count == 1
    # "cortexm" and "flash" are unique to this doc — not in existing.txt
    results = r.retrieve("cortexm flash", k=5)
    assert len(results) >= 1


def test_add_documents_does_not_rebuild(tmp_path):
    r = _make_retriever(tmp_path)
    initial_docs = len(r._docs)
    new = tmp_path / "extra.txt"
    new.write_text("LM317 voltage regulator adjustable 1.25V 37V")
    r.add_documents([new])
    assert r._built is True              # still marked built — no rebuild
    assert len(r._docs) == initial_docs + 1


def test_add_documents_new_term_gets_max_idf(tmp_path):
    r = _make_retriever(tmp_path)
    new = tmp_path / "rare_part.txt"
    new.write_text("XYZABC9999 unique identifier rare component spec")
    r.add_documents([new])
    # Rare term should have IDF > 0 (not missing from index)
    assert r._idf.get("xyzabc9999", 0.0) > 0.0


def test_add_documents_existing_terms_idf_unchanged(tmp_path):
    r = _make_retriever(tmp_path)
    idf_before = dict(r._idf)
    new = tmp_path / "more.txt"
    new.write_text("capacitor resistor inductor passive component")
    r.add_documents([new])
    # Existing terms keep their IDF values unchanged
    for term, idf in idf_before.items():
        assert r._idf[term] == pytest.approx(idf)


def test_add_documents_updates_avg_len(tmp_path):
    r = _make_retriever(tmp_path)
    avg_before = r._avg_len
    new = tmp_path / "long_doc.txt"
    # Write a much longer document
    new.write_text(" ".join([f"token{i}" for i in range(1000)]))
    r.add_documents([new])
    assert r._avg_len != pytest.approx(avg_before)


def test_add_documents_skips_oversized_file(tmp_path):
    r = _make_retriever(tmp_path)
    initial_count = len(r._docs)
    oversized = tmp_path / "big.txt"
    # Create a file > _MAX_BYTES by writing metadata, then patch stat
    oversized.write_text("tiny content")
    import axiom_research_retriever as rmod
    original_max = rmod._MAX_BYTES
    rmod._MAX_BYTES = 5   # temporarily lower cap
    try:
        count = r.add_documents([oversized])
    finally:
        rmod._MAX_BYTES = original_max
    assert count == 0
    assert len(r._docs) == initial_count


def test_add_documents_builds_if_not_yet_built(tmp_path):
    r = LocalRetriever(roots=[tmp_path])
    assert r._built is False
    new = tmp_path / "first.txt"
    new.write_text("transistor NPN 2N2222 amplifier switching")
    r.add_documents([new])
    assert r._built is True


def test_merge_delta_triggers_rebuild(tmp_path):
    r = _make_retriever(tmp_path)
    new = tmp_path / "delta.txt"
    new.write_text("MOSFET IRF540 N-channel 100V 33A power switch")
    r.add_documents([new])
    initial_idf = dict(r._idf)
    r.merge_delta()
    assert r._built is True
    # After merge, IDF is recomputed (may differ from delta approximation)
    # Just check that idf table is populated and results still work
    results = r.retrieve("MOSFET IRF540", k=5)
    assert len(results) >= 1


# ── DatasheetIngester ─────────────────────────────────────────────────────────

def _make_ingester(tmp_path: Path, retriever: LocalRetriever) -> DatasheetIngester:
    cache = tmp_path / "cache"
    return DatasheetIngester(retriever=retriever, cache_dir=cache)


def test_ingest_file_text(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    ds = doc_dir / "part_a.txt"
    ds.write_text("ADS1115 16-bit ADC I2C SPI 4-channel Texas Instruments")
    n = ing.ingest_file(ds)
    assert n >= 1

    results = r.retrieve("ADS1115 ADC", k=3)
    assert any("ADS1115" in (res.snippet + res.title) for res in results)


def test_ingest_file_idempotent(tmp_path):
    """Idempotency guarantee: no new chunk files created on second call for same mtime."""
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    ds = doc_dir / "part_b.txt"
    ds.write_text("MCP4725 DAC 12-bit I2C Microchip")

    n1 = ing.ingest_file(ds)
    chunk_files_after_first = list((cache_dir).rglob("chunk_*.txt"))

    n2 = ing.ingest_file(ds)   # same file, same mtime
    chunk_files_after_second = list((cache_dir).rglob("chunk_*.txt"))

    assert n1 > 0
    # No NEW chunk files created — extraction was skipped (idempotent at PDF level)
    assert len(chunk_files_after_second) == len(chunk_files_after_first)


def test_ingest_file_reingest_on_update(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    ds = doc_dir / "updatable.txt"
    ds.write_text("LM358 op-amp dual channel")
    ing.ingest_file(ds)

    # Modify file (simulate mtime change)
    time.sleep(0.05)
    ds.write_text("LM358 op-amp dual channel updated TLV2372 replacement")
    # Force mtime change
    os.utime(ds, times=(time.time() + 1, time.time() + 1))

    n2 = ing.ingest_file(ds)
    assert n2 >= 1


def test_ingest_folder_returns_chunk_counts(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    (doc_dir / "alpha.txt").write_text("TPS61040 boost converter 1.8V 6V")
    (doc_dir / "beta.txt").write_text("MAX232 RS232 transceiver 5V dual")
    (doc_dir / "gamma.md").write_text("# DS18B20\nOne-wire temperature sensor")
    (doc_dir / "skip.csv").write_text("a,b,c")   # unsupported extension

    results = ing.ingest_folder(doc_dir)
    assert len(results) == 3   # .csv skipped
    assert all(v > 0 for v in results.values())


def test_ingester_stats(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    (doc_dir / "p1.txt").write_text("ATMEGA328P microcontroller AVR")
    ing.ingest_file(doc_dir / "p1.txt")

    s = ing.stats()
    assert s["indexed_sources"] == 1
    assert s["total_chunks"] >= 1
    assert "cache_dir" in s


def test_ingester_skips_unsupported_extension(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    (doc_dir / "data.xlsx").write_text("spreadsheet content")
    results = ing.ingest_folder(doc_dir)
    assert results == {}   # no supported files


def test_ingester_chunk_cache_persists_to_disk(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    ds = doc_dir / "cached_part.txt"
    ds.write_text("Si4703 FM tuner radio receiver I2C Silicon Labs")
    ing.ingest_file(ds)

    # Check chunk files exist on disk
    chunk_files = list(cache_dir.rglob("chunk_*.txt"))
    assert len(chunk_files) >= 1


def test_ingester_index_saved_to_disk(tmp_path):
    doc_dir   = tmp_path / "docs"
    cache_dir = tmp_path / "cache"
    doc_dir.mkdir()
    r = LocalRetriever(roots=[doc_dir])
    r.build()
    ing = DatasheetIngester(r, cache_dir=cache_dir)

    (doc_dir / "indexed.txt").write_text("W25Q128 flash memory SPI 128Mbit")
    ing.ingest_file(doc_dir / "indexed.txt")

    import json
    index_file = cache_dir / "ingester_index.json"
    assert index_file.exists()
    idx = json.loads(index_file.read_text())
    assert len(idx) == 1
