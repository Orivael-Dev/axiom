"""Tests for the CUAD → legal-rag-bench adapter (cuad_loader).

The transformation logic is tested deterministically with synthetic SQuAD-shaped
input (no network / no real CUAD download).  Validates chunking, char-offset →
chunk mapping, contract-as-parent id format (so _parent_of collapses correctly),
pooling of multiple contracts, and skipping of unanswerable questions.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "legal"))

from cuad_loader import (  # noqa: E402
    _slugify,
    _chunk_with_offsets,
    _chunk_index_for_offset,
    _extract_squad_records,
    records_to_bench,
    load_cuad,
)
from legal_rag_bench import _parent_of  # noqa: E402


# ── slugify ─────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert _slugify("Master Services Agreement") == "master_services_agreement"

    def test_strips_punctuation(self):
        assert _slugify("ACME Corp. (2021) — NDA!") == "acme_corp_2021_nda"

    def test_empty_falls_back(self):
        assert _slugify("") == "contract"
        assert _slugify("!!!") == "contract"


# ── chunking with offsets ─────────────────────────────────────────────────────

class TestChunkWithOffsets:
    def test_single_chunk_short_text(self):
        chunks = _chunk_with_offsets("the tenant pays rent", max_tokens=400)
        assert len(chunks) == 1
        assert chunks[0][0] == "the tenant pays rent"
        assert chunks[0][1] == 0

    def test_splits_on_token_budget(self):
        text = " ".join(f"w{i}" for i in range(10))
        chunks = _chunk_with_offsets(text, max_tokens=4)
        assert len(chunks) == 3            # 4 + 4 + 2
        # spans are contiguous and word-aligned
        assert chunks[0][0] == "w0 w1 w2 w3"

    def test_offsets_map_back_into_text(self):
        text = "alpha beta gamma delta epsilon"
        chunks = _chunk_with_offsets(text, max_tokens=2)
        for ctext, start, end in chunks:
            assert text[start:end] == ctext

    def test_empty_text(self):
        assert _chunk_with_offsets("") == []


# ── offset → chunk index ───────────────────────────────────────────────────────

class TestChunkIndexForOffset:
    def test_finds_covering_chunk(self):
        text = "alpha beta gamma delta epsilon zeta"
        chunks = _chunk_with_offsets(text, max_tokens=2)
        # "gamma" starts at the index of the 3rd word → chunk 1
        gamma_start = text.index("gamma")
        assert _chunk_index_for_offset(chunks, gamma_start) == 1

    def test_returns_none_when_out_of_range(self):
        chunks = _chunk_with_offsets("alpha beta", max_tokens=2)
        assert _chunk_index_for_offset(chunks, 9999) is None


# ── SQuAD record extraction + bench conversion ────────────────────────────────

def _squad_fixture() -> dict:
    lease_ctx = ("This Lease Agreement is between the landlord and the tenant. "
                 "The tenant shall pay rent monthly for the leased premises. "
                 "Governing Law: this lease is governed by the laws of NSW.")
    nda_ctx = ("This Non-Disclosure Agreement protects confidential proprietary "
               "information disclosed between the parties under strict obligations.")
    return {
        "data": [
            {"title": "Big Lease", "paragraphs": [{
                "context": lease_ctx,
                "qas": [
                    {"id": "lease-rent",
                     "question": "Highlight the parts related to rent in this lease",
                     "answers": [{"text": "pay rent monthly",
                                  "answer_start": lease_ctx.index("pay rent")}],
                     "is_impossible": False},
                    {"id": "lease-impossible",
                     "question": "Highlight the parts related to IP assignment",
                     "answers": [], "is_impossible": True},
                ]}]},
            {"title": "Secret NDA", "paragraphs": [{
                "context": nda_ctx,
                "qas": [
                    {"id": "nda-conf",
                     "question": "Highlight the confidential information clause",
                     "answers": [{"text": "confidential proprietary information",
                                  "answer_start": nda_ctx.index("confidential")}],
                     "is_impossible": False},
                ]}]},
        ]
    }


class TestRecordsToBench:
    def test_extract_squad_records(self):
        recs = _extract_squad_records(_squad_fixture())
        assert len(recs) == 2
        assert recs[0]["title"] == "Big Lease"
        assert len(recs[0]["qas"]) == 2

    def test_corpus_and_qa_built(self):
        recs = _extract_squad_records(_squad_fixture())
        corpus, qa = records_to_bench(recs, max_tokens=400)
        assert len(corpus) >= 2          # at least one chunk per contract
        # the impossible question is skipped → 2 answerable queries remain
        assert len(qa) == 2
        assert {q["id"] for q in qa} == {"lease-rent", "nda-conf"}

    def test_chunk_ids_collapse_to_contract_parent(self):
        recs = _extract_squad_records(_squad_fixture())
        corpus, qa = records_to_bench(recs, max_tokens=400)
        # every gold id is "<slug>-c<N>-s1" → _parent_of gives the contract slug
        for q in qa:
            par = _parent_of(q["relevant_passage_id"])
            assert "-c" not in par and "-s" not in par
        rent = next(q for q in qa if q["id"] == "lease-rent")
        assert _parent_of(rent["relevant_passage_id"]) == "big_lease"

    def test_gold_chunk_actually_contains_answer(self):
        recs = _extract_squad_records(_squad_fixture())
        corpus, qa = records_to_bench(recs, max_tokens=400)
        by_id = {c["id"]: c["text"] for c in corpus}
        rent = next(q for q in qa if q["id"] == "lease-rent")
        assert "rent" in by_id[rent["relevant_passage_id"]].lower()

    def test_multiple_contracts_pooled(self):
        # both contracts' chunks share one corpus → cross-genre distractors
        recs = _extract_squad_records(_squad_fixture())
        corpus, _ = records_to_bench(recs, max_tokens=400)
        slugs = {_parent_of(c["id"]) for c in corpus}
        assert "big_lease" in slugs and "secret_nda" in slugs

    def test_duplicate_titles_disambiguated(self):
        recs = [
            {"title": "Agreement", "context": "first contract text here", "qas": []},
            {"title": "Agreement", "context": "second contract text here", "qas": []},
        ]
        corpus, _ = records_to_bench(recs, max_tokens=400)
        slugs = {_parent_of(c["id"]) for c in corpus}
        assert slugs == {"agreement", "agreement_1"}

    def test_answer_start_misaligned_falls_back_to_substring(self):
        ctx = "alpha beta the tenant pays rent monthly gamma delta"
        recs = [{"title": "X", "context": ctx, "qas": [
            {"id": "q1", "question": "rent?",
             "answers": [{"text": "tenant pays rent", "answer_start": -1}],
             "is_impossible": False}]}]
        corpus, qa = records_to_bench(recs, max_tokens=400)
        assert len(qa) == 1
        by_id = {c["id"]: c["text"] for c in corpus}
        assert "tenant pays rent" in by_id[qa[0]["relevant_passage_id"]]


# ── load_cuad IO wrapper (local json) ──────────────────────────────────────────

class TestLoadCuadJson:
    def test_loads_local_squad_json(self, tmp_path):
        import json
        f = tmp_path / "cuad.json"
        f.write_text(json.dumps(_squad_fixture()), encoding="utf-8")
        corpus, qa = load_cuad(str(f), max_tokens=400)
        assert len(qa) == 2
        assert all("relevant_passage_id" in q for q in qa)
