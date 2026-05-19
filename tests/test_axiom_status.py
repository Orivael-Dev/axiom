"""Tests for axiom_status — the live roadmap + activity tracker."""
from __future__ import annotations

import json
import sys
import subprocess
from datetime import date
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_exoskeleton", "axiom_status",
        )):
            sys.modules.pop(mod, None)
    yield


_TRACKER_FIXTURE = """\
# AXIOM — Tracker fixture

start_date: 2026-05-13

## Month 1 (2026-05-13 → 2026-06-09): Clarify story and assets

### Asset checklist
- [x] One-page memo
- [ ] Investor deck v1 outline
- [ ] Outreach CRM seeded with 50 named buyers
- [ ] Demo v1 recorded

### Weekly cadence
- Mon — record demo
- Wed — review competitive_analysis output

## Month 2 (2026-06-10 → 2026-07-07): First conversations

### Asset checklist
- [ ] 25 discovery calls booked
- [ ] Objection log started
"""


def _write_tracker(tmp_path: Path) -> Path:
    p = tmp_path / "ROADMAP_TRACKER.md"
    p.write_text(_TRACKER_FIXTURE, encoding="utf-8")
    return p


def test_parse_tracker_reads_months_and_checklist(isolated, tmp_path):
    from axiom_status import parse_tracker
    p = _write_tracker(tmp_path)
    tracker = parse_tracker(p)
    assert tracker.start_date == date(2026, 5, 13)
    assert len(tracker.months) == 2
    m1 = tracker.months[0]
    assert m1.index == 1
    assert m1.start == date(2026, 5, 13)
    assert m1.end == date(2026, 6, 9)
    assert "Clarify story" in m1.theme
    done = [d for d, _ in m1.checklist if d]
    todo = [label for d, label in m1.checklist if not d]
    assert len(done) == 1
    assert "One-page memo" in [label for d, label in m1.checklist if d][0]
    assert "Demo v1 recorded" in todo
    assert m1.cadence and "Mon — record demo" in m1.cadence[0]


def test_month_and_week_derivation(isolated, tmp_path):
    from axiom_status import parse_tracker
    p = _write_tracker(tmp_path)
    tracker = parse_tracker(p)
    # 2026-05-19 is the 7th day of month 1 → week 1 of 4.
    today = date(2026, 5, 19)
    m = tracker.month_for(today)
    assert m is not None
    assert m.index == 1
    assert tracker.week_in_month(today, m) == 1
    # 2026-06-08 → day 26 of month 1 → week 4.
    assert tracker.week_in_month(date(2026, 6, 8), m) == 4


def test_build_status_counts_checklist_progress(isolated, tmp_path):
    from axiom_status import build_status
    p = _write_tracker(tmp_path)
    s = build_status(
        tracker_path=p, today=date(2026, 5, 19),
        ledger_path=tmp_path / "no-ledger.jsonl",
    )
    assert s.month_index == 1
    assert s.week_index == 1
    assert s.checklist_total == 4
    assert s.checklist_done == 1
    assert "Investor deck v1 outline" in s.checklist_todo


def test_build_status_force_month_and_week(isolated, tmp_path):
    from axiom_status import build_status
    p = _write_tracker(tmp_path)
    s = build_status(
        tracker_path=p, today=date(2026, 5, 19),
        force_month=2, force_week=3,
        ledger_path=tmp_path / "x.jsonl",
    )
    assert s.month_index == 2
    assert s.week_index == 3
    assert s.checklist_total == 2


def test_json_output_shape(isolated, tmp_path, capsys):
    from axiom_status import main
    p = _write_tracker(tmp_path)
    rc = main([
        "--tracker", str(p),
        "--ledger",  str(tmp_path / "ledger.jsonl"),
        "--repo-root", str(tmp_path),    # no git here → empty commits
        "--json",
    ])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert "month" in d and "week" in d
    assert "asset_checklist" in d
    assert d["asset_checklist"]["total"] >= 2
    assert "recent_runs_by_use_case" in d
    assert "recent_commits" in d


def test_human_output_renders(isolated, tmp_path, capsys):
    from axiom_status import main
    p = _write_tracker(tmp_path)
    rc = main([
        "--tracker", str(p),
        "--ledger",  str(tmp_path / "ledger.jsonl"),
        "--repo-root", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "AXIOM STATUS" in out
    assert "Asset checklist" in out
    assert "Investor deck v1 outline" in out


def test_missing_tracker_renders_friendly_message(
    isolated, tmp_path, capsys,
):
    from axiom_status import main
    rc = main([
        "--tracker", str(tmp_path / "does-not-exist.md"),
        "--ledger",  str(tmp_path / "ledger.jsonl"),
        "--repo-root", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no docs/internal/ROADMAP_TRACKER.md" in out or \
           "no month matches" in out


def test_update_checks_off_matching_todo(isolated, tmp_path, capsys):
    from axiom_status import main
    p = _write_tracker(tmp_path)
    rc = main([
        "--tracker", str(p),
        "--update", "Demo v1",
    ])
    assert rc == 0
    capsys.readouterr()                     # discard "checked off:" line
    body = p.read_text(encoding="utf-8")
    assert "[x] Demo v1 recorded" in body
    # And it stays checked across re-parse.
    rc = main([
        "--tracker", str(p),
        "--ledger", str(tmp_path / "ledger.jsonl"),
        "--repo-root", str(tmp_path),
        "--json",
    ])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["asset_checklist"]["done"] == 2


def test_update_returns_nonzero_when_no_match(isolated, tmp_path):
    from axiom_status import main
    p = _write_tracker(tmp_path)
    rc = main([
        "--tracker", str(p),
        "--update", "no such item exists in the tracker",
    ])
    assert rc != 0


def test_recent_runs_by_use_case_from_ledger(isolated, tmp_path):
    """End-to-end: write 3 ledger entries via LedgerWriter; confirm
    axiom_status groups them by use_case."""
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent
    from axiom_exoskeleton_ledger import LedgerWriter
    from axiom_event_token.backends import BackendResult

    class _Stub:
        name = "stub"
        model = "stub-model"
        def generate(self, *, system, prompt, max_output_tokens,
                     timeout_s=60.0):
            return BackendResult(
                text="OK", input_tokens=10, output_tokens=3,
                latency_ms=1, backend=self.name, model=self.model,
            )

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    ledger_path = tmp_path / "ledger.jsonl"
    exo = ExoskeletonAgent(
        c, backend=_Stub(), ledger=LedgerWriter(ledger_path),
    )
    exo.invoke("investor_research", "thesis A")
    exo.invoke("investor_research", "thesis B")
    exo.invoke("customer_discovery", "call A")

    from axiom_status import build_status
    s = build_status(
        tracker_path=tmp_path / "ROADMAP_TRACKER.md",   # missing OK
        ledger_path=ledger_path,
        repo_root=tmp_path,
        today=date(2026, 5, 19),
    )
    # No tracker present → no month/week, but ledger summary still works.
    assert s.recent_runs_by_use_case.get("investor_research") == 2
    assert s.recent_runs_by_use_case.get("customer_discovery") == 1


def test_recent_commits_uses_git_log(isolated, tmp_path):
    """Initialise a real git repo, make two commits, confirm capture."""
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "tester"],
        check=True, capture_output=True,
    )
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "a.txt"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q",
         "-m", "first commit"],
        check=True, capture_output=True,
    )
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "b.txt"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q",
         "-m", "second commit"],
        check=True, capture_output=True,
    )

    from axiom_status import recent_commits
    items = recent_commits(repo_root=tmp_path)
    subjects = {msg for _, msg in items}
    assert "first commit" in subjects
    assert "second commit" in subjects
