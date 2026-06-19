"""CUAD → legal-rag-bench adapter.

CUAD (Contract Understanding Atticus Dataset) is the standard contracts
retrieval benchmark: ~510 commercial contracts, 41 clause categories, answers
annotated as spans within each contract.  It ships SQuAD-style:

    {"data": [{"title": "...", "paragraphs": [{"context": "<full contract>",
        "qas": [{"id": "...", "question": "Highlight ... 'Governing Law' ...",
                 "answers": [{"text": "...", "answer_start": 123}],
                 "is_impossible": false}]}]}]}

This adapter turns it into the two structures legal_rag_bench expects:

  corpus : [{"id": "<contract>-c<N>-s1", "text": "<chunk>"}]
  qa     : [{"id": "...", "question": "...", "relevant_passage_id": "<chunk id>"}]

Design choices that make it a real test of the contracts genre-buoyancy:

  * ALL contracts are pooled into one corpus.  A clause query then has to find
    the right clause in the right contract amid every other contract's chunks —
    so off-genre contracts are genuine distractors, which is exactly what
    contract_buoyancy_rerank() is built to suppress.
  * Chunk ids are "<slug>-c<idx>-s1" so _parent_of() collapses them to the
    CONTRACT (the slug).  The contract-buoyancy track scores at contract
    granularity → it measures GENRE ROUTING (did we reach the right contract?),
    while the bench's chunk-level Hit@k measures clause precision separately.

Honest limitation: genre-buoyancy only fires when the clause QUESTION carries a
genre cue (e.g. "License Grant", "Non-Compete").  CUAD has many genre-neutral
clause types (e.g. "Governing Law", "Anti-Assignment"); for those the re-rank is
a measured no-op (the safety guard), so this eval also confirms buoyancy does
not HURT the genre-neutral majority.

Usage:
    from cuad_loader import load_cuad
    corpus, qa = load_cuad("/path/to/CUADv1.json")           # local SQuAD json
    corpus, qa = load_cuad("theatticusproject/cuad-qa")      # HF dataset id

The field accessors are isolated in _extract_squad_records() / _records_from_hf()
so adapting to the Nemotron LegalBench-CUAD-v2 parquet schema is a one-spot edit
once its exact column names are confirmed from the dataset README.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# A CUAD "record" is normalised to this shape before chunking:
#   {"title": str, "context": str,
#    "qas": [{"id": str, "question": str,
#             "answers": [{"text": str, "answer_start": int}],
#             "is_impossible": bool}]}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str, *, fallback: str = "contract") -> str:
    """Turn a contract title into an id-safe slug (no '-c\\d+-s\\d+' collisions)."""
    slug = _SLUG_RE.sub("_", (title or "").lower()).strip("_")
    return slug or fallback


def _chunk_with_offsets(
    text: str, *, max_tokens: int = 400,
) -> List[Tuple[str, int, int]]:
    """Split text into ~max_tokens word chunks, tracking char spans.

    Returns [(chunk_text, start_char, end_char)] so an answer_start can be
    mapped deterministically to the chunk that contains it.  Word-aligned so a
    chunk boundary never splits a token.
    """
    if not text:
        return []
    chunks: List[Tuple[str, int, int]] = []
    # Iterate words with their char offsets.
    words = list(re.finditer(r"\S+", text))
    if not words:
        return []
    i = 0
    while i < len(words):
        window = words[i:i + max_tokens]
        start_char = window[0].start()
        end_char = window[-1].end()
        chunks.append((text[start_char:end_char], start_char, end_char))
        i += max_tokens
    return chunks


def _chunk_index_for_offset(
    chunks: List[Tuple[str, int, int]], answer_start: int,
) -> Optional[int]:
    """Index of the chunk whose char span covers answer_start, or None."""
    for idx, (_, start, end) in enumerate(chunks):
        if start <= answer_start < end:
            return idx
    # Fall back to substring containment handled by the caller.
    return None


def _extract_squad_records(data: dict) -> List[dict]:
    """Normalise a SQuAD-style CUAD dict into the internal record shape."""
    records: List[dict] = []
    for article in data.get("data", []):
        title = article.get("title", "")
        for para in article.get("paragraphs", []):
            context = para.get("context", "")
            qas = []
            for qa in para.get("qas", []):
                qas.append({
                    "id": str(qa.get("id", "")),
                    "question": qa.get("question", ""),
                    "answers": qa.get("answers", []) or [],
                    "is_impossible": bool(qa.get("is_impossible", False)),
                })
            records.append({"title": title, "context": context, "qas": qas})
    return records


def records_to_bench(
    records: List[dict], *, max_tokens: int = 400,
) -> Tuple[List[dict], List[dict]]:
    """Convert normalised CUAD records into (corpus, qa) bench structures.

    A QA row is emitted only for answerable questions (non-impossible, ≥1
    answer) whose answer span maps to a chunk — these are the gold-bearing
    retrieval targets.  Unanswerable questions don't define a passage to find,
    so they're skipped (they belong to a separate has-answer classification
    task, not retrieval).
    """
    corpus: List[dict] = []
    qa: List[dict] = []
    seen_slugs: Dict[str, int] = {}

    for rec in records:
        # Disambiguate duplicate titles so chunk ids stay unique.
        base = _slugify(rec["title"])
        n = seen_slugs.get(base, 0)
        seen_slugs[base] = n + 1
        slug = base if n == 0 else f"{base}_{n}"

        chunks = _chunk_with_offsets(rec["context"], max_tokens=max_tokens)
        chunk_ids: List[str] = []
        for idx, (ctext, _, _) in enumerate(chunks):
            cid = f"{slug}-c{idx}-s1"
            chunk_ids.append(cid)
            corpus.append({"id": cid, "text": ctext})

        for qrow in rec["qas"]:
            if qrow["is_impossible"] or not qrow["answers"]:
                continue
            ans = qrow["answers"][0]
            ans_text = ans.get("text", "")
            ans_start = ans.get("answer_start", -1)

            gold_idx = _chunk_index_for_offset(chunks, ans_start)
            if gold_idx is None and ans_text:
                # answer_start missing/misaligned — fall back to substring search
                for idx, (ctext, _, _) in enumerate(chunks):
                    if ans_text[:80] in ctext:
                        gold_idx = idx
                        break
            if gold_idx is None:
                continue

            qa.append({
                "id": qrow["id"] or f"{slug}-q{len(qa)}",
                "question": qrow["question"],
                "relevant_passage_id": chunk_ids[gold_idx],
            })

    return corpus, qa


def _records_from_hf(dataset_id: str, *, split: str, token: Optional[str]) -> List[dict]:
    """Load CUAD from a HuggingFace dataset id into normalised records.

    Handles the common SQuAD-flattened HF schema (one row per question with
    'title', 'context', 'question', 'answers'={'text':[...], 'answer_start':[...]}).
    Groups rows back into per-context records.
    """
    from datasets import load_dataset  # lazy: only needed for the HF path
    ds = load_dataset(dataset_id, split=split, token=token)
    by_context: Dict[Tuple[str, str], dict] = {}
    for row in ds:
        title = row.get("title", "")
        context = row.get("context", "")
        key = (title, context)
        rec = by_context.setdefault(
            key, {"title": title, "context": context, "qas": []})
        answers = row.get("answers", {}) or {}
        texts = answers.get("text", []) or []
        starts = answers.get("answer_start", []) or []
        rec["qas"].append({
            "id": str(row.get("id", "")),
            "question": row.get("question", ""),
            "answers": [{"text": t, "answer_start": s}
                        for t, s in zip(texts, starts)],
            "is_impossible": len(texts) == 0,
        })
    return list(by_context.values())


def load_cuad(
    path_or_id: str,
    *,
    max_tokens: int = 400,
    split: str = "test",
    token: Optional[str] = None,
) -> Tuple[List[dict], List[dict]]:
    """Load CUAD as (corpus, qa) for legal_rag_bench.

    path_or_id:
      * a local .json file in SQuAD-style CUAD format, OR
      * a HuggingFace dataset id (e.g. 'theatticusproject/cuad-qa').
    """
    p = Path(path_or_id)
    if p.exists() and p.suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        records = _extract_squad_records(data)
    else:
        records = _records_from_hf(path_or_id, split=split, token=token)
    return records_to_bench(records, max_tokens=max_tokens)
