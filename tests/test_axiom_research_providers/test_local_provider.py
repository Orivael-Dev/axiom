"""LocalCorpusProvider tests — delegates to LocalRetriever / DomainRouted."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_EXTERNAL_RETRIEVAL", "0")
    yield tmp_path


def _seed(tmp_path: Path) -> Path:
    (tmp_path / "doc.md").write_text(
        "# semaglutide adolescent\nGLP-1 agonist for adolescents.",
        encoding="utf-8",
    )
    return tmp_path


def test_local_provider_delegates(isolated):
    from axiom_research_retriever import LocalRetriever
    from axiom_research_providers.local import LocalCorpusProvider
    root = _seed(isolated)
    inner = LocalRetriever(roots=[root])
    p = LocalCorpusProvider(inner)
    hits = p.retrieve("semaglutide", k=3)
    assert hits, "should surface the seeded doc"
    assert hits[0].provider == "local"
    assert "semaglutide" in hits[0].title.lower() or \
           "semaglutide" in hits[0].snippet.lower()


def test_local_provider_passes_domain_to_routed(isolated):
    from axiom_research_retriever import (
        DomainRoutedRetriever, LocalRetriever,
    )
    from axiom_research_providers.local import LocalCorpusProvider

    med_dir = isolated / "med"
    gen_dir = isolated / "gen"
    med_dir.mkdir()
    gen_dir.mkdir()
    (med_dir / "med.md").write_text(
        "# warfarin\nAnticoagulation for atrial fibrillation.",
        encoding="utf-8",
    )
    (gen_dir / "gen.md").write_text(
        "# warfarin\nPop history of warfarin.",
        encoding="utf-8",
    )
    routed = DomainRoutedRetriever(
        default=LocalRetriever(roots=[gen_dir]),
        per_domain={"medical": LocalRetriever(roots=[med_dir])},
    )
    p = LocalCorpusProvider(routed)

    med_hits = p.retrieve("warfarin", k=3, domain="medical")
    assert med_hits and "atrial" in med_hits[0].snippet.lower()
    gen_hits = p.retrieve("warfarin", k=3, domain="general")
    assert gen_hits and "history" in gen_hits[0].snippet.lower()


def test_local_provider_stats_exposed(isolated):
    from axiom_research_retriever import LocalRetriever
    from axiom_research_providers.local import LocalCorpusProvider
    p = LocalCorpusProvider(LocalRetriever(roots=[_seed(isolated)]))
    s = p.stats()
    assert s["name"] == "local"
    assert "*" in s["domains"]
    assert "inner" in s
