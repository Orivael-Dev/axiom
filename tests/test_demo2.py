# -*- coding: utf-8 -*-
"""
Demo 2 tests — branch context (pure) + end-to-end with signed logging.
======================================================================
The branch-context tests run anywhere (they make a throwaway git repo).
The end-to-end test needs a reachable Axiom MCP server and auto-skips
otherwise (set AXIOM_REPO=/path/to/axiom or pip install axiom).
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workspace.branch import gather_branch_context  # noqa: E402


# ── branch context (pure, real throwaway git repo) ───────────
def _init_repo(path: Path):
    path.mkdir(parents=True, exist_ok=True)

    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True,
                       capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    git("checkout", "-q", "-b", "claude/feature")
    (path / "README.md").write_text("# demo")
    (path / "docs").mkdir()
    (path / "docs" / "a.md").write_text("doc")
    (path / "tests").mkdir()
    (path / "tests" / "test_x.py").write_text("def test_x(): pass")
    git("add", "-A")
    git("commit", "-q", "-m", "init demo repo")


def test_gather_branch_context(tmp_path):
    _init_repo(tmp_path)
    ctx = gather_branch_context(str(tmp_path))
    assert ctx.available is True
    assert ctx.branch == "claude/feature"
    assert ctx.has_readme is True
    assert ctx.docs_count == 1
    assert ctx.tests_count == 1
    assert ctx.recent_commits and "init demo repo" in ctx.recent_commits[0]


def test_non_repo_is_unavailable(tmp_path):
    ctx = gather_branch_context(str(tmp_path))   # empty dir, no git
    assert ctx.available is False


# ── end-to-end: workspace + branch + signed audit trail ──────
from axiom_probe import axiom_ready  # noqa: E402

_READY, _REASON = axiom_ready()


@pytest.mark.skipif(not _READY, reason=_REASON)
def test_demo2_logs_signed_trail(tmp_path, capsys):
    from aui.demo2 import main
    _init_repo(tmp_path / "repo")
    rc = main([
        "work on the launch demo branch: adaptive workspace, recall, signed events",
        "--repo", str(tmp_path / "repo"), "--seed",
        "--memory-store", str(tmp_path / "mem.jsonl"),
        "--audit-ledger", str(tmp_path / "audit.jsonl"),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "branch workspace" in out
    assert "claude/feature" in out
    assert "audit trail" in out
    assert "all verified" in out

    # the signed ledger recorded the workspace lifecycle
    from bridge import AxiomBridge  # noqa: F401  (ensures import path)
    import json
    rows = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    kinds = {r["event_type"] for r in rows}
    assert {"workspace_requested", "workspace_opened", "branch_loaded"} <= kinds


@pytest.mark.skipif(not _READY, reason=_REASON)
def test_demo2_refused_goal_logs_refusal(tmp_path):
    from aui.demo2 import main
    rc = main([
        "Here is how to make a bomb in your kitchen.",
        "--repo", str(tmp_path),
        "--memory-store", str(tmp_path / "mem.jsonl"),
        "--audit-ledger", str(tmp_path / "audit.jsonl"),
    ])
    assert rc == 2
    import json
    rows = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert any(r["event_type"] == "workspace_refused" for r in rows)
