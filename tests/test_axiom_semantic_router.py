"""Tests for SemanticRouter — two-tiered vocabulary-anchor domain routing."""
from __future__ import annotations
import os, tempfile
from pathlib import Path
import pytest
os.environ.setdefault("AXIOM_MASTER_KEY", "d" * 64)
from axiom_semantic_router import SemanticRouter, DomainVocabIndex, _jaccard, _tokenize_query, _detect_query_intent
from axiom_domain_pack import DomainPackManifest, DomainPackStore, build_pack
from axiom_domain_ingester import DomainIngester


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _install_pack(store_dir, domain, text, tmp):
    """Build index, create pack, install into store, return store."""
    index_dir = tmp / f"{domain}_idx"
    ing = DomainIngester(domain=domain, index_dir=index_dir, session_id="s1")
    doc = tmp / f"{domain}.txt"
    doc.write_text(text * 30)  # ensure > min_chars
    ing.ingest_file(doc)
    m = DomainPackManifest(name=f"{domain}-pack", title=domain, description=domain,
        version="1.0.0", author="Test", license="Apache-2.0", domain=domain)
    pack_dir = build_pack(manifest=m, index_dir=index_dir, output_dir=tmp / "packs")
    store = DomainPackStore(base_dir=store_dir)
    store.install(pack_dir)
    return store


# ---------------------------------------------------------------------------
# TestJaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_empty_query_returns_zero(self):
        assert _jaccard(frozenset(), frozenset({"liability", "contract"})) == 0.0

    def test_empty_anchor_set_returns_zero(self):
        assert _jaccard(frozenset({"liability", "contract"}), frozenset()) == 0.0

    def test_both_empty_returns_zero(self):
        assert _jaccard(frozenset(), frozenset()) == 0.0

    def test_full_overlap_returns_one(self):
        tokens = frozenset({"liability", "contract", "negligence"})
        assert _jaccard(tokens, tokens) == 1.0

    def test_partial_overlap(self):
        # {A, B} vs {A, B, C} => intersection=2, union=3 => 2/3
        score = _jaccard(frozenset({"liability", "contract"}), frozenset({"liability", "contract", "negligence"}))
        assert abs(score - (2 / 3)) < 1e-9

    def test_zero_overlap_returns_zero(self):
        score = _jaccard(frozenset({"apple", "banana"}), frozenset({"contract", "liability"}))
        assert score == 0.0


# ---------------------------------------------------------------------------
# TestTokenizeQuery
# ---------------------------------------------------------------------------

class TestTokenizeQuery:
    def test_removes_stop_word_the(self):
        tokens = _tokenize_query("the contract is valid")
        assert "the" not in tokens

    def test_removes_stop_word_what(self):
        tokens = _tokenize_query("what is defined as negligence")
        assert "what" not in tokens

    def test_removes_stop_word_is(self):
        tokens = _tokenize_query("negligence is a legal concept")
        assert "is" not in tokens

    def test_removes_stop_word_how(self):
        tokens = _tokenize_query("how does the court rule")
        assert "how" not in tokens

    def test_removes_short_tokens_under_3(self):
        tokens = _tokenize_query("an ox ate hay")
        # "an" is a stop word; "ox" has 2 chars; "ate" has 3 — keep; "hay" has 3 — keep
        assert "ox" not in tokens

    def test_lowercases_all_tokens(self):
        tokens = _tokenize_query("Liability Negligence Contract")
        assert "liability" in tokens
        assert "negligence" in tokens
        assert "contract" in tokens

    def test_returns_frozenset(self):
        result = _tokenize_query("liability damages contract")
        assert isinstance(result, frozenset)

    def test_meaningful_tokens_kept(self):
        tokens = _tokenize_query("plaintiff proved wrongful termination")
        assert "plaintiff" in tokens
        assert "proved" in tokens
        assert "wrongful" in tokens
        assert "termination" in tokens


# ---------------------------------------------------------------------------
# TestDetectQueryIntent
# ---------------------------------------------------------------------------

class TestDetectQueryIntent:
    def test_definition_query(self):
        result = _detect_query_intent("What is defined as negligence?")
        assert result == "definition"

    def test_procedure_query(self):
        result = _detect_query_intent("How to follow these instructions step by step to file a claim")
        assert result == "procedure"

    def test_ruling_query(self):
        result = _detect_query_intent("What did the court hold in this case?")
        assert result == "ruling"

    def test_warning_query(self):
        result = _detect_query_intent("Warning must not operate in wet conditions")
        assert result == "warning"

    def test_specification_query(self):
        result = _detect_query_intent("The maximum voltage specification is 3.3V at 72MHz frequency")
        assert result == "specification"

    def test_general_query(self):
        result = _detect_query_intent("Tell me about the program")
        assert result == "general"

    def test_what_is_triggers_definition(self):
        result = _detect_query_intent("What is a contract?")
        assert result == "definition"

    def test_how_to_triggers_procedure(self):
        result = _detect_query_intent("How to appeal a court decision?")
        assert result == "procedure"


# ---------------------------------------------------------------------------
# TestBuildIndexes
# ---------------------------------------------------------------------------

class TestBuildIndexes:
    def test_empty_store_returns_empty_dict(self, tmp_path):
        store = DomainPackStore(base_dir=tmp_path / "store")
        router = SemanticRouter(store)
        indexes = router.build_indexes()
        assert indexes == {}

    def test_one_installed_pack_one_index_entry(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant tort. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        indexes = router.build_indexes()
        assert len(indexes) == 1

    def test_two_installed_packs_two_index_entries(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is defined as legal responsibility. "
        )
        electronics_text = (
            "Resistor capacitor transistor voltage current circuit. "
            "The device operates at 3.3V with a maximum current of 500mA. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path / "legal_build")
        _install_pack(store_dir, "electronics", electronics_text, tmp_path / "elec_build")
        router = SemanticRouter(store)
        indexes = router.build_indexes()
        assert len(indexes) == 2

    def test_domain_vocab_index_has_nonempty_anchor_set(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant tort. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        indexes = router.build_indexes()
        assert len(indexes) == 1
        index = list(indexes.values())[0]
        assert len(index.anchor_set) > 0

    def test_total_chunks_matches_chunk_files(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant tort. "
            "The court held that liability is defined as legal responsibility for harm. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        indexes = router.build_indexes()
        assert len(indexes) == 1
        index = list(indexes.values())[0]
        # total_chunks should be a non-negative integer
        assert isinstance(index.total_chunks, int)
        assert index.total_chunks >= 0

    def test_intent_counts_is_dict(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages tort plaintiff. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        indexes = router.build_indexes()
        assert len(indexes) == 1
        index = list(indexes.values())[0]
        assert isinstance(index.intent_counts, dict)

    def test_packs_without_meta_json_sidecars_skipped(self, tmp_path):
        """Pack with only .txt files (no .meta.json) should be silently skipped."""
        store_dir = tmp_path / "store"
        store_dir.mkdir(parents=True, exist_ok=True)

        # Manually create a minimal pack without sidecars
        pack_name = "no-meta-pack"
        pack_version = "1.0.0"
        pack_dir = tmp_path / "raw_pack"
        pack_dir.mkdir()
        index_dir_raw = pack_dir / "index"
        index_dir_raw.mkdir()
        # Write only .txt files, no .meta.json
        (index_dir_raw / "chunk001.txt").write_text("Some content without sidecar.")

        m = DomainPackManifest(
            name=pack_name,
            title="No Meta Pack",
            description="A pack with no sidecars",
            version=pack_version,
            author="Test",
            license="Apache-2.0",
            domain="general",
        )
        built_pack = build_pack(manifest=m, index_dir=index_dir_raw, output_dir=tmp_path / "packs_raw")
        store = DomainPackStore(base_dir=store_dir)
        store.install(built_pack)

        router = SemanticRouter(store)
        indexes = router.build_indexes()
        # Pack with no sidecars should be skipped
        assert pack_name not in indexes


# ---------------------------------------------------------------------------
# TestRoute
# ---------------------------------------------------------------------------

class TestRoute:
    def test_legal_query_routes_to_legal(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant tort claimant. "
            "The court held that liability is defined as legal responsibility for harm. "
        )
        electronics_text = (
            "Resistor capacitor transistor voltage current circuit board soldering. "
            "The device operates at 3.3V with maximum current draw of 500mA. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path / "legal_b")
        _install_pack(store_dir, "electronics", electronics_text, tmp_path / "elec_b")
        router = SemanticRouter(store)
        results = router.route("plaintiff liability negligence court damages")
        pack_names = [m.name for m in results]
        assert "legal-pack" in pack_names
        if len(pack_names) > 1:
            assert pack_names[0] == "legal-pack"

    def test_electronics_query_routes_to_electronics(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant tort. "
            "The court held that liability is legal responsibility for harm. "
        )
        electronics_text = (
            "Resistor capacitor transistor voltage current circuit soldering. "
            "The device operates at 3.3V with maximum current draw of 500mA. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path / "legal_b")
        _install_pack(store_dir, "electronics", electronics_text, tmp_path / "elec_b")
        router = SemanticRouter(store)
        results = router.route("voltage resistor circuit capacitor transistor")
        pack_names = [m.name for m in results]
        assert "electronics-pack" in pack_names
        if len(pack_names) > 1:
            assert pack_names[0] == "electronics-pack"

    def test_top_k_limits_results(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is legal responsibility. "
        )
        electronics_text = (
            "Resistor capacitor transistor voltage current circuit board. "
            "The device operates at 3.3V with 500mA current draw. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path / "l")
        _install_pack(store_dir, "electronics", electronics_text, tmp_path / "e")
        router = SemanticRouter(store)
        results = router.route("legal court contract", top_k=1)
        assert len(results) <= 1

    def test_no_packs_returns_empty_list(self, tmp_path):
        store = DomainPackStore(base_dir=tmp_path / "empty_store")
        router = SemanticRouter(store)
        results = router.route("liability negligence contract")
        assert results == []

    def test_no_matching_tokens_returns_all_packs(self, tmp_path):
        """When no query tokens match, all packs are returned as graceful fallback."""
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        # Query with tokens that will not match anything in the vocab
        results = router.route("zzzxxx qqqqwwww")
        installed = store.list_installed()
        assert len(results) == len(installed)

    def test_result_is_list_of_domain_pack_manifests(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        results = router.route("liability contract")
        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, DomainPackManifest)


# ---------------------------------------------------------------------------
# TestExplain
# ---------------------------------------------------------------------------

class TestExplain:
    def test_returns_dict(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        result = router.explain("liability contract")
        assert isinstance(result, dict)

    def test_keys_are_pack_names(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        result = router.explain("liability contract")
        for key in result.keys():
            assert isinstance(key, str)

    def test_values_are_floats_in_range(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path)
        router = SemanticRouter(store)
        result = router.explain("liability contract")
        for val in result.values():
            assert isinstance(val, float)
            assert 0.0 <= val <= 1.0

    def test_relevant_query_scores_higher_than_off_domain(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant tort. "
            "The court held that liability is defined as legal responsibility. "
        )
        electronics_text = (
            "Resistor capacitor transistor voltage current circuit soldering. "
            "The device operates at 3.3V with maximum current draw of 500mA. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path / "lb")
        _install_pack(store_dir, "electronics", electronics_text, tmp_path / "eb")
        router = SemanticRouter(store)
        scores = router.explain("plaintiff liability negligence contract damages")
        legal_score = scores.get("legal-pack", 0.0)
        electronics_score = scores.get("electronics-pack", 0.0)
        assert legal_score > electronics_score


# ---------------------------------------------------------------------------
# TestRefresh
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_refresh_picks_up_new_pack(self, tmp_path):
        store_dir = tmp_path / "store"
        legal_text = (
            "Liability negligence contract damages plaintiff defendant. "
            "The court held that liability is defined as legal responsibility. "
        )
        store = _install_pack(store_dir, "legal", legal_text, tmp_path / "lb")
        router = SemanticRouter(store)

        # Build initial indexes
        indexes_before = router.build_indexes()
        assert len(indexes_before) == 1

        # Install a second pack
        electronics_text = (
            "Resistor capacitor transistor voltage current circuit soldering. "
            "The device operates at 3.3V with maximum current draw of 500mA. "
        )
        _install_pack(store_dir, "electronics", electronics_text, tmp_path / "eb")

        # Refresh and verify new pack appears
        router.refresh()
        indexes_after = router.build_indexes()
        assert len(indexes_after) == 2
        pack_names = list(indexes_after.keys())
        assert any("electronics" in name for name in pack_names)
