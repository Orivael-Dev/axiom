"""Minimal RAG demo: BM25 retrieval (new add_documents method) + Qwen 0.5B GGUF.

Indexes the deduped 14-row knowledge base distilled from
I:/Orivael/dataset/dataset.jsonl (1.1M rows -> 14 unique outputs) into the
in-memory BM25 LocalRetriever via the new `add_documents()` method, then
answers a query by feeding the retrieved row's output to the quantized
Qwen2.5-Coder-0.5B SRD-4 model as grounding context.

Run:
    python research/rag_demo_qwen05b.py "how do I build a coding assistant"
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from axiom_research_retriever import LocalRetriever

KB_PATH    = Path("I:/Orivael/dataset/dataset_kb14.jsonl")
MODEL_PATH = Path("I:/Orivael/models/qwen25_coder_0p5b_srd4_q4km/"
                  "qwen25_coder_0p5b_srd4_q4km.gguf")


def build_retriever(kb_path: Path):
    """Index the KB rows via the new add_documents() path. Returns (retriever, answers)."""
    work = Path(tempfile.mkdtemp(prefix="rag_kb_"))
    paths, answers = [], {}
    with kb_path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            row = json.loads(line)
            searchable = f"{row.get('task','')} {row.get('instruction','')}".strip()
            p = work / f"kb_{i:03d}.txt"
            p.write_text(searchable, encoding="utf-8")
            paths.append(p)
            answers[p.name] = row.get("output", "").strip()
    r = LocalRetriever(roots=[])
    r.build()
    r.add_documents(paths)                       # <-- the new method
    return r, answers


def retrieve_context(r, answers, query, k=1):
    hits = r.retrieve(query, k=k)
    out = []
    for h in hits:
        ans = answers.get(Path(h.uri).name, "")
        out.append((h.score, h.title, ans))
    return out


def generate(query, context):
    from llama_cpp import Llama
    llm = Llama(model_path=str(MODEL_PATH), n_ctx=2048, verbose=False)
    prompt = (
        "<|im_start|>system\nYou are a concise coding assistant. Use the "
        "reference to ground your answer.<|im_end|>\n"
        f"<|im_start|>user\nReference: {context}\n\nQuestion: {query}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    res = llm(prompt, max_tokens=200, temperature=0.3, stop=["<|im_end|>"])
    return res["choices"][0]["text"].strip()


def main():
    query = " ".join(sys.argv[1:]) or "how do I build a coding assistant with a local LLM"
    r, answers = build_retriever(KB_PATH)
    print(f"query: {query!r}\n")
    ctx = retrieve_context(r, answers, query, k=1)
    if not ctx:
        print("no retrieval hit"); return
    score, title, context = ctx[0]
    print(f"retrieved (score {score:.4f}): {title}")
    print(f"context: {context}\n")
    print("generating with Qwen 0.5B ...")
    print("-" * 60)
    print(generate(query, context))


if __name__ == "__main__":
    main()
