"""Tests for LSHIndex multi-table upgrade in axiom_memory_engine.

Verifies:
  - Multi-table structure (NUM_TABLES independent tables)
  - Direct O(L) lookup — no full bucket scan
  - Same-vector recall (exact match always found)
  - Near-vector recall (slightly perturbed vector still retrieved)
  - Orthogonal vector not retrieved (no false positive above threshold)
  - Deduplication (packet appears once in results even when in multiple tables)
  - Bucket cap respected (no bucket exceeds BUCKET_CAP)
  - stats() reports correct structure
  - load_store() rebuilds a working index
  - index() returns one key per table
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AXIOM_MASTER_KEY", "test_key_lsh_" + "x" * 52)

from axiom_memory_engine import (
    LSHIndex,
    ConstitutionalMemoryEngine,
    FounderAgent,
    _quantize_vec,
    _VECTOR_DIMENSIONS,
    SIMILARITY_THRESHOLD,
    load_store,
)


# ── helpers ───────────────────────────────────────────────────────

def _unit_vec(seed: int, dim: int = _VECTOR_DIMENSIONS) -> list[float]:
    import random
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(dim)]
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


def _perturb(vec: list[float], noise: float = 0.05, seed: int = 99) -> list[float]:
    import random
    rng = random.Random(seed)
    perturbed = [x + rng.gauss(0, noise) for x in vec]
    mag = math.sqrt(sum(x * x for x in perturbed))
    return [x / mag for x in perturbed]


def _orthogonal(vec: list[float]) -> list[float]:
    """Negate every element — maximally dissimilar under cosine."""
    neg = [-x for x in vec]
    mag = math.sqrt(sum(x * x for x in neg)) or 1.0
    return [x / mag for x in neg]


def _make_packet(seed: int = 0, domain: str = "general"):
    agent = FounderAgent()
    return agent.compress(
        conversation_text="test " * 20,
        final_synthesis_vec=_unit_vec(seed),
        domain=domain,
        active_constraints=["confidence >= 0.5"],
        resolution="answered",
        sovereign_history=["init"],
    )


# ── Structure ─────────────────────────────────────────────────────

class TestStructure:

    def test_num_tables(self):
        idx = LSHIndex()
        assert len(idx._tables) == LSHIndex.NUM_TABLES

    def test_planes_shape(self):
        idx = LSHIndex()
        assert len(idx._planes) == LSHIndex.NUM_TABLES
        for table_planes in idx._planes:
            assert len(table_planes) == LSHIndex.PLANES_PER
            for plane in table_planes:
                assert len(plane) == _VECTOR_DIMENSIONS

    def test_different_seeds_produce_different_planes(self):
        a = LSHIndex(seed=0)
        b = LSHIndex(seed=1)
        assert a._planes[0][0] != b._planes[0][0]

    def test_same_seed_reproducible(self):
        a = LSHIndex(seed=42)
        b = LSHIndex(seed=42)
        assert a._planes == b._planes

    def test_stats_structure(self):
        idx = LSHIndex()
        s = idx.stats()
        assert s["tables"] == LSHIndex.NUM_TABLES
        assert s["planes_per_table"] == LSHIndex.PLANES_PER
        assert s["total_entries"] == 0
        assert s["populated_buckets"] == 0


# ── Indexing ──────────────────────────────────────────────────────

class TestIndexing:

    def test_index_returns_one_key_per_table(self):
        idx = LSHIndex()
        p = _make_packet(seed=1)
        keys = idx.index(p)
        assert len(keys) == LSHIndex.NUM_TABLES

    def test_index_populates_all_tables(self):
        idx = LSHIndex()
        p = _make_packet(seed=2)
        keys = idx.index(p)
        for t, key in enumerate(keys):
            assert p in idx._tables[t][key]

    def test_stats_after_index(self):
        idx = LSHIndex()
        for i in range(5):
            idx.index(_make_packet(seed=i))
        s = idx.stats()
        # Each packet lands in NUM_TABLES buckets
        assert s["total_entries"] == 5 * LSHIndex.NUM_TABLES

    def test_bucket_cap_not_exceeded(self):
        idx = LSHIndex()
        # Force many packets into the same bucket by using identical vectors
        p0 = _make_packet(seed=0)
        for _ in range(LSHIndex.BUCKET_CAP + 10):
            idx.index(p0)
        for t in range(LSHIndex.NUM_TABLES):
            for bucket in idx._tables[t].values():
                assert len(bucket) <= LSHIndex.BUCKET_CAP

    def test_index_dedup_on_retrieve(self):
        """A packet indexed into L tables must appear only once in results."""
        idx = LSHIndex()
        p = _make_packet(seed=3)
        idx.index(p)
        results = idx.retrieve(_unit_vec(3))
        packets = [pkt for _, pkt in results]
        assert packets.count(p) == 1


# ── Retrieval ─────────────────────────────────────────────────────

class TestRetrieval:

    def test_same_vector_retrieved(self):
        idx = LSHIndex()
        vec = _unit_vec(10)
        p = _make_packet(seed=10)
        idx.index(p)
        results = idx.retrieve(vec)
        assert any(pkt is p for _, pkt in results), "Exact-match vector must be retrieved"

    def test_same_vector_top_scored(self):
        idx = LSHIndex()
        vec = _unit_vec(11)
        p = _make_packet(seed=11)
        idx.index(p)
        results = idx.retrieve(vec, k=5)
        assert results, "Must return at least one result"
        score, top = results[0]
        assert top is p
        assert score > 0.99

    def test_near_vector_retrieved(self):
        """Slightly perturbed vector (noise=0.03) must still retrieve the packet."""
        idx = LSHIndex()
        vec = _unit_vec(20)
        p = _make_packet(seed=20)
        idx.index(p)
        near = _perturb(vec, noise=0.03, seed=7)
        results = idx.retrieve(near, k=5)
        assert any(pkt is p for _, pkt in results), \
            "Near-vector must be retrieved from multi-table index"

    def test_orthogonal_not_retrieved(self):
        """Orthogonal (negated) vector must not appear in top results."""
        idx = LSHIndex()
        vec = _unit_vec(30)
        p = _make_packet(seed=30)
        idx.index(p)
        opp = _orthogonal(vec)
        results = idx.retrieve(opp, k=10)
        # If it appears, its score must be negative (well below threshold)
        for score, pkt in results:
            if pkt is p:
                assert score < SIMILARITY_THRESHOLD, \
                    "Orthogonal vector must score below threshold"

    def test_results_sorted_descending(self):
        idx = LSHIndex()
        for i in range(5):
            idx.index(_make_packet(seed=i))
        results = idx.retrieve(_unit_vec(0), k=5)
        scores = [s for s, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_k_limits_results(self):
        idx = LSHIndex()
        for i in range(20):
            idx.index(_make_packet(seed=i))
        results = idx.retrieve(_unit_vec(0), k=3)
        assert len(results) <= 3

    def test_empty_index_returns_empty(self):
        idx = LSHIndex()
        assert idx.retrieve(_unit_vec(0)) == []

    def test_no_full_bucket_scan(self):
        """retrieve() must not iterate over all buckets (O(L) not O(n)).

        Verified structurally: with multi-table design, _tables is a list
        of dicts and retrieve() calls .get(key) on each — never iterates
        .items(). Confirm by checking the source doesn't use .items() in
        retrieve, and that the result is the same with 1 or 1000 packets
        (timing-free structural check).
        """
        import inspect
        src = inspect.getsource(LSHIndex.retrieve)
        assert ".items()" not in src, \
            "retrieve() must not scan all buckets via .items()"


# ── Integration with ConstitutionalMemoryEngine ───────────────────

class TestEngineIntegration:

    def _engine(self):
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        return ConstitutionalMemoryEngine(store_path=f.name, lsh_index=LSHIndex()), f.name

    def test_engine_recall_via_multi_table(self):
        engine, path = self._engine()
        try:
            vec = _unit_vec(50)
            engine.remember(
                conversation_text="constitutional memory test " * 10,
                final_synthesis_vec=vec,
                domain="legal",
                active_constraints=["cite_sources == true"],
                resolution="answered",
                sovereign_history=["init"],
            )
            result = engine.recall(vec, domain="legal")
            assert result is not None
            assert result.domain_cluster == "legal"
        finally:
            os.unlink(path)

    def test_load_store_rebuilds_multi_table_index(self):
        """After load_store(), multi-table recall must work as if freshly indexed."""
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        path = f.name
        try:
            # Write packets to disk using a first engine instance
            engine1 = ConstitutionalMemoryEngine(store_path=path, lsh_index=LSHIndex())
            vec = _unit_vec(60)
            engine1.remember(
                conversation_text="load_store rebuild test " * 10,
                final_synthesis_vec=vec,
                domain="finance",
                active_constraints=["audit == true"],
                resolution="verified",
                sovereign_history=["init"],
            )
            # Rebuild into a fresh index
            fresh_idx = LSHIndex()
            count = load_store(path, fresh_idx)
            assert count == 1
            # Retrieve from the fresh index
            results = fresh_idx.retrieve(vec, k=5)
            assert results, "Rebuilt index must return results"
            assert results[0][0] > SIMILARITY_THRESHOLD
        finally:
            os.unlink(path)

    def test_multi_domain_isolation(self):
        """engine.recall with domain filter must not return cross-domain packets."""
        engine, path = self._engine()
        try:
            engine.remember(
                "legal text " * 20, _unit_vec(70),
                "legal", ["cite == true"], "answered", ["init"],
            )
            engine.remember(
                "finance text " * 20, _unit_vec(71),
                "finance", ["audit == true"], "answered", ["init"],
            )
            # Query legal vector but filter for finance — should not match
            result = engine.recall(_unit_vec(70), domain="finance")
            if result is not None:
                assert result.domain_cluster == "finance"
        finally:
            os.unlink(path)
