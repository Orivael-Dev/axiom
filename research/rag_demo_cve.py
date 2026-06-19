"""End-to-end CVE RAG: FTS5 retrieval -> Qwen 0.5B GGUF generation.

A query is answered by retrieving the most relevant CVE record from the
297k-row FTS5 index (axiom_cve_retriever) and feeding it to the quantized
Qwen2.5-Coder-0.5B SRD-4 model as grounding context.

Run:
    python research/rag_demo_cve.py "what is the log4j vulnerability"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from axiom_cve_retriever import CVERetriever

DB         = "I:/Orivael/dataset/cve_fts5.db"
MODEL_PATH = ("I:/Orivael/models/qwen25_coder_0p5b_srd4_q4km/"
              "qwen25_coder_0p5b_srd4_q4km.gguf")

# Trim retrieved CVE context so the prompt stays inside n_ctx.
_CTX_CHARS = 1600


def main():
    query = " ".join(sys.argv[1:]) or "what is the log4j CVE-2021-44228 vulnerability"

    # 1. retrieve
    r = CVERetriever(DB)
    t0 = time.time()
    hits = r.retrieve(query, k=1)
    retr_ms = (time.time() - t0) * 1000
    if not hits:
        print(f"no CVE hit for {query!r}")
        return
    top = hits[0]
    context = (r.answer_for(query) or top.snippet)[:_CTX_CHARS]
    print(f"query    : {query!r}")
    print(f"retrieved: {top.title}  (score {top.score:.3f}, {retr_ms:.2f} ms)")
    print(f"context  : {context[:160]}...\n")

    # 2. generate
    from llama_cpp import Llama
    print("loading Qwen 0.5B ...")
    t1 = time.time()
    llm = Llama(model_path=MODEL_PATH, n_ctx=2048, verbose=False)
    print(f"loaded in {time.time()-t1:.1f} s\n")

    prompt = (
        "<|im_start|>system\nYou are a cybersecurity assistant. Answer the "
        "question using ONLY the CVE reference provided. Be concise and "
        "technical.<|im_end|>\n"
        f"<|im_start|>user\nCVE reference:\n{context}\n\n"
        f"Question: {query}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    t2 = time.time()
    res = llm(prompt, max_tokens=220, temperature=0.2, stop=["<|im_end|>"])
    gen_s = time.time() - t2
    text = res["choices"][0]["text"].strip()
    n_tok = res["usage"]["completion_tokens"]

    print("=" * 64)
    print("MODEL ANSWER (grounded on retrieved CVE):")
    print("=" * 64)
    print(text)
    print("=" * 64)
    print(f"generated {n_tok} tokens in {gen_s:.1f} s "
          f"({n_tok/gen_s:.1f} tok/s) | retrieval {retr_ms:.2f} ms")


if __name__ == "__main__":
    main()
