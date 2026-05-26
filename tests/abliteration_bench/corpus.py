"""Prompt corpus for the abliteration bench.

Each ``Prompt`` carries a human-labelled ``expected_class`` — the
ground truth.  The bench reports both the classifier's accuracy (does
AXIOM agree with the human label?) and the gate's block rate (given
the model complied, did the gate stop it?).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Prompt:
    id: str
    text: str
    expected_class: str   # "HARM" | "DECEIVE"
    category: str         # finer-grained tag for per-category breakdowns


def _load_jsonl(path: Path) -> list[Prompt]:
    rows: list[Prompt] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            obj = json.loads(line)
            rows.append(Prompt(
                id=str(obj["id"]),
                text=str(obj["text"]),
                expected_class=str(obj["expected_class"]),
                category=str(obj["category"]),
            ))
    return rows


def load_builtin_corpus() -> list[Prompt]:
    """Load the bundled HARM + DECEIVE corpus (~40 prompts, no downloads)."""
    return _load_jsonl(_DATA_DIR / "builtin_harm.jsonl") + \
           _load_jsonl(_DATA_DIR / "builtin_deceive.jsonl")


def load_corpus_from(path: Path) -> list[Prompt]:
    """Load a custom JSONL corpus (HarmBench / AdvBench export, etc)."""
    return _load_jsonl(path)


def group_by_class(prompts: Iterable[Prompt]) -> dict[str, list[Prompt]]:
    out: dict[str, list[Prompt]] = {}
    for p in prompts:
        out.setdefault(p.expected_class, []).append(p)
    return out


def group_by_category(prompts: Iterable[Prompt]) -> dict[str, list[Prompt]]:
    out: dict[str, list[Prompt]] = {}
    for p in prompts:
        out.setdefault(p.category, []).append(p)
    return out
