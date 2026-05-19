"""Research console HTTP server — wires web/research_console.html to a
live ExoskeletonAgent so the page is testable end-to-end in a browser.

Routes:
  GET  /                  — serves web/research_console.html
  GET  /api/health        — liveness + backend info
  GET  /api/use-cases     — list of exoskeleton workflow names
  POST /api/research      — real run; returns shape consumed by runResearch()
  GET  /api/ledger        — recent ledger entries (limit query-param)

Run locally:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    # Defaults to LocalNanoBackend pointing at OLLAMA_URL (or 127.0.0.1:11434).
    python3 -m axiom_research_server                    # → http://127.0.0.1:8765
    # Force NIM:
    NVIDIA_NIM_API_KEY=nvapi-... AXIOM_BACKEND=nim python3 -m axiom_research_server

Bind / port:
    AXIOM_RESEARCH_HOST  default 127.0.0.1
    AXIOM_RESEARCH_PORT  default 8765

By default binds to 127.0.0.1 only — exposed to other hosts only when
the deployer explicitly sets AXIOM_RESEARCH_HOST. Bearer-token auth
(AXIOM_RESEARCH_TOKEN) activates a require-bearer middleware identical
to axiom_server.py's pattern, with /api/health public.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field


LOG = logging.getLogger("axiom.research_server")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

REPO_ROOT  = Path(__file__).resolve().parent
HTML_PATH  = REPO_ROOT / "web" / "research_console.html"

# Workflow names not in the exoskeleton pack — these get a default
# "general research" path that uses the customer_discovery delegate
# (its JSON output shape generalizes well).
_WORKFLOW_ALIASES = {
    "general_research": "customer_discovery",
}

_DOMAIN_LABELS = {
    "general":      "General",
    "medical":      "Medical",
    "finance":      "Finance",
    "security":     "Security",
    "hr":           "HR",
    "supply_chain": "Supply Chain",
}

_WORKFLOW_LABELS = {
    "general_research":     "General Research",
    "investor_research":    "Investor Research",
    "enterprise_targeting": "Enterprise Targeting",
    "outreach_personalization": "Outreach Personalization",
    "demo_scripts":         "Demo Scripts",
    "sales_objection_handling": "Sales Objection Handling",
    "competitive_analysis": "Competitive Analysis",
    "grant_application":    "Grant Application",
    "patent_counsel_packet":"Patent Packet",
    "customer_discovery":   "Customer Discovery",
}


# ── App-wide state (built lazily on first request to avoid blocking import) ─

class _ServerState:
    def __init__(self) -> None:
        self.exo            = None      # ExoskeletonAgent
        self.ledger_path    = None      # Path
        self.backend_label  = "unknown"
        self.pack_origin    = "(unbuilt)"

    def ensure(self) -> None:
        if self.exo is not None:
            return
        from axiom_event_token.backends import default_backend
        from axiom_exoskeleton import ExoskeletonAgent
        from axiom_exoskeleton_ledger import LedgerWriter, default_ledger_path

        ledger_path = Path(os.environ.get(
            "AXIOM_EXOSKELETON_LEDGER",
            str(default_ledger_path()),
        )).expanduser()
        ledger = LedgerWriter(ledger_path)

        backend = default_backend()
        exo = ExoskeletonAgent.from_default_pack(backend=backend, ledger=ledger)

        self.exo            = exo
        self.ledger_path    = ledger_path
        self.backend_label  = f"{backend.name} · {backend.model}"
        self.pack_origin    = "default-pack (built in tempdir on first request)"
        LOG.info("research server ready: backend=%s ledger=%s",
                 self.backend_label, ledger_path)


_state = _ServerState()


# ── Request / response shapes ────────────────────────────────────────────


class ResearchRequest(BaseModel):
    query:    str = Field(..., min_length=1, max_length=4000)
    domain:   str = "general"
    workflow: str = "general_research"


# ── App + middleware ─────────────────────────────────────────────────────


app = FastAPI(
    title="AXIOM Re:Search Engine",
    description="Live wiring for web/research_console.html — Exoskeleton "
                "delegate invocation, signed EventToken, signed ledger.",
    version="0.1.0",
)

# CORS off by default. Set AXIOM_RESEARCH_CORS_ORIGINS to a CSV (or "*")
# when the page is served from a different origin than the API.
_cors_raw = os.environ.get("AXIOM_RESEARCH_CORS_ORIGINS", "").strip()
_cors_origins = ([o.strip() for o in _cors_raw.split(",") if o.strip()]
                 if _cors_raw else [])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# Optional bearer-token auth — same shape as axiom_server.py.
_API_TOKEN = os.environ.get("AXIOM_RESEARCH_TOKEN", "").strip()
_PUBLIC_PATHS = {"/api/health", "/openapi.json", "/docs", "/redoc"}

if _API_TOKEN:
    @app.middleware("http")
    async def require_bearer(request: Request, call_next):
        path = request.url.path
        if path == "/" or path in _PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        if (not header.startswith(prefix)
                or not hmac.compare_digest(header[len(prefix):], _API_TOKEN)):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)
else:
    LOG.warning(
        "AXIOM_RESEARCH_TOKEN not set — server is unauthenticated. "
        "Bind to 127.0.0.1 only or set AXIOM_RESEARCH_TOKEN before exposing."
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _split_into_findings(text: str, *, max_items: int = 6) -> List[str]:
    """Best-effort parse of a delegate's output into bullet items."""
    if not text:
        return []
    # JSON pass — if the delegate returned a JSON object, harvest list-valued fields.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            picks: List[str] = []
            for v in obj.values():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item.strip():
                            picks.append(item.strip())
                elif isinstance(v, str) and v.strip():
                    picks.append(v.strip())
            if picks:
                return picks[:max_items]
    except (ValueError, TypeError):
        pass
    # Plain-text pass — split on newlines / numbered bullets.
    lines = [l.strip(" -•*\t") for l in re.split(r"[\r\n]+", text) if l.strip()]
    return [l for l in lines if l][:max_items]


def _short_tldr(text: str, *, max_chars: int = 320) -> str:
    """First non-empty line, or the first paragraph; truncated."""
    if not text:
        return ""
    text = text.strip()
    # If JSON object, use the first non-list value as the gist.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for k in ("tldr", "summary", "response", "outreach_angle",
                      "their_strength", "core_claim", "pain_articulated"):
                if isinstance(obj.get(k), str):
                    return obj[k][:max_chars]
            for v in obj.values():
                if isinstance(v, str) and v.strip():
                    return v.strip()[:max_chars]
    except (ValueError, TypeError):
        pass
    first = re.split(r"[\r\n]{2,}", text)[0]
    return first[:max_chars]


def _stub_sources(query: str, domain: str) -> List[dict]:
    """Deterministic source stubs derived from the query, so the page is
    populated without a real retriever. CLEARLY labeled as stubs in the
    response so callers don't mistake them for live retrieval."""
    base = [
        {
            "title":   "AXIOM event-token primary spec",
            "uri":     "docs/training/event-token-spec.md",
            "kind":    "internal-doc · STUB",
            "score":   0.92,
            "snippet": "Selective activation runs ONLY the agents a query "
                       "needs; null layers are first-class.",
        },
        {
            "title":   "ORVL-016 Provisional Patent",
            "uri":     "patents/ORVL016_AXM.pdf",
            "kind":    "patent-pdf · STUB",
            "score":   0.88,
            "snippet": "Per-layer + coordinator + outer HMAC signatures "
                       "provide composition integrity end-to-end.",
        },
        {
            "title":   f"Domain reference: {_DOMAIN_LABELS.get(domain, domain)}",
            "uri":     f"external/{domain}-reference-2026.html",
            "kind":    "external-web · STUB",
            "score":   0.71,
            "snippet": f"Reference material for the {domain} domain. "
                       f"Retrieval is stubbed in this build — wire a real "
                       f"retriever to replace this entry.",
        },
        {
            "title":   "Query echo (debug)",
            "uri":     "internal/debug-echo",
            "kind":    "debug · STUB",
            "score":   0.42,
            "snippet": f'You asked: "{query[:140]}"',
        },
    ]
    return base


def _stub_branches(query: str, domain: str, top_conf: float) -> dict:
    """Query-derived deterministic branches so the QRF column is populated
    without invoking the live LatentEngine. Clearly labeled as stubs."""
    top = max(0.55, min(0.85, top_conf))
    rival = max(0.45, top - 0.15)
    risk  = max(0.40, top - 0.20)
    kill  = 0.18
    branches = [
        {"id": "Branch 01", "title": "Primary Synthesis Path",
         "probability": top, "status": "passed", "distance": 0.28, "citations": 3,
         "summary": "Strongest path. Synthesizes the answer while staying "
                    "inside confidence + constitutional limits.",
         "detail": ("Status: PASSED  (STUB)\nGate: monotonic confidence held\n"
                    f"Constitutional distance: 0.28\nCitations: 3\n"
                    f"Signature: stub_sig_branch_01\n\nQuery: {query[:200]}"),
         },
        {"id": "Branch 02", "title": "Rival Hypothesis",
         "probability": rival, "status": "rival", "distance": 0.39, "citations": 2,
         "summary": "Competing interpretation. Kept visible so the report "
                    "shows uncertainty rather than false certainty.",
         "detail": ("Status: RIVAL  (STUB)\nGate: allowed as alternate path\n"
                    "Constitutional distance: 0.39\nCitations: 2\n"
                    "Signature: stub_sig_branch_02"),
         },
        {"id": "Branch 03", "title": f"{_DOMAIN_LABELS.get(domain, 'Domain')} Risk View",
         "probability": risk, "status": "passed", "distance": 0.42, "citations": 2,
         "summary": f"Frames the result through the {domain} domain's "
                    f"adoption, compliance, and audit concerns.",
         "detail": ("Status: PASSED  (STUB)\nGate: domain framing accepted\n"
                    "Constitutional distance: 0.42\nCitations: 2\n"
                    "Signature: stub_sig_branch_03"),
         },
        {"id": "Branch 04", "title": "Overclaim Path",
         "probability": kill, "status": "killed", "distance": 0.91, "citations": 0,
         "summary": "Rejected because it overstates certainty and lacks "
                    "evidence support.",
         "detail": ("Status: KILLED  (STUB)\nGate: overclaim risk\n"
                    "Constitutional distance: 0.91\nCitations: 0\n"
                    "Kill record: stub_kill_04\nPreserved for audit; "
                    "excluded from the report."),
         },
    ]
    passed = sum(1 for b in branches if b["status"] != "killed")
    return {
        "probability_band":       top,
        "constitutional_distance": 0.31,
        "branch_health":           f"{passed} / {len(branches)} passed",
        "branches":                branches,
    }


# ── Routes ──────────────────────────────────────────────────────────────


@app.get("/")
async def root():
    if not HTML_PATH.exists():
        raise HTTPException(status_code=500,
                             detail=f"missing {HTML_PATH}")
    return FileResponse(HTML_PATH, media_type="text/html")


@app.get("/api/health")
async def health():
    state_built = _state.exo is not None
    return {
        "ok":              True,
        "service":         "axiom-research-server",
        "state_built":     state_built,
        "backend":         _state.backend_label if state_built else "(not yet built)",
        "pack":            _state.pack_origin,
        "ledger_path":     str(_state.ledger_path) if _state.ledger_path else None,
        "html_path":       str(HTML_PATH),
        "html_present":    HTML_PATH.exists(),
        "bearer_auth":     bool(_API_TOKEN),
    }


@app.get("/api/use-cases")
async def list_use_cases():
    _state.ensure()
    real = list(_state.exo.use_cases())
    aliased = list(_WORKFLOW_ALIASES.keys())
    return {
        "real_delegates":  real,
        "aliases":         _WORKFLOW_ALIASES,
        "all_workflows":   sorted(set(real + aliased)),
    }


@app.post("/api/research")
async def research(req: ResearchRequest):
    _state.ensure()
    exo = _state.exo

    # Resolve workflow name → real delegate (alias-aware).
    delegate_name = _WORKFLOW_ALIASES.get(req.workflow, req.workflow)
    if delegate_name not in exo.use_cases():
        raise HTTPException(
            status_code=400,
            detail=f"unknown workflow: {req.workflow}. Try "
                   f"{sorted(exo.use_cases())}",
        )

    t0 = time.monotonic()
    try:
        token = exo.invoke(delegate_name, req.query)
    except Exception as e:
        LOG.exception("exoskeleton invoke failed")
        raise HTTPException(status_code=502,
                             detail=f"delegate run failed: {e}")
    wall_ms = int((time.monotonic() - t0) * 1000)

    if token.text is None:
        raise HTTPException(status_code=502,
                             detail="delegate produced no output layer")

    p = token.text.payload
    output_text = p.get("output", "") or ""
    findings = _split_into_findings(output_text)
    tldr = _short_tldr(output_text)

    qrf = _stub_branches(req.query, req.domain,
                          top_conf=0.74 if p.get("output_tokens", 0) > 0 else 0.55)

    return {
        "query":          req.query,
        "workflow":       req.workflow,
        "workflowLabel":  _WORKFLOW_LABELS.get(req.workflow, req.workflow),
        "domain":         req.domain,
        "domainLabel":    _DOMAIN_LABELS.get(req.domain, req.domain),

        "report": {
            "tldr":          tldr or "(delegate returned empty output)",
            "keyFindings":   findings or [
                "(no structured findings could be parsed from delegate output)",
            ],
            "openQuestions": [
                "Is the chosen workflow the right one for this query?",
                "Should retrieval be wired to real sources (currently stubbed)?",
            ],
            "raw_output":    output_text,
        },

        "probabilityBand":         qrf["probability_band"],
        "constitutionalDistance":  qrf["constitutional_distance"],
        "branchHealth":            qrf["branch_health"],

        "sources":  _stub_sources(req.query, req.domain),
        "branches": qrf["branches"],

        "receipt": {
            "token_id":  token.id,
            "workflow":  _WORKFLOW_LABELS.get(req.workflow, req.workflow),
            "backend":   f"{p.get('backend', '?')} · {p.get('model', '?')}",
            "signed_at": (token.created_at or "").rstrip("Z") + "Z"
                          if token.created_at else "",
            "verified":  bool(token.verify()),
            "ledger":    str(_state.ledger_path) + "  (+1 entry)"
                          if _state.ledger_path else "(none)",
        },
        "cost": {
            "input_tokens":  int(p.get("input_tokens", 0)),
            "output_tokens": int(p.get("output_tokens", 0)),
            "latency_ms":    int(p.get("latency_ms", wall_ms)),
        },
        "_meta": {
            "wall_clock_ms":           wall_ms,
            "delegate_invoked":        delegate_name,
            "sources_are_stubbed":     True,
            "branches_are_stubbed":    True,
            "synthesis_is_real":       True,
            "ledger_write":            "appended",
        },
    }


@app.get("/api/ledger")
async def ledger(limit: int = 20):
    from axiom_exoskeleton_ledger import query_ledger
    _state.ensure()
    entries = query_ledger(path=_state.ledger_path, limit=max(1, min(1000, limit)))
    return {
        "ledger_path": str(_state.ledger_path),
        "count":       len(entries),
        "entries":     [e.to_dict() for e in entries],
    }


# ── Entry point ─────────────────────────────────────────────────────────


def main(argv=None) -> int:
    import argparse, uvicorn
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=os.environ.get(
        "AXIOM_RESEARCH_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get(
        "AXIOM_RESEARCH_PORT", "8765")))
    ap.add_argument("--reload", action="store_true",
                    help="autoreload on file change (dev only)")
    args = ap.parse_args(argv)

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY required (export a 32-byte hex value).",
              file=sys.stderr)
        return 2

    LOG.info("starting research console: http://%s:%d", args.host, args.port)
    LOG.info("HTML: %s (present=%s)", HTML_PATH, HTML_PATH.exists())
    uvicorn.run("axiom_research_server:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
