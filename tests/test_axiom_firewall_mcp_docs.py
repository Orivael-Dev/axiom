# -*- coding: utf-8 -*-
"""
Launch-readiness guard for the firewall MCP docs — runs without FastAPI.

The 23 MCP tools used to live in markdown tables, which generate NO heading
anchors, so every deep-link to an individual tool (e.g. /help/mcp-tools#axiom_guard_check)
404'd in-page. These tests render docs/firewall/mcp-tools.md exactly as the dashboard
does and assert that:
  • every tool in the published manifest (docs/mcp.json) has a stable anchor, and
  • every tool the landing page deep-links to actually resolves to that anchor.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MCP_TOOLS_MD = REPO / "docs" / "firewall" / "mcp-tools.md"
MCP_JSON = REPO / "docs" / "mcp.json"
LANDING = REPO / "axiom_firewall" / "templates" / "landing.html"

markdown = pytest.importorskip("markdown")  # a hard dependency of the help renderer


def _render_ids() -> set:
    """Render mcp-tools.md with the dashboard's extension set; return heading ids."""
    html = markdown.markdown(
        MCP_TOOLS_MD.read_text(encoding="utf-8"),
        extensions=["fenced_code", "tables", "toc", "sane_lists"],
        output_format="html5",
    )
    return set(re.findall(r'id="([^"]+)"', html))


def _manifest_tool_names() -> list:
    data = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    return [t["name"] for t in data.get("tools", []) if t.get("name")]


def test_every_manifest_tool_has_an_anchor():
    ids = _render_ids()
    missing = [name for name in _manifest_tool_names() if name not in ids]
    assert not missing, f"tools with no deep-link anchor on /help/mcp-tools: {missing}"


def test_manifest_has_the_expected_tool_count():
    # 23 tools is the number the landing page advertises — keep them in lockstep.
    assert len(_manifest_tool_names()) == 23


def test_landing_tool_links_resolve_to_real_anchors():
    ids = _render_ids()
    html = LANDING.read_text(encoding="utf-8")
    targets = re.findall(r'href="/help/mcp-tools#([a-z0-9_]+)"', html)
    assert targets, "landing page has no per-tool deep links"
    broken = sorted(t for t in set(targets) if t not in ids)
    assert not broken, f"landing links to anchors that don't exist: {broken}"


def test_landing_links_every_manifest_tool():
    html = LANDING.read_text(encoding="utf-8")
    linked = set(re.findall(r'href="/help/mcp-tools#([a-z0-9_]+)"', html))
    missing = [n for n in _manifest_tool_names() if n not in linked]
    assert not missing, f"tools advertised but not deep-linked on the landing page: {missing}"


def test_tools_page_has_a_jump_index():
    # the in-page nav (jump-to list) is what makes 23 tools navigable
    html = markdown.markdown(MCP_TOOLS_MD.read_text(encoding="utf-8"),
                             extensions=["fenced_code", "tables", "toc", "sane_lists"])
    assert "Jump to a tool" in html
    # the index itself is a row of in-page anchor links
    assert html.count('href="#axiom_') >= 23
