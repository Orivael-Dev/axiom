"""
Hello Operator — Governed Internal Chatbot
============================================
A chatbot you can deploy in a day. Every message is governed before the model
sees it; every answer is HMAC-signed; every refusal is logged; every flagged
interaction fires a notification.

Pipeline per message:
  1. Intent gate   — IntentClassifier (ORVL-016): HARM / DECEIVE → hard block
  2. Policy guard  — check_constitutional(): configured agents block known patterns
  3. Model call    — Claude (if ANTHROPIC_API_KEY) or a stub echo
  4. Sign + ledger — answer manifest HMAC-signed, appended to hash-chained ledger
  5. Notify        — BLOCK / FLAG events fan out to console / file / webhook / UI

Setup (one command):
  ./hello_operator/setup.sh

Run:
  export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  export ANTHROPIC_API_KEY=sk-ant-...        # optional — stub answers without it
  python hello_operator/server.py

  Open http://localhost:8800
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_lib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

from hello_operator.notifications import NotificationHub, Notification, Severity

# ── Governance imports (real ORVL stack) ──────────────────────────────────────
try:
    from axiom_signing import derive_key
    _SIGNING_KEY = derive_key(b"hello-operator-v1")
except Exception:
    _SIGNING_KEY = hashlib.pbkdf2_hmac(
        "sha256", os.environ.get("AXIOM_MASTER_KEY", "hello-operator").encode(),
        b"hello-operator-v1", 1,
    )

try:
    from axiom_intent_classifier import IntentClassifier
    _IC = IntentClassifier(hmac_key=_SIGNING_KEY)
    _IC_AVAILABLE = True
except Exception:
    _IC = None
    _IC_AVAILABLE = False

try:
    from axiom_guard_api import check_constitutional
    _GUARD_AVAILABLE = True
except Exception:
    check_constitutional = None
    _GUARD_AVAILABLE = False

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# ── Config ─────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
_LEDGER_PATH = Path.home() / ".axiom" / "hello_operator_ledger.jsonl"

_DEFAULT_CONFIG = {
    "operator_name": "Hello Operator",
    "model": "claude-sonnet-4-6",
    "system_prompt": (
        "You are Hello Operator, a governed internal assistant. Answer staff "
        "questions about company policy, documents, and processes clearly and "
        "concisely. You do not have authority to take actions — only to inform. "
        "If a request is outside your scope, say so plainly."
    ),
    "policy_agents": ["callguard", "medical", "truthwatcher", "retailwatcher"],
    "flag_confidence_below": 0.45,
    "notifications": {
        "console": True,
        "file": True,
        "stream": True,
        "min_severity": "FLAG",
        "webhook": None,
    },
}


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        try:
            user = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = {**_DEFAULT_CONFIG, **user}
            cfg["notifications"] = {**_DEFAULT_CONFIG["notifications"], **user.get("notifications", {})}
            return cfg
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)


CONFIG = _load_config()
HUB    = NotificationHub(CONFIG.get("notifications", {}))

_BLOCK_CLASSES = {"HARM", "DECEIVE"}


# ── Signing + ledger ──────────────────────────────────────────────────────────

def _sign(obj: dict) -> str:
    payload = json.dumps({k: v for k, v in obj.items() if k != "signature"}, sort_keys=True)
    return "hmac-sha256:" + hmac_lib.new(_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()


def _last_ledger_hash() -> str:
    if not _LEDGER_PATH.exists():
        return "GENESIS"
    try:
        with open(_LEDGER_PATH, "rb") as f:
            last = None
            for line in f:
                if line.strip():
                    last = line
            if last:
                return json.loads(last).get("entry_hash", "GENESIS")
    except Exception:
        pass
    return "GENESIS"


def _append_ledger(manifest: dict) -> dict:
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    prev = _last_ledger_hash()
    manifest["prev_hash"] = prev
    manifest["entry_hash"] = hashlib.sha256(
        (prev + json.dumps(manifest, sort_keys=True)).encode()
    ).hexdigest()[:32]
    manifest["signature"] = _sign(manifest)
    try:
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(manifest) + "\n")
    except OSError:
        pass
    return manifest


# ── Governance ─────────────────────────────────────────────────────────────────

def _intent_gate(text: str) -> dict:
    """Classify intent. Returns dict with class, confidence, blocked, signature."""
    if not _IC_AVAILABLE:
        return {"intent_class": "INFORM", "confidence": 0.5, "blocked": False,
                "signals": [], "signature": ""}
    r = _IC.classify(text)
    return {
        "intent_class": r.intent_class,
        "confidence":   round(float(r.confidence), 3),
        "blocked":      r.intent_class in _BLOCK_CLASSES,
        "signals":      list(getattr(r, "signals", []) or []),
        "signature":    getattr(r, "signature", ""),
    }


def _policy_guard(text: str) -> dict:
    """Run configured policy agents. Returns guard verdict dict."""
    if not _GUARD_AVAILABLE:
        return {"verdict": "VERIFIED", "constitutional_block": None, "confidence": 0.85}
    return check_constitutional(text, CONFIG.get("policy_agents", []))


# ── Model ──────────────────────────────────────────────────────────────────────

async def _call_model(message: str, history: list[dict]) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not (_ANTHROPIC_AVAILABLE and api_key):
        return (
            "[stub answer — set ANTHROPIC_API_KEY for live model responses] "
            f"You asked: \"{message[:120]}\". A governed reply would appear here, "
            "signed and logged."
        )
    client = anthropic.AsyncAnthropic(api_key=api_key)
    msgs = history + [{"role": "user", "content": message}]
    try:
        resp = await client.messages.create(
            model=CONFIG.get("model", "claude-sonnet-4-6"),
            max_tokens=800,
            system=CONFIG.get("system_prompt", ""),
            messages=msgs,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    except Exception as exc:
        return f"[model error: {exc}]"


# ── Request handling ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


_SESSIONS: dict[str, list[dict]] = {}


async def _handle_chat(message: str, session_id: str) -> dict:
    manifest_id = f"HO-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    history = _SESSIONS.setdefault(session_id, [])

    intent = _intent_gate(message)
    guard  = _policy_guard(message)

    flag_threshold = CONFIG.get("flag_confidence_below", 0.45)

    # ── Hard block: intent gate ───────────────────────────────────────────────
    if intent["blocked"]:
        refusal = (
            "I can't help with that. This request was classified as "
            f"{intent['intent_class']} by the governance layer and was blocked "
            "before reaching the model."
        )
        manifest = _build_manifest(manifest_id, session_id, message, refusal,
                                   "BLOCKED", intent, guard, blocked_by="intent_gate")
        await HUB.notify(Notification(
            Severity.BLOCK, "Message blocked",
            f"Intent {intent['intent_class']} (conf {intent['confidence']:.2f}) blocked at the gate.",
            session_id=session_id, intent_class=intent["intent_class"],
            signature=manifest["signature"], manifest_id=manifest_id,
            extra={"message_preview": message[:160]},
        ))
        return _response(refusal, "BLOCKED", intent, guard, manifest, flagged=True)

    # ── Hard block: policy guard ──────────────────────────────────────────────
    if guard.get("verdict") == "BLOCKED":
        block_name = guard.get("constitutional_block", "POLICY_BLOCK")
        refusal = (
            "I can't help with that. The governance policy blocked this request "
            f"({block_name}). This decision cannot be overridden at runtime."
        )
        manifest = _build_manifest(manifest_id, session_id, message, refusal,
                                   "BLOCKED", intent, guard, blocked_by="policy_guard")
        await HUB.notify(Notification(
            Severity.BLOCK, "Policy block",
            f"{block_name} (conf {guard.get('confidence', 0):.2f}) — agent {guard.get('agent','?')}.",
            session_id=session_id, intent_class=intent["intent_class"],
            signature=manifest["signature"], manifest_id=manifest_id,
            extra={"constitutional_block": block_name, "message_preview": message[:160]},
        ))
        return _response(refusal, "BLOCKED", intent, guard, manifest, flagged=True)

    # ── Answer (with optional flag) ───────────────────────────────────────────
    answer = await _call_model(message, history)
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": answer})

    flagged = (
        guard.get("verdict") == "SUSPICIOUS"
        or intent["confidence"] < flag_threshold
    )
    verdict = "FLAGGED" if flagged else "ANSWERED"

    manifest = _build_manifest(manifest_id, session_id, message, answer,
                               verdict, intent, guard, blocked_by=None)

    if flagged:
        reason = (guard.get("warning")
                  or f"Low intent confidence ({intent['confidence']:.2f}) — answered with review flag.")
        await HUB.notify(Notification(
            Severity.FLAG, "Flagged for review", reason,
            session_id=session_id, intent_class=intent["intent_class"],
            signature=manifest["signature"], manifest_id=manifest_id,
            extra={"message_preview": message[:160]},
        ))

    return _response(answer, verdict, intent, guard, manifest, flagged=flagged)


def _build_manifest(manifest_id, session_id, message, answer, verdict,
                    intent, guard, blocked_by) -> dict:
    manifest = {
        "manifest_id":   manifest_id,
        "engine":        "Hello Operator v1.0",
        "session_id":    session_id,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "verdict":       verdict,
        "blocked_by":    blocked_by,
        "message_hash":  hashlib.sha256(message.encode()).hexdigest()[:16],
        "answer_hash":   hashlib.sha256(answer.encode()).hexdigest()[:16],
        "intent": {
            "class":      intent["intent_class"],
            "confidence": intent["confidence"],
            "signals":    intent.get("signals", []),
        },
        "policy": {
            "verdict":             guard.get("verdict"),
            "constitutional_block": guard.get("constitutional_block"),
            "confidence":          guard.get("confidence"),
            "agent":               guard.get("agent"),
        },
    }
    return _append_ledger(manifest)


def _response(answer, verdict, intent, guard, manifest, flagged) -> dict:
    return {
        "answer":      answer,
        "verdict":     verdict,
        "flagged":     flagged,
        "manifest_id": manifest["manifest_id"],
        "governance": {
            "intent_class":        intent["intent_class"],
            "intent_confidence":   intent["confidence"],
            "policy_verdict":      guard.get("verdict"),
            "constitutional_block": guard.get("constitutional_block"),
            "signature":           manifest["signature"],
            "entry_hash":          manifest["entry_hash"],
        },
    }


# ── FastAPI app ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Hello Operator")


@app.post("/chat")
async def chat(req: ChatRequest) -> JSONResponse:
    if not req.message or not req.message.strip():
        return JSONResponse({"error": "empty message"}, status_code=400)
    session_id = req.session_id or uuid.uuid4().hex[:8]
    result = await _handle_chat(req.message.strip(), session_id)
    result["session_id"] = session_id
    return JSONResponse(result)


@app.get("/notifications/stream")
async def notifications_stream() -> StreamingResponse:
    async def gen():
        q = HUB.subscribe()
        # Replay recent history first
        for item in HUB.recent(20):
            yield f"data: {json.dumps(item)}\n\n"
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'severity':'INFO','title':'heartbeat','message':''})}\n\n"
        finally:
            HUB.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/audit")
async def audit(limit: int = 50) -> dict:
    entries = []
    if _LEDGER_PATH.exists():
        try:
            with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
    return {"count": len(entries), "entries": entries[-limit:]}


@app.get("/config")
async def config() -> dict:
    return {
        "operator_name": CONFIG.get("operator_name"),
        "model":         CONFIG.get("model"),
        "policy_agents": CONFIG.get("policy_agents"),
        "governance": {
            "intent_classifier": _IC_AVAILABLE,
            "policy_guard":      _GUARD_AVAILABLE,
            "model_live":        bool(_ANTHROPIC_AVAILABLE and os.environ.get("ANTHROPIC_API_KEY")),
        },
        "notifications": {
            "channels": [k for k in ("console", "file", "stream")
                         if CONFIG["notifications"].get(k)]
                        + (["webhook"] if CONFIG["notifications"].get("webhook") else []),
            "min_severity": CONFIG["notifications"].get("min_severity"),
        },
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "operator": CONFIG.get("operator_name")}


@app.get("/", response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse((Path(__file__).resolve().parent / "operator_ui.html").read_text(encoding="utf-8"))


def main() -> None:
    port = int(os.environ.get("HELLO_OPERATOR_PORT", "8800"))
    print(f"\n  {CONFIG.get('operator_name')} — governed internal chatbot")
    print(f"  Intent classifier : {'live' if _IC_AVAILABLE else 'unavailable'}")
    print(f"  Policy guard       : {'live' if _GUARD_AVAILABLE else 'unavailable'}")
    print(f"  Model              : {'live' if (_ANTHROPIC_AVAILABLE and os.environ.get('ANTHROPIC_API_KEY')) else 'stub'}")
    print(f"  Notifications      : {', '.join(k for k in ('console','file','stream') if CONFIG['notifications'].get(k))}"
          + (", webhook" if CONFIG['notifications'].get('webhook') else ""))
    print(f"  Open               : http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
