"""
Branch-aware workspace — gather dev context for a repo branch.
==============================================================
The "work on the X branch" half of an AX OS dev workspace: read the
current git branch and a quick inventory (README / docs / tests / recent
commits) so the AUI can lay out a development workspace. Pure local
filesystem + git; no Axiom dependency (Axiom handles safety + recall +
the signed audit trail separately).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class BranchContext:
    branch: str
    has_readme: bool
    docs_count: int
    tests_count: int
    recent_commits: List[str] = field(default_factory=list)
    available: bool = True


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def gather_branch_context(repo_path: str = ".") -> BranchContext:
    """Inventory the current branch of ``repo_path`` (or mark unavailable)."""
    repo = Path(repo_path)
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0:
        return BranchContext(branch="", has_readme=False, docs_count=0,
                             tests_count=0, recent_commits=[], available=False)

    has_readme = any((repo / n).exists()
                     for n in ("README.md", "README.rst", "README"))
    docs = repo / "docs"
    docs_count = sum(1 for p in docs.rglob("*") if p.is_file()) if docs.is_dir() else 0
    tests = repo / "tests"
    tests_count = sum(1 for _ in tests.rglob("test_*.py")) if tests.is_dir() else 0
    log = _git(repo, "log", "--oneline", "-5")
    commits = log.stdout.splitlines() if log.returncode == 0 else []

    return BranchContext(
        branch=head.stdout.strip(),
        has_readme=has_readme,
        docs_count=docs_count,
        tests_count=tests_count,
        recent_commits=commits,
    )
