"""Tests for axiom_research_retriever — local BM25-ish retriever."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_research_retriever",)):
            sys.modules.pop(mod, None)
    yield


def _build_corpus(root: Path):
    """Three small docs with distinct vocabulary so scoring is checkable."""
    (root / "alpha.md").write_text(
        "# Event Token Spec\n"
        "The event token is signed via HMAC and carries layer reports "
        "for text, audio, and video agents.",
        encoding="utf-8",
    )
    (root / "beta.md").write_text(
        "# Patent Strategy\n"
        "Patent counsel packet timelines and continuation strategy for "
        "ORVL-016, ORVL-017, ORVL-023 inventions.",
        encoding="utf-8",
    )
    (root / "gamma.py").write_text(
        '"""Some Python module."""\n'
        "def calculate_token_budget(prompt, output):\n"
        "    return prompt + output\n",
        encoding="utf-8",
    )
    sub = root / "sub"
    sub.mkdir()
    (sub / "delta.md").write_text(
        "# Backend Selection\n"
        "AXIOM ships LocalNanoBackend for Ollama and NIMBackend for "
        "NVIDIA NIM, plus a ChainedBackend for fallback.",
        encoding="utf-8",
    )


def test_retriever_finds_event_token_doc(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    hits = r.retrieve("event token signed", k=4)
    assert hits, "should find at least one hit"
    assert hits[0].uri.endswith("alpha.md")
    assert "Event Token" in hits[0].title
    assert hits[0].score == 1.0          # normalized to top
    assert "signed" in hits[0].snippet.lower()


def test_retriever_finds_backend_doc(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    hits = r.retrieve("nvidia nim backend ollama", k=4)
    assert hits
    assert hits[0].uri.endswith("delta.md")
    assert "Backend" in hits[0].title


def test_retriever_ranks_by_relevance(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    hits = r.retrieve("patent strategy continuation", k=4)
    assert hits
    # beta.md is the right answer; alpha/gamma have ~0 patent vocab.
    assert hits[0].uri.endswith("beta.md")
    assert all(h.score <= 1.0 for h in hits)
    if len(hits) > 1:
        assert hits[0].score >= hits[1].score


def test_retriever_no_match_returns_empty(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    # Query terms that aren't in any doc → no hits.
    hits = r.retrieve("xenomorphism quantum cobblestone unicorn", k=4)
    assert hits == []


def test_retriever_empty_query(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    assert r.retrieve("") == []
    assert r.retrieve("   ") == []


def test_retriever_kind_strings(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    hits = r.retrieve("token budget calculate", k=4)
    kinds = {h.uri.rsplit("/", 1)[-1]: h.kind for h in hits}
    if "gamma.py" in kinds:
        assert "py" in kinds["gamma.py"]


def test_retriever_indexes_subdirectories(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    r.build()
    rels = {d.relative for d in r._docs}
    assert any(rel.endswith("delta.md") for rel in rels), \
        f"subdir not indexed; got {rels}"


def test_retriever_skips_excluded_segments(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "should_skip.md").write_text("event token event token",
                                              encoding="utf-8")
    (tmp_path / "real.md").write_text("event token event token",
                                       encoding="utf-8")
    r = LocalRetriever(roots=[tmp_path])
    r.build()
    rels = {d.relative for d in r._docs}
    assert not any("__pycache__" in rel for rel in rels)
    assert "real.md" in rels


def test_retriever_caps_file_size(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    (tmp_path / "huge.md").write_text("event token " * 50_000,
                                       encoding="utf-8")
    (tmp_path / "small.md").write_text("event token event token event",
                                        encoding="utf-8")
    r = LocalRetriever(roots=[tmp_path])
    r.build()
    rels = {d.relative for d in r._docs}
    assert "small.md" in rels
    assert "huge.md" not in rels    # capped


def test_retriever_stats(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    _build_corpus(tmp_path)
    r = LocalRetriever(roots=[tmp_path])
    s = r.stats()
    assert s["built"] is False
    r.build()
    s = r.stats()
    assert s["built"] is True
    assert s["indexed_files"] >= 3


def test_retriever_snippet_centred_on_first_hit(isolated, tmp_path):
    from axiom_research_retriever import LocalRetriever
    (tmp_path / "long.md").write_text(
        ("padding " * 200) + " IMPORTANT_TERM here " + ("filler " * 200),
        encoding="utf-8",
    )
    r = LocalRetriever(roots=[tmp_path])
    hits = r.retrieve("important_term", k=1)
    assert hits
    assert "IMPORTANT_TERM" in hits[0].snippet
