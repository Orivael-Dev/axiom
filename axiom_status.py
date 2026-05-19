"""Live AXIOM status — month/week, asset checklist, recent activity.

Reads three sources and prints a compact "where are we right now?"
report:

  1. `docs/internal/ROADMAP_TRACKER.md` — month/week boundaries,
     asset checklist, weekly cadence.
  2. The exoskeleton ledger (`~/.axiom/exoskeleton-ledger.jsonl`)
     — recent delegate runs, grouped by use_case.
  3. `git log` — recent commits on the current branch.

This file is the project's "keep us in line" surface. Run it at
the start of a session to recover context fast, especially in
fresh Claude Code sessions that just read `CLAUDE.md`.

CLI:
    python3 -m axiom_status                # full report
    python3 -m axiom_status --json         # machine-readable
    python3 -m axiom_status --week 2       # force a specific week
    python3 -m axiom_status --month 3      # force a specific month
    python3 -m axiom_status --update \\
        "One-page memo"                    # check off a checklist item
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


def default_tracker_path() -> Path:
    here = Path(__file__).resolve().parent
    return here / "docs" / "internal" / "ROADMAP_TRACKER.md"


# ── tracker parsing ──────────────────────────────────────────────────


_START_RE  = re.compile(r"^\s*start_date\s*:\s*(\d{4}-\d{2}-\d{2})\s*$")
_MONTH_RE  = re.compile(
    r"^##\s*Month\s+(\d+)\s*\((\d{4}-\d{2}-\d{2})\s*"
    r"(?:→|->|-)\s*(\d{4}-\d{2}-\d{2})\)\s*:?\s*(.*?)\s*$"
)
_CHECK_RE  = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*-\s+(?!\[)\s*(.+?)\s*$")
_SECTION_RE = re.compile(r"^###\s*(.+?)\s*$")


@dataclass
class MonthBlock:
    index:    int
    start:    date
    end:      date
    theme:    str
    checklist: list[tuple[bool, str]]   = field(default_factory=list)
    cadence:   list[str]                = field(default_factory=list)


@dataclass
class Tracker:
    start_date: Optional[date]
    months:     list[MonthBlock] = field(default_factory=list)
    path:       Optional[Path]   = None

    def month_for(self, today: date) -> Optional[MonthBlock]:
        for m in self.months:
            if m.start <= today <= m.end:
                return m
        # Past the end → return the last month so the report still
        # renders something useful instead of going silent.
        if self.months and today > self.months[-1].end:
            return self.months[-1]
        if self.months and today < self.months[0].start:
            return self.months[0]
        return None

    def week_in_month(self, today: date, month: MonthBlock) -> int:
        if today < month.start:
            return 1
        days = (today - month.start).days
        return min(4, max(1, days // 7 + 1))


def parse_tracker(path: Path) -> Tracker:
    if not path.exists():
        return Tracker(start_date=None, months=[], path=path)
    start: Optional[date] = None
    months: list[MonthBlock] = []
    current: Optional[MonthBlock] = None
    section: Optional[str] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if start is None:
            m_start = _START_RE.match(line)
            if m_start:
                start = _iso_to_date(m_start.group(1))
                continue
        m_month = _MONTH_RE.match(line)
        if m_month:
            current = MonthBlock(
                index=int(m_month.group(1)),
                start=_iso_to_date(m_month.group(2)),
                end=_iso_to_date(m_month.group(3)),
                theme=m_month.group(4).strip(),
            )
            months.append(current)
            section = None
            continue
        m_sec = _SECTION_RE.match(line)
        if m_sec and current is not None:
            section = m_sec.group(1).strip().lower()
            continue
        m_check = _CHECK_RE.match(line)
        if m_check and current is not None:
            done = m_check.group(1).lower() == "x"
            label = m_check.group(2).strip()
            if section and "cadence" in section:
                current.cadence.append(label)
            else:
                current.checklist.append((done, label))
            continue
        m_bullet = _BULLET_RE.match(line)
        if m_bullet and current is not None and section and \
                "cadence" in section:
            current.cadence.append(m_bullet.group(1).strip())
            continue
    return Tracker(start_date=start, months=months, path=path)


def _iso_to_date(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


# ── ledger summary ───────────────────────────────────────────────────


def recent_runs_by_use_case(
    *, since_iso: str, ledger_path: Optional[Path] = None,
) -> dict[str, int]:
    try:
        from axiom_exoskeleton_ledger import query_ledger
    except ImportError:
        return {}
    entries = query_ledger(path=ledger_path, since=since_iso)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.use_case] = counts.get(e.use_case, 0) + 1
    return counts


# ── git summary ──────────────────────────────────────────────────────


def recent_commits(*, since: str = "7 days ago",
                   repo_root: Optional[Path] = None) -> list[tuple[str, str]]:
    """List `(short_sha, subject)` for commits in the window."""
    cwd = str(repo_root) if repo_root else None
    try:
        out = subprocess.run(
            ["git", "log", f"--since={since}",
             "--pretty=format:%h %s", "--no-merges"],
            check=False, capture_output=True, text=True, cwd=cwd,
        )
    except FileNotFoundError:
        return []
    if out.returncode != 0:
        return []
    items: list[tuple[str, str]] = []
    for line in out.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            items.append((parts[0], parts[1]))
    return items


# ── status assembly ─────────────────────────────────────────────────


@dataclass
class Status:
    today_iso:   str
    month_index: Optional[int]
    month_total: int
    month_theme: str
    week_index:  Optional[int]
    week_total:  int                = 4
    checklist_done:  int            = 0
    checklist_total: int            = 0
    checklist_todo:  list[str]      = field(default_factory=list)
    cadence:         list[str]      = field(default_factory=list)
    recent_runs_by_use_case: dict[str, int] = field(default_factory=dict)
    recent_commits:  list[tuple[str, str]]  = field(default_factory=list)
    tracker_missing: bool           = False

    def to_json(self) -> dict:
        return {
            "today":            self.today_iso,
            "month":            self.month_index,
            "month_total":      self.month_total,
            "month_theme":      self.month_theme,
            "week":             self.week_index,
            "week_total":       self.week_total,
            "asset_checklist": {
                "done":  self.checklist_done,
                "total": self.checklist_total,
                "open":  self.checklist_todo,
            },
            "weekly_cadence":           self.cadence,
            "recent_runs_by_use_case":  self.recent_runs_by_use_case,
            "recent_commits": [
                {"sha": s, "subject": m}
                for s, m in self.recent_commits
            ],
            "tracker_missing":          self.tracker_missing,
        }


def build_status(
    *,
    tracker_path: Optional[Path] = None,
    today:        Optional[date] = None,
    force_month:  Optional[int]  = None,
    force_week:   Optional[int]  = None,
    ledger_path:  Optional[Path] = None,
    repo_root:    Optional[Path] = None,
    since_days:   int            = 7,
) -> Status:
    tracker_path = tracker_path or default_tracker_path()
    tracker = parse_tracker(tracker_path)
    today = today or date.today()
    today_iso = today.isoformat()

    month: Optional[MonthBlock] = None
    if force_month is not None and tracker.months:
        for m in tracker.months:
            if m.index == force_month:
                month = m
                break
    if month is None:
        month = tracker.month_for(today)

    # Ledger + git summaries don't depend on the tracker, so compute
    # them up-front so we still emit something useful when the
    # tracker is absent.
    cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
    since_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    since_iso = since_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    runs = recent_runs_by_use_case(
        since_iso=since_iso, ledger_path=ledger_path,
    )
    commits = recent_commits(
        since=f"{since_days} days ago", repo_root=repo_root,
    )

    if month is None:
        return Status(
            today_iso=today_iso,
            month_index=None, month_total=len(tracker.months),
            month_theme="(no roadmap tracker)",
            week_index=None,
            recent_runs_by_use_case=runs,
            recent_commits=commits,
            tracker_missing=(not tracker_path.exists()),
        )

    week = force_week if force_week is not None \
        else tracker.week_in_month(today, month)

    done = sum(1 for d, _ in month.checklist if d)
    total = len(month.checklist)
    todo  = [label for d, label in month.checklist if not d]

    return Status(
        today_iso=today_iso,
        month_index=month.index,
        month_total=len(tracker.months),
        month_theme=month.theme,
        week_index=week,
        week_total=4,
        checklist_done=done,
        checklist_total=total,
        checklist_todo=todo,
        cadence=list(month.cadence),
        recent_runs_by_use_case=runs,
        recent_commits=commits,
    )


# ── rendering ────────────────────────────────────────────────────────


def render_human(s: Status) -> str:
    out: list[str] = []
    out.append(f"AXIOM STATUS  · {s.today_iso}")
    out.append("─" * 42)
    if s.month_index is None:
        if s.tracker_missing:
            out.append(
                "(no docs/internal/ROADMAP_TRACKER.md found — "
                "run from repo root or set the tracker path)"
            )
        else:
            out.append("(no month matches today's date in the tracker)")
        return "\n".join(out)
    out.append(
        f"Month {s.month_index} / {s.month_total}    "
        f"week {s.week_index} of {s.week_total}    "
        f"theme: {s.month_theme}"
    )
    out.append("")
    pct = (
        round(100 * s.checklist_done / s.checklist_total)
        if s.checklist_total else 0
    )
    out.append(
        f"Asset checklist:    {s.checklist_done} / "
        f"{s.checklist_total} done  ({pct}%)"
    )
    for label in s.checklist_todo[:8]:
        out.append(f"  [ ] {label}")
    if len(s.checklist_todo) > 8:
        out.append(f"  ... and {len(s.checklist_todo) - 8} more")
    out.append("")
    if s.cadence:
        out.append("Weekly cadence:")
        for line in s.cadence:
            out.append(f"  · {line}")
        out.append("")
    if s.recent_runs_by_use_case:
        out.append("Recent delegate runs (last 7d, from ledger):")
        sorted_runs = sorted(
            s.recent_runs_by_use_case.items(),
            key=lambda kv: kv[1], reverse=True,
        )
        width = max(len(k) for k, _ in sorted_runs)
        for name, n in sorted_runs:
            out.append(f"  {name.ljust(width)}  {n}")
        out.append("")
    else:
        out.append("Recent delegate runs (last 7d): (none)")
        out.append("")
    if s.recent_commits:
        out.append(f"Recent commits (last 7d):  {len(s.recent_commits)}")
        for sha, msg in s.recent_commits[:8]:
            out.append(f"  {sha}  {msg}")
        if len(s.recent_commits) > 8:
            out.append(f"  ... and {len(s.recent_commits) - 8} more")
    else:
        out.append("Recent commits (last 7d):  (none)")
    return "\n".join(out)


# ── --update: check off a TODO ──────────────────────────────────────


def check_off(tracker_path: Path, label: str) -> bool:
    """Mark the first unchecked checklist item matching `label`.

    Match is case-insensitive substring on the label text. Returns
    True iff a line was modified.
    """
    if not tracker_path.exists():
        return False
    lines = tracker_path.read_text(encoding="utf-8").splitlines()
    needle = label.strip().lower()
    changed = False
    for i, line in enumerate(lines):
        m = _CHECK_RE.match(line)
        if not m:
            continue
        if m.group(1).lower() == "x":
            continue
        if needle in m.group(2).strip().lower():
            lines[i] = line.replace("[ ]", "[x]", 1)
            changed = True
            break
    if changed:
        tracker_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8",
        )
    return changed


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="axiom-status",
        description="Live AXIOM roadmap + activity status.",
    )
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")
    ap.add_argument("--tracker", help="path to ROADMAP_TRACKER.md")
    ap.add_argument("--ledger", help="path to exoskeleton ledger JSONL")
    ap.add_argument("--repo-root",
                    help="git repo root (default: cwd)")
    ap.add_argument("--month", type=int,
                    help="force month index (1-based)")
    ap.add_argument("--week", type=int,
                    help="force week index (1-4)")
    ap.add_argument("--since-days", type=int, default=7,
                    help="lookback window for ledger + git "
                         "(default: 7)")
    ap.add_argument("--update", metavar="LABEL",
                    help="check off the first TODO whose label "
                         "contains this substring (case-insensitive)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    tracker_path = (
        Path(args.tracker) if args.tracker else default_tracker_path()
    )
    if args.update:
        ok = check_off(tracker_path, args.update)
        if ok:
            print(f"checked off: {args.update!r}")
            return 0
        print(
            f"no matching open TODO for {args.update!r}",
            file=sys.stderr,
        )
        return 1

    status = build_status(
        tracker_path=tracker_path,
        ledger_path=Path(args.ledger) if args.ledger else None,
        repo_root=Path(args.repo_root) if args.repo_root else None,
        force_month=args.month, force_week=args.week,
        since_days=args.since_days,
    )
    if args.json:
        print(json.dumps(status.to_json(), indent=2, ensure_ascii=True))
    else:
        print(render_human(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
