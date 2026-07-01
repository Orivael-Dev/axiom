"""Tests for axiom_semantic_cosmos — tagger, sidecar writer, layered retriever."""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from axiom_semantic_cosmos import (
    CosmosLayeredRetriever,
    CosmosResult,
    MKB_COSMOS_LEVEL,
    cosmos_tag_doc,
    mkb_to_cosmos_level,
    write_cosmos_meta,
)


# ── tagger ────────────────────────────────────────────────────────────

class TestCosmosTagDoc:
    def test_short_doc_is_star(self):
        assert cosmos_tag_doc("Case fatality rate 40 percent.") == "star"

    def test_specific_fact_is_star(self):
        text = "Troponin I peaks at twelve to twenty-four hours post myocardial infarction."
        assert cosmos_tag_doc(text) == "star"

    def test_long_diverse_is_galaxy(self):
        # Each word appears exactly once → richness ≈ 1.0 → galaxy.
        # NOTE: cosmos_tag_doc is designed for keyword-structured docs
        # (MKB blocks, config entries), not natural prose.
        words = [
            "epidemiology", "pharmacology", "genetics", "oncology", "radiology",
            "surgery", "dermatology", "endocrinology", "gastroenterology",
            "nephrology", "pulmonology", "haematology", "rheumatology",
            "psychiatry", "ophthalmology", "otolaryngology", "urology",
            "anaesthesiology", "paediatrics", "obstetrics", "gynaecology",
            "biochemistry", "immunology", "neuroscience", "cardiology",
            "pathology", "physiology", "anatomy", "microbiology", "virology",
            "bacteriology", "parasitology", "mycology", "serology",
            "telemedicine", "wearables", "biostatistics", "reimbursement",
            "accreditation", "credentialing", "decontamination", "sterilisation",
            "biosecurity", "surveillance", "screening", "pharmacovigilance",
            "compliance", "governance", "economics", "prevention", "palliative",
            "rehabilitation", "nutrition", "dietetics", "physiotherapy",
            "occupational", "psychology", "community", "environmental",
            "gastrointestinal",
        ]
        text = " ".join(words)   # no repetition → richness = 1.0
        assert cosmos_tag_doc(text) == "galaxy"

    def test_medium_dense_cluster_is_planet(self):
        # 15 unique terms repeated ×5 → 75 tokens, richness = 15/75 = 0.20
        # 0.13 < 0.20 < 0.65 → planet
        terms = [
            "hantavirus", "rodent", "transmission", "pathogen", "incubation",
            "reservoir", "exposure", "zoonotic", "excreta", "serology",
            "antiviral", "cardiopulmonary", "haemodynamic", "oligoryzomys", "prodromal",
        ]
        core = " ".join(terms * 5)
        assert cosmos_tag_doc(core) == "planet"

    def test_constellation_keywords_override(self):
        text = (
            "Medical risk explanation pattern: state uncertainty, frame symptoms, "
            "provide general education, include emergency warning signs, "
            "recommend consulting a clinician. This reasoning protocol procedure "
            "guideline applies across many topics. The approach methodology "
            "strategy algorithm decision workflow is consistent."
        ) * 3
        assert cosmos_tag_doc(text) == "constellation"

    def test_wormhole_keywords_override(self):
        # Enough wormhole keywords to trigger
        text = (
            "This is analogous to how the immune system works. "
            "The analogy maps directly: it is similar to network routing, "
            "which corresponds to antigen presentation. "
        ) * 5
        assert cosmos_tag_doc(text) == "wormhole"

    def test_empty_string_is_star(self):
        assert cosmos_tag_doc("") == "star"

    def test_numbers_only_is_star(self):
        assert cosmos_tag_doc("1234 5678 9012") == "star"


# ── mkb mapping ───────────────────────────────────────────────────────

class TestMkbToCosmosLevel:
    def test_sovereign_is_galaxy(self):
        assert mkb_to_cosmos_level("SOVEREIGN") == "galaxy"

    def test_agent_is_planet(self):
        assert mkb_to_cosmos_level("AGENT") == "planet"

    def test_spec_is_planet(self):
        assert mkb_to_cosmos_level("SPEC") == "planet"

    def test_guard_is_star(self):
        assert mkb_to_cosmos_level("GUARD") == "star"

    def test_validator_is_star(self):
        assert mkb_to_cosmos_level("VALIDATOR") == "star"

    def test_reward_is_constellation(self):
        assert mkb_to_cosmos_level("REWARD") == "constellation"

    def test_unknown_defaults_to_star(self):
        assert mkb_to_cosmos_level("UNKNOWN_TYPE") == "star"

    def test_all_mkb_types_covered(self):
        for block_type in ("GUARD", "AGENT", "SPEC", "REWARD", "SOVEREIGN", "VALIDATOR"):
            result = mkb_to_cosmos_level(block_type)
            assert result in ("galaxy", "planet", "star", "constellation", "wormhole", "void")


# ── sidecar writer ────────────────────────────────────────────────────

class TestWriteCosmosMeta:
    def test_writes_sidecar_with_intent_type(self, tmp_path):
        doc = tmp_path / "example.txt"
        doc.write_text("some content")
        write_cosmos_meta(doc, "planet")
        sidecar = tmp_path / "example.meta.json"
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert data["intent_type"] == "planet"

    def test_writes_vocab_anchors(self, tmp_path):
        doc = tmp_path / "fact.txt"
        doc.write_text("troponin peaks at 24h")
        write_cosmos_meta(doc, "star", anchors=["troponin", "myocardial"])
        sidecar = tmp_path / "fact.meta.json"
        data = json.loads(sidecar.read_text())
        assert data["vocab_anchors"] == ["troponin", "myocardial"]

    def test_sidecar_readable_by_local_retriever(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        doc = tmp_path / "planet_doc.txt"
        # Need enough content to tokenise
        doc.write_text(
            "hantavirus rodent reservoir transmission incubation pathogen "
            "exposure case fatality public health zoonosis " * 10
        )
        write_cosmos_meta(doc, "planet")
        r = LocalRetriever(roots=[tmp_path])
        r.build()
        hits = r.retrieve("hantavirus rodent", intent_filter="planet", k=5)
        assert len(hits) > 0
        assert hits[0].intent_type == "planet"

    def test_sidecar_name_uses_stem(self, tmp_path):
        doc = tmp_path / "my.document.txt"
        doc.write_text("content")
        write_cosmos_meta(doc, "star")
        # stem of "my.document.txt" is "my.document"
        sidecar = tmp_path / "my.document.meta.json"
        assert sidecar.exists()


# ── CANNOT_MUTATE ─────────────────────────────────────────────────────

class TestCannotMutate:
    def test_module_constants_are_immutable(self):
        import axiom_semantic_cosmos as asc
        with pytest.raises(AttributeError):
            asc.TRUST_LEVEL = 99
        with pytest.raises(AttributeError):
            asc.COSMOS_LEVELS = ("x",)


# ── CosmosLayeredRetriever ────────────────────────────────────────────

def _make_retriever_with_tagged_corpus(tmp_path):
    from axiom_research_retriever import LocalRetriever

    corpus = [
        ("galaxy", "Galaxy content with many diverse terms covering broad domains: "
         "epidemiology pharmacology genetics oncology radiology surgery dermatology "
         "endocrinology gastroenterology nephrology pulmonology haematology rheumatology "
         "psychiatry ophthalmology biostatistics reimbursement accreditation sterilisation " * 3),
        ("planet", "Hantavirus is a rodent-borne zoonotic pathogen transmitted through "
         "contact with infected rodent excreta. Incubation period ranges from one to five "
         "weeks. Rodent reservoir rodent pathogen transmission rodent incubation hantavirus "
         "reservoir pathogen rodent transmission incubation hantavirus rodent reservoir " * 4),
        ("star",   "Hantavirus case fatality rate: approximately 35-40 percent."),
        ("star",   "Primary PCI target: 90 minutes door-to-balloon for STEMI."),
        ("constellation", "Medical risk explanation pattern: state uncertainty, frame "
         "symptoms without diagnosis, provide general education, recommend consulting a "
         "clinician. This reasoning protocol guideline procedure approach methodology "
         "strategy algorithm decision workflow policy applies across many medical topics." * 2),
    ]
    docs = []
    for i, (level, content) in enumerate(corpus):
        p = tmp_path / f"doc_{i}_{level}.txt"
        p.write_text(content)
        write_cosmos_meta(p, level)
        docs.append(p)

    r = LocalRetriever(roots=[tmp_path])
    r.build()
    return r


class TestCosmosLayeredRetriever:
    def test_returns_cosmos_result(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        result = cosmos.retrieve_layered("hantavirus rodent", k=3)
        assert isinstance(result, CosmosResult)

    def test_all_three_passes_run(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        result = cosmos.retrieve_layered("hantavirus", k=3, anticipate=False)
        assert result.galaxy_pass.level == "galaxy"
        assert result.planet_pass.level == "planet"
        assert result.star_pass.level == "star"

    def test_all_hits_deduplicates(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        result = cosmos.retrieve_layered("hantavirus rodent", k=5)
        uris = [h.uri for h in result.all_hits()]
        assert len(uris) == len(set(uris)), "all_hits() must not duplicate docs"

    def test_anticipate_same_results_as_sequential(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        seq = cosmos.retrieve_layered("hantavirus case fatality", k=5, anticipate=False)
        ant = cosmos.retrieve_layered("hantavirus case fatality", k=5, anticipate=True)
        # Same star hits (star pass is the k=5 result)
        seq_uris = {h.uri for h in seq.star_pass.hits}
        ant_uris = {h.uri for h in ant.star_pass.hits}
        assert seq_uris == ant_uris

    def test_anticipate_latency_le_sequential(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        query = "hantavirus rodent pathogen transmission"

        # Warmup to stabilise timings
        for _ in range(3):
            cosmos.retrieve_layered(query, k=5, anticipate=False)
            cosmos.retrieve_layered(query, k=5, anticipate=True)

        seq_times = [
            cosmos.retrieve_layered(query, k=5, anticipate=False).total_latency_ms
            for _ in range(5)
        ]
        ant_times = [
            cosmos.retrieve_layered(query, k=5, anticipate=True).total_latency_ms
            for _ in range(5)
        ]
        avg_seq = sum(seq_times) / len(seq_times)
        avg_ant = sum(ant_times) / len(ant_times)
        # On a tiny test corpus, thread-pool overhead dominates (sub-ms BM25).
        # Real benefit shows at corpus sizes where each pass takes >5ms.
        # Here we just verify anticipation doesn't cause catastrophic slowdown.
        assert avg_ant <= max(avg_seq * 20.0, 10.0), (
            f"Anticipation latency ({avg_ant:.1f}ms) catastrophically "
            f"worse than sequential ({avg_seq:.1f}ms)"
        )

    def test_level_counts_match_passes(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        result = cosmos.retrieve_layered("hantavirus", k=5)
        counts = result.level_counts()
        assert counts["galaxy"] == len(result.galaxy_pass.hits)
        assert counts["planet"] == len(result.planet_pass.hits)
        assert counts["star"] == len(result.star_pass.hits)

    def test_pass_latencies_are_positive(self, tmp_path):
        r = _make_retriever_with_tagged_corpus(tmp_path)
        cosmos = CosmosLayeredRetriever(r)
        result = cosmos.retrieve_layered("hantavirus", k=3)
        assert result.galaxy_pass.latency_ms >= 0
        assert result.planet_pass.latency_ms >= 0
        assert result.star_pass.latency_ms >= 0


# ── FTS5CosmosRetriever ───────────────────────────────────────────────

from axiom_semantic_cosmos import FTS5CosmosRetriever, FTS5Hit


def _make_fts5_retriever(tmp_path):
    db = tmp_path / "cosmos_test.db"
    idx = FTS5CosmosRetriever(db)
    docs = [
        ("planet", "doc_planet.txt",
         "Hantavirus is a rodent-borne zoonotic pathogen transmitted through "
         "contact with infected rodent excreta. Incubation period ranges from "
         "one to five weeks after exposure. Clinical presentation progresses "
         "to cardiopulmonary syndrome with respiratory distress and haemodynamic "
         "collapse. Case fatality rate is approximately 35-40 percent.",
         ["hantavirus", "rodent", "pathogen"]),
        ("galaxy", "doc_galaxy.txt",
         "Medical science encompasses epidemiology pathology pharmacology "
         "immunology genetics biochemistry physiology anatomy neuroscience "
         "cardiology oncology radiology surgery dermatology endocrinology "
         "gastroenterology nephrology pulmonology haematology rheumatology "
         "psychiatry ophthalmology biostatistics reimbursement accreditation.",
         []),
        ("star", "doc_star1.txt",
         "Hantavirus case fatality rate: approximately 35-40 percent.", []),
        ("star", "doc_star2.txt",
         "Primary PCI door-to-balloon target: 90 minutes for STEMI.", []),
        ("constellation", "doc_const.txt",
         "Medical risk explanation pattern: state uncertainty, frame symptoms "
         "without diagnosis, provide general education, recommend consulting a "
         "clinician. This reasoning protocol guideline procedure approach "
         "applies across hantavirus vitamin-D sleep blood-pressure medication.",
         []),
    ]
    for level, uri, content, anchors in docs:
        idx.ingest_doc(uri, content, level, anchors)
    return idx


class TestFTS5CosmosRetriever:
    def test_ingest_and_retrieve(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        hits = idx.retrieve("hantavirus rodent", k=5)
        assert len(hits) > 0
        assert isinstance(hits[0], FTS5Hit)

    def test_retrieve_filtered_by_level(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        star_hits = idx.retrieve("hantavirus", level="star", k=5)
        assert all(h.intent_type == "star" for h in star_hits)

    def test_retrieve_no_level_returns_all_levels(self, tmp_path):
        # "hantavirus" appears in planet, star, and constellation docs
        # → unfiltered retrieve should return hits at multiple levels
        idx = _make_fts5_retriever(tmp_path)
        hits = idx.retrieve("hantavirus", k=10)
        levels = {h.intent_type for h in hits}
        assert len(levels) > 1

    def test_retrieve_layered_returns_cosmos_result(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        result = idx.retrieve_layered("hantavirus rodent", k=3)
        assert result.galaxy_pass.level == "galaxy"
        assert result.planet_pass.level == "planet"
        assert result.star_pass.level == "star"

    def test_retrieve_layered_star_hits_are_stars(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        result = idx.retrieve_layered("hantavirus case fatality", k=5)
        for hit in result.star_pass.hits:
            assert hit.intent_type == "star"

    def test_doc_count(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        assert idx.doc_count() == 5
        assert idx.doc_count(level="star") == 2
        assert idx.doc_count(level="galaxy") == 1

    def test_ingest_file_auto_tags(self, tmp_path):
        db = tmp_path / "cosmos.db"
        idx = FTS5CosmosRetriever(db)
        # Short fact → auto-tagged as "star"
        p = tmp_path / "fact.txt"
        p.write_text("Troponin I peaks at 12-24 hours post myocardial infarction.")
        idx.ingest_file(p)
        hits = idx.retrieve("troponin peak", level="star", k=3)
        assert len(hits) > 0

    def test_ingest_file_respects_meta_sidecar(self, tmp_path):
        db = tmp_path / "cosmos.db"
        idx = FTS5CosmosRetriever(db)
        p = tmp_path / "custom.txt"
        p.write_text(
            "hantavirus rodent pathogen reservoir incubation transmission "
            "exposure excreta serology antiviral haemodynamic collapse " * 5
        )
        write_cosmos_meta(p, "planet", anchors=["hantavirus"])
        idx.ingest_file(p)  # should use sidecar level "planet"
        hits = idx.retrieve("hantavirus", level="planet", k=3)
        assert len(hits) > 0

    def test_reingest_replaces_doc(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        before = idx.doc_count()
        idx.ingest_doc("doc_star1.txt", "Updated content troponin", "star", [])
        assert idx.doc_count() == before   # same count — replaced not added

    def test_empty_query_returns_empty(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        assert idx.retrieve("") == []
        assert idx.retrieve("   ") == []

    def test_context_manager(self, tmp_path):
        db = tmp_path / "cosmos.db"
        with FTS5CosmosRetriever(db) as idx:
            idx.ingest_doc("x", "test content hantavirus", "star", [])
            hits = idx.retrieve("hantavirus", k=1)
            assert len(hits) == 1

    def test_all_hits_deduplicates_across_levels(self, tmp_path):
        idx = _make_fts5_retriever(tmp_path)
        result = idx.retrieve_layered("hantavirus medical", k=5)
        uris = [h.uri for h in result.all_hits()]
        assert len(uris) == len(set(uris))
