"""Tests for the /help route — renders docs/research_engine.md."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
httpx   = pytest.importorskip("httpx")
from fastapi.testclient import TestClient   # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_research_server", "axiom_exoskeleton",
        )):
            sys.modules.pop(mod, None)
    yield


def _client():
    from axiom_research_server import app
    return TestClient(app)


def test_help_route_returns_html_when_md_exists(isolated):
    """The Markdown source ships with the repo, so the route should
    always render real HTML (not the missing-file 500)."""
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"].lower()
    # Title shows up in both the styled-via-markdown path and the
    # plain-fallback path.
    assert "Re:Search Engine" in r.text


def test_help_route_renders_h1_from_markdown(isolated):
    """If the `markdown` library is installed, the first `# heading`
    in the source should turn into an <h1> tag — easy way to confirm
    the conversion ran end-to-end."""
    pytest.importorskip("markdown")
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200
    # The TOC extension adds `id="..."` attrs, so match the
    # opening tag prefix + the heading text rather than the bare
    # `<h1>...</h1>` form.
    assert "<h1" in r.text and "Re:Search Engine — instructions</h1>" in r.text


def test_help_route_includes_backbar_link(isolated):
    """The page should always carry a 'back to console' link so
    visitors don't dead-end."""
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200
    assert 'href="/"' in r.text


def test_help_route_500_when_md_missing(isolated, monkeypatch, tmp_path):
    """Move the Markdown file out of the way — route should 500
    with the expected file path in the detail, not silently fall
    through to mock content."""
    from axiom_research_server import HELP_MD_PATH
    moved = tmp_path / "research_engine.md.bak"
    HELP_MD_PATH.rename(moved)
    try:
        client = _client()
        r = client.get("/help")
        assert r.status_code == 500
        assert "research_engine.md" in r.text
    finally:
        moved.rename(HELP_MD_PATH)


def test_help_route_mentions_byo_llm_section(isolated):
    """Quality gate: the doc has a 'Bring your own LLM' section —
    if it disappears, this test catches it before the page goes live."""
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200
    assert "Bring your own LLM" in r.text
    assert "OpenAI-compatible" in r.text
