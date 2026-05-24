"""Regression: axiom_constitutional.client must load + run validate_output
without the `openai` package installed.

The `axiom-mcp` pipx environment used by Claude Desktop / Cursor / Continue
won't necessarily have `openai`. The MCP tool `axiom_guard_check` ends
up calling `validate_output`, which is a pure-regex/heuristic guard — no
LLM call required. Yet a top-level `from openai import OpenAI` in client.py
once made the whole module unimportable in those envs, surfacing as a
JSON-RPC error: `{"code":-32000,"message":"No module named 'openai'"}`.

These tests pin the lazy-import contract so the bug can't return.
"""
from __future__ import annotations

import sys

import pytest


class _ImportBlocker:
    """Meta-path finder that raises ImportError for any `openai` import.

    Simulates a Python environment in which the `openai` package isn't
    installed — i.e. the `axiom-mcp` pipx env when the user hasn't run
    `pipx inject axiom-constitutional openai`."""

    def find_spec(self, name, path=None, target=None):
        if name == "openai" or name.startswith("openai."):
            raise ImportError(f"openai blocked by test ({name})")
        return None


@pytest.fixture
def no_openai(monkeypatch):
    """Make `import openai` fail and force a fresh load of the client
    module so the top-level imports re-execute under the block."""
    # Drop any cached modules that might have already pulled in openai.
    for mod in list(sys.modules):
        if (mod.startswith("openai") or
                mod.startswith("axiom_constitutional.client") or
                mod == "axiom_constitutional"):
            del sys.modules[mod]
    blocker = _ImportBlocker()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        # Re-drop so subsequent tests get a clean re-import.
        for mod in list(sys.modules):
            if mod.startswith("axiom_constitutional.client"):
                del sys.modules[mod]


def test_client_module_imports_without_openai(no_openai, monkeypatch):
    """The MCP failure mode was `from openai import OpenAI` at the top
    of client.py. With the lazy-import fix, importing the module — and
    pulling validate_output out of it — must succeed."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "0" * 64)
    from axiom_constitutional.client import validate_output  # noqa: F401
    # If we got here, the module loaded with no `openai` available.


def test_validate_output_runs_without_openai(no_openai, monkeypatch):
    """`axiom_guard_check` MCP tool calls validate_output on the user's
    prompt. That path is pure regex — must work with no LLM client."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "0" * 64)
    from axiom_constitutional.client import validate_output
    clean, ok = validate_output("hello world", "is this safe?")
    assert isinstance(clean, str)
    assert isinstance(ok, bool)


def test_mcp_guard_check_handler_runs_without_openai(no_openai, monkeypatch):
    """End-to-end: the actual MCP `axiom_guard_check` handler — the
    code path triggered by Claude Desktop calling
    `tools/call axiom_guard_check` — must return a verdict dict, not
    crash with `No module named 'openai'`."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "0" * 64)
    # Clear cached MCP server so it re-imports under the blocker.
    for mod in list(sys.modules):
        if mod.startswith("axiom_mcp_server") or mod.startswith("axiom_constitutional"):
            del sys.modules[mod]
    from axiom_mcp_server import _HANDLERS
    result = _HANDLERS["axiom_guard_check"]({"input": "is this prompt safe?"})
    assert isinstance(result, dict)
    # Result must carry a verdict (PASSED / BLOCKED / etc.) — exact value
    # depends on classifier, but the field must exist.
    assert "verdict" in result, f"missing verdict in {result!r}"


def test_build_client_raises_helpful_error_without_openai(no_openai, monkeypatch):
    """If a user actually invokes an LLM-calling path (NOT validate_output)
    in an env without openai, they should get a clear, actionable error
    that names the `pipx inject` fix — not a bare ModuleNotFoundError."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "0" * 64)
    monkeypatch.setenv("AXIOM_API_KEY", "test-key")
    from axiom_constitutional.client import _build_client
    with pytest.raises(ImportError) as excinfo:
        _build_client()
    msg = str(excinfo.value)
    assert "openai" in msg.lower()
    assert "pipx inject" in msg or "pip install openai" in msg
