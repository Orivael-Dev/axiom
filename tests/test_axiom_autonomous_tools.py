"""Tests for axiom_autonomous.tools — registry + fs + shell tools.

Uses LocalSandbox throughout. No docker, no LLM.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("axiom_autonomous", "axiom_signing",
                           "axiom_event_token", "axiom_exoskeleton")):
            sys.modules.pop(mod, None)
    yield


@pytest.fixture
def sandbox(isolated, tmp_path):
    from axiom_autonomous.sandbox import LocalSandbox
    return LocalSandbox(tmp_path / "work")


def test_registry_default_contains_v1_tools(isolated):
    from axiom_autonomous.tools import default_registry
    reg = default_registry()
    names = reg.names()
    for required in ("write_file", "read_file", "list_dir",
                     "apply_patch", "run_shell", "run_tests", "finish"):
        assert required in names, f"missing tool: {required}"


def test_registry_schema_includes_risk_label(isolated):
    from axiom_autonomous.tools import default_registry
    schema = default_registry().schema()
    assert "risk=high" in schema   # run_shell is high-risk
    assert "risk=low" in schema    # read_file / list_dir / finish


def test_write_file_then_read_file_roundtrip(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    reg = default_registry()
    call_w = ToolCall(tool="write_file",
                      args={"path": "primes.py", "content": "print(2)\n"})
    obs_w = reg.dispatch(call_w, sandbox)
    assert obs_w.ok
    assert "wrote" in obs_w.output

    call_r = ToolCall(tool="read_file", args={"path": "primes.py"})
    obs_r = reg.dispatch(call_r, sandbox)
    assert obs_r.ok
    assert obs_r.output == "print(2)\n"


def test_read_missing_file_returns_error(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    obs = default_registry().dispatch(
        ToolCall(tool="read_file", args={"path": "absent.py"}), sandbox,
    )
    assert not obs.ok
    assert "not found" in obs.error


def test_list_dir_returns_sorted_entries(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    reg = default_registry()
    for name in ("c.py", "a.py", "b.py"):
        reg.dispatch(
            ToolCall(tool="write_file", args={"path": name, "content": ""}),
            sandbox,
        )
    obs = reg.dispatch(
        ToolCall(tool="list_dir", args={"path": "."}), sandbox,
    )
    assert obs.ok
    assert obs.structured["entries"] == ["a.py", "b.py", "c.py"]


def test_run_shell_python_version(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    obs = default_registry().dispatch(
        ToolCall(tool="run_shell", args={"command": ["python3", "--version"]}),
        sandbox,
    )
    assert obs.ok, f"python3 --version failed: {obs.error}"
    assert "Python" in obs.output


def test_run_tests_parses_pytest_counts(sandbox):
    """Real pytest run on a tiny generated test file.

    Skips when pytest isn't importable from the runtime that the
    sandbox spawns (rare). The point is to prove the parser pulls
    the right counts out of real pytest output.
    """
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    reg = default_registry()
    reg.dispatch(
        ToolCall(tool="write_file",
                 args={"path": "test_smoke.py",
                       "content":
                           "def test_one(): assert 1 == 1\n"
                           "def test_two(): assert 2 == 2\n"}),
        sandbox,
    )
    obs = reg.dispatch(
        ToolCall(tool="run_tests", args={"args": ["test_smoke.py"]}),
        sandbox,
    )
    if "No module named pytest" in (obs.error + obs.output):
        pytest.skip("pytest not importable in sandbox subprocess")
    assert obs.structured.get("passed") == 2, (
        f"expected 2 passed, got {obs.structured}; output:\n{obs.output}"
    )
    assert obs.structured.get("failed") == 0


def test_apply_patch_modifies_file(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    reg = default_registry()
    reg.dispatch(
        ToolCall(tool="write_file",
                 args={"path": "foo.txt", "content": "alpha\nbeta\n"}),
        sandbox,
    )
    diff = "@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n"
    obs = reg.dispatch(
        ToolCall(tool="apply_patch", args={"path": "foo.txt", "diff": diff}),
        sandbox,
    )
    assert obs.ok, f"apply_patch failed: {obs.error}"
    new_text = sandbox.read_file("foo.txt")
    assert "gamma" in new_text and "beta" not in new_text


def test_finish_marks_ok_with_reason(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import default_registry
    obs = default_registry().dispatch(
        ToolCall(tool="finish", args={"reason": "all done"}), sandbox,
    )
    assert obs.ok
    assert obs.structured["finish"] is True
    assert "all done" in obs.output


def test_unknown_tool_raises(sandbox):
    from axiom_autonomous.models import ToolCall
    from axiom_autonomous.tools import ToolNotFoundError, default_registry
    with pytest.raises(ToolNotFoundError):
        default_registry().dispatch(
            ToolCall(tool="frobnicate", args={}), sandbox,
        )
