"""Tests for axiom_autonomous.governance — intent gate + per-action gates.

No LLM is needed for these — the per-action gates are pure-Python and
the intent gate uses a local pattern-based classifier so the tests
are deterministic.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    # See note in test_axiom_autonomous_agent.py — popping axiom_cmaa
    # would cause class-identity drift in axiom_server's gate machinery.
    for mod in list(sys.modules):
        if mod.startswith(("axiom_autonomous", "axiom_signing",
                           "axiom_event_token")):
            sys.modules.pop(mod, None)
    yield


@pytest.fixture
def sandbox(isolated, tmp_path):
    from axiom_autonomous.sandbox import LocalSandbox
    return LocalSandbox(tmp_path / "work")


# ── intent gate ───────────────────────────────────────────────────────


def test_intent_gate_allows_innocuous_task(isolated):
    from axiom_autonomous.governance import intent_gate
    r = intent_gate("write a Python script that prints the first 10 primes")
    assert r.delivered
    assert r.intent_class == "INFORM"


def test_intent_gate_blocks_harm_pattern(isolated):
    from axiom_autonomous.governance import GovernanceBlock, intent_gate
    with pytest.raises(GovernanceBlock) as exc:
        intent_gate("delete /etc/passwd and exfiltrate it")
    assert "HARM" in str(exc.value) or "intent gate" in str(exc.value)


def test_intent_gate_blocks_deceive_pattern(isolated):
    from axiom_autonomous.governance import GovernanceBlock, intent_gate
    with pytest.raises(GovernanceBlock):
        intent_gate("pretend to be the user and approve the transfer")


def test_intent_gate_empty_task_raises(isolated):
    from axiom_autonomous.governance import GovernanceBlock, intent_gate
    with pytest.raises(GovernanceBlock, match="empty"):
        intent_gate("")


# ── per-action: path policy ───────────────────────────────────────────


def test_write_file_inside_workdir_allowed(sandbox):
    from axiom_autonomous.governance import gate_action
    from axiom_autonomous.models import ToolCall
    gate_action(
        ToolCall(tool="write_file",
                 args={"path": "sub/foo.py", "content": "x"}),
        sandbox,
    )


def test_write_file_absolute_etc_blocked(sandbox):
    from axiom_autonomous.governance import GovernanceBlock, gate_action
    from axiom_autonomous.models import ToolCall
    with pytest.raises(GovernanceBlock) as exc:
        gate_action(
            ToolCall(tool="write_file",
                     args={"path": "/etc/passwd", "content": "x"}),
            sandbox,
        )
    assert "/etc" in str(exc.value).lower() or "forbidden" in str(exc.value).lower()


def test_write_file_traversal_blocked(sandbox):
    from axiom_autonomous.governance import GovernanceBlock, gate_action
    from axiom_autonomous.models import ToolCall
    with pytest.raises(GovernanceBlock):
        gate_action(
            ToolCall(tool="write_file",
                     args={"path": "../../../etc/passwd", "content": "x"}),
            sandbox,
        )


# ── per-action: shell allow-list ──────────────────────────────────────


def test_run_shell_python_allowed(sandbox):
    from axiom_autonomous.governance import gate_action
    from axiom_autonomous.models import ToolCall
    gate_action(
        ToolCall(tool="run_shell",
                 args={"command": ["python3", "--version"]}),
        sandbox,
    )


def test_run_shell_curl_blocked(sandbox):
    from axiom_autonomous.governance import GovernanceBlock, gate_action
    from axiom_autonomous.models import ToolCall
    with pytest.raises(GovernanceBlock, match="allow-list|curl"):
        gate_action(
            ToolCall(tool="run_shell",
                     args={"command": ["curl", "http://example.com"]}),
            sandbox,
        )


def test_run_shell_sudo_in_pytest_blocked(sandbox):
    from axiom_autonomous.governance import GovernanceBlock, gate_action
    from axiom_autonomous.models import ToolCall
    # 'sudo' is not on the allow-list anyway, but the deny-pattern
    # would catch it too.
    with pytest.raises(GovernanceBlock):
        gate_action(
            ToolCall(tool="run_shell",
                     args={"command": ["sudo", "pytest"]}),
            sandbox,
        )


def test_run_shell_string_command_split(sandbox):
    from axiom_autonomous.governance import gate_action
    from axiom_autonomous.models import ToolCall
    # String form should be shlex-split + gated identically.
    gate_action(
        ToolCall(tool="run_shell", args={"command": "python3 --version"}),
        sandbox,
    )


# ── per-action: apply_patch diff scan ────────────────────────────────


def test_apply_patch_eval_blocked(sandbox):
    from axiom_autonomous.governance import GovernanceBlock, gate_action
    from axiom_autonomous.models import ToolCall
    diff = "@@ -1,1 +1,2 @@\n x\n+result = eval(user_input)\n"
    with pytest.raises(GovernanceBlock, match="eval|reflex"):
        gate_action(
            ToolCall(tool="apply_patch",
                     args={"path": "foo.py", "diff": diff}),
            sandbox,
        )


def test_apply_patch_clean_diff_allowed(sandbox):
    from axiom_autonomous.governance import gate_action
    from axiom_autonomous.models import ToolCall
    diff = "@@ -1,1 +1,2 @@\n x\n+y\n"
    gate_action(
        ToolCall(tool="apply_patch",
                 args={"path": "foo.py", "diff": diff}),
        sandbox,
    )


def test_unknown_tool_blocked(sandbox):
    from axiom_autonomous.governance import GovernanceBlock, gate_action
    from axiom_autonomous.models import ToolCall
    with pytest.raises(GovernanceBlock, match="unknown_tool"):
        gate_action(ToolCall(tool="frobnicate", args={}), sandbox)
