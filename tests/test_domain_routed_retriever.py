"""Tests for DomainRoutedRetriever — per-domain corpus routing.

Mirrors test_domain_routed_backend.py in shape: stub corpora seeded
on disk, DomainRoutedRetriever wraps LocalRetriever instances per
domain, dispatch verified by inspecting which corpus the hits came
from.

Also covers default_retriever() auto-wrapping when
AXIOM_RETRIEVAL_DIR_<DOMAIN> env vars are present.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    # default_retriever() would otherwise wrap us in a MultiProvider
    # retriever and hit PubMed / ClinicalTrials / openFDA over the
    # network. Force local-only so this test stays hermetic.
    monkeypatch.setenv("AXIOM_EXTERNAL_RETRIEVAL", "0")
    for mod in list(sys.modules):
        if mod.startswith(("axiom_research_retriever",)):
            sys.modules.pop(mod, None)
    # Clear any per-domain corpus env from prior tests.
    for k in list(os.environ):
        if k.startswith("AXIOM_RETRIEVAL_DIR_"):
            monkeypatch.delenv(k, raising=False)
    yield


def _seed_corpus(root: Path, label: str, body: str):
    """Write a single .md file under root so the LocalRetriever has
    something to index. The body is what gets matched against queries
    so callers can verify which corpus served a given hit."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{label}.md").write_text(
        f"# {label}\n{body}\n",
        encoding="utf-8",
    )


# ─── DomainRoutedRetriever dispatch ─────────────────────────────────

class TestDomainRoutedRetriever:
    def test_dispatches_to_matching_domain(self, isolated, tmp_path):
        from axiom_research_retriever import (
            DomainRoutedRetriever, LocalRetriever,
        )
        med_dir = tmp_path / "medical"
        sec_dir = tmp_path / "security"
        gen_dir = tmp_path / "general"
        _seed_corpus(med_dir, "med-doc",
                     "warfarin dosing in atrial fibrillation patients")
        _seed_corpus(sec_dir, "sec-doc",
                     "warfarin is also a CTF challenge keyword somehow")
        _seed_corpus(gen_dir, "gen-doc",
                     "warfarin is sometimes mentioned in pop history")

        routed = DomainRoutedRetriever(
            default=LocalRetriever(roots=[gen_dir]),
            per_domain={
                "medical":  LocalRetriever(roots=[med_dir]),
                "security": LocalRetriever(roots=[sec_dir]),
            },
        )

        med_hits = routed.retrieve("warfarin dosing", domain="medical", k=3)
        sec_hits = routed.retrieve("warfarin dosing", domain="security", k=3)
        gen_hits = routed.retrieve("warfarin dosing", domain="general", k=3)

        assert med_hits and "med-doc" in med_hits[0].uri
        assert sec_hits and "sec-doc" in sec_hits[0].uri
        # "general" has no explicit override -> default corpus (gen_dir).
        assert gen_hits and "gen-doc" in gen_hits[0].uri

    def test_unknown_domain_falls_back_to_default(self, isolated, tmp_path):
        from axiom_research_retriever import (
            DomainRoutedRetriever, LocalRetriever,
        )
        default_dir = tmp_path / "default"
        med_dir = tmp_path / "medical"
        _seed_corpus(default_dir, "default-doc", "shared term")
        _seed_corpus(med_dir, "med-doc", "shared term")
        routed = DomainRoutedRetriever(
            default=LocalRetriever(roots=[default_dir]),
            per_domain={"medical": LocalRetriever(roots=[med_dir])},
        )
        # finance has no override; must go to default.
        hits = routed.retrieve("shared term", domain="finance", k=3)
        assert hits and "default-doc" in hits[0].uri

    def test_no_domain_uses_default(self, isolated, tmp_path):
        from axiom_research_retriever import (
            DomainRoutedRetriever, LocalRetriever,
        )
        default_dir = tmp_path / "default"
        med_dir = tmp_path / "medical"
        _seed_corpus(default_dir, "default-doc", "shared term")
        _seed_corpus(med_dir, "med-doc", "shared term")
        routed = DomainRoutedRetriever(
            default=LocalRetriever(roots=[default_dir]),
            per_domain={"medical": LocalRetriever(roots=[med_dir])},
        )
        hits = routed.retrieve("shared term", k=3)  # no domain kwarg
        assert hits and "default-doc" in hits[0].uri

    def test_case_insensitive_dispatch(self, isolated, tmp_path):
        from axiom_research_retriever import (
            DomainRoutedRetriever, LocalRetriever,
        )
        default_dir = tmp_path / "default"
        med_dir = tmp_path / "medical"
        _seed_corpus(default_dir, "default-doc", "term")
        _seed_corpus(med_dir, "med-doc", "term")
        routed = DomainRoutedRetriever(
            default=LocalRetriever(roots=[default_dir]),
            per_domain={"MEDICAL": LocalRetriever(roots=[med_dir])},
        )
        # Caller sends lowercase; per_domain key was uppercase.
        hits = routed.retrieve("term", domain="medical", k=3)
        assert hits and "med-doc" in hits[0].uri

    def test_stats_reports_per_domain(self, isolated, tmp_path):
        """/api/health calls retriever.stats() — operators need to see
        which corpus each domain points at."""
        from axiom_research_retriever import (
            DomainRoutedRetriever, LocalRetriever,
        )
        default_dir = tmp_path / "default"
        med_dir = tmp_path / "medical"
        _seed_corpus(default_dir, "default-doc", "a")
        _seed_corpus(med_dir, "med-doc", "a")
        routed = DomainRoutedRetriever(
            default=LocalRetriever(roots=[default_dir]),
            per_domain={"medical": LocalRetriever(roots=[med_dir])},
        )
        s = routed.stats()
        assert s["kind"] == "domain-routed"
        assert "default" in s
        assert "medical" in s["per_domain"]
        # The medical stub must point at med_dir, not default_dir.
        assert str(med_dir) in str(s["per_domain"]["medical"]["roots"])

    def test_requires_default(self, isolated):
        from axiom_research_retriever import DomainRoutedRetriever
        with pytest.raises(ValueError, match="default retriever"):
            DomainRoutedRetriever(default=None, per_domain={})  # type: ignore[arg-type]


# ─── default_retriever() env-driven discovery ───────────────────────

class TestDefaultRetrieverAutoWrap:
    def test_no_overrides_returns_plain_local_retriever(self, isolated, tmp_path):
        """Existing deployments without AXIOM_RETRIEVAL_DIR_<DOMAIN>
        env vars get a LocalRetriever, not the routed wrapper."""
        from axiom_research_retriever import (
            DomainRoutedRetriever, LocalRetriever, default_retriever,
        )
        r = default_retriever(repo_root=tmp_path)
        assert isinstance(r, LocalRetriever)
        assert not isinstance(r, DomainRoutedRetriever)

    def test_single_override_wraps_in_routed(self, isolated, tmp_path, monkeypatch):
        med_dir = tmp_path / "medical-corpus"
        _seed_corpus(med_dir, "med-doc", "diabetes management guideline")
        monkeypatch.setenv(
            "AXIOM_RETRIEVAL_DIR_MEDICAL", str(med_dir),
        )
        from axiom_research_retriever import (
            DomainRoutedRetriever, default_retriever,
        )
        r = default_retriever(repo_root=tmp_path)
        assert isinstance(r, DomainRoutedRetriever)
        hits = r.retrieve("diabetes management", domain="medical", k=3)
        assert hits and "med-doc" in hits[0].uri

    def test_comma_separated_paths_become_multi_root(
        self, isolated, tmp_path, monkeypatch,
    ):
        """Operators can set
            AXIOM_RETRIEVAL_DIR_MEDICAL=/data/pubmed,/data/guidelines
        to index multiple corpora into the same domain."""
        pubmed = tmp_path / "pubmed"
        guidelines = tmp_path / "guidelines"
        _seed_corpus(pubmed, "pubmed-doc", "diabetes evidence")
        _seed_corpus(guidelines, "guideline-doc", "diabetes management protocol")
        monkeypatch.setenv(
            "AXIOM_RETRIEVAL_DIR_MEDICAL",
            f"{pubmed},{guidelines}",
        )
        from axiom_research_retriever import default_retriever
        r = default_retriever(repo_root=tmp_path)
        hits = r.retrieve("diabetes", domain="medical", k=5)
        uris = " ".join(h.uri for h in hits)
        assert "pubmed-doc" in uris and "guideline-doc" in uris

    def test_missing_path_is_skipped_not_fatal(
        self, isolated, tmp_path, monkeypatch,
    ):
        """A typo'd path shouldn't crash the server on boot — log
        a warning and keep going. The misconfigured domain just falls
        back to the default."""
        monkeypatch.setenv(
            "AXIOM_RETRIEVAL_DIR_MEDICAL", "/nonexistent/path/typo",
        )
        from axiom_research_retriever import (
            DomainRoutedRetriever, default_retriever,
        )
        r = default_retriever(repo_root=tmp_path)
        # No real per-domain dir resolved => no wrapping happens.
        assert not isinstance(r, DomainRoutedRetriever)


# ─── LocalRetriever back-compat ─────────────────────────────────────

def test_plain_local_retriever_accepts_domain_kwarg(isolated, tmp_path):
    """The research server passes `domain=...` to retrieve()
    unconditionally. LocalRetriever must accept (and ignore) it so
    deployments without per-domain config don't crash."""
    from axiom_research_retriever import LocalRetriever
    _seed_corpus(tmp_path / "x", "x-doc", "the quick brown fox")
    r = LocalRetriever(roots=[tmp_path / "x"])
    hits = r.retrieve("quick brown", k=3, domain="medical")
    assert hits and "x-doc" in hits[0].uri
