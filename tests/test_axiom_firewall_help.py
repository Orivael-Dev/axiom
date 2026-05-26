"""Tests for the firewall /help routes — render docs/firewall/*.md."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
httpx   = pytest.importorskip("httpx")
from fastapi.testclient import TestClient   # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_FIREWALL_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("AXIOM_FIREWALL_SESSION_SECRET", "test-session-secret")
    # Point packs at an empty tmp dir so /help/packs/<name> tests below
    # can drop in a signed test pack without colliding with the real
    # packs/ tree (those are signed under the prod master key).
    monkeypatch.setenv("AXIOM_FIREWALL_PACKS_DIR", str(tmp_path / "packs"))
    for mod in (
        "axiom_firewall.db", "axiom_firewall.auth",
        "axiom_firewall.dashboard",
        "axiom_signing", "axiom_intent_classifier",
    ):
        if mod in sys.modules:
            del sys.modules[mod]
    yield tmp_path


def _write_signed_pack(packs_root: Path, name: str = "test-pack") -> dict:
    """Sign and persist a minimal pack manifest in packs_root/<name>/pack.json.

    Returns the on-disk dict so tests can assert against its fields.
    Uses sign_first_party so _load_pack's verify check succeeds under
    the test AXIOM_MASTER_KEY."""
    from axiom_firewall.skill_pack import sign_first_party
    manifest = {
        "format_version": "1.0",
        "name":           name,
        "title":          "Test Pack",
        "description":    "A pack used to exercise the per-pack help route.",
        "version":        "0.1.0",
        "author":         "Orivael Dev",
        "license":        "MIT",
        "homepage":       "https://docs.orivael.dev/firewall/packs/test-pack",
        "tags":           ["test", "compliance"],
        "tested_against": ["axiom-firewall>=0.1.0"],
        "policy": {
            "version": 1,
            "additional_block_patterns": [
                {"class": "HARM",    "regex": "block this"},
                {"class": "DECEIVE", "regex": "or this"},
            ],
            "disabled_default_classes": ["CLARIFY"],
            "allow_only_classes": None,
        },
    }
    manifest["signature"] = sign_first_party(manifest)
    pack_dir = packs_root / "packs" / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _client():
    from axiom_firewall.dashboard import app
    return TestClient(app)


def test_help_index_returns_html(isolated):
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"].lower()
    # Landing page lands on the index (or quickstart as fallback).
    assert "<h1" in r.text


def test_help_index_has_nav_to_other_docs(isolated):
    """Every customer-facing .md under docs/firewall/ should appear in
    the per-doc nav row so beta testers can jump between Quickstart,
    Skill Packs, etc. without leaving the dashboard."""
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200
    # The most load-bearing docs for a beta tester:
    for expected_slug in ("quickstart", "skill-packs", "api-reference"):
        assert f'href="/help/{expected_slug}"' in r.text, \
            f"missing nav link for {expected_slug}"


def test_help_quickstart_renders(isolated):
    client = _client()
    r = client.get("/help/quickstart")
    assert r.status_code == 200
    # The actual quickstart content from docs/firewall/quickstart.md
    assert "Quickstart" in r.text
    assert "axfw_" in r.text   # the api-key prefix is mentioned


@pytest.mark.parametrize("slug", ["launch", "billing", "operations-runbook"])
def test_help_internal_docs_are_404(isolated, slug):
    """Operator-only docs (launch playbook, billing internals,
    operations runbook) must not be reachable via /help/<slug>. The
    files live under docs/firewall/internal/, the Dockerfile excludes
    that subdirectory from the image, and dashboard.py keeps an
    explicit denylist as a third line of defense."""
    client = _client()
    r = client.get(f"/help/{slug}")
    assert r.status_code == 404, \
        f"/help/{slug} returned {r.status_code} — internal doc must be hidden"


def test_help_index_rewrites_relative_md_links(isolated):
    """Markdown source uses `[Quickstart](quickstart.md)`. Without
    rewriting, that renders as `<a href="quickstart.md">`, which from
    /help (no trailing slash) resolves to /quickstart.md — a 404. The
    renderer must rewrite to an absolute /help/<slug> URL."""
    client = _client()
    r = client.get("/help")
    assert r.status_code == 200
    # The rewritten form is what we expect on every cross-doc link.
    assert 'href="/help/quickstart"' in r.text
    assert 'href="/help/custom-policies"' in r.text
    # The raw .md href must not survive into the rendered HTML.
    assert 'href="quickstart.md"' not in r.text
    assert 'href="custom-policies.md"' not in r.text


def test_help_renderer_neutralises_internal_md_links(isolated):
    """A markdown link to an internal-only doc (billing.md, launch.md,
    operations-runbook.md) must not render as a clickable /help/<slug>
    URL — that would 404 thanks to the denylist. Rewrite to `#` so the
    anchor text is preserved without leaking a broken link."""
    # Import after the isolated fixture has reset the module — the
    # render function reads the denylist as a module-level constant.
    from axiom_firewall.dashboard import _help_render_markdown
    rendered = _help_render_markdown(
        "See [Billing](billing.md) and [Launch](launch.md) for details."
    )
    assert 'href="/help/billing"' not in rendered
    assert 'href="/help/launch"' not in rendered
    assert 'href="billing.md"' not in rendered
    # Anchor text survives, URL is neutralised.
    assert "Billing" in rendered
    assert 'href="#"' in rendered


def test_help_renderer_preserves_external_links(isolated):
    """Absolute URLs, mailto:, fragments, and root-relative paths must
    pass through unchanged — only `<slug>.md` cross-doc links rewrite."""
    from axiom_firewall.dashboard import _help_render_markdown
    rendered = _help_render_markdown(
        "[Trust](https://orivael.dev/trust) "
        "[Sales](mailto:sales@orivael.dev) "
        "[Dash](/dashboard) "
        "[Anchor](#section)"
    )
    assert 'href="https://orivael.dev/trust"' in rendered
    assert 'href="mailto:sales@orivael.dev"' in rendered
    assert 'href="/dashboard"' in rendered
    assert 'href="#section"' in rendered


def test_help_index_does_not_link_internal_docs(isolated):
    """The nav row built from docs/firewall/*.md should not surface
    the internal docs even if a future change moves them back to the
    top level (the denylist still hides them, but a leaked nav link
    is itself a confused-deputy signal worth catching in tests)."""
    client = _client()
    r = client.get("/help")
    for forbidden in ("launch", "billing", "operations-runbook"):
        assert f'href="/help/{forbidden}"' not in r.text, \
            f"internal doc {forbidden!r} surfaced in /help nav"


def test_help_unknown_slug_returns_404(isolated):
    client = _client()
    r = client.get("/help/not-a-real-doc")
    assert r.status_code == 404
    assert "no doc named" in r.text


def test_help_blocks_path_traversal(isolated):
    """Slug must be alphanumeric + hyphens/underscores — anything
    else is refused with 400 (defense against `../`-style requests)."""
    client = _client()
    r = client.get("/help/..%2Fsomething")
    # FastAPI will URL-decode the path; our regex check refuses dots.
    assert r.status_code in (400, 404)


def test_help_topbar_links_back_to_dashboard(isolated):
    client = _client()
    r = client.get("/help/quickstart")
    assert r.status_code == 200
    assert 'href="/dashboard"' in r.text
    assert "← Dashboard" in r.text


def test_dashboard_nav_links_to_help(isolated):
    """The base.html nav should expose the new /help link in both
    the signed-in and signed-out branches."""
    client = _client()
    r = client.get("/")
    assert r.status_code == 200
    # Signed-out nav: Help link visible
    assert 'href="/help"' in r.text


# ─── per-pack help page (/help/packs/<name>) ───────────────────────────

def test_help_pack_page_renders_manifest(isolated):
    """`/help/packs/<name>` replaces the broken `homepage` URLs every
    signed pack carries. It must load the manifest, verify the
    signature, and render the marketing-shaped fields plus a policy
    summary driven by the manifest contents."""
    manifest = _write_signed_pack(isolated, name="test-pack")
    client = _client()
    r = client.get("/help/packs/test-pack")
    assert r.status_code == 200, r.text
    # Marketing-shaped fields:
    assert manifest["title"] in r.text
    assert manifest["description"] in r.text
    assert manifest["version"] in r.text
    assert manifest["author"] in r.text
    assert manifest["license"] in r.text
    # Tags rendered as chips:
    for tag in manifest["tags"]:
        assert f">{tag}<" in r.text
    # Policy summary covers the manifest's actual policy shape:
    assert "2 block pattern" in r.text or "2 HARM" in r.text or "Adds" in r.text
    assert "CLARIFY" in r.text   # disabled_default_classes
    # Backlinks present:
    assert 'href="/dashboard/packs"' in r.text
    assert 'href="/help/skill-packs"' in r.text


def test_help_pack_page_404_for_unknown(isolated):
    """Unknown packs return 404 — no half-rendered shell."""
    client = _client()
    r = client.get("/help/packs/never-published-pack-xyz")
    assert r.status_code == 404


def test_help_pack_page_404_for_unsigned(isolated):
    """A pack.json that lacks a valid first-party signature must NOT
    render — the same defense the dashboard install flow uses."""
    pack_dir = isolated / "packs" / "tampered"
    pack_dir.mkdir(parents=True)
    bad = {
        "format_version": "1.0", "name": "tampered", "title": "X",
        "description": "x", "version": "0.0.0", "author": "x",
        "license": "MIT", "tags": [], "tested_against": [],
        "policy": {"version": 1},
        "signature": "0" * 64,   # syntactically valid hex, semantically wrong
    }
    (pack_dir / "pack.json").write_text(json.dumps(bad), encoding="utf-8")
    client = _client()
    r = client.get("/help/packs/tampered")
    assert r.status_code == 404


def test_help_pack_page_blocks_traversal(isolated):
    """A pack name containing `/` or `..` must be refused — defense
    against attempting to read arbitrary files via the manifest path."""
    client = _client()
    # FastAPI may decode %2F early; either way the response must not
    # be 200 and must not surface arbitrary file contents.
    for evil in ("..%2Fetc", "..%2F..%2Fetc%2Fpasswd"):
        r = client.get(f"/help/packs/{evil}")
        assert r.status_code in (404, 400)


def test_packs_template_links_to_help_pack_page(isolated):
    """The Skill Packs dashboard card's `Docs →` must point at
    `/help/packs/<name>` (the on-host route) rather than the broken
    `homepage` URL stamped into the signed manifest. Tested by
    rendering the template directly so we don't need a logged-in
    session."""
    from fastapi.templating import Jinja2Templates
    tpl_dir = Path(__file__).resolve().parents[1] / "axiom_firewall" / "templates"
    templates = Jinja2Templates(directory=str(tpl_dir))
    # Minimal fake pack record matching the dict shape the real handler
    # passes — just needs .name + a couple of display fields.
    class _Fake:
        name = "test-pack"
        version = "0.1.0"
        title = "Test Pack"
        description = "x"
        author = "Orivael Dev"
        license = "MIT"
        tags: list[str] = []
        homepage = "https://docs.orivael.dev/firewall/packs/test-pack"
    fake_request = type("R", (), {"session": {}, "scope": {"type": "http"}})()
    body = templates.get_template("packs.html").render(
        request=fake_request, packs=[_Fake()], installed=None,
        registry_url=None, brand_domain="orivael.dev", docs_host="docs.orivael.dev",
        dashboard_host="firewall.orivael.dev", tier_limits={}, tier_prices={},
        free_tier=False, beta_feedback_url=None, tenant=None,
    )
    assert 'href="/help/packs/test-pack"' in body
    # The broken homepage URL must not appear in the rendered card.
    assert "docs.orivael.dev/firewall/packs/test-pack" not in body
