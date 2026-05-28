"""Research-only SRD benchmark harness.

This package is NOT shipped — it lives outside the importable Axiom
surface so the ML deps (torch, transformers, datasets) stay out of
pyproject.toml. See `requirements.txt` in this folder.

The harness produces `docs/SRD_RESULTS.md` — a one-page empirical
answer to "does SRD beat existing K-quants at matched bpw on
TinyLlama-1.1B WikiText-2 perplexity?"

Modules:
  quantize_model  — apply SRD in-place to a HuggingFace model
  bench_perplexity — WikiText-2 sliding-window PPL evaluator
  bench_llamacpp  — K-quant baseline via llama-cli --perplexity
  plot_results    — perplexity-vs-bpw scatter
"""
