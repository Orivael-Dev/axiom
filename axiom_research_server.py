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
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, StreamingResponse,
)
from pydantic import BaseModel, Field


LOG = logging.getLogger("axiom.research_server")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

REPO_ROOT  = Path(__file__).resolve().parent
HTML_PATH  = REPO_ROOT / "web" / "research_console.html"
LEDGER_HTML_PATH = REPO_ROOT / "web" / "ledger_viewer.html"
HELP_MD_PATH = REPO_ROOT / "docs" / "research_engine.md"

# UI domain → QRFEngine domain. QRF supports five; "general" stays stubbed.
_DOMAIN_TO_QRF = {
    "medical":      "medical",
    "finance":      "financial",
    "security":     "security",
    "hr":           "hr",
    "supply_chain": "supply_chain",
}

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
    "code_generation":      "Code Generation",
    "test_generation":      "Test Generation",
}


# ── App-wide state (built lazily on first request to avoid blocking import) ─

class _ServerState:
    def __init__(self) -> None:
        self.exo            = None      # ExoskeletonAgent
        self.ledger_path    = None      # Path
        self.backend_label  = "unknown"
        self.pack_origin    = "(unbuilt)"
        self.retriever      = None      # LocalRetriever
        self._qrf_cache     = {}        # domain → QRFEngine

    def ensure(self) -> None:
        if self.exo is not None:
            return
        from axiom_event_token.backends import default_backend
        from axiom_exoskeleton import ExoskeletonAgent
        from axiom_exoskeleton_ledger import LedgerWriter, default_ledger_path
        from axiom_research_retriever import default_retriever

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
        self.retriever      = default_retriever()
        LOG.info("research server ready: backend=%s ledger=%s",
                 self.backend_label, ledger_path)

    def qrf_for(self, domain: str):
        """Return a cached QRFEngine for `domain`, or None if unsupported."""
        qrf_domain = _DOMAIN_TO_QRF.get(domain)
        if qrf_domain is None:
            return None
        if qrf_domain not in self._qrf_cache:
            from axiom_qrf import QRFEngine
            from axiom_signing import derive_key
            self._qrf_cache[qrf_domain] = QRFEngine(
                domain=qrf_domain,
                hmac_key=derive_key(b"axiom-research-qrf-v1"),
            )
        return self._qrf_cache[qrf_domain]


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


def _retrieve_sources(query: str, domain: str, *, k: int = 5) -> tuple[list[dict], bool]:
    """Run the live LocalRetriever. Returns (sources, is_real).

    Falls back to a single STUB entry only if the retriever is missing
    or returns no hits — keeping the UI populated so the user can see
    what happened.
    """
    if _state.retriever is None:
        return _fallback_source_stubs(query, domain), False
    try:
        # `domain` is honoured by DomainRoutedRetriever (per-corpus
        # dispatch) and ignored by plain LocalRetriever — so the call
        # site is uniform regardless of which retriever is wired.
        hits = _state.retriever.retrieve(query, k=k, domain=domain)
    except Exception as e:
        LOG.warning("retriever raised: %s", e)
        hits = []
    if not hits:
        return _fallback_source_stubs(query, domain), False
    return [h.to_dict() for h in hits], True


def _fallback_source_stubs(query: str, domain: str) -> list[dict]:
    return [
        {
            "title":   f"No local matches · {_DOMAIN_LABELS.get(domain, domain)}",
            "uri":     "internal/no-retrieval-hit",
            "kind":    "stub · no-hit",
            "score":   0.0,
            "snippet": f'Retriever found no matches for "{query[:140]}". '
                        f"Add more docs under ./docs or wire a remote "
                        f"retriever to populate this column.",
        },
    ]


def _qrf_branches(query: str, domain: str, *, workflow_label: str) -> tuple[dict, bool]:
    """Real QRF for supported domains; stub for unsupported ('general')."""
    engine = _state.qrf_for(domain)
    if engine is None:
        return _stub_branches(query, domain, top_conf=0.74), False
    prompt = f"[{workflow_label}] {query}"
    try:
        result = engine.forecast(prompt)
    except Exception as e:
        LOG.warning("QRF.forecast raised: %s", e)
        return _stub_branches(query, domain, top_conf=0.55), False

    live = [b for b in result.branches if b.get("score", 0.0) > 0.0]
    branches: list[dict] = []
    for idx, b in enumerate(result.branches):
        score = float(b.get("score", 0.0))
        prob  = float(b.get("probability_weight", 0.0))
        metrics = b.get("metrics") or {}
        # Constitutional distance proxy: 1 - safety (clipped).
        safety = float(metrics.get("safety", 1.0))
        distance = round(max(0.0, min(1.0, 1.0 - safety)), 2)
        is_killed = score == 0.0
        if is_killed:
            status = "killed"
        elif idx == 0:
            status = "passed"
        elif idx == 1 and len(live) > 1:
            status = "rival"
        else:
            status = "passed"
        response_text = (b.get("response") or "").strip()
        summary = (response_text[:240] + "…") if len(response_text) > 240 else response_text
        detail_lines = [
            f"Status: {status.upper()}",
            f"QRF branch: {b.get('branch', '?')}",
            f"Score: {score}",
            f"Probability weight: {prob}",
            f"Metrics: {metrics}",
            f"Constitutional distance (1 - safety): {distance}",
            f"Signature: {result.hmac_signature[:16]}…",
            "",
            response_text,
        ]
        branches.append({
            "id":           f"Branch {idx + 1:02d}",
            "title":        b.get("branch", f"Branch {idx + 1}"),
            "probability":  round(prob, 4),
            "status":       status,
            "distance":     distance,
            "citations":    0,
            "summary":      summary or "(no response)",
            "detail":       "\n".join(detail_lines),
        })
    passed = sum(1 for b in branches if b["status"] != "killed")
    return {
        "probability_band":       _band_to_float(result.probability_band, live),
        "constitutional_distance": round(
            sum(b["distance"] for b in branches) / max(1, len(branches)), 2),
        "branch_health":           f"{passed} / {len(branches)} passed",
        "branches":                branches,
        "_qrf_signature":          result.hmac_signature,
        "_qrf_top_branch":         result.top_branch,
        "_qrf_band":               result.probability_band,
    }, True


def _band_to_float(band: str, live: list) -> float:
    """Convert QRF's symbolic band into a 0..1 float for the UI metric."""
    if live:
        return round(float(live[0].get("probability_weight", 0.0)), 4)
    return {"HIGH": 0.55, "MODERATE": 0.40,
            "LOW":  0.20, "UNCERTAIN": 0.10}.get(band, 0.0)


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

    return _run_research(req, delegate_name)


def _run_research(req: "ResearchRequest", delegate_name: str) -> dict:
    """Shared research pipeline used by both /api/research and the
    SSE endpoint. Always synchronous; SSE just emits stage events
    around the same call."""
    exo = _state.exo

    # Stage 1: retrieve
    sources, retrieval_real = _retrieve_sources(req.query, req.domain)

    # Stage 2: QRF branch reasoning (real for supported domains)
    workflow_label = _WORKFLOW_LABELS.get(req.workflow, req.workflow)
    qrf, qrf_real = _qrf_branches(req.query, req.domain,
                                   workflow_label=workflow_label)

    # Stage 3: synthesize via the exoskeleton delegate. The
    # `domain_context` sets a request-scoped contextvar so any
    # DomainRoutedBackend in the chain dispatches to the per-domain
    # LLM (e.g. AXIOM_BACKEND_MEDICAL). No-op for the plain default
    # backend — falls through harmlessly.
    from axiom_event_token.backends import domain_context

    t0 = time.monotonic()
    try:
        with domain_context(req.domain):
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

    return {
        "query":          req.query,
        "workflow":       req.workflow,
        "workflowLabel":  workflow_label,
        "domain":         req.domain,
        "domainLabel":    _DOMAIN_LABELS.get(req.domain, req.domain),

        "report": {
            "tldr":          tldr or "(delegate returned empty output)",
            "keyFindings":   findings or [
                "(no structured findings could be parsed from delegate output)",
            ],
            "openQuestions": [
                "Is the chosen workflow the right one for this query?",
                "Is your retrieval corpus broad enough to support this question?",
            ],
            "raw_output":    output_text,
        },

        "probabilityBand":         qrf["probability_band"],
        "constitutionalDistance":  qrf["constitutional_distance"],
        "branchHealth":            qrf["branch_health"],

        "sources":  sources,
        "branches": qrf["branches"],

        "receipt": {
            "token_id":  token.id,
            "workflow":  workflow_label,
            "backend":   f"{p.get('backend', '?')} · {p.get('model', '?')}",
            "signed_at": (token.created_at or "").rstrip("Z") + "Z"
                          if token.created_at else "",
            "verified":  bool(token.verify()),
            "ledger":    str(_state.ledger_path) + "  (+1 entry)"
                          if _state.ledger_path else "(none)",
            "qrf_signature": qrf.get("_qrf_signature", "")[:24] if qrf_real else "",
        },
        "cost": {
            "input_tokens":  int(p.get("input_tokens", 0)),
            "output_tokens": int(p.get("output_tokens", 0)),
            "latency_ms":    int(p.get("latency_ms", wall_ms)),
        },
        "_meta": {
            "wall_clock_ms":           wall_ms,
            "delegate_invoked":        delegate_name,
            "sources_are_stubbed":     not retrieval_real,
            "branches_are_stubbed":    not qrf_real,
            "synthesis_is_real":       True,
            "retriever_indexed_files": (_state.retriever.stats().get("indexed_files", 0)
                                          if _state.retriever else 0),
            "ledger_write":            "appended",
        },
    }


@app.post("/api/research/stream")
async def research_stream(req: ResearchRequest):
    """Server-sent events variant of /api/research.

    Emits per-stage `event:` lines so the UI can show real progress
    (retrieve → branch → synthesize → done). The final `result` event
    carries the same payload as the synchronous endpoint."""
    _state.ensure()
    exo = _state.exo
    delegate_name = _WORKFLOW_ALIASES.get(req.workflow, req.workflow)
    if delegate_name not in exo.use_cases():
        raise HTTPException(
            status_code=400,
            detail=f"unknown workflow: {req.workflow}.",
        )

    def _sse(event: str, data: Any) -> bytes:
        return (f"event: {event}\n"
                f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")

    def _generator():
        try:
            yield _sse("stage", {"name": "retrieve", "message": "Searching local index…"})
            sources, retrieval_real = _retrieve_sources(req.query, req.domain)
            yield _sse("partial", {"sources_count": len(sources),
                                    "retrieval_real": retrieval_real})

            yield _sse("stage", {"name": "branch", "message": "Running QRF branches…"})
            workflow_label = _WORKFLOW_LABELS.get(req.workflow, req.workflow)
            qrf, qrf_real = _qrf_branches(req.query, req.domain,
                                           workflow_label=workflow_label)
            yield _sse("partial", {"branch_count": len(qrf["branches"]),
                                    "branches_real": qrf_real,
                                    "probability_band": qrf["probability_band"]})

            yield _sse("stage", {"name": "synthesize",
                                  "message": f"Invoking {delegate_name}…"})
            result = _run_research(req, delegate_name)
            yield _sse("result", result)
            yield _sse("done", {"ok": True})
        except HTTPException as e:
            yield _sse("error", {"status": e.status_code, "detail": e.detail})
        except Exception as e:
            LOG.exception("stream pipeline failed")
            yield _sse("error", {"status": 500, "detail": str(e)})

    return StreamingResponse(_generator(),
                              media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                        "X-Accel-Buffering": "no"})


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


@app.get("/api/runs")
async def runs(limit: int = 15):
    """Unified recent-runs list for the resume picker.

    Merges exoskeleton + medical ledgers, sorted by timestamp desc.
    Neither ledger needs the state to be built — they're readable at
    any time as long as the files exist on disk.
    """
    from axiom_exoskeleton_ledger import query_ledger as exo_q
    from axiom_medical_ledger import (
        query_ledger as med_q,
        default_ledger_path as med_default,
    )
    cap = max(1, min(50, limit))
    out: list[dict] = []
    try:
        exo_path = _state.ledger_path  # may be None if state not yet built
        for e in exo_q(path=exo_path, limit=cap):
            out.append({
                "kind":          "exoskeleton",
                "token_id":      e.token_id,
                "question":      e.input_excerpt,
                "timestamp_utc": e.timestamp_utc,
                "backend":       e.backend,
                "model":         e.model,
                "verified":      bool(e.verified),
            })
    except Exception:
        pass
    try:
        for e in med_q(limit=cap):
            out.append({
                "kind":          "medical",
                "token_id":      e.coordinator_token_id,
                "question":      e.research_question,
                "timestamp_utc": e.timestamp_utc,
                "backend":       "medical-research",
                "model":         e.profile,
                "verified":      bool(e.verified),
            })
    except Exception:
        pass
    out.sort(key=lambda r: r["timestamp_utc"], reverse=True)
    return {"runs": out[:cap]}


@app.get("/ledger")
async def ledger_viewer():
    if not LEDGER_HTML_PATH.exists():
        raise HTTPException(status_code=500,
                             detail=f"missing {LEDGER_HTML_PATH}")
    return FileResponse(LEDGER_HTML_PATH, media_type="text/html")


# ── /help — renders docs/research_engine.md as a styled HTML page ─────


_HELP_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Re:Search Engine — Instructions</title>
  <style>
    :root {{
      --bg: #0a0e1c;
      --bg-2: #11172b;
      --text: #e6edf6;
      --muted: #9aa6c4;
      --accent: #72f7d4;
      --accent-2: #8ea7ff;
      --warning: #ffd166;
      --success: #63e6be;
      --border: rgba(255,255,255,0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI",
            Roboto, Helvetica, Arial, sans-serif;
    }}
    .wrap {{ max-width: 820px; margin: 0 auto; padding: 60px 24px 100px; }}
    .topbar {{
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 40px; padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }}
    .topbar a {{ color: var(--accent); text-decoration: none; }}
    .topbar a:hover {{ text-decoration: underline; }}
    h1 {{ font-size: 36px; line-height: 1.15; margin: 0 0 18px;
          letter-spacing: -0.02em; }}
    h2 {{ font-size: 22px; margin: 42px 0 14px;
          letter-spacing: -0.015em; }}
    h3 {{ font-size: 17px; margin: 30px 0 10px; color: var(--accent); }}
    p, li {{ color: #d4dbed; }}
    ul, ol {{ padding-left: 22px; }}
    li {{ margin-bottom: 6px; }}
    a {{ color: var(--accent); border-bottom: 1px solid
                              rgba(114, 247, 212, 0.3); text-decoration: none; }}
    a:hover {{ color: var(--accent-2); border-bottom-color: var(--accent-2); }}
    code {{
      font-family: ui-monospace, Menlo, Consolas, monospace;
      font-size: 0.9em;
      background: rgba(255,255,255,0.06);
      padding: 1px 6px;
      border-radius: 4px;
    }}
    pre {{
      background: var(--bg-2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      overflow-x: auto;
      font-size: 13px;
      line-height: 1.5;
    }}
    pre code {{ background: transparent; padding: 0; }}
    hr {{ border: none; border-top: 1px solid var(--border);
          margin: 40px 0; }}
    blockquote {{
      border-left: 3px solid var(--accent);
      margin: 20px 0; padding: 8px 16px;
      background: rgba(114, 247, 212, 0.06);
      border-radius: 0 8px 8px 0;
      color: var(--text); font-style: italic;
    }}
    table {{ border-collapse: collapse; margin: 18px 0; }}
    th, td {{ border: 1px solid var(--border); padding: 8px 12px; }}
    th {{ background: var(--bg-2); }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <a href="/">← Re:Search console</a>
      <span>Re:Search Engine — Instructions</span>
    </div>
    {body}
  </div>
</body>
</html>
"""


_HELP_PLAIN_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Instructions</title></head>
<body style="font:14px/1.5 monospace; padding:32px; background:#0a0e1c; color:#e6edf6;">
  <p style="color:#ffd166;">⚠ <code>markdown</code> Python package not
  installed; serving raw text. Install with <code>pip install
  markdown</code> + restart for the styled view.</p>
  <pre>{body}</pre>
</body></html>
"""


@app.get("/help")
async def help_page():
    """Serve docs/research_engine.md rendered as styled HTML.

    Single source of truth: the same Markdown file is what readers
    see on GitHub and what the live server renders here. Edit one
    place, both surfaces update.
    """
    if not HELP_MD_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"missing {HELP_MD_PATH}",
        )
    md_text = HELP_MD_PATH.read_text(encoding="utf-8")
    try:
        import markdown as _md
        body_html = _md.markdown(
            md_text,
            extensions=["fenced_code", "tables", "toc", "sane_lists"],
            output_format="html5",
        )
        html = _HELP_HTML_TEMPLATE.format(body=body_html)
    except ImportError:
        # Graceful fallback when the markdown lib isn't installed.
        import html as _html
        html = _HELP_PLAIN_TEMPLATE.format(body=_html.escape(md_text))
    return HTMLResponse(content=html)


# ── Medical research instrument routes ───────────────────────────────


class MedicalResearchRequest(BaseModel):
    question: str = Field(..., min_length=1)
    profile:  str = Field(default="summarize")
    sources:  Optional[list[dict]] = None


@app.post("/api/medical/research")
async def medical_research(req: MedicalResearchRequest):
    """Run the AXM medical research instrument.

    Returns the per-layer signed EventTokens, the
    MedicalCoordinatorToken, the bracketed Token Descriptor, and
    the human-review flag — the shape consumed by the medical
    workflow tab in web/research_console.html.
    """
    from axiom_medical_agent import (
        MedicalResearchAgent, MedicalAgentError,
        LAYER_ACTIVATION_PROFILES,
    )
    from axiom_medical_ledger import LedgerWriter, default_ledger_path
    if req.profile not in LAYER_ACTIVATION_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown profile: {req.profile}. Try "
                   f"{sorted(LAYER_ACTIVATION_PROFILES)}",
        )
    _state.ensure()
    from axiom_event_token.backends import default_backend
    ledger = LedgerWriter(default_ledger_path())
    try:
        agent = MedicalResearchAgent.from_default_pack(
            backend=default_backend(),
            ledger=ledger,
            research_question=req.question,
        )
        result = agent.research(
            req.question,
            sources=req.sources,
            profile=req.profile,
        )
    except MedicalAgentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        LOG.exception("medical research failed")
        raise HTTPException(
            status_code=502,
            detail=f"medical research failed: {e}",
        )
    return {
        "question":              req.question,
        "profile":               result.profile,
        "container_id":          result.container_id,
        "event_tokens":          [t.to_dict() for t in result.event_tokens],
        "coordinator_tokens":    [c.to_dict()
                                   for c in result.coordinator_tokens],
        "descriptor":            result.descriptor,
        "manifest_root":         result.manifest_root,
        "requires_human_review": result.requires_human_review,
        "tier_distribution":     dict(result.tier_distribution),
    }


@app.get("/api/medical/profiles")
async def medical_profiles():
    """List the 5 activation profiles + which layers they fire."""
    from axiom_medical_agent import LAYER_ACTIVATION_PROFILES
    return {
        "profiles": {
            name: list(layers)
            for name, layers in sorted(LAYER_ACTIVATION_PROFILES.items())
        }
    }


@app.get("/api/medical/ledger")
async def medical_ledger(limit: int = 20):
    """Recent medical-research ledger entries (signed audit trail)."""
    from axiom_medical_ledger import (
        query_ledger, default_ledger_path,
    )
    path = default_ledger_path()
    entries = query_ledger(path=path, limit=max(1, min(1000, limit)))
    return {
        "ledger_path": str(path),
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
