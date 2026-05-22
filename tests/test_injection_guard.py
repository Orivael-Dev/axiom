"""Tests for OutputInjectionGuard — narrowed cmd_backtick + output_format param.

Covers two changes:
  A. cmd_backtick now requires a known shell command inside the backticks
     (so legitimate markdown-inline code like `os.path.join()` passes).
  B. check(output_format="code") skips CMD_INJECTION + TEMPLATE_INJ
     categories so code-generation flows don't trip the guard. XSS,
     SSRF, PATH_TRAVERSAL, NOSQL_INJ remain enforced.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    # Sandbox the review queue so tests don't write to the real one.
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_signing", "axiom_constitutional",
        )):
            sys.modules.pop(mod, None)
    yield


# ── A. cmd_backtick narrowing ──────────────────────────────────────────────


def test_cmd_backtick_blocks_shell_command(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    # Real injection — `id` is a shell command.
    r = g.check("Value is `id`")
    assert r["blocked"] is True
    assert r["pattern_name"] == "cmd_backtick"


def test_cmd_backtick_blocks_whoami(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("User: `whoami` reports the current account")
    assert r["blocked"] is True
    assert r["pattern_name"] == "cmd_backtick"


def test_cmd_backtick_blocks_cat_etc_passwd(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    # Should hit cmd_backtick OR path_etc_passwd — either is a real block.
    r = g.check("Run `cat /etc/passwd` to dump users")
    assert r["blocked"] is True
    assert r["category"] in ("CMD_INJECTION", "PATH_TRAVERSAL")


def test_cmd_backtick_allows_python_function_call(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    # False-positive case before the fix — Python function name in backticks.
    r = g.check("Use `os.path.join()` to build paths portably.")
    assert r["blocked"] is False, f"unexpectedly blocked: {r}"


def test_cmd_backtick_allows_variable_name(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("Assign the result to `user_input` and validate it.")
    assert r["blocked"] is False


def test_cmd_backtick_allows_import_statement(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("Add `import json` at the top of the module.")
    assert r["blocked"] is False


# ── B. output_format="code" relaxes CMD + TEMPLATE categories ─────────────


def test_format_code_skips_cmd_backtick(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    # Even with a real shell command in backticks, format="code" lets it
    # pass — the caller has explicitly opted out of CMD_INJECTION checks
    # because they know the response is rendered code, not executed shell.
    r = g.check("Run `id` to check the current user.", output_format="code")
    assert r["blocked"] is False


def test_format_code_skips_jinja_template(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("Render with {{ user.name }} placeholder.",
                output_format="code")
    assert r["blocked"] is False


def test_format_code_still_blocks_xss(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check('Inject: <script>alert(1)</script>', output_format="code")
    assert r["blocked"] is True
    assert r["category"] == "XSS"


def test_format_code_still_blocks_ssrf(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("curl http://169.254.169.254/latest/meta-data/",
                output_format="code")
    assert r["blocked"] is True
    assert r["category"] == "SSRF"


def test_format_code_still_blocks_path_traversal(isolated):
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("Read /etc/passwd for the user list.", output_format="code")
    assert r["blocked"] is True
    assert r["category"] == "PATH_TRAVERSAL"


def test_format_default_still_blocks_jinja(isolated):
    """Without output_format='code', Jinja patterns still hit."""
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    g = OutputInjectionGuard()
    r = g.check("Render with {{ user.name }} placeholder.")
    assert r["blocked"] is True
    assert r["category"] == "TEMPLATE_INJ"


def test_module_level_check_accepts_output_format(isolated):
    from axiom_constitutional.guards import axiom_injection_guard as ig
    r = ig.check("Run `id` to check.", output_format="code")
    assert r["blocked"] is False
