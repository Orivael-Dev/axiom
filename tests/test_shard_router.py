"""Tests for ShardRouter, RAGBundle, and SPLADEReranker (fallback path)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_research_retriever import RetrievedSource
from axiom_shard_router import (
    ShardConfig,
    ShardRouter,
    RAGBundle,
    DEFAULT_SHARD_PATTERNS,
)
from axiom_splade_reranker import SPLADEReranker


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_retriever(domain: str, answer_text: str = "test answer") -> MagicMock:
    r = MagicMock()
    r.answer.return_value = (answer_text, False)
    r.retrieve.return_value = [
        RetrievedSource(
            title=domain.upper(), uri=f"fts5://{domain}/test",
            kind=f"fts5 · {domain}", score=0.9,
            snippet=answer_text, provider=domain,
        )
    ]
    r.stats.return_value = {"rows": 1}
    return r


def _shard(domain: str, pattern_key: str, answer: str = "test answer") -> ShardConfig:
    return ShardConfig(
        domain=domain,
        pattern=DEFAULT_SHARD_PATTERNS[pattern_key],
        retriever=_mock_retriever(domain, answer),
    )


def _make_fts5_db(tmp: Path, domain: str = "cve") -> Path:
    db = tmp / f"{domain}.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS cve "
        "USING fts5(cve_id, question, answer, tokenize='unicode61')"
    )
    conn.execute(
        "INSERT INTO cve(cve_id, question, answer) VALUES (?,?,?)",
        ("CVE-2021-44228", "what is CVE-2021-44228?", "Log4Shell RCE vulnerability"),
    )
    conn.commit()
    conn.close()
    return db


# ── ShardRouter.route ─────────────────────────────────────────────────────────

class TestRoute:
    def test_cve_pattern_routes_to_cve_shard(self):
        router = ShardRouter([_shard("cve", "cve")])
        match  = router.route("what is CVE-2021-44228?")
        assert match is not None
        assert match.domain == "cve"

    def test_bug_pattern_routes_to_bugs_shard(self):
        router = ShardRouter([_shard("cve", "cve"), _shard("bugs", "bugs")])
        match  = router.route("details for BUG-1234")
        assert match is not None
        assert match.domain == "bugs"

    def test_no_pattern_returns_none(self):
        router = ShardRouter([_shard("cve", "cve")])
        assert router.route("what is the weather today?") is None

    def test_first_matching_shard_wins(self):
        r1 = _shard("cve", "cve")
        r2 = _shard("cve2", "cve")   # second shard also matches CVE pattern
        router = ShardRouter([r1, r2])
        assert router.route("CVE-2021-44228").domain == "cve"


# ── ShardRouter.query (single shard) ─────────────────────────────────────────

class TestQuerySingle:
    def test_identifier_query_uses_answer_path(self):
        cve_r = _mock_retriever("cve", "Log4Shell answer")
        router = ShardRouter([ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], cve_r)])
        hits = router.query("CVE-2021-44228 vulnerability")
        assert hits
        cve_r.answer.assert_called_once()
        cve_r.retrieve.assert_not_called()

    def test_answer_none_falls_back_to_retrieve(self):
        cve_r = _mock_retriever("cve")
        cve_r.answer.return_value = (None, False)
        router = ShardRouter([ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], cve_r)])
        hits = router.query("CVE-2021-44228")
        cve_r.retrieve.assert_called_once()

    def test_hit_title_contains_cve_id(self):
        router = ShardRouter([_shard("cve", "cve", "Log4Shell details")])
        hits = router.query("what is CVE-2021-44228?")
        assert hits[0].title == "CVE-2021-44228"

    def test_cache_hit_reflected_in_kind(self):
        cve_r = _mock_retriever("cve", "cached answer")
        cve_r.answer.return_value = ("cached answer", True)
        router = ShardRouter([ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], cve_r)])
        hits = router.query("CVE-2021-44228")
        assert "cache" in hits[0].kind


# ── ShardRouter.query_parallel ────────────────────────────────────────────────

class TestQueryParallel:
    def test_fans_out_to_all_shards(self):
        r1 = _mock_retriever("cve",  "cve answer")
        r2 = _mock_retriever("bugs", "bug answer")
        router = ShardRouter([
            ShardConfig("cve",  DEFAULT_SHARD_PATTERNS["cve"],  r1),
            ShardConfig("bugs", DEFAULT_SHARD_PATTERNS["bugs"], r2),
        ])
        hits = router.query_parallel("general security question", k=10)
        assert len(hits) == 2   # one from each shard
        r1.retrieve.assert_called_once()
        r2.retrieve.assert_called_once()

    def test_free_text_uses_parallel(self):
        r1 = _mock_retriever("cve")
        router = ShardRouter([ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], r1)])
        router.query("no identifier here")
        r1.retrieve.assert_called_once()   # parallel path → retrieve, not answer

    def test_deduplicates_by_uri(self):
        r1 = _mock_retriever("cve")
        r2 = _mock_retriever("cve")
        # Both return the same URI
        router = ShardRouter([
            ShardConfig("cve",  DEFAULT_SHARD_PATTERNS["cve"], r1),
            ShardConfig("bugs", DEFAULT_SHARD_PATTERNS["bugs"], r2),
        ])
        hits = router.query_parallel("no match", k=10)
        uris = [h.uri for h in hits]
        assert len(uris) == len(set(uris))

    def test_empty_shards_returns_empty(self):
        router = ShardRouter([])
        assert router.query_parallel("anything") == []

    def test_shard_error_is_swallowed(self):
        bad = _mock_retriever("bad")
        bad.retrieve.side_effect = RuntimeError("db exploded")
        good = _mock_retriever("good", "good answer")
        router = ShardRouter([
            ShardConfig("bad",  re.compile(r"NEVER"),      bad),
            ShardConfig("good", re.compile(r".+"),         good),
        ])
        hits = router.query_parallel("anything")
        assert hits   # good shard still returns results


# ── RAGBundle pack / verify / unpack ─────────────────────────────────────────

class TestRAGBundle:
    def test_pack_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db  = _make_fts5_db(tmp, "cve")
            out = tmp / "bundle.rag.axm"
            result = RAGBundle.pack([("cve", db, None)], out)
            assert out.exists()
            assert result["fingerprint"]
            assert result["shards"] == ["cve"]

    def test_verify_clean_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db  = _make_fts5_db(tmp, "cve")
            out = tmp / "bundle.rag.axm"
            RAGBundle.pack([("cve", db, None)], out)
            ok, info = RAGBundle.verify(out)
            assert ok
            assert info["verified"]
            assert "cve" in info["shards"]

    def test_verify_detects_tampered_db(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db  = _make_fts5_db(tmp, "cve")
            out = tmp / "bundle.rag.axm"
            RAGBundle.pack([("cve", db, None)], out)
            # Corrupt the db inside the zip
            import zipfile, io
            data = out.read_bytes()
            with zipfile.ZipFile(io.BytesIO(data), "r") as zin:
                names = zin.namelist()
                files = {n: zin.read(n) for n in names}
            db_name = next(n for n in names if n.endswith(".db"))
            files[db_name] = b"CORRUPTED"
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zout:
                for n, b in files.items():
                    zout.writestr(n, b)
            out.write_bytes(buf.getvalue())
            ok, info = RAGBundle.verify(out)
            assert not ok
            assert "mismatch" in str(info).lower()

    def test_verify_detects_tampered_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db  = _make_fts5_db(tmp, "cve")
            out = tmp / "bundle.rag.axm"
            RAGBundle.pack([("cve", db, None)], out)
            import zipfile, io
            data = out.read_bytes()
            with zipfile.ZipFile(io.BytesIO(data), "r") as zin:
                names = zin.namelist()
                files = {n: zin.read(n) for n in names}
            manifest = json.loads(files["rag_manifest.json"])
            manifest["shards"][0]["db_sha256"] = "00" * 32   # wrong hash
            files["rag_manifest.json"] = json.dumps(manifest).encode()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zout:
                for n, b in files.items():
                    zout.writestr(n, b)
            out.write_bytes(buf.getvalue())
            ok, info = RAGBundle.verify(out)
            assert not ok

    def test_unpack_extracts_db_file(self):
        with tempfile.TemporaryDirectory() as d:
            tmp  = Path(d)
            db   = _make_fts5_db(tmp, "cve")
            out  = tmp / "bundle.rag.axm"
            dest = tmp / "unpacked"
            RAGBundle.pack([("cve", db, None)], out)
            RAGBundle.unpack(out, dest)
            assert (dest / "shards" / "cve.db").exists()

    def test_unpack_raises_on_tampered_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db  = _make_fts5_db(tmp, "cve")
            out = tmp / "bundle.rag.axm"
            RAGBundle.pack([("cve", db, None)], out)
            # Corrupt manifest signature
            import zipfile, io
            data = out.read_bytes()
            with zipfile.ZipFile(io.BytesIO(data), "r") as zin:
                files = {n: zin.read(n) for n in zin.namelist()}
            manifest = json.loads(files["rag_manifest.json"])
            manifest["signature"] = "0" * 64
            files["rag_manifest.json"] = json.dumps(manifest).encode()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zout:
                for n, b in files.items():
                    zout.writestr(n, b)
            out.write_bytes(buf.getvalue())
            with pytest.raises(RuntimeError, match="verification failed"):
                RAGBundle.unpack(out, tmp / "dest", verify=True)

    def test_multi_shard_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            tmp  = Path(d)
            db1  = _make_fts5_db(tmp, "cve")
            db2  = _make_fts5_db(tmp, "bugs")
            out  = tmp / "multi.rag.axm"
            res  = RAGBundle.pack([("cve", db1, None), ("bugs", db2, None)], out)
            assert sorted(res["shards"]) == ["bugs", "cve"]
            ok, info = RAGBundle.verify(out)
            assert ok
            assert sorted(info["shards"]) == ["bugs", "cve"]

    def test_pack_missing_db_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(FileNotFoundError):
                RAGBundle.pack(
                    [("cve", Path(d) / "nonexistent.db", None)],
                    Path(d) / "out.rag.axm",
                )


# ── SPLADEReranker (no-model fallback) ───────────────────────────────────────

class TestSPLADERerankerFallback:
    """Tests run without transformers/torch; reranker must degrade to identity."""

    def _hits(self, n: int = 3) -> List[RetrievedSource]:
        return [
            RetrievedSource(
                title=f"Hit {i}", uri=f"fts5://shard/hit{i}",
                kind="fts5", score=float(n - i) / n,
                snippet=f"content for hit {i}", provider="test",
            )
            for i in range(n)
        ]

    def test_rerank_returns_same_hits_when_no_model(self):
        r    = SPLADEReranker(model_name="nonexistent/model-that-does-not-exist")
        hits = self._hits(3)
        out  = r.rerank("test query", hits)
        assert len(out) == 3
        assert [h.title for h in out] == [h.title for h in hits]

    def test_empty_hits_returns_empty(self):
        r   = SPLADEReranker()
        out = r.rerank("query", [])
        assert out == []

    def test_available_false_without_model(self):
        r = SPLADEReranker(model_name="nonexistent/model")
        assert r.available is False

    def test_rerank_with_router_integration(self):
        r1  = _mock_retriever("cve", "Log4Shell answer")
        router = ShardRouter([ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], r1)])
        reranker = SPLADEReranker(model_name="nonexistent/model")
        hits = router.query("CVE-2021-44228", reranker=reranker)
        assert hits   # reranker fallback doesn't drop results


# ── DEFAULT_SHARD_PATTERNS coverage ──────────────────────────────────────────

class TestDefaultPatterns:
    @pytest.mark.parametrize("domain,sample", [
        ("cve",        "CVE-2021-44228"),
        ("bugs",       "BUG-1234"),
        ("errors",     "ERR-4032 fault in motor"),
        ("obd",        "check P0301 misfire"),
        ("runbooks",   "process died with SIGABRT"),
        ("regulatory", "GDPR Art.17 deletion"),
        ("tsb",        "TSB-21-034 update"),
    ])
    def test_pattern_matches_sample(self, domain, sample):
        pat = DEFAULT_SHARD_PATTERNS[domain]
        assert pat.search(sample), f"{domain} pattern did not match {sample!r}"
