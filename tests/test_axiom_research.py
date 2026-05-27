"""Tests for axiom_research — the signed multi-branch research engine.

Covers the four pieces independently + the full pipeline end-to-end:

  1. LocalFilesRetriever — finds + scores documents from a tmp corpus
  2. Synthesizer — composes a sensible prompt, calls the LLM, returns text
  3. ResearchEngine — orchestrates retrieve → QRF → synth into a signed report
  4. ResearchReport — HMAC under axiom-research-v1, tamper-detected
  5. QRFAgent — selectively activatable as a peer in the event-token Coordinator

The synthesizer LLM is always a StubLLMClient in tests — never a real
network call. QRF runs in its existing "no LLM API" heuristic mode
(LatentEngine without use_api), which produces deterministic empty
branches; that's enough to validate the SHAPE of the pipeline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    # Force QRF/LatentEngine into heuristic mode so tests don't try
    # to call any LLM (NIM, OpenAI, etc.). axiom_latent.LatentEngine
    # inspects env vars at __init__; clearing them yields client=None.
    for k in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "NIM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_research", "axiom_event_token", "axiom_signing",
            "axiom_qrf", "axiom_latent",
        )):
            sys.modules.pop(mod, None)
    yield


# ─── 1. LocalFilesRetriever ─────────────────────────────────────────────


def test_local_files_retriever_finds_keyword_matches(isolated, tmp_path):
    from axiom_research import LocalFilesRetriever
    (tmp_path / "a.md").write_text(
        "Vitamin D supplementation may improve sleep quality.", encoding="utf-8")
    (tmp_path / "b.md").write_text(
        "Cardiovascular benefits of exercise.", encoding="utf-8")
    (tmp_path / "c.md").write_text(
        "Vitamin D affects sleep architecture in deficient subjects.",
        encoding="utf-8")

    r = LocalFilesRetriever(tmp_path).retrieve(
        "vitamin D sleep", top_k=5,
    )

    assert len(r) == 2  # a.md + c.md, b.md has no overlap
    paths = {d.path for d in r}
    assert "a.md" in paths
    assert "c.md" in paths
    # Top hit has the higher score
    assert r[0].score >= r[1].score


def test_local_files_retriever_returns_empty_for_no_matches(isolated, tmp_path):
    from axiom_research import LocalFilesRetriever
    (tmp_path / "a.md").write_text("Unrelated content.", encoding="utf-8")
    r = LocalFilesRetriever(tmp_path).retrieve("quantum entanglement")
    assert r == []


def test_local_files_retriever_respects_top_k(isolated, tmp_path):
    from axiom_research import LocalFilesRetriever
    for i in range(10):
        (tmp_path / f"doc_{i}.md").write_text(
            f"matching keyword content {i}", encoding="utf-8")
    r = LocalFilesRetriever(tmp_path).retrieve("matching", top_k=3)
    assert len(r) == 3


def test_local_files_retriever_filters_by_extension(isolated, tmp_path):
    from axiom_research import LocalFilesRetriever
    (tmp_path / "a.md").write_text("matching keyword in md", encoding="utf-8")
    (tmp_path / "a.py").write_text("matching keyword in py", encoding="utf-8")
    r = LocalFilesRetriever(tmp_path, extensions=(".md",)).retrieve("matching")
    paths = {d.path for d in r}
    assert "a.md" in paths
    assert "a.py" not in paths


def test_local_files_retriever_snippet_contains_match(isolated, tmp_path):
    from axiom_research import LocalFilesRetriever
    text = "PREFIX " * 30 + "the answer is vitamin D" + " SUFFIX" * 30
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    r = LocalFilesRetriever(tmp_path).retrieve("vitamin")
    assert len(r) == 1
    assert "vitamin" in r[0].snippet.lower()


# ─── 2. Synthesizer ─────────────────────────────────────────────────────


def test_synthesizer_calls_llm_with_canonical_prompt_shape(isolated):
    from axiom_research import RetrievedDoc, StubLLMClient, Synthesizer
    stub = StubLLMClient(response="## My report\nGrounded in [doc_0].\n")
    s = Synthesizer(stub)
    out = s.synthesize(
        query="Does vitamin D improve sleep?",
        docs=[RetrievedDoc(path="a.md", snippet="vitamin D helps", score=0.9)],
        branches=[{"branch_label": "yes, modestly", "probability_weight": 0.6}],
    )
    assert out == "## My report\nGrounded in [doc_0].\n"
    # Prompt must include the query, the doc snippet, AND the branch label
    assert "Does vitamin D improve sleep?" in stub.last_prompt
    assert "vitamin D helps" in stub.last_prompt
    assert "yes, modestly" in stub.last_prompt
    assert "[doc_0]" in stub.last_prompt


def test_synthesizer_handles_empty_docs_gracefully(isolated):
    from axiom_research import StubLLMClient, Synthesizer
    s = Synthesizer(StubLLMClient())
    out = s.synthesize(query="anything", docs=[], branches=[])
    # Stub returns its default response — the test is that this does NOT crash
    assert isinstance(out, str)
    assert len(out) > 0


# ─── 3. ResearchEngine end-to-end ───────────────────────────────────────


def test_research_engine_produces_signed_report(isolated, tmp_path):
    from axiom_research import (
        LocalFilesRetriever, ResearchEngine, StubLLMClient,
    )
    (tmp_path / "doc.md").write_text(
        "Vitamin D affects sleep. Evidence is mixed.", encoding="utf-8")
    engine = ResearchEngine(
        llm=StubLLMClient(response="## Answer\nMixed evidence [doc_0].\n"),
        retriever=LocalFilesRetriever(tmp_path),
        qrf_enabled=False,   # skip QRF for this shape test
    )
    report = engine.run("Does vitamin D improve sleep?")

    assert report.verify() is True
    assert report.payload["query"] == "Does vitamin D improve sleep?"
    assert "Mixed evidence" in report.payload["answer_markdown"]
    assert len(report.payload["citations"]) == 1
    assert report.payload["citations"][0]["path"] == "doc.md"
    assert report.payload["synth_model"] == "stub/test"


def test_research_engine_rejects_unknown_domain(isolated):
    from axiom_research import ResearchEngine, StubLLMClient
    with pytest.raises(ValueError, match="Unknown domain"):
        ResearchEngine(llm=StubLLMClient(), domain="quantum-eldritch")


def test_research_engine_qrf_enabled_attaches_branches(isolated, tmp_path):
    """QRF runs in heuristic mode (no API key) — branches list is
    populated even when LLM is unavailable. The engine must NOT crash
    if QRF returns empty branches, and the signed report still verifies.
    """
    from axiom_research import (
        LocalFilesRetriever, ResearchEngine, StubLLMClient,
    )
    (tmp_path / "doc.md").write_text("relevant content", encoding="utf-8")
    engine = ResearchEngine(
        llm=StubLLMClient(),
        retriever=LocalFilesRetriever(tmp_path),
        domain="financial",
        qrf_enabled=True,
    )
    report = engine.run("relevant question")
    assert report.verify() is True
    # branches list is present (may be empty in heuristic mode); shape is
    # what matters — every branch dict has the canonical keys
    assert "branches" in report.payload
    assert isinstance(report.payload["branches"], list)
    for b in report.payload["branches"]:
        assert "branch_label" in b
        assert "probability_weight" in b


# ─── 4. ResearchReport signing ──────────────────────────────────────────


def test_report_signature_verifies(isolated):
    from axiom_research import ResearchReport
    r = ResearchReport.signed(payload={"query": "x", "answer_markdown": "y"})
    assert r.verify() is True


def test_tampered_report_fails_verify(isolated):
    from axiom_research import ResearchReport
    original = ResearchReport.signed(payload={"query": "x", "answer": "y"})
    tampered = ResearchReport(
        payload={**original.payload, "answer": "TAMPERED"},
        confidence=original.confidence,
        signature=original.signature,
    )
    assert tampered.verify() is False


def test_research_report_uses_dedicated_namespace(isolated):
    from axiom_research import RESEARCH_KEY_NS
    from axiom_event_token.models import (
        COORD_KEY_NS, LAYER_KEY_NS, TOKEN_KEY_NS,
    )
    assert RESEARCH_KEY_NS == b"axiom-research-v1"
    assert RESEARCH_KEY_NS != LAYER_KEY_NS
    assert RESEARCH_KEY_NS != COORD_KEY_NS
    assert RESEARCH_KEY_NS != TOKEN_KEY_NS


def test_research_report_roundtrip_through_json(isolated):
    from axiom_research import ResearchReport
    original = ResearchReport.signed(
        payload={"query": "q", "answer_markdown": "a", "branches": []},
    )
    raw = original.to_json()
    restored = ResearchReport.from_dict(json.loads(raw))
    assert restored.signature == original.signature
    assert restored.verify() is True


# Section 5 (QRFAgent in the event-token Coordinator) lives with the
# event_token bonded-pair PR — the Coordinator's `qrf` agent registration
# ships there, not here.
