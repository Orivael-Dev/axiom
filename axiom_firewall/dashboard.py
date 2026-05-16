"""FastAPI dashboard for Axiom Intent Firewall.

Brand: orivael.dev (per docs/PHASE_1_DECISIONS.md §5)

Routes:
  GET  /                        landing page
  GET  /signup, POST /signup    create tenant
  GET  /login,  POST /login     session login
  POST /logout                  clear session
  GET  /dashboard               keys + usage
  POST /dashboard/keys          create new API key
  POST /v1/guard/check          authenticated intent classification

Run:
  uvicorn axiom_firewall.dashboard:app --reload --port 8004
"""
from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from axiom_intent_classifier import IntentClassifier
from axiom_signing import derive_key

from .auth import (
    TIER_PRICE_USD, TIER_RATE_LIMITS,
    authenticate, check_password, hash_password, record_call,
)
from .db import (
    find_tenant_by_email, find_tenant_by_id, init_registry,
    insert_api_key, insert_tenant, list_api_keys, usage_summary,
)
from .models import ApiKey, Tenant

BRAND_DOMAIN = "orivael.dev"
DASHBOARD_HOST = f"firewall.{BRAND_DOMAIN}"
DOCS_HOST = f"docs.{BRAND_DOMAIN}"
API_HOST = f"api.{BRAND_DOMAIN}"

SESSION_SECRET = os.environ.get(
    "AXIOM_FIREWALL_SESSION_SECRET",
    "dev-only-replace-before-deploy",
)

app = FastAPI(title="Axiom Intent Firewall — Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

init_registry()
_classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))


def _ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "brand_domain": BRAND_DOMAIN,
        "dashboard_host": DASHBOARD_HOST,
        "docs_host": DOCS_HOST,
        "api_host": API_HOST,
        "tier_limits": TIER_RATE_LIMITS,
        "tier_prices": TIER_PRICE_USD,
        **extra,
    }


def _current_tenant(request: Request) -> Tenant | None:
    tid = request.session.get("tenant_id")
    return find_tenant_by_id(tid) if tid else None


# ─── Public pages ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", _ctx(request))


@app.get("/signup", response_class=HTMLResponse)
def signup_get(request: Request):
    return templates.TemplateResponse(request, "signup.html", _ctx(request))


@app.post("/signup")
def signup_post(request: Request, email: str = Form(...), password: str = Form(...)):
    if find_tenant_by_email(email):
        return templates.TemplateResponse(
            request, "signup.html",
            _ctx(request, error="An account with that email already exists."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "signup.html",
            _ctx(request, error="Password must be at least 8 characters."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    t = Tenant.new(email=email, pw_hash=hash_password(password), tier="free")
    insert_tenant(t)
    request.session["tenant_id"] = t.tenant_id
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse(request, "login.html", _ctx(request))


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    t = find_tenant_by_email(email)
    if not t or not check_password(password, t.pw_hash):
        return templates.TemplateResponse(
            request, "login.html",
            _ctx(request, error="Invalid email or password."),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.session["tenant_id"] = t.tenant_id
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


# ─── Authenticated dashboard ─────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx(
            request, tenant=t,
            keys=list_api_keys(t.tenant_id),
            usage=usage_summary(t.tenant_id),
            tier_limit=TIER_RATE_LIMITS[t.tier],
        ),
    )


@app.post("/dashboard/keys")
def create_key(request: Request, name: str = Form(...)):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    k = ApiKey.new(tenant_id=t.tenant_id, name=name)
    insert_api_key(k)
    request.session["new_secret"] = k.secret
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# ─── Authenticated API ───────────────────────────────────────────────────

_VERDICT_FOR_CLASS = {
    "INFORM":     "allow",
    "CLARIFY":    "allow",
    "REFUSE":     "allow",
    "UNCERTAIN":  "allow",
    "HARM":       "block",
    "DECEIVE":    "block",
}


@app.post("/v1/guard/check")
async def guard_check(request: Request):
    """Authenticated intent classification.

    Header:  Authorization: Bearer axfw_<key>
    Body:    {"text": "<prompt to classify>"}
    Returns: {"verdict": "allow" | "block",
              "intent": {"class", "confidence", "signals", "signature"}}
    """
    started_at = perf_counter()

    auth_header = request.headers.get("Authorization", "")
    secret = auth_header.removeprefix("Bearer ").strip()
    auth = authenticate(secret)
    if not auth:
        raise HTTPException(401, "Invalid or missing API key")
    tenant, key = auth

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be valid JSON")

    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "Field 'text' must be a string")

    result = _classifier.classify(text)
    verdict = _VERDICT_FOR_CLASS.get(result.intent_class, "allow")

    record_call(
        tenant_id=tenant.tenant_id, key_id=key.key_id,
        endpoint="/v1/guard/check",
        verdict=verdict, intent_class=result.intent_class,
        confidence=result.confidence, started_at=started_at,
    )

    return JSONResponse({
        "verdict": verdict,
        "intent": {
            "class": result.intent_class,
            "confidence": result.confidence,
            "signals": list(result.signals),
            "signature": result.signature,
        },
    })
