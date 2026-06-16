"""
HTTP/SSE transport wrapper for AxiomMCPServer.

Exposes the stdio JSON-RPC MCP server over HTTP with the standard
MCP HTTP+SSE transport so Claude Code on the web or mobile can connect.

Transport (MCP spec 2024-11-05):
  GET  /sse                    — open SSE stream; server sends session endpoint
  POST /messages?sessionId=X   — send JSON-RPC request; response via SSE
  GET  /healthz                — liveness probe

Auth: Bearer token via AXIOM_MCP_TOKEN env var.
      If unset the server refuses all connections (fail-closed).

Usage:
  AXIOM_MASTER_KEY=xxx AXIOM_MCP_TOKEN=yyy \\
      uvicorn axiom_mcp_http:app --host 0.0.0.0 --port 8006
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import AsyncGenerator

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from axiom_mcp_server import AxiomMCPServer

# ── Auth ────────────────────────────────────────────────────────────────────

_TOKEN = os.environ.get("AXIOM_MCP_TOKEN", "")
if not _TOKEN:
    import sys
    print("FATAL: AXIOM_MCP_TOKEN is not set. Refusing to start.", file=sys.stderr)
    sys.exit(1)

def _check_bearer(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ").strip() != _TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# ── Session registry ─────────────────────────────────────────────────────────
# Each SSE connection gets a queue. POST /messages puts a response on the
# queue; the SSE generator drains it.

_sessions: dict[str, asyncio.Queue] = {}
_server = AxiomMCPServer()

# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Axiom MCP HTTP", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://claude.ai", "https://www.claude.ai"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "server": "axiom-mcp-http"}


@app.get("/sse")
async def sse_connect(
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Open an SSE stream. Client receives its session endpoint then waits for responses."""
    _check_bearer(authorization)
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sessions[session_id] = queue

    async def event_stream() -> AsyncGenerator[str, None]:
        # Tell the client which URL to POST requests to
        endpoint = f"/messages?sessionId={session_id}"
        yield f"event: endpoint\ndata: {endpoint}\n\n"
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive ping
                    yield ": ping\n\n"
        finally:
            _sessions.pop(session_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tell nginx/Caddy not to buffer
        },
    )


@app.post("/messages")
async def post_message(
    request: Request,
    sessionId: str = Query(...),
    authorization: str | None = Header(default=None),
) -> Response:
    """Receive a JSON-RPC request, process it, send response via the SSE stream."""
    _check_bearer(authorization)

    queue = _sessions.get(sessionId)
    if queue is None:
        raise HTTPException(status_code=404, detail="Unknown sessionId — open /sse first")

    body = await request.body()
    line = body.decode("utf-8").strip()

    # Run the blocking MCP handler in a thread so we don't stall the event loop
    loop = asyncio.get_event_loop()
    response_json = await loop.run_in_executor(None, _server.handle_request, line)

    if response_json:
        await queue.put(response_json)

    return Response(status_code=202)
