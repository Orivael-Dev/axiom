# -*- coding: utf-8 -*-
"""
Panel-provider tests — fill WorkspacePlan panels from real data (pure).
=======================================================================
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aui.panels import fill_plan, _authorized_agents  # noqa: E402
from aui.plan import Panel, WorkspacePlan  # noqa: E402


def _repo(tmp: Path):
    tmp.mkdir(parents=True, exist_ok=True)

    def git(*a):
        subprocess.run(["git", "-C", str(tmp), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    git("checkout", "-q", "-b", "claude/feature")
    (tmp / "README.md").write_text("# demo")
    (tmp / "NOTES.md").write_text("- ship the demo\n- wire the panels\n")
    (tmp / "docs").mkdir()
    (tmp / "docs" / "vision.md").write_text("vision")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_a.py").write_text("def test_a(): pass")
    git("add", "-A")
    git("commit", "-q", "-m", "seed demo repo")


class FakeBridge:
    def list_tools(self):
        return ["axiom_workspace", "axiom_memory", "axiom_ledger", "axiom_marketplace"]

    def audit_list(self, limit=None):
        return {"events": [
            {"event_type": "agent_approved", "subject": "tone-beatz-agent"},
            {"event_type": "agent_approved", "subject": "tax-helper"},
            {"event_type": "agent_revoked", "subject": "tax-helper"},  # net: not authorized
        ]}


def _plan(*kinds):
    return WorkspacePlan(goal="g", allowed=True, scene="dev",
                         panels=[Panel(kind=k, title=k.title(), status="pending")
                                 for k in kinds])


def test_fill_local_and_bridge_panels(tmp_path):
    _repo(tmp_path)
    plan = _plan("files", "branch", "tests", "docs", "notes", "tools", "agents", "tracks")
    fill_plan(plan, repo=str(tmp_path), bridge=FakeBridge())
    by = {p.kind: p for p in plan.panels}

    assert "README.md" in by["files"].items and by["files"].status == "ready"
    assert by["branch"].items[0] == "on claude/feature"
    assert "test_a.py" in by["tests"].items
    assert "vision.md" in by["docs"].items
    assert "ship the demo" in by["notes"].items
    assert "axiom_workspace" in by["tools"].items
    assert by["agents"].items == ["tone-beatz-agent"]   # tax-helper revoked → excluded
    # a kind with no provider stays pending
    assert by["tracks"].status == "pending" and by["tracks"].items == []


def test_authorized_agents_net_state():
    assert _authorized_agents(FakeBridge()) == ["tone-beatz-agent"]


def test_fill_is_resilient_without_bridge(tmp_path):
    _repo(tmp_path)
    plan = _plan("files", "tools", "agents")
    fill_plan(plan, repo=str(tmp_path), bridge=None)  # no bridge
    by = {p.kind: p for p in plan.panels}
    assert by["files"].status == "ready"          # local still fills
    assert by["tools"].status == "pending"        # bridge-only kinds stay pending
    assert by["agents"].status == "pending"
