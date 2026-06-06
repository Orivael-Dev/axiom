"""
Embeddings via the local LLM's OpenAI-compatible /v1/embeddings endpoint.
========================================================================
Used by curiosity for true latent salience. Requires the local LLM enabled and
an `embed_model` set (e.g. Ollama's `nomic-embed-text`). Fails soft → returns
None, and curiosity falls back to its keyword heuristic.
"""
from __future__ import annotations

import json
import urllib.request
from typing import List, Optional, Sequence


def llm_embed(texts: Sequence[str]) -> Optional[List[list]]:
    from aui.settings import load
    cfg = load()["llm"]
    if not cfg.get("enabled") or not cfg.get("embed_model") or not texts:
        return None
    base = str(cfg["base_url"]).rstrip("/")
    body = json.dumps({"model": cfg["embed_model"], "input": list(texts)}).encode("utf-8")
    headers = {"content-type": "application/json"}
    if cfg.get("api_key"):
        headers["authorization"] = f"Bearer {cfg['api_key']}"
    try:
        req = urllib.request.Request(base + "/embeddings", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        vecs = [d.get("embedding") for d in data.get("data", [])]
        return vecs if all(v for v in vecs) else None
    except Exception:
        return None
