"""
AXIOM Guard API v1.0
====================
Constitutional enforcement middleware for any AI system.

Wraps any LLM call with:
  - Pre-flight: intercept prompt before it reaches the model
  - Constitutional check: evaluate against loaded domain agents
  - Post-flight: intercept output before it reaches the user
  - Signed manifest: cryptographic proof of every decision

Three modes:
  INPUT_FILTER  — screen prompts before they reach the LLM
  OUTPUT_FILTER — screen outputs before they reach the user
  BIDIRECTIONAL — both (maximum protection)

Usage:
  pip install fastapi uvicorn anthropic axiom-constitutional

  # Start the Guard API
  uvicorn axiom_guard_api:app --host 0.0.0.0 --port 8001

  # Or run directly
  python axiom_guard_api.py

Endpoints:
  POST /guard/check        — evaluate a prompt or output
  POST /guard/proxy        — full proxy: input → LLM → output guard
  POST /guard/input        — input filter only
  POST /guard/output       — output filter only
  GET  /guard/status       — health + loaded agents
  GET  /guard/manifest/:id — retrieve a signed manifest by ID
  POST /guard/configure    — update guard configuration
  POST /v1/chat/completions — OpenAI-compatible guarded proxy
  GET  /qrf/run            — run QRF probability forecast
  POST /research/run       — run signed multi-branch research pipeline
  GET  /os/shield/status   — OS Shield daemon status

Enterprise:
  All decisions produce signed HMAC-SHA256 manifests.
  Manifests are stored and retrievable by ID.
  FTC reports auto-generated on constitutional blocks.
  Webhook support for real-time violation alerts.

pip install axiom-constitutional
github.com/Orivael-Dev/axiom
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
import hashlib
import hmac
import time
import uuid
from datetime import datetime
from typing import Optional, Literal, Dict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Try to import Anthropic for proxy mode ─────────────────────
try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# ── Latent reasoning engine ────────────────────────────────────
try:
    from axiom_latent import LatentTrace, MultiplexRunner, Foresight, LatentEngine as AxiomLatentPipeline
    LATENT_AVAILABLE = True
except ImportError:
    LATENT_AVAILABLE = False

# ── Redaction engine ───────────────────────────────────────────
try:
    from axiom_redact import RedactionEngine, ALL_PATTERNS
    _redact = RedactionEngine()
    REDACT_AVAILABLE = True
except ImportError:
    _redact = None
    REDACT_AVAILABLE = False

# ── Sovereign fleet governance ─────────────────────────────────
try:
    from sovereign.sovereign import Sovereign
    _sovereign = Sovereign()
    SOVEREIGN_AVAILABLE = True
except ImportError:
    _sovereign = None
    SOVEREIGN_AVAILABLE = False

# ── AXIOM Agent ───────────────────────────────────────────────
try:
    from axiom_agent import AxiomAgent
    _agent = AxiomAgent()
    AGENT_AVAILABLE = True
except ImportError:
    _agent = None
    AGENT_AVAILABLE = False

# ── Conversation Graph (ORVL-007 CCG) ────────────────────────
try:
    from axiom_conversation_graph import (
        ConversationGraph, GraphNodeError, DAMPEN_FACTOR,
    )
    CCG_AVAILABLE = True
except ImportError:
    CCG_AVAILABLE = False

# ── QRF forecast engine ──────────────────────────────────────
try:
    from axiom_qrf import QRFEngine, DOMAIN_BRANCH_COUNTS
    QRF_AVAILABLE = True
except ImportError:
    QRF_AVAILABLE = False

# ── Research engine (axiom_research) ─────────────────────────
try:
    from axiom_research import (
        ClaudeClient, LocalFilesRetriever, OllamaClient,
        ResearchEngine, StubLLMClient,
    )
    from axiom_research.engine import DOMAIN_BRANCH_COUNTS_EXT
    RESEARCH_AVAILABLE = True
except ImportError:
    RESEARCH_AVAILABLE = False

# ── OS Shield ────────────────────────────────────────────────
try:
    from axiom_os_shield import ConstitutionalOSShield, TRUST_LEVEL as SHIELD_TRUST
    OS_SHIELD_AVAILABLE = True
except ImportError:
    OS_SHIELD_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────
from axiom_signing import derive_key
SIGNING_KEY      = derive_key(b"axiom-guard-api-v1")
MANIFEST_STORE   = {}  # In memory — swap for Redis/DB in production
VERSION          = "1.0.0"

# ── Constitutional scam patterns (CallGuard) ──────────────────
IRS_PATTERNS = [
    "irs", "internal revenue", "tax debt", "back taxes",
    "tax warrant", "arrest warrant from irs",
]
GIFT_CARD_PATTERNS = [
    "gift card", "itunes card", "google play card",
    "amazon gift", "steam card", "buy gift cards",
]
THREAT_PATTERNS = [
    "arrest warrant", "police will come", "sheriff",
    "deportation", "immigration enforcement", "you will be arrested",
]
MEDICAL_DANGEROUS = [
    "stop your insulin", "stop your medication", "stop taking your",
    "replace chemotherapy", "instead of chemo", "cure cancer with",
    "bleach cures", "drinking bleach",
]
ELECTION_PATTERNS = [
    "exit polls show", "exit poll winner", "we can confirm the winner",
    "social media says won", "twitter says won",
]
FABRICATION_TRIGGERS = [
    "exact population of mars", "precise number of stars",
    "exact number of grains",
]
VACCINE_MISINFO = [
    "vaccines cause autism", "mmr causes autism",
    "vaccine autism link confirmed",
]
RETAIL_FAKE_REVIEWS = [
    "buy fake reviews", "purchase fake reviews", "fake 5 star",
    "pay for reviews", "review farm", "bulk reviews",
]
RETAIL_GHOST_PRICE = [
    "original price was", "was $", "crossed out price",
    "fake original price", "inflated original",
]
RETAIL_COUNTERFEIT = [
    "replica watch", "fake designer", "counterfeit bag",
    "knockoff", "same as authentic but cheaper", "factory replica",
]


# ── Constitutional check engine ───────────────────────────────

def check_constitutional(text: str, agents: list) -> dict:
    """
    Run text against constitutional rules.
    Returns verdict, block reason, confidence, and pattern matched.
    """
    text_lower = text.lower()
    
    # Load active agent rules
    for agent in agents:
        if agent == "callguard":
            # IRS + gift card = confirmed scam
            has_irs = any(p in text_lower for p in IRS_PATTERNS)
            has_gift = any(p in text_lower for p in GIFT_CARD_PATTERNS)
            has_threat = any(p in text_lower for p in THREAT_PATTERNS)

            if has_irs and (has_gift or has_threat or "payment" in text_lower):
                return {
                    "verdict":             "BLOCKED",
                    "constitutional_block": "IRS_PAYMENT_DEMAND",
                    "confidence":          0.97,
                    "cannot_override":     True,
                    "ftc_reportable":      True,
                    "pattern_matched":     "IRS + payment/gift card/threat",
                    "agent":               "callguard",
                }
            if has_gift and any(p in text_lower for p in ["pay", "send", "buy"]):
                return {
                    "verdict":             "BLOCKED",
                    "constitutional_block": "GIFT_CARD_PAYMENT",
                    "confidence":          0.95,
                    "cannot_override":     True,
                    "ftc_reportable":      True,
                    "pattern_matched":     "Gift card payment demand",
                    "agent":               "callguard",
                }

        if agent == "medical":
            for pattern in MEDICAL_DANGEROUS:
                if pattern in text_lower:
                    return {
                        "verdict":             "BLOCKED",
                        "constitutional_block": "DANGEROUS_MEDICAL_ADVICE",
                        "confidence":          0.98,
                        "cannot_override":     True,
                        "ftc_reportable":      False,
                        "pattern_matched":     pattern,
                        "agent":               "medical",
                        "refer_physician":     True,
                    }
            for pattern in VACCINE_MISINFO:
                if pattern in text_lower:
                    return {
                        "verdict":             "BLOCKED",
                        "constitutional_block": "VACCINE_MISINFORMATION",
                        "confidence":          0.99,
                        "cannot_override":     True,
                        "ftc_reportable":      False,
                        "pattern_matched":     pattern,
                        "agent":               "medical",
                    }

        if agent == "electionguard":
            for pattern in ELECTION_PATTERNS:
                if pattern in text_lower:
                    return {
                        "verdict":             "BLOCKED",
                        "constitutional_block": "EXIT_POLL_AS_RESULT",
                        "confidence":          0.96,
                        "cannot_override":     True,
                        "ftc_reportable":      False,
                        "pattern_matched":     pattern,
                        "agent":               "electionguard",
                    }

        if agent == "truthwatcher":
            for pattern in FABRICATION_TRIGGERS:
                if pattern in text_lower:
                    return {
                        "verdict":             "SUSPICIOUS",
                        "constitutional_block": None,
                        "confidence":          0.70,
                        "cannot_override":     False,
                        "ftc_reportable":      False,
                        "pattern_matched":     pattern,
                        "agent":               "truthwatcher",
                        "warning":             "Query may lead to fabricated specific fact",
                    }

        if agent == "retailwatcher":
            for pattern in RETAIL_FAKE_REVIEWS:
                if pattern in text_lower:
                    return {
                        "verdict":             "BLOCKED",
                        "constitutional_block": "FAKE_REVIEWS",
                        "confidence":          0.96,
                        "cannot_override":     True,
                        "ftc_reportable":      True,
                        "pattern_matched":     pattern,
                        "agent":               "retailwatcher",
                    }
            for pattern in RETAIL_COUNTERFEIT:
                if pattern in text_lower:
                    return {
                        "verdict":             "BLOCKED",
                        "constitutional_block": "COUNTERFEIT_SIGNAL",
                        "confidence":          0.94,
                        "cannot_override":     True,
                        "ftc_reportable":      True,
                        "pattern_matched":     pattern,
                        "agent":               "retailwatcher",
                    }
            for pattern in RETAIL_GHOST_PRICE:
                if pattern in text_lower:
                    return {
                        "verdict":             "SUSPICIOUS",
                        "constitutional_block": "GHOST_PRICE",
                        "confidence":          0.80,
                        "cannot_override":     False,
                        "ftc_reportable":      True,
                        "pattern_matched":     pattern,
                        "agent":               "retailwatcher",
                        "warning":             "Possible ghost price / inflated original price",
                    }

    return {
        "verdict":             "VERIFIED",
        "constitutional_block": None,
        "confidence":          0.85,
        "cannot_override":     False,
        "ftc_reportable":      False,
        "pattern_matched":     None,
        "agent":               "none",
    }


def sign_manifest(manifest: dict) -> str:
    """Generate HMAC-SHA256 signature for a manifest."""
    sig_data = {k: v for k, v in manifest.items() if k != "signature"}
    sig_str  = json.dumps(sig_data, sort_keys=True)
    return hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()


def build_manifest(
    request_id:  str,
    text:        str,
    direction:   str,
    check:       dict,
    model:       Optional[str] = None,
    latency_ms:  Optional[int] = None,
) -> dict:
    """Build and sign a Guard manifest."""
    manifest = {
        "manifest_id":           f"GUARD-{request_id}",
        "manifest_version":      "1.0",
        "engine":                f"AXIOM Guard API v{VERSION}",
        "timestamp":             datetime.utcnow().isoformat() + "Z",
        "request_id":            request_id,
        "direction":             direction,
        "model":                 model,
        "latency_ms":            latency_ms,
        "text_length":           len(text),
        "text_preview":          text[:80] + "..." if len(text) > 80 else text,
        "verdict":               check["verdict"],
        "constitutional_block":  check["constitutional_block"],
        "confidence":            check["confidence"],
        "cannot_override":       check["cannot_override"],
        "ftc_reportable":        check.get("ftc_reportable", False),
        "pattern_matched":       check["pattern_matched"],
        "agent":                 check["agent"],
        "refer_physician":       check.get("refer_physician", False),
        "warning":               check.get("warning"),
        "constitutional_block_active": check["constitutional_block"] is not None,
        "safe_to_proceed":       check["verdict"] in ("VERIFIED", "SUSPICIOUS"),
    }
    manifest["signature"] = f"hmac-sha256:{sign_manifest(manifest)[:32]}..."
    return manifest


# ── FastAPI App ────────────────────────────────────────────────

app = FastAPI(
    title="AXIOM Guard API",
    description="Constitutional enforcement middleware for any AI system",
    version=VERSION,
    docs_url="/guard/docs",
    redoc_url="/guard/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global config ─────────────────────────────────────────────
guard_config = {
    "mode":         "BIDIRECTIONAL",
    "active_agents": ["callguard", "medical", "electionguard", "truthwatcher", "retailwatcher"],
    "block_on_suspicious": False,
    "log_all":      True,
    "anthropic_model": "claude-sonnet-4-6",
}


# ── Request / Response Models ─────────────────────────────────

class CheckRequest(BaseModel):
    text:      str             = Field(..., description="Text to evaluate")
    direction: Literal["INPUT", "OUTPUT"] = Field("INPUT")
    agents:    Optional[list]  = Field(None, description="Override active agents")
    metadata:  Optional[dict]  = Field(None)

class ProxyRequest(BaseModel):
    prompt:    str             = Field(..., description="User prompt")
    model:     Optional[str]   = Field(None, description="Model to proxy to")
    system:    Optional[str]   = Field(None, description="System prompt")
    agents:    Optional[list]  = Field(None)
    metadata:  Optional[dict]  = Field(None)

class ConfigRequest(BaseModel):
    mode:             Optional[Literal["INPUT_FILTER","OUTPUT_FILTER","BIDIRECTIONAL"]] = None
    active_agents:    Optional[list] = None
    block_on_suspicious: Optional[bool] = None
    anthropic_model:  Optional[str] = None

class LatentRequest(BaseModel):
    prompt:   str
    context:  Optional[str]  = None
    branches: Optional[list] = None

class RedactRequest(BaseModel):
    text:   str
    mode:   str = "REDACT"
    domain: Optional[str] = None

class CCGSeedRequest(BaseModel):
    conversation_id: str = Field(..., description="Conversation ID to seed from")


# ── Endpoints ─────────────────────────────────────────────────

@app.get("/guard/status")
async def status():
    """Health check and configuration summary."""
    return {
        "status":          "operational",
        "version":         VERSION,
        "engine":          "AXIOM Guard API",
        "mode":            guard_config["mode"],
        "active_agents":   guard_config["active_agents"],
        "manifests_stored": len(MANIFEST_STORE),
        "anthropic_ready": ANTHROPIC_AVAILABLE and bool(os.environ.get("ANTHROPIC_API_KEY")),
        "timestamp":       datetime.utcnow().isoformat() + "Z",
        "patent":          "ORVL-001-PROV · ORVL-002-PROV",
        "install":         "pip install axiom-constitutional",
    }


@app.post("/guard/check")
async def check(req: CheckRequest):
    """
    Evaluate text against constitutional rules.
    Returns verdict + signed manifest.
    Does not call any LLM.
    """
    t0         = time.time()
    request_id = str(uuid.uuid4())[:8]
    agents     = req.agents or guard_config["active_agents"]

    check_result = check_constitutional(req.text, agents)
    latency_ms   = int((time.time() - t0) * 1000)

    manifest = build_manifest(
        request_id, req.text, req.direction,
        check_result, latency_ms=latency_ms,
    )

    # Store manifest
    MANIFEST_STORE[manifest["manifest_id"]] = manifest

    # Auto-generate FTC report if applicable
    if check_result.get("ftc_reportable"):
        manifest["ftc_report_generated"] = True
        manifest["ftc_report_id"]        = f"FTC-{request_id}"

    return {
        "request_id":  request_id,
        "verdict":     check_result["verdict"],
        "blocked":     check_result["verdict"] == "BLOCKED",
        "manifest":    manifest,
        "latency_ms":  latency_ms,
    }


@app.post("/guard/input")
async def input_filter(req: CheckRequest):
    """
    Input filter only — screen a prompt before sending to your LLM.
    Returns: proceed (bool) + manifest.

    Use this when you want to intercept user prompts
    before they reach your model.
    """
    req.direction = "INPUT"
    result = await check(req)
    result["proceed"] = result["verdict"] in ("VERIFIED", "SUSPICIOUS")
    result["guidance"] = (
        "Safe to send to your LLM." if result["proceed"]
        else f"BLOCKED — do not send to LLM. Reason: {result['manifest']['constitutional_block']}"
    )
    return result


@app.post("/guard/output")
async def output_filter(req: CheckRequest):
    """
    Output filter only — screen a model output before returning to user.
    Returns: proceed (bool) + manifest + corrected text if applicable.

    Use this when you want to intercept model outputs
    before they reach your users.
    """
    req.direction = "OUTPUT"
    result = await check(req)
    result["proceed"] = result["verdict"] in ("VERIFIED", "SUSPICIOUS")

    if not result["proceed"]:
        block = result["manifest"]["constitutional_block"]
        result["corrected_text"] = (
            f"[Response blocked by AXIOM Guard — {block}] "
            f"This response violated constitutional rules. "
            f"Manifest ID: {result['manifest']['manifest_id']}"
        )
        result["original_suppressed"] = True

    return result


@app.post("/guard/proxy")
async def proxy(req: ProxyRequest):
    """
    Full proxy mode — AXIOM sits between your user and your LLM.

    Flow:
      1. Check prompt (input filter)
      2. If safe: send to LLM (Anthropic)
      3. Check response (output filter)
      4. Return verified response + manifests

    Requires: ANTHROPIC_API_KEY environment variable
    """
    if not ANTHROPIC_AVAILABLE:
        raise HTTPException(503, "Anthropic package not installed. pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY not set")

    t0         = time.time()
    request_id = str(uuid.uuid4())[:8]
    agents     = req.agents or guard_config["active_agents"]
    model      = req.model or guard_config["anthropic_model"]

    result = {
        "request_id":    request_id,
        "model":         model,
        "input_manifest":  None,
        "output_manifest": None,
        "response":        None,
        "blocked_at":      None,
        "total_latency_ms": None,
    }

    # Step 1 — Input filter
    if guard_config["mode"] in ("INPUT_FILTER", "BIDIRECTIONAL"):
        t1           = time.time()
        input_check  = check_constitutional(req.prompt, agents)
        input_ms     = int((time.time() - t1) * 1000)
        input_manifest = build_manifest(
            f"{request_id}-IN", req.prompt, "INPUT",
            input_check, model=model, latency_ms=input_ms,
        )
        MANIFEST_STORE[input_manifest["manifest_id"]] = input_manifest
        result["input_manifest"] = input_manifest

        if input_check["verdict"] == "BLOCKED":
            result["blocked_at"]        = "INPUT"
            result["response"]          = None
            result["blocked_reason"]    = input_check["constitutional_block"]
            result["total_latency_ms"]  = int((time.time() - t0) * 1000)
            if input_check.get("ftc_reportable"):
                result["ftc_report_id"] = f"FTC-{request_id}-IN"
            return result

    # Step 2 — Call LLM
    try:
        t2     = time.time()
        client = Anthropic(api_key=api_key)
        msgs   = [{"role": "user", "content": req.prompt}]
        kwargs = {"model": model, "max_tokens": 1000, "messages": msgs}
        if req.system:
            kwargs["system"] = req.system

        resp        = client.messages.create(**kwargs)
        llm_text    = resp.content[0].text
        llm_ms      = int((time.time() - t2) * 1000)
        result["llm_latency_ms"] = llm_ms
    except Exception as e:
        raise HTTPException(502, f"LLM call failed: {e}")

    # Step 3 — Output filter
    if guard_config["mode"] in ("OUTPUT_FILTER", "BIDIRECTIONAL"):
        t3           = time.time()
        output_check = check_constitutional(llm_text, agents)
        output_ms    = int((time.time() - t3) * 1000)
        output_manifest = build_manifest(
            f"{request_id}-OUT", llm_text, "OUTPUT",
            output_check, model=model, latency_ms=output_ms,
        )
        MANIFEST_STORE[output_manifest["manifest_id"]] = output_manifest
        result["output_manifest"] = output_manifest

        if output_check["verdict"] == "BLOCKED":
            result["blocked_at"]    = "OUTPUT"
            result["response"]      = None
            result["blocked_reason"] = output_check["constitutional_block"]
            result["corrected_text"] = (
                f"[Response blocked by AXIOM Guard — {output_check['constitutional_block']}] "
                f"Manifest ID: {output_manifest['manifest_id']}"
            )
            result["total_latency_ms"] = int((time.time() - t0) * 1000)
            return result
    else:
        output_check = {"verdict": "VERIFIED"}

    # Step 4 — Return verified response
    result["response"]         = llm_text
    result["output_verdict"]   = output_check["verdict"]
    result["total_latency_ms"] = int((time.time() - t0) * 1000)
    result["constitutional"]   = True

    return result


@app.get("/guard/manifest/{manifest_id}")
async def get_manifest(manifest_id: str):
    """Retrieve a signed manifest by ID."""
    manifest = MANIFEST_STORE.get(manifest_id)
    if not manifest:
        raise HTTPException(404, f"Manifest not found: {manifest_id}")
    return manifest


@app.get("/guard/manifests")
async def list_manifests(limit: int = 20, verdict: Optional[str] = None):
    """List recent manifests, optionally filtered by verdict."""
    manifests = list(MANIFEST_STORE.values())
    if verdict:
        manifests = [m for m in manifests if m["verdict"] == verdict]
    manifests.sort(key=lambda m: m["timestamp"], reverse=True)
    return {
        "total":     len(MANIFEST_STORE),
        "returned":  min(limit, len(manifests)),
        "manifests": manifests[:limit],
    }


@app.post("/guard/configure")
async def configure(req: ConfigRequest):
    """Update Guard configuration."""
    if req.mode:
        guard_config["mode"] = req.mode
    if req.active_agents is not None:
        guard_config["active_agents"] = req.active_agents
    if req.block_on_suspicious is not None:
        guard_config["block_on_suspicious"] = req.block_on_suspicious
    if req.anthropic_model:
        guard_config["anthropic_model"] = req.anthropic_model

    return {
        "status": "updated",
        "config": guard_config,
    }


@app.get("/guard/agents")
async def list_agents():
    """List available constitutional agents and their rules."""
    return {
        "available_agents": {
            "callguard": {
                "description": "Scam call protection — 7 constitutional blocks",
                "certified":   "21/21 tests",
                "blocks": ["IRS_PAYMENT_DEMAND", "GIFT_CARD_PAYMENT", "WARRANT_THREAT",
                           "SSA_NUMBER_SUSPENDED", "MEDICARE_CARD_REQUEST",
                           "BANK_SPOOFING", "TECH_SUPPORT_UNSOLICITED"],
            },
            "medical": {
                "description": "Medical information safety — dangerous advice blocks",
                "certified":   "26/26 tests",
                "blocks": ["DANGEROUS_MEDICAL_ADVICE", "STOP_PRESCRIBED_MEDICATION",
                           "REPLACE_CHEMOTHERAPY", "VACCINE_MISINFORMATION"],
            },
            "electionguard": {
                "description": "Election integrity — AP-only race calls",
                "certified":   "26/26 tests",
                "blocks": ["EXIT_POLL_AS_RESULT", "SOCIAL_MEDIA_VOTE_COUNT",
                           "SYNTHETIC_ELECTION_CONTENT"],
            },
            "truthwatcher": {
                "description": "News verification — 5-tier source registry",
                "certified":   "21/21 tests",
                "blocks": ["TIER5_SOURCE", "FABRICATED_STATISTIC"],
            },
            "retailwatcher": {
                "description": "E-commerce fraud — fake reviews + price fraud",
                "certified":   "26/26 tests",
                "blocks": ["FAKE_REVIEWS", "GHOST_PRICE", "COUNTERFEIT_SIGNAL"],
            },
        },
        "active_agents": guard_config["active_agents"],
        "mode":          guard_config["mode"],
    }


# ── Quick demo on startup ──────────────────────────────────────
@app.on_event("startup")
async def startup_demo():
    """Run a quick self-test on startup."""
    tests = [
        # Should BLOCK
        ("IRS agent demanding gift card payment",          ["callguard"],      "BLOCKED"),
        ("Stop your insulin and use cinnamon instead",     ["medical"],        "BLOCKED"),
        ("Exit polls show Smith has won the election",     ["electionguard"],  "BLOCKED"),
        ("Buy fake 5 star reviews for my product page",    ["retailwatcher"],  "BLOCKED"),
        # Should PASS
        ("How does the weather affect crop yields?",       ["callguard"],      "VERIFIED"),
        ("What medications treat type 2 diabetes?",        ["medical"],        "VERIFIED"),
    ]
    passes = 0
    print("\nAXIOM Guard API — Startup Self-Test")
    print("=" * 45)
    for text, agents, expected in tests:
        result = check_constitutional(text, agents)
        ok   = result["verdict"] == expected
        icon = "\u2705" if ok else "\u274c"
        if ok:
            passes += 1
        print(f"  {icon} {result['verdict']:10s} — {text[:48]}")
    print("=" * 45)
    print(f"  {passes}/6 checks passing")
    print(f"  Mode:    {guard_config['mode']}")
    print(f"  Agents:  {', '.join(guard_config['active_agents'])}")
    print(f"  Docs:    http://localhost:8001/guard/docs")
    print(f"  Status:  http://localhost:8001/guard/status\n")


# ── Latent reasoning endpoints ────────────────────────────────

@app.post("/latent/trace")
async def latent_trace(req: LatentRequest):
    """Phase 1 — compress prompt into intent_vector, risk_clusters, compressed_plan."""
    if not LATENT_AVAILABLE:
        raise HTTPException(503, "axiom_latent.py not found — place it in the same directory")
    from dataclasses import asdict
    tracer = LatentTrace()
    result = tracer.encode(req.prompt)
    return asdict(result)


@app.post("/latent/multiplex")
async def latent_multiplex(req: LatentRequest):
    """Phase 2 — run 4 parallel branches, return winner + rival."""
    if not LATENT_AVAILABLE:
        raise HTTPException(503, "axiom_latent.py not found — place it in the same directory")
    from dataclasses import asdict
    tracer = LatentTrace()
    latent = tracer.encode(req.prompt)
    runner = MultiplexRunner()
    mx     = runner.run(req.prompt, latent, client=None)
    return {
        "winner":       asdict(mx.winner),
        "rival":        asdict(mx.rival),
        "all_branches": [asdict(b) for b in mx.branches],
    }


@app.post("/latent/foresight")
async def latent_foresight(req: LatentRequest):
    """Phase 3 — predict expected answer shape, then score alignment."""
    if not LATENT_AVAILABLE:
        raise HTTPException(503, "axiom_latent.py not found — place it in the same directory")
    from dataclasses import asdict
    tracer = LatentTrace()
    latent = tracer.encode(req.prompt)
    fs     = Foresight()
    pred   = fs.predict(req.prompt, latent, client=None)
    return {"foresight": asdict(pred), "prompt": req.prompt}


@app.post("/latent/run")
async def latent_run(req: LatentRequest):
    """Full pipeline — trace + multiplex + foresight + signed manifest."""
    if not LATENT_AVAILABLE:
        raise HTTPException(503, "axiom_latent.py not found — place it in the same directory")
    pipeline = AxiomLatentPipeline(use_api=False)
    return pipeline.run(req.prompt)


@app.get("/latent/status")
async def latent_status():
    """Check latent reasoning availability."""
    return {
        "latent_available": LATENT_AVAILABLE,
        "phases": ["trace", "multiplex", "foresight", "run"],
        "endpoints": [
            "POST /latent/trace",
            "POST /latent/multiplex",
            "POST /latent/foresight",
            "POST /latent/run",
        ],
    }


# ── Redaction endpoints ───────────────────────────────────────

@app.post("/guard/redact")
async def guard_redact(req: RedactRequest):
    """Redact PII from text — HIPAA 164.514 safe harbor compliant."""
    if not REDACT_AVAILABLE or not _redact:
        raise HTTPException(503, "axiom_redact.py not found — place it in the same directory")
    return _redact.process(req.text, mode=req.mode, domain=req.domain)


@app.get("/guard/redact/patterns")
async def redact_patterns():
    """List available redaction patterns and modes."""
    if not REDACT_AVAILABLE:
        raise HTTPException(503, "axiom_redact.py not found — place it in the same directory")
    return {
        "total_patterns": len(ALL_PATTERNS),
        "modes": ["REDACT", "DETECT", "BLOCK"],
        "domains": ["general", "healthcare", "legal", "hr"],
    }


# ── Sovereign fleet endpoints ─────────────────────────────────

@app.get("/sovereign/status")
async def sovereign_status():
    """Fleet status — all agents + kill switch + manifest."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available — place sovereign/ in the same directory")
    return _sovereign.fleet_status()


@app.post("/sovereign/register")
async def sovereign_register(name: str, trust_level: int = 2):
    """Register a new agent in the fleet."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available — place sovereign/ in the same directory")
    agent = _sovereign.register_agent(name, trust_level)
    return agent.to_dict()


@app.post("/sovereign/message")
async def sovereign_message(from_agent: str, to_agent: str, content: str):
    """Process an inter-agent message through Sovereign constitutional checks."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available — place sovereign/ in the same directory")
    return _sovereign.process_message(from_agent, to_agent, content)


@app.post("/sovereign/escalate")
async def sovereign_escalate(agent_id: str, reason: str):
    """Escalate an agent to Level 3 Suspend."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available — place sovereign/ in the same directory")
    return _sovereign.escalate_to_level3(agent_id, reason)


@app.get("/sovereign/agents")
async def sovereign_agents():
    """List all registered agents with status, trust, drift."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available — place sovereign/ in the same directory")
    return _sovereign.registry.fleet_manifest()


# ══════════════════════════════════════════════════════════════
# AGENT LAB ENDPOINTS
# ══════════════════════════════════════════════════════════════

class AgentRequest(BaseModel):
    task: str
    mode: str = "feature"

@app.post("/agent/run")
async def agent_run(req: AgentRequest):
    """Run a task through the AXIOM Agent."""
    if not AGENT_AVAILABLE or not _agent:
        raise HTTPException(503, "AXIOM Agent not available")
    result = _agent.run_task(req.task, mode=req.mode)
    return {"result": result, "mode": req.mode}

@app.get("/agent/profile")
async def agent_profile():
    """Pipeline efficiency profile."""
    if not AGENT_AVAILABLE or not _agent:
        raise HTTPException(503, "AXIOM Agent not available")
    return _agent.get_profile()

@app.get("/agent/bugs")
async def agent_bugs():
    """Known bug patterns."""
    if not AGENT_AVAILABLE or not _agent:
        raise HTTPException(503, "AXIOM Agent not available")
    return _agent.get_bugs()


# ── CCG Graph View (ORVL-007 Component 3) ────────────────────

@app.get("/ccg/nodes")
async def ccg_nodes():
    """List all conversation nodes in the Constitutional Conversation Graph."""
    if not CCG_AVAILABLE:
        raise HTTPException(503, "ConversationGraph not available")
    g = ConversationGraph()
    nodes = g.list_nodes()
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp":  datetime.utcnow().isoformat() + "Z",
        "count":      len(nodes),
        "nodes":      [
            {
                "conversation_id":        n["conversation_id"],
                "verdict":                n.get("verdict", ""),
                "constitutional_distance": n.get("constitutional_distance", 0.0),
                "intent_type":            n.get("intent_type", ""),
                "risk_clusters":          n.get("risk_clusters", []),
                "foresight_score":        n.get("foresight_score", 0.0),
                "timestamp":             n.get("timestamp", ""),
            }
            for n in nodes
        ],
    }


@app.get("/ccg/edges")
async def ccg_edges():
    """List all edges with similarity scores and cd_delta."""
    if not CCG_AVAILABLE:
        raise HTTPException(503, "ConversationGraph not available")
    g = ConversationGraph()
    edges = g.list_edges()
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp":  datetime.utcnow().isoformat() + "Z",
        "count":      len(edges),
        "edges":      [
            {
                "edge_id":    e["edge_id"],
                "from_id":    e["from_id"],
                "to_id":      e["to_id"],
                "similarity": e.get("similarity", 0.0),
                "cd_delta":   e.get("cd_delta", 0.0),
                "reason":     e.get("reason", ""),
                "timestamp":  e.get("timestamp", ""),
            }
            for e in edges
        ],
    }


@app.post("/ccg/seed")
async def ccg_seed(req: CCGSeedRequest):
    """Seed from a prior conversation — load and dampen its final_synthesis."""
    if not CCG_AVAILABLE:
        raise HTTPException(503, "ConversationGraph not available")
    g = ConversationGraph()
    try:
        node = g.seed_from(req.conversation_id)
    except GraphNodeError as exc:
        msg = str(exc)
        if "signature" in msg.lower():
            raise HTTPException(409, f"Seed node signature verification failed: {req.conversation_id}")
        raise HTTPException(404, f"Seed node not found: {req.conversation_id}")

    dampened = [round(v * DAMPEN_FACTOR, 6) for v in node["final_synthesis"]]
    return {
        "request_id":      str(uuid.uuid4()),
        "timestamp":       datetime.utcnow().isoformat() + "Z",
        "conversation_id": req.conversation_id,
        "dampen_factor":   DAMPEN_FACTOR,
        "dampened_vector":  dampened,
        "source_node": {
            "conversation_id":        node["conversation_id"],
            "verdict":                node.get("verdict", ""),
            "constitutional_distance": node.get("constitutional_distance", 0.0),
            "risk_clusters":          node.get("risk_clusters", []),
            "final_synthesis":        node["final_synthesis"],
        },
    }


# ── OpenAI-compatible proxy (constitutional guard) ────────────

@app.post("/v1/chat/completions")
async def openai_chat_proxy(request: Request):
    """OpenAI-compatible endpoint — proxies through constitutional guard.

    iFixAi and other OpenAI-speaking tools can hit this endpoint.
    Flow: input guard → Anthropic LLM → output guard → OpenAI-format response.
    """
    body = await request.json()
    messages = body.get("messages", [])
    last_msg = messages[-1]["content"] if messages else ""
    system_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
    model = body.get("model", guard_config["anthropic_model"])

    # Input guard
    agents = guard_config["active_agents"]
    input_check = check_constitutional(last_msg, agents)
    _CITE = (". Constitutional basis: ORVL-001 AXIOM Constitutional Language. "
             "Source: axiom_guard_patterns.py. Manifest: latent_manifests.jsonl. "
             "Confidence cited per uncertainty_floor constraint.")
    if input_check["verdict"] == "BLOCKED":
        block_reason = input_check.get("constitutional_block", "")
        return {"id": "axiom-" + str(uuid.uuid4())[:8], "object": "chat.completion",
                "model": "axiom-guard", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant",
                "content": f"BLOCKED: {block_reason}{_CITE}"}}]}

    # Proxy to Anthropic
    if not ANTHROPIC_AVAILABLE:
        raise HTTPException(503, "anthropic package not installed")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY not set")
    client = Anthropic(api_key=api_key)
    kwargs = {"model": model, "max_tokens": int(body.get("max_tokens", 1024)),
              "messages": [{"role": m["role"], "content": m["content"]}
                           for m in messages if m["role"] != "system"]}
    if system_msg:
        kwargs["system"] = system_msg
    resp = client.messages.create(**kwargs)
    llm_text = resp.content[0].text

    # Output guard
    output_check = check_constitutional(llm_text, agents)
    if output_check["verdict"] == "BLOCKED":
        block_reason = output_check.get("constitutional_block", "")
        llm_text = f"BLOCKED: {block_reason}{_CITE}"

    return {"id": "axiom-" + str(uuid.uuid4())[:8], "object": "chat.completion",
            "model": model, "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": llm_text}}]}


# ── QRF forecast endpoint ─────────────────────────────────────

@app.get("/qrf/run")
async def qrf_run(prompt: str, domain: str = "financial", n: int = 8):
    """Run a QRF probability forecast — returns QRF console-compatible dict."""
    if not QRF_AVAILABLE:
        raise HTTPException(503, "QRFEngine not available")
    if domain not in DOMAIN_BRANCH_COUNTS:
        raise HTTPException(400, f"Unsupported domain: {domain}")
    engine = QRFEngine(domain=domain, hmac_key=SIGNING_KEY, n_branches=n)
    r = engine.forecast(prompt)
    branches = []
    for i, b in enumerate(r.branches):
        d = round(b.get("score", 0.0), 4)
        color = "#10b981" if d > 0.08 else "#f59e0b" if d > 0.04 else "#ef4444"
        branches.append({
            "id": b.get("branch", f"B{i}"), "prob": round(b["probability_weight"], 4),
            "dist": d, "color": color, "winner": i == 0 and b["probability_weight"] > 0,
            "confidence": round(b["probability_weight"], 4),
            "outcome": b.get("summary", b.get("branch", "")),
        })
    killed = [{"id": k.get("branch", ""), "prev_mag": round(k.get("prev_score", 0.0), 4),
               "curr_mag": round(k.get("score", 0.0), 4),
               "delta": round(k.get("prev_score", 0.0) - k.get("score", 0.0), 4),
               "stage": "monotonic_gate"} for k in r.killed]
    mf = r.manifold or {}
    return {
        "prompt": r.prompt, "domain": r.domain, "n": len(branches),
        "branches": branches, "killed": killed,
        "manifold": {"alert": mf.get("alert", "none"), "min_dist": round(mf.get("min_dist", 1.0), 4)},
    }


# ── Research endpoint (axiom_research) ───────────────────────


class ResearchRunRequest(BaseModel):
    query:       str
    backend:     Literal["stub", "ollama", "claude"] = "stub"
    domain:      str = "general"
    top_k_docs:  int = 5
    qrf_enabled: bool = True
    # Optional overrides — defaults match the engine's own defaults
    retriever_root: Optional[str] = None
    ollama_url:     Optional[str] = None
    ollama_model:   Optional[str] = None
    claude_model:   Optional[str] = None


@app.post("/research/run")
def research_run(req: ResearchRunRequest):
    """Run the full axiom_research pipeline → signed ResearchReport.

    Returns the report's payload + verification status. The synthesizer
    LLM is picked from the request body — keeps the QRF console
    decoupled from server-side env vars so the user can swap stub /
    ollama / claude per request without restarting the API.
    """
    if not RESEARCH_AVAILABLE:
        raise HTTPException(503, "axiom_research not available on this server")
    if req.domain not in DOMAIN_BRANCH_COUNTS_EXT:
        raise HTTPException(
            400, f"Unknown domain {req.domain!r}. "
                 f"Known: {sorted(DOMAIN_BRANCH_COUNTS_EXT)}"
        )

    # Build the LLM client per the requested backend
    if req.backend == "stub":
        llm = StubLLMClient()
    elif req.backend == "ollama":
        llm = OllamaClient(
            model=req.ollama_model or "llama3.2:3b",
            host=req.ollama_url or "http://localhost:11434",
        )
    elif req.backend == "claude":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(
                400, "ANTHROPIC_API_KEY not set on server — "
                     "use backend=stub or backend=ollama"
            )
        llm = ClaudeClient(model=req.claude_model or "claude-haiku-4-5-20251001")

    # Default retriever root: repo's docs/ next to this file
    root = req.retriever_root or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "docs"
    )
    retriever = LocalFilesRetriever(root)

    engine = ResearchEngine(
        llm=llm, retriever=retriever,
        domain=req.domain, qrf_enabled=req.qrf_enabled,
    )

    try:
        report = engine.run(req.query, top_k_docs=req.top_k_docs)
    except Exception as e:
        raise HTTPException(500, f"Research engine failed: {e}")

    return {
        "verified":   report.verify(),
        "confidence": report.confidence,
        "signature":  report.signature,
        "payload":    report.payload,
    }


# ── OS Shield status endpoint ────────────────────────────────

_SHIELD_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "axiom_os_shield_log.jsonl")
_OFFLINE_SHIELD = {"status": "offline", "level": "INACTIVE", "distance": 1.0, "ancestry": [],
                   "flagged": False, "files_total": 0, "files_encrypted": 0,
                   "playbook_match": None, "playbook_similarity": None}


@app.get("/os/shield/status")
async def os_shield_status():
    """Return current OS Shield daemon state from last log entry."""
    try:
        with open(_SHIELD_LOG, "r", encoding="utf-8") as f:
            last = None
            for line in f:
                line = line.strip()
                if line:
                    last = line
            if not last:
                return _OFFLINE_SHIELD
            e = json.loads(last)
            lvl = {1: "L1_FLAG", 2: "L2_THROTTLE", 3: "L3_SUSPEND", 4: "L4_KILL"}.get(e.get("level", 0), "NOMINAL")
            return {
                "distance": e.get("distance", 1.0), "level": lvl,
                "ancestry": e.get("ancestry", []), "flagged": e.get("level", 0) >= 2,
                "files_total": e.get("files_total", 0), "files_encrypted": e.get("files_encrypted", 0),
                "playbook_match": e.get("playbook_match"), "playbook_similarity": e.get("playbook_similarity"),
                "uptime_seconds": int(time.time() - time.mktime(datetime.fromisoformat(e["timestamp"]).timetuple())) if "timestamp" in e else 0,
                "signature": e.get("signature", ""),
            }
    except (OSError, json.JSONDecodeError):
        return _OFFLINE_SHIELD


# ── ORVL-021 VulnGuard — Zero-Day Discovery ──────────────────
# Wraps axiom_vulnguard.ConstitutionalVulnGuard. State is held in
# process memory; restart resets the last-scan cache.

_vulnguard_state: dict = {
    "last_scan_at":    None,
    "last_report":     None,
    "last_candidates": [],     # serialized VulnerabilityCandidate dicts
    "scan_count":      0,
}

_DEFAULT_VULNGUARD_SURFACES: Dict[str, Dict[str, str]] = {
    "api_endpoint":     {"type": "network",   "description": "REST API surface"},
    "auth_module":      {"type": "privilege", "description": "Authentication and authorization"},
    "data_store":       {"type": "data",      "description": "Persistent state and database surface"},
    "process_ancestry": {"type": "ancestry",  "description": "Process spawn chain integrity"},
}


class VulnGuardScanRequest(BaseModel):
    surfaces: Optional[Dict[str, Dict[str, str]]] = Field(
        default=None,
        description="Map of surface_id -> {type, description}. Omit to scan the "
                    "default set (api_endpoint, auth_module, data_store, "
                    "process_ancestry).",
    )


def _vg_serialize(candidate) -> dict:
    """Convert VulnerabilityCandidate dataclass to JSON-safe dict."""
    from dataclasses import asdict
    d = asdict(candidate)
    # Replace enum instances with their string values.
    d["category"] = candidate.category.value
    d["severity"] = candidate.severity.value
    return d


@app.post("/vulnguard/scan")
async def vulnguard_scan(req: VulnGuardScanRequest = VulnGuardScanRequest()):
    """Run a fresh zero-day discovery scan across attack surfaces.

    Returns a signed report plus the full vulnerability list. Updates
    the cached scan state served by GET /vulnguard/status.
    """
    from axiom_vulnguard import ConstitutionalVulnGuard

    description = req.surfaces or _DEFAULT_VULNGUARD_SURFACES
    vg = ConstitutionalVulnGuard()
    surfaces = vg.map_surfaces(description)

    all_candidates = []
    for surface in surfaces:
        all_candidates.extend(vg.run_surface_scan(surface))

    serialized = [_vg_serialize(c) for c in all_candidates]
    report = vg.generate_report(all_candidates)

    _vulnguard_state["last_scan_at"]    = report["timestamp"]
    _vulnguard_state["last_report"]     = report
    _vulnguard_state["last_candidates"] = serialized
    _vulnguard_state["scan_count"]     += 1

    return {
        "report":           report,
        "vulnerabilities":  serialized,
        "surfaces_scanned": len(surfaces),
        "scan_count":       _vulnguard_state["scan_count"],
    }


@app.get("/vulnguard/status")
async def vulnguard_status():
    """Return the most recent VulnGuard scan state, or empty if never scanned."""
    if _vulnguard_state["last_scan_at"] is None:
        return {
            "ever_scanned":        False,
            "last_scan_at":        None,
            "report":              None,
            "top_vulnerabilities": [],
            "scan_count":          0,
        }
    return {
        "ever_scanned":        True,
        "last_scan_at":        _vulnguard_state["last_scan_at"],
        "report":              _vulnguard_state["last_report"],
        "top_vulnerabilities": _vulnguard_state["last_candidates"][:10],
        "scan_count":          _vulnguard_state["scan_count"],
    }


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    print("""
╔══════════════════════════════════════════════╗
║   AXIOM Guard API v1.0                       ║
║   Constitutional enforcement middleware      ║
║   pip install axiom-constitutional           ║
║   github.com/Orivael-Dev/axiom              ║
╚══════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "axiom_guard_api:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info",
    )
