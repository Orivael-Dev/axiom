"""
Panel providers — fill WorkspacePlan panels with real data.
============================================================
build_plan produces panels as empty `pending` slots ("gather files / tools
/ agents here"). This fills them from local data and the Axiom bridge, so
the workspace shows the actual files, tools, branch context, and
authorized agents for the goal. Panels with no data source (domain-creative
kinds like tracks / plugins) stay `pending` — honest, not faked.

All Axiom access is via the injected bridge; the rest is local filesystem
and git. No Axiom source here.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from workspace.branch import gather_branch_context

_SKIP = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def _list_files(repo: str, limit: int = 12) -> List[str]:
    try:
        p = Path(repo)
        names = sorted(f.name + ("/" if f.is_dir() else "")
                       for f in p.iterdir()
                       if not f.name.startswith(".") and f.name not in _SKIP)
        return names[:limit]
    except Exception:
        return []


def _list_glob(repo: str, subdir: str, pattern: str, limit: int = 12) -> List[str]:
    try:
        d = Path(repo) / subdir
        if not d.is_dir():
            return []
        return sorted(str(f.relative_to(d)) for f in d.rglob(pattern) if f.is_file())[:limit]
    except Exception:
        return []


def _tools(bridge) -> List[str]:
    try:
        return sorted(bridge.list_tools())
    except Exception:
        return []


def _notes(repo: str, limit: int = 8) -> List[str]:
    for name in ("NOTES.md", "notes.md", "TODO.md", "todo.md"):
        f = Path(repo) / name
        if f.is_file():
            try:
                lines = [ln.strip("#-* ").strip()
                         for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
                return lines[:limit]
            except Exception:
                return []
    return []


def _authorized_agents(bridge) -> List[str]:
    """Currently-authorized agents, derived from the signed audit ledger."""
    try:
        events = bridge.audit_list(limit=200).get("events", [])
    except Exception:
        return []
    state: dict = {}
    for e in events:
        subj = e.get("subject")
        if not subj:
            continue
        if e.get("event_type") == "agent_approved":
            state[subj] = True
        elif e.get("event_type") == "agent_revoked":
            state[subj] = False
    return sorted(s for s, ok in state.items() if ok)


def _provide(kind: str, repo: str, bridge, branch) -> List[str]:
    if kind == "files":
        return _list_files(repo)
    if kind == "tools":
        return _tools(bridge) if bridge is not None else []
    if kind == "notes":
        return _notes(repo)
    if kind == "branch":
        if not branch.available:
            return []
        return [f"on {branch.branch}", *branch.recent_commits[:4]]
    if kind == "tests":
        return _list_glob(repo, "tests", "test_*.py")
    if kind == "docs":
        return _list_glob(repo, "docs", "*.md")
    if kind == "agents":
        return _authorized_agents(bridge) if bridge is not None else []
    return []  # session / tracks / plugins / documents / reminders / guidelines


def fill_plan(plan, *, repo: str = ".", bridge=None):
    """Populate each panel's items from real data; mark filled panels ready."""
    branch = gather_branch_context(repo)
    for panel in plan.panels:
        if panel.kind in ("intent", "safety", "context", "memory"):
            continue  # already populated by build_plan
        items = _provide(panel.kind, repo, bridge, branch)
        if items:
            panel.items = items
            panel.status = "ready"
    return plan
