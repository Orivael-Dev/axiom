"""Axiom Inference OS — NGC HTTP server.

Thin FastAPI wrapper around InferenceOS.run() so the control plane
is accessible over HTTP when deployed as an NGC container.

Endpoints:
  POST /v1/infer   — run the 7-layer Axiom pipeline
  GET  /v1/health  — liveness + backend status
  GET  /           — alias for /v1/health
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from axiom_inference_os import InferenceOS, InferenceRequest

app   = FastAPI(title="Axiom Inference OS", version="1.0.0")
_ios  = InferenceOS()
_t0   = time.time()


# ── Request / response models ─────────────────────────────────────────────────

class InferRequest(BaseModel):
    query:      str
    session_id: str  = "default"
    tenant_id:  str  = "default"
    domain:     str | None = None


class InferResponse(BaseModel):
    request_id:       str
    output:           str
    intent_class:     str
    output_verdict:   str
    route:            str
    model_used:       str
    input_tokens:     int
    output_tokens:    int
    tokens_saved:     int
    total_latency_ms: int
    delta_turn:       int
    memory_lod:       int
    shaping_tokens_saved: int
    shaping_transforms:   list[str]
    audit_id:         str
    signature:        str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
@app.get("/v1/health")
def health() -> JSONResponse:
    backend = os.environ.get("AXIOM_BACKEND", "trtllm")
    trtllm_url = os.environ.get("TRTLLM_URL", "http://trtllm:8000/v1")
    return JSONResponse({
        "status":    "ok",
        "uptime_s":  round(time.time() - _t0, 1),
        "backend":   backend,
        "trtllm_url": trtllm_url,
        "model":     os.environ.get("TRTLLM_MODEL", "meta/llama-3.1-8b-instruct"),
    })


@app.post("/v1/infer", response_model=InferResponse)
def infer(req: InferRequest) -> InferResponse:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    result = _ios.run(InferenceRequest(
        query=req.query,
        session_id=req.session_id,
        tenant_id=req.tenant_id,
        domain=req.domain,
    ))

    return InferResponse(
        request_id=result.request_id,
        output=result.output,
        intent_class=result.intent_class,
        output_verdict=result.output_verdict,
        route=result.route,
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        tokens_saved=result.tokens_saved,
        total_latency_ms=result.total_latency_ms,
        delta_turn=result.delta_turn,
        memory_lod=result.memory_lod,
        shaping_tokens_saved=result.shaping_tokens_saved,
        shaping_transforms=list(result.shaping_transforms),
        audit_id=result.audit_id,
        signature=result.signature,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("AXIOM_PORT", 8080)),
        log_level="info",
    )
