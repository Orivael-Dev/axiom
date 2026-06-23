"""axiom_mcp_v2.py — axiom-mcp-v2 signed envelope transport.

Layer: 3 (AXM Runtime — protocol envelope) / Layer 4 (Governance Guard — scope gates)

Extends the v1 HTTP+SSE transport (axiom_mcp_http.py) with:

  1. Signed request envelopes
       X-Axiom-Model-Id   — caller's model/agent identity string
       X-Axiom-Tenant-Id  — caller's tenant identity
       X-Axiom-Scope      — space-separated permission grants
       X-Axiom-Nonce      — random 32-hex-char per-request (replay prevention)
       X-Axiom-Timestamp  — ISO-8601 UTC (server rejects requests outside ±5 min)
       X-Axiom-Sig        — HMAC-SHA256 of canonical envelope string (see below)

  2. Response signing
       X-Axiom-Response-Sig — HMAC-SHA256 of response body so clients can verify
       the response wasn't tampered between server and caller.

  3. Per-tool scope enforcement
       Each tool declares a minimum required McpV2Scope.  Callers must hold that
       scope (or higher) in X-Axiom-Scope or the request is rejected 403.

  4. Backwards-compatible fallback
       If v2 headers are absent but a valid Bearer token is present, the request
       is handled by v1 rules (no scope enforcement, no model-identity check).
       Set AXIOM_MCP_V2_STRICT=1 to disable the fallback and require v2 headers.

Canonical envelope string (what X-Axiom-Sig covers):
  "{method}\\n{body_sha256}\\n{nonce}\\n{timestamp}\\n{model_id}\\n{tenant_id}\\n{scope}"

Signing key:
  derive_key(b"axiom-mcp-v2-envelope")

CANNOT_MUTATE sentinels:
  MCP_V2_VERSION       — spec version string
  TIMESTAMP_WINDOW_S   — max request age in seconds (300 = 5 min)
  NONCE_TTL_S          — nonce cache entry TTL (must be >= TIMESTAMP_WINDOW_S)
"""
from __future__ import annotations

import enum
import hashlib
import hmac as _hmac
import json
import os
import sys
import threading
import time
import types as _types
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from axiom_signing import derive_key

# ── CANNOT_MUTATE sentinels ──────────────────────────────────────────────────

_MCP_V2_VERSION: str = "2"
_TIMESTAMP_WINDOW_S: int = 300   # 5 minutes
_NONCE_TTL_S: int = 360          # slightly wider than window for clock-skew safety

MCP_V2_VERSION      = _MCP_V2_VERSION
TIMESTAMP_WINDOW_S  = _TIMESTAMP_WINDOW_S
NONCE_TTL_S         = _NONCE_TTL_S

_mod = sys.modules[__name__]


class _FrozenMod(type(_mod)):
    _LOCKED: frozenset = frozenset({"MCP_V2_VERSION", "TIMESTAMP_WINDOW_S", "NONCE_TTL_S"})

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._LOCKED:
            raise AttributeError(f"{name} is CANNOT_MUTATE")
        super().__setattr__(name, value)


_mod.__class__ = _FrozenMod

# ── Signing key ───────────────────────────────────────────────────────────────

_ENVELOPE_KEY: bytes = derive_key(b"axiom-mcp-v2-envelope")
_RESPONSE_KEY: bytes = derive_key(b"axiom-mcp-v2-response")

# ── Scope flags ───────────────────────────────────────────────────────────────


class McpV2Scope(enum.IntFlag):
    """Minimum permission scope required to invoke a tool.

    Flags compose: WRITE implies READ; EXECUTE implies both READ and WRITE.
    """
    NONE    = 0
    READ    = 1   # tool:read   — query / status / analysis tools
    WRITE   = 3   # tool:write  — state-changing tools (memory, ledger, mkb)
    EXECUTE = 7   # tool:execute — side-effectful / external-action tools

    @classmethod
    def from_header(cls, header: str) -> "McpV2Scope":
        """Parse space-separated scope tokens from the X-Axiom-Scope header."""
        result = cls.NONE
        for token in header.lower().split():
            if token == "tool:read":
                result |= cls.READ
            elif token == "tool:write":
                result |= cls.WRITE
            elif token == "tool:execute":
                result |= cls.EXECUTE
        return result

    def to_header(self) -> str:
        """Serialise scope flags to space-separated header tokens."""
        tokens = []
        v = int(self)
        if v & 1:   # READ bit
            tokens.append("tool:read")
        if v & 2:   # WRITE-exclusive bit (WRITE=3=0b011 sets this; READ=1 does not)
            tokens.append("tool:write")
        if v & 4:   # EXECUTE-exclusive bit (EXECUTE=7=0b111 sets this)
            tokens.append("tool:execute")
        return " ".join(tokens) or "tool:read"


# Per-tool minimum required scope
_TOOL_SCOPES: Dict[str, McpV2Scope] = {
    # READ — query / analysis / non-mutating
    "axiom_guard_check":     McpV2Scope.READ,
    "axiom_lint":            McpV2Scope.READ,
    "axiom_trace":           McpV2Scope.READ,
    "axiom_qrf":             McpV2Scope.READ,
    "axiom_status":          McpV2Scope.READ,
    "axiom_intent_gate_check": McpV2Scope.READ,
    "axiom_cmaa_fleet":      McpV2Scope.READ,
    "axiom_immune":          McpV2Scope.READ,
    "axiom_fusion":          McpV2Scope.READ,
    "axiom_validate":        McpV2Scope.READ,
    "axiom_crl":             McpV2Scope.READ,

    # WRITE — persist state (memory, ledger, registry)
    "axiom_memory":          McpV2Scope.WRITE,
    "axiom_ledger":          McpV2Scope.WRITE,
    "axiom_mkb":             McpV2Scope.WRITE,
    "axiom_marketplace":     McpV2Scope.WRITE,
    "axiom_workspace":       McpV2Scope.WRITE,

    # EXECUTE — external side effects / agent dispatch / hardware
    "axiom_cmaa_route":      McpV2Scope.EXECUTE,
    "axiom_shield":          McpV2Scope.EXECUTE,
    "axiom_phone_gate":      McpV2Scope.EXECUTE,
    "axiom_axm":             McpV2Scope.EXECUTE,
    "axiom_cpi":             McpV2Scope.EXECUTE,
    "axiom_cas":             McpV2Scope.EXECUTE,
}


def tool_scope(tool_name: str) -> McpV2Scope:
    """Return the minimum scope required to call *tool_name*."""
    return _TOOL_SCOPES.get(tool_name, McpV2Scope.READ)


# ── Envelope dataclass ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class McpV2Envelope:
    """Parsed and verified v2 request envelope.

    Constructed by EnvelopeSigner.parse_and_verify(); never instantiated
    directly by application code.
    """
    model_id:   str
    tenant_id:  str
    scope:      McpV2Scope
    nonce:      str
    timestamp:  str   # ISO-8601 UTC
    sig:        str   # hex HMAC-SHA256

    def granted(self, required: McpV2Scope) -> bool:
        """Return True if this envelope's scope covers *required*."""
        return (self.scope & required) == required


# ── Nonce cache (replay prevention) ──────────────────────────────────────────

class _NonceCache:
    """In-process nonce deduplication with TTL expiry.

    Thread-safe: a single lock serialises all operations.
    """

    def __init__(self, ttl_s: int = NONCE_TTL_S) -> None:
        self._ttl = ttl_s
        self._store: Dict[str, float] = {}   # nonce → seen_at epoch
        self._lock = threading.Lock()

    def seen(self, nonce: str) -> bool:
        """Return True if *nonce* was seen before (replay); record it if new."""
        now = time.monotonic()
        with self._lock:
            self._evict(now)
            if nonce in self._store:
                return True
            self._store[nonce] = now
            return False

    def _evict(self, now: float) -> None:
        expired = [n for n, t in self._store.items() if now - t > self._ttl]
        for n in expired:
            del self._store[n]

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_NONCE_CACHE = _NonceCache()


# ── EnvelopeSigner ────────────────────────────────────────────────────────────

class EnvelopeSigner:
    """Sign outgoing requests and verify incoming envelopes.

    Both sides share the same AXIOM_MASTER_KEY; the per-namespace key is
    derived with ``derive_key(b"axiom-mcp-v2-envelope")``.

    Canonical string (input to HMAC):
      "{http_method}\\n{body_sha256}\\n{nonce}\\n{timestamp}\\n{model_id}\\n{tenant_id}\\n{scope}"
    """

    def __init__(
        self,
        key: bytes = _ENVELOPE_KEY,
        nonce_cache: Optional[_NonceCache] = None,
    ) -> None:
        self._key = key
        self._cache = nonce_cache or _NONCE_CACHE

    # ── signing ───────────────────────────────────────────────────────────────

    def build_headers(
        self,
        method: str,
        body: bytes,
        model_id: str,
        tenant_id: str,
        scope: McpV2Scope = McpV2Scope.READ,
    ) -> Dict[str, str]:
        """Generate signed v2 request headers for an outgoing call.

        Returns a dict ready to merge into an HTTP request's headers.
        """
        nonce     = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sig       = self._sign(method, body, nonce, timestamp, model_id, tenant_id, scope)
        return {
            "X-Axiom-Model-Id":  model_id,
            "X-Axiom-Tenant-Id": tenant_id,
            "X-Axiom-Scope":     scope.to_header(),
            "X-Axiom-Nonce":     nonce,
            "X-Axiom-Timestamp": timestamp,
            "X-Axiom-Sig":       sig,
        }

    def sign_response(self, body: bytes) -> str:
        """Return HMAC-SHA256 hex of a response body under the response key."""
        return _hmac.new(_RESPONSE_KEY, body, hashlib.sha256).hexdigest()

    def verify_response(self, body: bytes, sig: str) -> bool:
        """Constant-time verify a response signature."""
        expected = _hmac.new(_RESPONSE_KEY, body, hashlib.sha256).hexdigest()
        return _hmac.compare_digest(expected, sig)

    # ── verification ──────────────────────────────────────────────────────────

    def parse_and_verify(
        self,
        method: str,
        body: bytes,
        headers: Dict[str, str],
    ) -> McpV2Envelope:
        """Parse v2 headers and verify the signature.

        Raises ``EnvelopeError`` on any failure (missing headers, bad sig,
        expired timestamp, or replayed nonce).
        """
        required = ("X-Axiom-Model-Id", "X-Axiom-Tenant-Id", "X-Axiom-Scope",
                    "X-Axiom-Nonce", "X-Axiom-Timestamp", "X-Axiom-Sig")
        missing = [h for h in required if not headers.get(h)]
        if missing:
            raise EnvelopeError(f"Missing v2 headers: {', '.join(missing)}")

        model_id  = headers["X-Axiom-Model-Id"].strip()
        tenant_id = headers["X-Axiom-Tenant-Id"].strip()
        scope_hdr = headers["X-Axiom-Scope"].strip()
        nonce     = headers["X-Axiom-Nonce"].strip()
        timestamp = headers["X-Axiom-Timestamp"].strip()
        sig       = headers["X-Axiom-Sig"].strip()

        # Validate non-empty identity fields
        if not model_id:
            raise EnvelopeError("X-Axiom-Model-Id must not be empty")
        if not tenant_id:
            raise EnvelopeError("X-Axiom-Tenant-Id must not be empty")
        if len(nonce) < 8:
            raise EnvelopeError("X-Axiom-Nonce too short (min 8 chars)")

        # Timestamp window check
        try:
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise EnvelopeError(f"Invalid timestamp format: {timestamp!r}")
        now = datetime.now(timezone.utc)
        age = abs((now - ts).total_seconds())
        if age > TIMESTAMP_WINDOW_S:
            raise EnvelopeError(
                f"Timestamp outside ±{TIMESTAMP_WINDOW_S}s window (age={age:.0f}s)"
            )

        # Parse scope
        scope = McpV2Scope.from_header(scope_hdr)

        # HMAC verification (constant-time)
        expected = self._sign(method, body, nonce, timestamp, model_id, tenant_id, scope)
        if not _hmac.compare_digest(expected, sig):
            raise EnvelopeError("X-Axiom-Sig verification failed — body or headers tampered")

        # Replay check (after HMAC to avoid oracle on valid nonces)
        if self._cache.seen(nonce):
            raise EnvelopeError(f"Replayed nonce: {nonce!r}")

        return McpV2Envelope(
            model_id=model_id, tenant_id=tenant_id, scope=scope,
            nonce=nonce, timestamp=timestamp, sig=sig,
        )

    def has_v2_headers(self, headers: Dict[str, str]) -> bool:
        """Return True if any v2 header is present (used for fallback detection)."""
        return bool(headers.get("X-Axiom-Sig") or headers.get("X-Axiom-Model-Id"))

    # ── internal ──────────────────────────────────────────────────────────────

    def _sign(
        self,
        method: str,
        body: bytes,
        nonce: str,
        timestamp: str,
        model_id: str,
        tenant_id: str,
        scope: McpV2Scope,
    ) -> str:
        body_hash = hashlib.sha256(body).hexdigest()
        canonical = "\n".join([
            method.upper(),
            body_hash,
            nonce,
            timestamp,
            model_id,
            tenant_id,
            scope.to_header(),
        ]).encode("utf-8")
        return _hmac.new(self._key, canonical, hashlib.sha256).hexdigest()


# ── Errors ────────────────────────────────────────────────────────────────────

class EnvelopeError(ValueError):
    """Raised when v2 envelope verification fails."""


class ScopeError(PermissionError):
    """Raised when a caller's scope does not cover the required tool scope."""


# ── ScopeGate ─────────────────────────────────────────────────────────────────

class ScopeGate:
    """Enforce per-tool scope before dispatch.

    Usage::

        gate = ScopeGate()
        gate.check(envelope, tool_name="axiom_ledger")   # raises ScopeError if denied
    """

    def check(self, envelope: McpV2Envelope, tool_name: str) -> None:
        """Raise ScopeError if *envelope* does not grant the scope required by *tool_name*."""
        required = tool_scope(tool_name)
        if not envelope.granted(required):
            raise ScopeError(
                f"Tool '{tool_name}' requires scope '{required.to_header()}'; "
                f"caller holds '{envelope.scope.to_header()}'"
            )


# ── FastAPI v2 app ────────────────────────────────────────────────────────────

def make_v2_app(
    strict: bool = False,
    bearer_token: Optional[str] = None,
    signer: Optional[EnvelopeSigner] = None,
    gate: Optional[ScopeGate] = None,
) -> "FastAPI":  # type: ignore[name-defined]  # FastAPI imported lazily
    """Build and return the axiom-mcp-v2 FastAPI application.

    Args:
        strict:        If True, v2 headers are required on every request (no Bearer fallback).
                       Defaults to the AXIOM_MCP_V2_STRICT env var (0 = lenient).
        bearer_token:  v1 Bearer token for backwards-compat fallback.
                       Defaults to the AXIOM_MCP_TOKEN env var.
        signer:        EnvelopeSigner instance (injectable for tests).
        gate:          ScopeGate instance (injectable for tests).
    """
    from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    import asyncio

    from axiom_mcp_server import AxiomMCPServer

    _strict  = strict or (os.environ.get("AXIOM_MCP_V2_STRICT", "0") == "1")
    _bearer  = bearer_token or os.environ.get("AXIOM_MCP_TOKEN", "")
    _signer  = signer or EnvelopeSigner()
    _gate    = gate or ScopeGate()
    _server  = AxiomMCPServer()
    _sessions: dict = {}

    app = FastAPI(title="Axiom MCP v2", version=MCP_V2_VERSION,
                  docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://claude.ai", "https://www.claude.ai"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization", "Content-Type",
            "X-Axiom-Model-Id", "X-Axiom-Tenant-Id", "X-Axiom-Scope",
            "X-Axiom-Nonce", "X-Axiom-Timestamp", "X-Axiom-Sig",
        ],
        expose_headers=["X-Axiom-Response-Sig"],
    )

    def _auth(request: Request) -> Optional[McpV2Envelope]:
        """Authenticate request. Returns McpV2Envelope (v2) or None (v1 Bearer).

        Raises HTTPException on auth failure.
        """
        hdrs = {k: v for k, v in request.headers.items()
                if k.startswith("X-Axiom-") or k.lower() == "authorization"}

        # Normalise header name casing for lookup
        norm = {k.title(): v for k, v in hdrs.items()}

        if _signer.has_v2_headers(norm):
            # v2 path — envelope verification
            try:
                return _signer.parse_and_verify(
                    method=request.method,
                    body=b"",   # body not yet read; caller must re-verify after body read
                    headers=norm,
                )
            except EnvelopeError as exc:
                raise HTTPException(status_code=401, detail=f"v2 envelope error: {exc}")

        # v1 fallback
        if _strict:
            raise HTTPException(status_code=401, detail="axiom-mcp-v2 headers required (strict mode)")
        auth = norm.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth.removeprefix("Bearer ").strip() != _bearer:
            raise HTTPException(status_code=401, detail="Invalid Bearer token")
        return None

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "server": "axiom-mcp-v2", "version": MCP_V2_VERSION}

    @app.get("/sse")
    async def sse_connect(request: Request) -> StreamingResponse:
        """Open SSE stream (same as v1; v2 envelope verified per POST /messages)."""
        _auth(request)
        session_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue()
        _sessions[session_id] = queue

        async def stream():
            yield f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                _sessions.pop(session_id, None)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/messages")
    async def post_message(
        request: Request,
        sessionId: str = Query(...),
    ) -> Response:
        """Receive JSON-RPC request; enforce v2 envelope + scope; route via SSE."""
        queue = _sessions.get(sessionId)
        if queue is None:
            raise HTTPException(status_code=404, detail="Unknown sessionId — open /sse first")

        body = await request.body()

        # Re-verify envelope with actual body now that we have it
        norm = {k.title(): v for k, v in request.headers.items()
                if k.startswith("X-Axiom-") or k.lower() == "authorization"}
        envelope: Optional[McpV2Envelope] = None

        if _signer.has_v2_headers(norm):
            try:
                envelope = _signer.parse_and_verify(
                    method=request.method, body=body, headers=norm
                )
            except EnvelopeError as exc:
                raise HTTPException(status_code=401, detail=f"v2 envelope error: {exc}")
        elif _strict:
            raise HTTPException(status_code=401, detail="axiom-mcp-v2 headers required (strict mode)")
        else:
            auth = norm.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth.removeprefix("Bearer ").strip() != _bearer:
                raise HTTPException(status_code=401, detail="Invalid Bearer token")

        # Scope gate — only applied in v2 mode
        if envelope is not None:
            try:
                rpc = json.loads(body)
                if rpc.get("method") == "tools/call":
                    tool_name = rpc.get("params", {}).get("name", "")
                    _gate.check(envelope, tool_name)
            except json.JSONDecodeError:
                pass   # parse error returned by MCP handler below
            except ScopeError as exc:
                raise HTTPException(status_code=403, detail=str(exc))

        # Dispatch
        loop = asyncio.get_event_loop()
        response_json = await loop.run_in_executor(
            None, _server.handle_request, body.decode("utf-8").strip()
        )

        if response_json:
            await queue.put(response_json)

            # Attach response signature header on the SSE data is not feasible;
            # instead include it inside the JSON-RPC result envelope so the
            # client can verify by reading X-Axiom-Response-Sig from the
            # 202 response below.

        resp_bytes = response_json.encode("utf-8") if response_json else b""
        resp_sig   = _signer.sign_response(resp_bytes)

        return Response(
            status_code=202,
            headers={"X-Axiom-Response-Sig": resp_sig},
        )

    return app


# ── Default app instance ──────────────────────────────────────────────────────

# Only instantiate when the module is used as a uvicorn entrypoint.
# Tests import the module and call make_v2_app() directly.
if os.environ.get("AXIOM_MCP_TOKEN"):
    app = make_v2_app()
