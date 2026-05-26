#!/usr/bin/env python3
"""Download HarmBench → convert → write as bench JSONL.

HarmBench (Mazeika et al. 2024, MIT-licensed) is a 400-behaviour public
benchmark from the Center for AI Safety.  Source CSV:

  https://raw.githubusercontent.com/centerforaisafety/HarmBench/main/
       data/behavior_datasets/harmbench_behaviors_text_all.csv

This script fetches that CSV, maps each row to the abliteration bench's
{HARM, DECEIVE} label scheme, and writes a JSONL the bench can consume
directly:

    python3 scripts/run_abliteration_bench.py \\
        --corpus tests/abliteration_bench/data/harmbench.jsonl

Mapping doctrine
----------------
HarmBench is broader than AXIOM's gate.  Three of its FunctionalCategory
buckets matter for us:

  standard      direct harmful-instruction prompts.  Mapped by
                SemanticCategory: cybercrime / illegal / chemical_biological
                / harassment_bullying / harmful → HARM;
                misinformation_disinformation → DECEIVE (these prompts
                ask the model to fabricate sources, impersonate
                authorities, or generate deceptive content).

  contextual    behaviour text alone is benign; harm is unlocked by the
                accompanying ContextString.  The bench prompt is therefore
                f"{context}\\n\\n{behaviour}" so the gate sees the full
                attack surface, classified as HARM.

  copyright     extraction of copyrighted books/lyrics.  Excluded — this
                is an IP threat model, not a safety one; AXIOM's gate
                is not a copyright filter.  The exclusion is documented
                in the bench README under "What this bench is NOT".

The downloader is fully self-contained (stdlib only, no extra deps) and
writes the JSONL deterministically so CI runs reproduce identically.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Iterable


HARMBENCH_URL = (
    "https://raw.githubusercontent.com/centerforaisafety/HarmBench/"
    "main/data/behavior_datasets/harmbench_behaviors_text_all.csv"
)

# SemanticCategory → AXIOM bench expected_class
_HARM_SEMANTIC = frozenset({
    "cybercrime_intrusion",
    "illegal",
    "chemical_biological",
    "harassment_bullying",
    "harmful",
})
_DECEIVE_SEMANTIC = frozenset({
    "misinformation_disinformation",
})


def _fetch(url: str, *, timeout: int = 30) -> str:
    """GET ``url`` and return the body as text."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "axiom-abliteration-bench/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _bench_id(behavior_id: str) -> str:
    """Stable, prefix-tagged id so HarmBench rows are distinguishable in
    reports from the built-in synthetic corpus."""
    return f"hb-{behavior_id}"


def _convert_row(row: dict) -> dict | None:
    """Convert one HarmBench CSV row to a bench JSONL entry, or return
    None if the row is out-of-scope for AXIOM's gate (copyright)."""
    functional = (row.get("FunctionalCategory") or "").strip()
    semantic = (row.get("SemanticCategory") or "").strip()
    behavior = (row.get("Behavior") or "").strip()
    behavior_id = (row.get("BehaviorID") or "").strip()
    context = (row.get("ContextString") or "").strip()

    if not behavior or not behavior_id:
        return None

    # Skip copyright — not in AXIOM's threat model.
    if functional == "copyright" or semantic == "copyright":
        return None

    if functional == "contextual":
        # Glue context to behaviour so the gate sees the actual attack.
        text = f"{context}\n\n{behavior}" if context else behavior
        expected = "HARM"
    elif functional == "standard":
        text = behavior
        if semantic in _DECEIVE_SEMANTIC:
            expected = "DECEIVE"
        elif semantic in _HARM_SEMANTIC:
            expected = "HARM"
        else:
            # Unknown semantic — skip rather than mis-label.
            return None
    else:
        return None

    return {
        "id": _bench_id(behavior_id),
        "category": f"harmbench:{semantic or 'unknown'}",
        "expected_class": expected,
        "text": text,
    }


def convert(csv_text: str) -> tuple[list[dict], dict]:
    """Convert HarmBench CSV text → (rows, summary)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    out: list[dict] = []
    skipped = Counter()
    total = 0
    for row in reader:
        total += 1
        entry = _convert_row(row)
        if entry is None:
            functional = (row.get("FunctionalCategory") or "").strip()
            semantic = (row.get("SemanticCategory") or "").strip()
            if functional == "copyright" or semantic == "copyright":
                skipped["copyright"] += 1
            else:
                skipped["unmapped"] += 1
            continue
        out.append(entry)

    # Deterministic ordering — sort by id so the JSONL is reproducible.
    out.sort(key=lambda r: r["id"])

    summary = {
        "source_rows": total,
        "written_rows": len(out),
        "harm_rows": sum(1 for r in out if r["expected_class"] == "HARM"),
        "deceive_rows": sum(1 for r in out if r["expected_class"] == "DECEIVE"),
        "skipped": dict(skipped),
    }
    return out, summary


def write_jsonl(rows: Iterable[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--url", default=HARMBENCH_URL,
        help=f"HarmBench CSV URL (default: {HARMBENCH_URL})",
    )
    ap.add_argument(
        "--out", type=Path,
        default=Path("tests/abliteration_bench/data/harmbench.jsonl"),
        help="Output JSONL path",
    )
    ap.add_argument(
        "--from-file", type=Path, default=None,
        help="Skip the network fetch and convert a locally cached CSV",
    )
    args = ap.parse_args(argv)

    if args.from_file is not None:
        csv_text = args.from_file.read_text(encoding="utf-8")
        source = str(args.from_file)
    else:
        try:
            csv_text = _fetch(args.url)
        except Exception as exc:
            print(f"error: failed to fetch HarmBench: {exc}", file=sys.stderr)
            return 2
        source = args.url

    rows, summary = convert(csv_text)
    write_jsonl(rows, args.out)

    print(f"HarmBench → bench JSONL")
    print(f"  source:        {source}")
    print(f"  source_rows:   {summary['source_rows']}")
    print(f"  written_rows:  {summary['written_rows']}")
    print(f"    HARM:          {summary['harm_rows']}")
    print(f"    DECEIVE:       {summary['deceive_rows']}")
    print(f"  skipped:")
    for k, v in summary["skipped"].items():
        print(f"    {k:13s}  {v}")
    print(f"  out:           {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
