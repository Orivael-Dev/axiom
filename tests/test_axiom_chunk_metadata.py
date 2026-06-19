"""Tests for chunk-level intent typing and metadata sidecars."""
from __future__ import annotations
import json, os, tempfile
from pathlib import Path
import pytest
os.environ.setdefault("AXIOM_MASTER_KEY", "d" * 64)
from axiom_domain_ingester import DomainIngester, IngestedChunk, ChunkConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingest_text(index_dir, text, domain="legal", intent_hint=None):
    ing = DomainIngester(domain=domain, index_dir=index_dir, session_id="test")
    p = index_dir.parent / "doc.txt"
    p.write_text(text)
    return ing.ingest_file(p)


# ---------------------------------------------------------------------------
# TestClassifyIntent
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    """Test DomainIngester._classify_intent()."""

    def _ingester(self, tmp_path):
        return DomainIngester(domain="legal", index_dir=tmp_path / "idx", session_id="t")

    def test_definition_text(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("Negligence is defined as a failure to exercise reasonable care.")
        assert result == "definition"

    def test_definition_means_signal(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("Consideration means something of value exchanged between parties.")
        assert result == "definition"

    def test_procedure_text(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("How to file a claim: first, complete form A, then submit to the court.")
        assert result == "procedure"

    def test_procedure_step_signal(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("Step 1: Gather evidence. Compile all relevant documents.")
        assert result == "procedure"

    def test_ruling_text(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("The court held that the defendant breached the contract.")
        assert result == "ruling"

    def test_ruling_verdict_signal(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("The verdict was entered in favour of the plaintiff after deliberation.")
        assert result == "ruling"

    def test_warning_text(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("Warning: You must not operate this device near flammable materials.")
        assert result == "warning"

    def test_warning_caution_signal(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("Caution: Shall not be used in temperatures above 60°C.")
        assert result == "warning"

    def test_specification_text_with_unit(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("The device operates at 3.3V with a maximum current draw of 500mA.")
        assert result == "specification"

    def test_specification_mhz_unit(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("Clock speed is 400 MHz at standard operating conditions.")
        assert result == "specification"

    def test_plain_text_returns_general(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("This document covers various aspects of the subject matter in detail.")
        assert result == "general"

    def test_empty_string_returns_general(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._classify_intent("")
        assert result == "general"


# ---------------------------------------------------------------------------
# TestExtractVocabAnchors
# ---------------------------------------------------------------------------

class TestExtractVocabAnchors:
    """Test DomainIngester._extract_vocab_anchors()."""

    def _ingester(self, tmp_path):
        return DomainIngester(domain="legal", index_dir=tmp_path / "idx", session_id="t")

    def test_returns_a_list(self, tmp_path):
        ing = self._ingester(tmp_path)
        result = ing._extract_vocab_anchors("liability damages negligence contract")
        assert isinstance(result, list)

    def test_stop_words_not_in_anchors_with(self, tmp_path):
        ing = self._ingester(tmp_path)
        anchors = ing._extract_vocab_anchors(
            "The contract with the parties from that legal document there."
        )
        assert "with" not in anchors
        assert "from" not in anchors
        assert "that" not in anchors
        assert "there" not in anchors

    def test_short_tokens_not_in_anchors(self, tmp_path):
        ing = self._ingester(tmp_path)
        anchors = ing._extract_vocab_anchors("is the to liability damages contract")
        assert "is" not in anchors
        assert "the" not in anchors
        assert "to" not in anchors

    def test_frequency_ordering(self, tmp_path):
        ing = self._ingester(tmp_path)
        # "liability" appears 5x, "apple" appears 1x
        text = ("liability " * 5) + ("apple ")
        anchors = ing._extract_vocab_anchors(text)
        assert len(anchors) >= 2
        # liability should rank before apple
        assert anchors.index("liability") < anchors.index("apple")

    def test_max_anchors_respected(self, tmp_path):
        ing = self._ingester(tmp_path)
        text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike"
        anchors = ing._extract_vocab_anchors(text, max_anchors=3)
        assert len(anchors) <= 3

    def test_domain_param_accepted(self, tmp_path):
        ing = self._ingester(tmp_path)
        # Should not raise; domain param accepted but doesn't change behavior currently
        result = ing._extract_vocab_anchors("liability damages contract", domain="legal")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestChunkMetadata
# ---------------------------------------------------------------------------

class TestChunkMetadata:
    """Test that ingest_file() populates IngestedChunk.intent_type and vocab_anchors."""

    VALID_INTENT_TYPES = {"definition", "procedure", "ruling", "warning", "specification", "general"}

    def test_intent_type_not_empty(self, tmp_path):
        chunks = _ingest_text(tmp_path / "idx", "This is a general text document. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk.intent_type != ""

    def test_vocab_anchors_is_list(self, tmp_path):
        chunks = _ingest_text(tmp_path / "idx", "This is a legal document about liability. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            assert isinstance(chunk.vocab_anchors, list)

    def test_intent_type_is_valid_value(self, tmp_path):
        chunks = _ingest_text(tmp_path / "idx", "This discusses various matters in detail. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk.intent_type in self.VALID_INTENT_TYPES

    def test_definition_text_intent_type(self, tmp_path):
        definition_text = (
            "Negligence is defined as the failure to exercise reasonable care "
            "that a reasonably prudent person would exercise. It refers to conduct "
            "that falls below the standard required by law. " * 10
        )
        chunks = _ingest_text(tmp_path / "idx", definition_text)
        assert len(chunks) > 0
        assert chunks[0].intent_type == "definition"


# ---------------------------------------------------------------------------
# TestMetaSidecar
# ---------------------------------------------------------------------------

class TestMetaSidecar:
    """Test that _write_chunk_meta() writes a .meta.json sidecar."""

    def test_sidecar_exists_after_ingestion(self, tmp_path):
        index_dir = tmp_path / "idx"
        chunks = _ingest_text(index_dir, "Legal liability means duty of care owed. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            meta_path = index_dir / f"{chunk.content_hash}.meta.json"
            assert meta_path.exists(), f"Sidecar missing for chunk {chunk.content_hash}"

    def test_sidecar_is_valid_json(self, tmp_path):
        index_dir = tmp_path / "idx"
        chunks = _ingest_text(index_dir, "Liability is defined as legal responsibility. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            meta_path = index_dir / f"{chunk.content_hash}.meta.json"
            raw = meta_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)  # must not raise
            assert isinstance(parsed, dict)

    def test_sidecar_contains_required_fields(self, tmp_path):
        index_dir = tmp_path / "idx"
        chunks = _ingest_text(index_dir, "Liability is defined as legal responsibility. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            meta_path = index_dir / f"{chunk.content_hash}.meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert "content_hash" in meta
            assert "intent_type" in meta
            assert "domain" in meta
            assert "vocab_anchors" in meta

    def test_sidecar_domain_matches_ingester(self, tmp_path):
        index_dir = tmp_path / "idx"
        domain = "legal"
        ing = DomainIngester(domain=domain, index_dir=index_dir, session_id="test")
        doc = tmp_path / "doc.txt"
        doc.write_text("Contract liability means legal obligation. " * 30)
        chunks = ing.ingest_file(doc)
        assert len(chunks) > 0
        for chunk in chunks:
            meta_path = index_dir / f"{chunk.content_hash}.meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert meta["domain"] == domain

    def test_sidecar_idempotent(self, tmp_path):
        index_dir = tmp_path / "idx"
        text = "Liability is defined as legal responsibility for negligence. " * 30
        ing = DomainIngester(domain="legal", index_dir=index_dir, session_id="test")
        doc = tmp_path / "doc.txt"
        doc.write_text(text)

        chunks1 = ing.ingest_file(doc)
        assert len(chunks1) > 0

        # Ingest again — should not corrupt the sidecar
        chunks2 = ing.ingest_file(doc)

        for chunk in chunks1:
            meta_path = index_dir / f"{chunk.content_hash}.meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert isinstance(meta, dict)
            assert "content_hash" in meta

    def test_sidecar_vocab_anchors_is_list_of_strings(self, tmp_path):
        index_dir = tmp_path / "idx"
        chunks = _ingest_text(index_dir, "Liability negligence damages contract. " * 30)
        assert len(chunks) > 0
        for chunk in chunks:
            meta_path = index_dir / f"{chunk.content_hash}.meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            anchors = meta["vocab_anchors"]
            assert isinstance(anchors, list)
            assert all(isinstance(a, str) for a in anchors)


# ---------------------------------------------------------------------------
# TestRetrieverSidecarLoading
# ---------------------------------------------------------------------------

class TestRetrieverSidecarLoading:
    """Test LocalRetriever.build() loads sidecars."""

    def test_definition_intent_type_loaded(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        definition_text = (
            "Negligence is defined as the failure to exercise reasonable care. "
            "It refers to conduct below the standard required by law. " * 10
        )
        _ingest_text(index_dir, definition_text)
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        assert len(retriever._docs) > 0
        definition_docs = [d for d in retriever._docs if d.intent_type == "definition"]
        assert len(definition_docs) > 0

    def test_vocab_anchors_is_nonempty_tuple(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        _ingest_text(index_dir, "Liability negligence damages contract obligation. " * 30)
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        assert len(retriever._docs) > 0
        # At least one doc should have non-empty anchors
        docs_with_anchors = [d for d in retriever._docs if d.vocab_anchors]
        assert len(docs_with_anchors) > 0
        for doc in docs_with_anchors:
            assert isinstance(doc.vocab_anchors, tuple)

    def test_chunks_without_sidecars_get_defaults(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        index_dir.mkdir(parents=True, exist_ok=True)
        # Write a .txt file without a sidecar
        chunk_file = index_dir / "orphan_chunk.txt"
        chunk_file.write_text("This chunk has no sidecar metadata file present.")
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        orphan_docs = [d for d in retriever._docs if d.path == chunk_file]
        assert len(orphan_docs) == 1
        assert orphan_docs[0].intent_type == "general"
        assert orphan_docs[0].vocab_anchors == ()

    def test_intent_filter_ruling_returns_only_ruling(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        # Ingest a ruling chunk
        ruling_text = (
            "The court held that the defendant was liable for damages caused by negligence. "
            "The judgment was entered in favour of the plaintiff. " * 10
        )
        _ingest_text(index_dir, ruling_text, domain="legal")
        # Ingest a definition chunk into a separate index and copy txt+meta over
        def_index = tmp_path / "def_idx"
        definition_text = (
            "Negligence is defined as failure to meet the standard of care. "
            "It refers to conduct below reasonable expectations. " * 10
        )
        _ingest_text(def_index, definition_text, domain="legal")
        for f in def_index.glob("*.txt"):
            import shutil
            shutil.copy(f, index_dir / f.name)
        for f in def_index.glob("*.meta.json"):
            import shutil
            shutil.copy(f, index_dir / f.name)
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        results = retriever.retrieve("court judgment", intent_filter="ruling")
        assert all(r.intent_type == "ruling" for r in results)

    def test_intent_filter_definition_excludes_ruling(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        ruling_text = (
            "The court held that the defendant was liable for damages. "
            "The judgment was entered after deliberation. " * 10
        )
        _ingest_text(index_dir, ruling_text, domain="legal")
        # Also ingest definition text
        def_index = tmp_path / "def_idx"
        def_index.mkdir(parents=True, exist_ok=True)
        def_text = (
            "Negligence is defined as failure to exercise reasonable care. "
            "Liability refers to legal responsibility for harm caused. " * 10
        )
        ing = DomainIngester(domain="legal", index_dir=def_index, session_id="t2")
        dp = tmp_path / "def_doc.txt"
        dp.write_text(def_text)
        ing.ingest_file(dp)
        import shutil
        for f in def_index.glob("*.txt"):
            shutil.copy(f, index_dir / f.name)
        for f in def_index.glob("*.meta.json"):
            shutil.copy(f, index_dir / f.name)
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        results = retriever.retrieve("definition", intent_filter="definition")
        assert all(r.intent_type != "ruling" for r in results)

    def test_retrieved_source_intent_type_matches_doc(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        ruling_text = (
            "The court held that the defendant was liable. "
            "Judgment was entered for the plaintiff. " * 10
        )
        _ingest_text(index_dir, ruling_text, domain="legal")
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        results = retriever.retrieve("court judgment ruling")
        assert len(results) > 0
        for r in results:
            # Each result's intent_type should be a valid type
            assert r.intent_type in {"definition", "procedure", "ruling", "warning", "specification", "general"}

    def test_retrieved_source_to_dict_includes_intent_type_when_non_general(self, tmp_path):
        from axiom_research_retriever import LocalRetriever
        index_dir = tmp_path / "idx"
        ruling_text = (
            "The court held that the defendant was liable for tortious conduct. "
            "The judgment was entered in favour of the claimant. " * 10
        )
        _ingest_text(index_dir, ruling_text, domain="legal")
        retriever = LocalRetriever(roots=[index_dir])
        retriever.build()
        results = retriever.retrieve("court held judgment")
        ruling_results = [r for r in results if r.intent_type == "ruling"]
        assert len(ruling_results) > 0
        for r in ruling_results:
            d = r.to_dict()
            assert "intent_type" in d
            assert d["intent_type"] == "ruling"
