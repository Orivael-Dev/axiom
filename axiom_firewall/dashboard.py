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

from fastapi import FastAPI, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from axiom_intent_classifier import IntentClassifier
from axiom_signing import derive_key

from . import billing, policy as policy_mod
from .auth import (
    TIER_PRICE_USD, TIER_RATE_LIMITS,
    authenticate, check_password, hash_password, record_call,
)
from .db import (
    find_tenant_by_email, find_tenant_by_id, init_registry,
    insert_api_key, insert_tenant, list_api_keys, usage_summary,
)
from .limits import (
    SIGNUP_MAX_PER_WINDOW, SIGNUP_WINDOW_SECONDS,
    check_monthly_quota, check_signup_rate,
    monthly_usage_count, seconds_until_next_month,
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


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For when behind a proxy."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/signup")
def signup_post(request: Request, email: str = Form(...), password: str = Form(...)):
    ip = _client_ip(request)
    allowed, retry_after = check_signup_rate(ip)
    if not allowed:
        headers = {"Retry-After": str(retry_after)}
        return templates.TemplateResponse(
            request, "signup.html",
            _ctx(
                request,
                error=(
                    f"Too many signup attempts from this IP. "
                    f"Try again in {retry_after // 60} minute(s)."
                ),
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=headers,
        )
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
    month_used = monthly_usage_count(t.tenant_id)
    tier_limit = TIER_RATE_LIMITS[t.tier]
    usage_pct = round(100 * month_used / tier_limit, 1) if tier_limit else 0.0
    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx(
            request, tenant=t,
            keys=list_api_keys(t.tenant_id),
            usage=usage_summary(t.tenant_id),
            month_used=month_used,
            usage_pct=min(usage_pct, 100.0),
            tier_limit=tier_limit,
            free_tier=(t.tier == "free"),
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


# ─── Policy editor ──────────────────────────────────────────────────────


_DEFAULT_POLICY_TEMPLATE = """{
  "version": 1,
  "additional_block_patterns": [
    {"class": "HARM",    "regex": "leak the customer list"},
    {"class": "DECEIVE", "regex": "pretend you are human"}
  ],
  "disabled_default_classes": [],
  "allow_only_classes": null
}
"""


@app.get("/dashboard/policy", response_class=HTMLResponse)
def policy_get(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    body = policy_mod.get_policy_body(t.tenant_id)
    return templates.TemplateResponse(
        request, "policy.html",
        _ctx(
            request, tenant=t,
            policy_body=body or _DEFAULT_POLICY_TEMPLATE,
            has_policy=bool(body),
        ),
    )


@app.post("/dashboard/policy")
def policy_post(request: Request, body: str = Form(...)):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    try:
        policy_mod.save_policy(t.tenant_id, body)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "policy.html",
            _ctx(
                request, tenant=t,
                policy_body=body,
                error=str(e),
                has_policy=True,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return templates.TemplateResponse(
        request, "policy.html",
        _ctx(
            request, tenant=t,
            policy_body=body,
            saved=True,
            has_policy=True,
        ),
    )


@app.post("/dashboard/policy/delete")
def policy_delete(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    policy_mod.delete_policy(t.tenant_id)
    return RedirectResponse("/dashboard/policy", status_code=status.HTTP_303_SEE_OTHER)


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

    Returns 429 (with Retry-After) when a free-tier tenant has used
    their monthly quota. Paid tiers have no hard cap — Stripe metered
    billing handles overage.
    """
    started_at = perf_counter()

    auth_header = request.headers.get("Authorization", "")
    secret = auth_header.removeprefix("Bearer ").strip()
    auth = authenticate(secret)
    if not auth:
        raise HTTPException(401, "Invalid or missing API key")
    tenant, key = auth

    quota_ok, used, cap = check_monthly_quota(tenant)
    if not quota_ok:
        retry = seconds_until_next_month()
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry)},
            content={
                "detail": (
                    f"Monthly quota exhausted for the free tier "
                    f"({used}/{cap} calls). Upgrade at /billing or "
                    f"wait {retry // 86400} day(s)."
                ),
                "used": used,
                "limit": cap,
                "retry_after_seconds": retry,
            },
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be valid JSON")

    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "Field 'text' must be a string")

    base_result = _classifier.classify(text)
    tenant_policy = policy_mod.get_policy(tenant.tenant_id)
    verdict, final_result = policy_mod.apply_policy(base_result, tenant_policy, text)

    record_call(
        tenant_id=tenant.tenant_id, key_id=key.key_id,
        endpoint="/v1/guard/check",
        verdict=verdict, intent_class=final_result.intent_class,
        confidence=final_result.confidence, started_at=started_at,
    )

    return JSONResponse({
        "verdict": verdict,
        "intent": {
            "class": final_result.intent_class,
            "confidence": final_result.confidence,
            "signals": list(final_result.signals),
            "signature": final_result.signature,
        },
    })


# ─── Billing ─────────────────────────────────────────────────────────────


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "billing.html",
        _ctx(
            request, tenant=t,
            billing_enabled=billing.is_enabled(),
            has_subscription=bool(t.stripe_subscription_id),
        ),
    )


@app.post("/billing/upgrade/{tier}")
def billing_upgrade(request: Request, tier: str):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not billing.is_enabled():
        raise HTTPException(503, "Billing is not configured on this deployment")
    try:
        url = billing.create_checkout_session(t, tier)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/billing/portal")
def billing_portal(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not billing.is_enabled():
        raise HTTPException(503, "Billing is not configured on this deployment")
    try:
        url = billing.create_portal_session(t)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/billing/success", response_class=HTMLResponse)
def billing_success(request: Request):
    return templates.TemplateResponse(request, "billing_success.html", _ctx(request))


@app.get("/billing/cancel", response_class=HTMLResponse)
def billing_cancel(request: Request):
    return templates.TemplateResponse(request, "billing_cancel.html", _ctx(request))


@app.post("/billing/webhook")
async def billing_webhook(request: Request,
                          stripe_signature: str = Header(default="")):
    if not billing.is_enabled():
        raise HTTPException(503, "Billing is not configured on this deployment")
    payload = await request.body()
    try:
        event = billing.verify_and_parse_webhook(payload, stripe_signature)
    except Exception as e:
        raise HTTPException(400, f"Webhook signature verification failed: {e}")
    result = billing.handle_event(event)
    return JSONResponse({"ok": True, "result": result})
