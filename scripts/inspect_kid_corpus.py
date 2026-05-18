#!/usr/bin/env python3
"""Inspect the kid-safety red-team corpus.

Transparency tool for auditors: dumps every prompt in the corpus
that backs the kid-safety audit, organized by category, with the
expected verdict and severity weight. Lets a regulator see exactly
what was tested — no AXIOM source-code reading required.

Modes:
    summary  — counts + severity totals per category (default)
    detail   — every prompt printed in full, Markdown table
    json     — raw corpus JSON for downstream tooling

Usage:
    python3 scripts/inspect_kid_corpus.py
    python3 scripts/inspect_kid_corpus.py detail
    python3 scripts/inspect_kid_corpus.py json --corpus kid_safety_v1
    python3 scripts/inspect_kid_corpus.py detail > corpus-snapshot.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPORA_DIR = REPO_ROOT / "axiom_report" / "red_team_corpora"


def load_corpus(name: str) -> dict:
    path = CORPORA_DIR / f"{name}.json"
    if not path.is_file():
        sys.exit(
            f"Corpus not found: {path}\n"
            f"Available: {sorted(p.stem for p in CORPORA_DIR.glob('*.json'))}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def summary(corpus: dict) -> None:
    prompts = corpus["prompts"]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for p in prompts:
        by_cat[p["category"]].append(p)

    print(f"# {corpus['name']} v{corpus['version']}")
    print()
    print(corpus.get("description", "").strip())
    print()
    print(f"Total prompts: **{len(prompts)}**")
    print(f"Categories:    **{len(by_cat)}**")
    print()
    print("| Category | Prompts | Severity total | Severities |")
    print("|----------|---------:|---------------:|------------|")
    rows = []
    for cat, items in by_cat.items():
        sev_total = sum(p.get("severity", 1) for p in items)
        sev_dist = ",".join(
            f"{s}×{c}"
            for s, c in sorted(Counter(p.get("severity", 1) for p in items).items())
        )
        rows.append((cat, len(items), sev_total, sev_dist))
    for cat, n, sev_total, sev_dist in sorted(rows, key=lambda r: -r[2]):
        print(f"| {cat} | {n} | {sev_total} | {sev_dist} |")
    print()
    verdicts = Counter(p.get("expected_verdict", "?") for p in prompts)
    print("Expected verdicts: " + ", ".join(
        f"**{v}**={n}" for v, n in sorted(verdicts.items())
    ))


def detail(corpus: dict) -> None:
    prompts = corpus["prompts"]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for p in prompts:
        by_cat[p["category"]].append(p)

    print(f"# {corpus['name']} v{corpus['version']} — full prompt listing")
    print()
    print(corpus.get("description", "").strip())
    print()
    for cat in sorted(by_cat):
        items = by_cat[cat]
        print(f"## {cat}  ({len(items)} prompts)")
        print()
        print("| id | severity | expected | prompt | notes |")
        print("|----|---------:|---------|--------|-------|")
        for p in items:
            pid = p.get("id", "?")
            sev = p.get("severity", 1)
            verdict = p.get("expected_verdict", "?")
            text = _md_escape(p.get("prompt", ""))
            notes = _md_escape(p.get("notes", ""))
            print(f"| `{pid}` | {sev} | `{verdict}` | {text} | {notes} |")
        print()


def _md_escape(s: str) -> str:
    """Escape pipes + newlines so Markdown tables don't break."""
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "mode",
        nargs="?",
        default="summary",
        choices=["summary", "detail", "json"],
        help="Output mode. Default: summary.",
    )
    p.add_argument(
        "--corpus",
        default="kid_safety_v1",
        help="Corpus name (file stem in axiom_report/red_team_corpora/).",
    )
    args = p.parse_args(argv[1:])

    corpus = load_corpus(args.corpus)
    if args.mode == "summary":
        summary(corpus)
    elif args.mode == "detail":
        detail(corpus)
    elif args.mode == "json":
        json.dump(corpus, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
