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

import logging
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Optional

from fastapi import FastAPI, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from axiom_intent_classifier import IntentClassifier
from axiom_signing import derive_key

from . import billing, policy as policy_mod, registry_client, skill_pack
from .auth import (
    TIER_PRICE_USD, TIER_RATE_LIMITS,
    authenticate, check_password, generate_recovery_code, hash_password,
    normalize_recovery_code, record_call,
)
from .db import (
    delete_tenant, find_tenant_by_email, find_tenant_by_id, init_registry,
    insert_api_key, insert_tenant, list_api_keys, revoke_api_key,
    update_tenant_password, update_tenant_recovery_hash, usage_summary,
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

# Deployment environment — "development" (default) or "production". In
# production we refuse to boot with the dev session secret and force
# Secure cookies. Set this in the prod compose file / k8s manifest.
AXIOM_ENV = os.environ.get("AXIOM_ENV", "development").strip().lower()
_IS_PROD = AXIOM_ENV == "production"

_DEV_SESSION_SECRET = "dev-only-replace-before-deploy"
SESSION_SECRET = os.environ.get(
    "AXIOM_FIREWALL_SESSION_SECRET",
    _DEV_SESSION_SECRET,
)

# Hard-fail at import time when AXIOM_ENV=production but the session
# secret is still the dev default (or too short to be unguessable).
# A boot-time RuntimeError surfaces in `docker compose up` and
# orchestrator dashboards immediately; a runtime warning is too easy
# to miss in busy log streams.
if _IS_PROD:
    if SESSION_SECRET == _DEV_SESSION_SECRET:
        raise RuntimeError(
            "AXIOM_ENV=production but AXIOM_FIREWALL_SESSION_SECRET is the "
            "dev default. Set it to a 32+ byte random string "
            "(`python3 -c 'import secrets; print(secrets.token_hex(32))'`) "
            "before booting. Refusing to start — session cookies would be "
            "forgeable by anyone with the source."
        )
    if len(SESSION_SECRET) < 32:
        raise RuntimeError(
            "AXIOM_ENV=production requires AXIOM_FIREWALL_SESSION_SECRET "
            f"to be at least 32 characters; got {len(SESSION_SECRET)}. "
            "Generate one with "
            "`python3 -c 'import secrets; print(secrets.token_hex(32))'`."
        )

# Beta-tester touchpoints. Set AXIOM_FIREWALL_BETA_FEEDBACK to a
# mailto: or URL when running a beta; renders a footer link + a
# welcome banner pointing at it. Leave blank to suppress (post-beta).
BETA_FEEDBACK_URL = os.environ.get(
    "AXIOM_FIREWALL_BETA_FEEDBACK", "",
).strip()

# Beta mode — disables self-serve upgrade buttons in /billing in favour
# of "Contact sales" mailto links. Set AXIOM_FIREWALL_BETA_MODE=0 to
# re-enable Stripe checkout once GA pricing is locked. Defaults to ON
# during the beta period — Stripe checkout works (the routes are still
# wired), but the UI funnels prospects to a human conversation first.
BETA_MODE = os.environ.get(
    "AXIOM_FIREWALL_BETA_MODE", "1",
).strip().lower() in ("1", "true", "yes")

# Sales contact email for the "Contact sales" CTA during beta + for the
# Enterprise tier always.
SALES_EMAIL = os.environ.get(
    "AXIOM_FIREWALL_SALES_EMAIL", f"sales@{BRAND_DOMAIN}",
).strip()

# CORS — locked down by default. Set AXIOM_FIREWALL_CORS_ORIGINS to a
# comma-separated list of origins (or "*" to allow all). The signup /
# dashboard pages are served same-origin, so this only matters for
# /v1/guard/check called directly from a browser.
_cors_origins_raw = os.environ.get("AXIOM_FIREWALL_CORS_ORIGINS", "").strip()
CORS_ORIGINS: list[str] = (
    [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    if _cors_origins_raw
    else []
)

log = logging.getLogger("axiom_firewall")
# Emit to stdout so the startup banner + warnings show up in container
# logs without requiring a custom uvicorn --log-config.
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s:    %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    log.info("axiom-firewall starting")
    log.info("  env:             %s", AXIOM_ENV)
    log.info("  brand:           %s", BRAND_DOMAIN)
    log.info("  tenant dir:      %s",
             os.environ.get("AXIOM_FIREWALL_TENANT_DIR", "tenants"))
    log.info("  billing enabled: %s", billing.is_enabled())
    log.info("  cors origins:    %s", CORS_ORIGINS or "(none)")
    if not _IS_PROD and SESSION_SECRET == _DEV_SESSION_SECRET:
        log.warning(
            "AXIOM_FIREWALL_SESSION_SECRET is using the dev default. "
            "Set it to a 32+ byte random string before exposing the "
            "dashboard publicly — otherwise anyone can forge session cookies. "
            "Set AXIOM_ENV=production to make this a hard error."
        )
    yield
    log.info("axiom-firewall shutting down")


app = FastAPI(
    title="Axiom Intent Firewall — Dashboard",
    lifespan=_lifespan,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=3600,
    )

# Session cookie hardening. Starlette's SessionMiddleware sets
# HttpOnly=True by default; we additionally pin SameSite=lax (blocks
# cross-site cookie sends on CSRF-shaped POSTs while still allowing
# top-level navigations from email/docs links) and force Secure=True
# in production so the browser refuses to send the cookie over plain
# HTTP. `max_age=14*86400` rolls sessions every 2 weeks instead of
# letting them live forever.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=_IS_PROD,
    max_age=14 * 86400,
)


class _RequestIdMiddleware(BaseHTTPMiddleware):
    """Tag every response + log entry with an X-Request-ID for tracing."""

    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response


# Hard caps on request body size. Caddy/ALB will usually enforce this
# upstream, but a defence-in-depth check inside the app means a direct
# uvicorn deployment (or a misconfigured reverse proxy) can't be DoS'd
# by a 1 GB form post. The `/v1/guard/check` API is intentionally lower
# than dashboard forms — guard requests are small JSON, no file upload.
MAX_REQUEST_BODY_BYTES         = 1 * 1024 * 1024    # 1 MiB — dashboard forms
MAX_GUARD_API_BODY_BYTES       = 256 * 1024         # 256 KiB — /v1/guard/check
_GUARD_API_PREFIXES = ("/v1/guard",)


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured cap.

    We check Content-Length pre-read so a malicious 1 GB body never
    lands in memory. For chunked uploads (no Content-Length) we let
    the request through; FastAPI/Starlette enforces its own per-read
    limits on form parsing.
    """

    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                size = int(cl)
            except ValueError:
                return JSONResponse(
                    {"error": "invalid Content-Length"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            limit = (
                MAX_GUARD_API_BODY_BYTES
                if any(request.url.path.startswith(p) for p in _GUARD_API_PREFIXES)
                else MAX_REQUEST_BODY_BYTES
            )
            if size > limit:
                return JSONResponse(
                    {"error": "request body too large", "limit_bytes": limit},
                    status_code=413,
                )
        return await call_next(request)


app.add_middleware(_RequestIdMiddleware)
app.add_middleware(_BodySizeLimitMiddleware)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Live-docs source. Read from the repo's docs/firewall/ tree so a
# single Markdown edit updates both the public docs site and the
# in-dashboard /help page.
FIREWALL_DOCS_DIR = BASE_DIR.parent / "docs" / "firewall"

init_registry()
_classifier = IntentClassifier(derive_key(b"axiom-firewall-v1"))


# ─── Health checks (ALB / Kubernetes liveness + readiness) ──────────────

@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness probe — the process is running. Never blocks; never hits the DB."""
    return JSONResponse({"status": "ok"})


@app.get("/readyz", include_in_schema=False)
def readyz():
    """Readiness probe — the process can serve traffic. Validates DB write.

    Returns 503 if the registry DB is unreachable (disk full / permissions /
    EFS hiccup). ALB / Kubernetes pull the pod out of rotation on 503.
    """
    try:
        init_registry()
        return JSONResponse({"status": "ready"})
    except Exception as e:
        log.exception("readiness check failed")
        return JSONResponse(
            {"status": "unready", "error": str(e)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def _ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "brand_domain": BRAND_DOMAIN,
        "dashboard_host": DASHBOARD_HOST,
        "docs_host": DOCS_HOST,
        "api_host": API_HOST,
        "tier_limits": TIER_RATE_LIMITS,
        "tier_prices": TIER_PRICE_USD,
        "beta_feedback_url": BETA_FEEDBACK_URL,
        "beta_mode":         BETA_MODE,
        "sales_email":       SALES_EMAIL,
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
    recovery_code = generate_recovery_code()
    t = Tenant.new(
        email=email,
        pw_hash=hash_password(password),
        tier="free",
        recovery_hash=hash_password(recovery_code),
    )
    insert_tenant(t)
    request.session["tenant_id"] = t.tenant_id
    # Stash the plaintext recovery code so the dashboard shows it ONCE.
    # Popped on render; never persisted past the next page view.
    request.session["new_recovery_code"] = recovery_code
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


@app.post("/dashboard/keys/{key_id}/revoke")
def revoke_key(request: Request, key_id: str):
    """Revoke an API key. Soft-delete: sets revoked_at + clears the
    bearer-hash so the key stops authenticating immediately, but the
    row stays for billing/audit joins. tenant_id comes from the
    session — never from the request — so a logged-in tenant can
    only revoke its OWN keys."""
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    revoked = revoke_api_key(t.tenant_id, key_id)
    request.session["revoke_result"] = (
        "ok" if revoked else "not_found"
    )
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# ─── Password reset (recovery-code flow) ────────────────────────────────
#
# No SMTP infra at launch. We give every tenant a single-use recovery
# code at signup (like a 2FA backup code). To reset: enter email +
# recovery code → set a new password. A fresh recovery code is issued
# at the end so the user always has exactly one usable code.


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_get(request: Request):
    return templates.TemplateResponse(
        request, "forgot_password.html", _ctx(request),
    )


@app.post("/forgot-password")
def forgot_password_post(
    request: Request,
    email: str = Form(...),
    recovery_code: str = Form(...),
    new_password: str = Form(...),
):
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request, "forgot_password.html",
            _ctx(request, error="New password must be at least 8 characters."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    # Treat email-not-found + bad-code identically to avoid leaking which
    # emails are registered. Constant-time PBKDF2 against a dummy hash on
    # the not-found branch to keep timing comparable.
    t = find_tenant_by_email(email)
    code_ok = False
    if t and t.recovery_hash:
        code_ok = check_password(normalize_recovery_code(recovery_code), t.recovery_hash)
    else:
        # Run a PBKDF2 anyway so the no-account branch isn't instantly fast.
        check_password("dummy", hash_password("dummy"))
    if not t or not code_ok:
        return templates.TemplateResponse(
            request, "forgot_password.html",
            _ctx(request, error="Email + recovery code did not match."),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    update_tenant_password(t.tenant_id, pw_hash=hash_password(new_password))
    new_code = generate_recovery_code()
    update_tenant_recovery_hash(
        t.tenant_id, recovery_hash=hash_password(new_code),
    )
    # Auto-login the user + show them the fresh recovery code once.
    request.session["tenant_id"] = t.tenant_id
    request.session["new_recovery_code"] = new_code
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# ─── Account settings + deletion ────────────────────────────────────────


@app.get("/dashboard/account", response_class=HTMLResponse)
def account_get(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "account.html",
        _ctx(request, tenant=t, has_subscription=bool(t.stripe_subscription_id)),
    )


@app.post("/dashboard/account/recovery/rotate")
def account_rotate_recovery(request: Request, password: str = Form(...)):
    """Issue a fresh recovery code. Requires current password as proof."""
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not check_password(password, t.pw_hash):
        return templates.TemplateResponse(
            request, "account.html",
            _ctx(
                request, tenant=t,
                has_subscription=bool(t.stripe_subscription_id),
                error="Current password did not match — recovery code NOT rotated.",
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    new_code = generate_recovery_code()
    update_tenant_recovery_hash(
        t.tenant_id, recovery_hash=hash_password(new_code),
    )
    request.session["new_recovery_code"] = new_code
    return RedirectResponse(
        "/dashboard/account", status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/dashboard/account/password")
def account_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not check_password(current_password, t.pw_hash):
        return templates.TemplateResponse(
            request, "account.html",
            _ctx(
                request, tenant=t,
                has_subscription=bool(t.stripe_subscription_id),
                error="Current password did not match.",
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request, "account.html",
            _ctx(
                request, tenant=t,
                has_subscription=bool(t.stripe_subscription_id),
                error="New password must be at least 8 characters.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    update_tenant_password(t.tenant_id, pw_hash=hash_password(new_password))
    return templates.TemplateResponse(
        request, "account.html",
        _ctx(
            request, tenant=t,
            has_subscription=bool(t.stripe_subscription_id),
            password_saved=True,
        ),
    )


@app.post("/dashboard/account/delete")
def account_delete(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(""),
):
    """Permanently delete the tenant + all per-tenant data.

    Requires:
      - Logged-in session
      - Current password (anti-CSRF-ish + intent confirmation)
      - The literal word DELETE in the `confirm` field

    Cascade order:
      1. Best-effort cancel Stripe subscription (don't fail deletion if Stripe errors)
      2. Drop tenant row + per-tenant SQLite file
      3. Clear session, redirect to landing
    """
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not check_password(password, t.pw_hash):
        return templates.TemplateResponse(
            request, "account.html",
            _ctx(
                request, tenant=t,
                has_subscription=bool(t.stripe_subscription_id),
                error="Password did not match — account NOT deleted.",
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if confirm.strip() != "DELETE":
        return templates.TemplateResponse(
            request, "account.html",
            _ctx(
                request, tenant=t,
                has_subscription=bool(t.stripe_subscription_id),
                error='Type the word DELETE (uppercase) to confirm.',
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    # Best-effort Stripe subscription cancellation.
    if t.stripe_subscription_id and billing.is_enabled():
        try:
            stripe = billing._stripe()
            stripe.Subscription.delete(t.stripe_subscription_id)
        except Exception:
            log.exception(
                "stripe subscription cancellation failed for tenant %s "
                "during account deletion — proceeding anyway",
                t.tenant_id,
            )
    delete_tenant(t.tenant_id)
    request.session.clear()
    return templates.TemplateResponse(
        request, "account_deleted.html", _ctx(request),
    )


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


# ─── Skill Pack browser + installer ─────────────────────────────────────


# Pack source: registry HTTP service (preferred for production) or
# local filesystem (default for self-host + dev).
PACKS_DIR = Path(os.environ.get(
    "AXIOM_FIREWALL_PACKS_DIR",
    str(BASE_DIR.parent / "packs"),
))
REGISTRY_URL = os.environ.get("AXIOM_FIREWALL_REGISTRY_URL", "").strip()


def _list_available_packs() -> list[skill_pack.SkillPackManifest]:
    """Available packs from the configured source (registry if set, else FS).

    Filesystem packs go through verify_first_party() too — same defense
    as the registry. The dashboard NEVER serves an unsigned pack to the
    install flow.
    """
    if REGISTRY_URL:
        try:
            return registry_client.list_packs(REGISTRY_URL)
        except registry_client.RegistryError as e:
            log.warning(
                "could not reach pack registry at %s: %s — falling back to local FS",
                REGISTRY_URL, e,
            )
            # Fall through to local FS so dashboard stays usable even
            # when the registry is down.
    if not PACKS_DIR.exists():
        return []
    out: list[skill_pack.SkillPackManifest] = []
    for entry in sorted(PACKS_DIR.iterdir()):
        manifest_path = entry / "pack.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = skill_pack.SkillPackManifest.parse(
                manifest_path.read_text(encoding="utf-8")
            )
        except (ValueError, OSError) as e:
            log.warning("skipping pack at %s: %s", manifest_path, e)
            continue
        if skill_pack.verify_first_party(manifest):
            out.append(manifest)
        else:
            log.warning("skipping unsigned pack at %s", manifest_path)
    return out


def _load_pack(name: str) -> skill_pack.SkillPackManifest | None:
    """Load one named pack from the configured source.

    Verifies the first-party signature before returning. Returns None
    on missing pack OR invalid signature.
    """
    if not name or "/" in name or ".." in name:
        return None
    if REGISTRY_URL:
        try:
            return registry_client.get_pack(REGISTRY_URL, name)
        except registry_client.RegistryError as e:
            log.warning(
                "could not fetch %s from registry: %s — falling back to local FS",
                name, e,
            )
    manifest_path = PACKS_DIR / name / "pack.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = skill_pack.SkillPackManifest.parse(
            manifest_path.read_text(encoding="utf-8")
        )
    except (ValueError, OSError):
        return None
    if not skill_pack.verify_first_party(manifest):
        return None
    return manifest


@app.get("/dashboard/packs", response_class=HTMLResponse)
def packs_index(request: Request):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    installed_packs = skill_pack.list_installed_packs(t.tenant_id)
    return templates.TemplateResponse(
        request, "packs.html",
        _ctx(
            request, tenant=t,
            packs=_list_available_packs(),
            installed_packs=installed_packs,
            installed_names={ip.name for ip in installed_packs},
            registry_url=REGISTRY_URL or None,
        ),
    )


@app.post("/dashboard/packs/install")
def packs_install(request: Request, name: str = Form(...)):
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    manifest = _load_pack(name)
    if manifest is None:
        raise HTTPException(
            404,
            f"Pack {name!r} not available "
            f"(registry={'enabled' if REGISTRY_URL else 'disabled'}, "
            "signature must be valid)",
        )
    # Signature check is enforced by _load_pack — verify_first_party
    # ran during loading. Double-check here too for defense-in-depth.
    if not skill_pack.verify_first_party(manifest):
        raise HTTPException(
            400,
            f"Pack {name!r} has an invalid or missing signature — refusing install.",
        )
    skill_pack.install_pack(t.tenant_id, manifest)
    return RedirectResponse("/dashboard/packs", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/dashboard/packs/uninstall")
def packs_uninstall(request: Request, name: Optional[str] = Form(None)):
    """Remove a pack from the stack.

    If `name` is provided, only that pack is removed and the merged
    policy is recomputed from the remaining active packs. If `name`
    is empty, every active pack is removed (the "Remove all" CTA).
    `uninstall_pack` itself handles clearing tenant_policy when no
    packs remain — no separate delete_policy call needed.
    """
    t = _current_tenant(request)
    if not t:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    skill_pack.uninstall_pack(t.tenant_id, name=name or None)
    return RedirectResponse("/dashboard/packs", status_code=status.HTTP_303_SEE_OTHER)


# ─── Authenticated API ───────────────────────────────────────────────────

_VERDICT_FOR_CLASS = {
    "INFORM":     "allow",
    "CLARIFY":    "allow",
    "REFUSE":     "allow",
    "UNCERTAIN":  "allow",
    "HARM":       "block",
    "DECEIVE":    "block",
}


# ── /help — live docs served from docs/firewall/*.md ──────────────────


_HELP_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Axiom Firewall</title>
  <link rel="stylesheet" href="/static/style.css">
  <style>
    .help-wrap {{ max-width: 820px; margin: 0 auto; padding: 40px 24px 80px; }}
    .help-topbar {{
      display: flex; justify-content: space-between; align-items: center;
      gap: 12px; flex-wrap: wrap;
      margin-bottom: 32px; padding-bottom: 14px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      font-size: 14px;
    }}
    .help-topbar a {{ color: inherit; text-decoration: none; }}
    .help-topbar a:hover {{ text-decoration: underline; }}
    .help-doc-nav {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 13px; opacity: 0.85; }}
    .help-doc-nav a {{ padding: 2px 0; }}
    .help-doc-nav a.current {{ font-weight: 700; opacity: 1; }}
    .help-wrap h1 {{ font-size: 32px; margin: 0 0 18px; letter-spacing: -0.02em; }}
    .help-wrap h2 {{ font-size: 20px; margin: 36px 0 12px; }}
    .help-wrap h3 {{ font-size: 16px; margin: 26px 0 10px; }}
    .help-wrap p, .help-wrap li {{ line-height: 1.65; }}
    .help-wrap code {{ font-family: ui-monospace, Menlo, Consolas, monospace;
      font-size: 0.9em; background: rgba(255,255,255,0.06); padding: 1px 6px;
      border-radius: 4px; }}
    .help-wrap pre {{ background: rgba(0,0,0,0.35); border: 1px solid
      rgba(255,255,255,0.08); border-radius: 8px; padding: 14px 16px;
      overflow-x: auto; font-size: 13px; line-height: 1.5; }}
    .help-wrap pre code {{ background: transparent; padding: 0; }}
    .help-wrap blockquote {{ border-left: 3px solid var(--accent, #72f7d4);
      margin: 18px 0; padding: 8px 16px;
      background: rgba(114, 247, 212, 0.07); border-radius: 0 8px 8px 0;
      font-style: italic; }}
    .help-wrap table {{ border-collapse: collapse; margin: 18px 0; }}
    .help-wrap th, .help-wrap td {{ border: 1px solid rgba(255,255,255,0.08);
      padding: 8px 12px; }}
  </style>
</head>
<body>
  <div class="help-wrap">
    <div class="help-topbar">
      <a href="/dashboard">← Dashboard</a>
      <nav class="help-doc-nav" aria-label="Firewall docs">{nav}</nav>
    </div>
    {body}
  </div>
</body>
</html>
"""


def _help_doc_listing() -> list[tuple[str, str, str]]:
    """Return [(slug, title, path), …] for every .md under docs/firewall/.

    Title is taken from the first `# heading` in the file; falls back
    to the slug if not found. Sorted with index.md first, then
    quickstart.md, then the rest alphabetically.
    """
    if not FIREWALL_DOCS_DIR.is_dir():
        return []
    pref_order = ["index", "quickstart"]
    out: list[tuple[str, str, str]] = []
    for p in FIREWALL_DOCS_DIR.glob("*.md"):
        slug = p.stem
        title = slug.replace("-", " ").title()
        try:
            for line in p.read_text(encoding="utf-8").splitlines()[:8]:
                if line.startswith("# "):
                    title = line[2:].strip() or title
                    break
        except OSError:
            continue
        out.append((slug, title, str(p)))
    out.sort(key=lambda r: (
        pref_order.index(r[0]) if r[0] in pref_order else 999,
        r[1].lower(),
    ))
    return out


def _help_render_nav(current: str) -> str:
    """HTML for the per-doc nav row at the top of /help pages."""
    items = []
    for slug, title, _path in _help_doc_listing():
        klass = "current" if slug == current else ""
        items.append(
            f'<a class="{klass}" href="/help/{slug}">{title}</a>'
        )
    return "".join(items)


def _help_render_markdown(md_text: str) -> str:
    try:
        import markdown as _md
        html = _md.markdown(
            md_text,
            extensions=["fenced_code", "tables", "toc", "sane_lists"],
            output_format="html5",
        )
    except ImportError:
        import html as _html
        return (
            '<p style="color:#ffd166;">⚠ <code>markdown</code> '
            'package not installed — serving raw text.</p>'
            f'<pre>{_html.escape(md_text)}</pre>'
        )
    return _HELP_REL_LINK_RE.sub(_help_rewrite_rel_link, html)


# Rewrite relative cross-doc links of the form `href="<slug>.md"` (with an
# optional fragment) to absolute `/help/<slug>` URLs. Without this the
# rendered HTML carries the raw `.md` href: from /help/index, the browser
# resolves it to /help/<slug>.md, which the /help/{slug} regex rejects
# (no dots allowed) → 404. From /help (no trailing slash) the browser even
# strips the parent segment and lands on /<slug>.md. Absolute URLs, scheme
# URLs, anchor-only links, and root-relative paths are all left alone.
_HELP_REL_LINK_RE = re.compile(
    r'href="(?!https?:|mailto:|//|/|#)([A-Za-z0-9_-]+)\.md(#[^"]*)?"'
)


def _help_rewrite_rel_link(m: re.Match[str]) -> str:
    slug, frag = m.group(1), m.group(2) or ""
    if slug in _HELP_DENYLIST_SLUGS:
        # Author-typed link to an internal doc — preserve the visible
        # anchor text but neutralise the URL so we don't emit a 404.
        return 'href="#"'
    return f'href="/help/{slug}{frag}"'


@app.get("/help", response_class=HTMLResponse)
def help_index(request: Request):
    """Render docs/firewall/index.md (or quickstart.md as a fallback)
    and list every other available doc in the top nav."""
    listing = _help_doc_listing()
    if not listing:
        raise HTTPException(
            status_code=500,
            detail=f"no firewall docs at {FIREWALL_DOCS_DIR}",
        )
    # Pick the first listed slug as the landing page.
    slug, title, path = listing[0]
    md_text = Path(path).read_text(encoding="utf-8")
    html = _HELP_HTML_TEMPLATE.format(
        title=title,
        nav=_help_render_nav(slug),
        body=_help_render_markdown(md_text),
    )
    return HTMLResponse(content=html)


# Operator-only docs that must never be served from /help/<slug>.
# The corresponding files live under docs/firewall/internal/ (which
# the *.md glob already excludes), and the Dockerfile no longer
# copies that subdirectory into the image — but this denylist is
# the third line of defense against the same content reappearing at
# the top level by accident.
_HELP_DENYLIST_SLUGS = frozenset({
    "launch", "billing", "operations-runbook",
})


@app.get("/help/{slug}", response_class=HTMLResponse)
def help_page(request: Request, slug: str):
    """Render docs/firewall/<slug>.md with the per-doc nav row."""
    # Defensive: only allow simple slugs — no traversal.
    if not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid slug")
    if slug in _HELP_DENYLIST_SLUGS:
        raise HTTPException(
            status_code=404,
            detail=f"no doc named {slug!r}",
        )
    target = FIREWALL_DOCS_DIR / f"{slug}.md"
    if not target.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no doc named {slug!r}",
        )
    md_text = target.read_text(encoding="utf-8")
    # Pull the title from the first H1, fall back to a titlecased slug.
    title = slug.replace("-", " ").title()
    for line in md_text.splitlines()[:8]:
        if line.startswith("# "):
            title = line[2:].strip() or title
            break
    html = _HELP_HTML_TEMPLATE.format(
        title=title,
        nav=_help_render_nav(slug),
        body=_help_render_markdown(md_text),
    )
    return HTMLResponse(content=html)


def _render_pack_help_body(manifest: skill_pack.SkillPackManifest) -> str:
    """Render a verified pack manifest as the body of a /help/packs/<name> page.

    The manifest has already been signature-verified by `_load_pack`, so
    this function trusts every field. We render the marketing-shaped
    fields (title / description / version / tags) plus a plain-English
    policy summary, and put the raw policy JSON in a <details> for the
    operators who want to audit the exact regexes."""
    from html import escape as e
    import json as _json

    pol = manifest.policy or {}
    add_patterns = pol.get("additional_block_patterns") or []
    disabled = pol.get("disabled_default_classes") or []
    allow_only = pol.get("allow_only_classes")
    tested = manifest.tested_against or []

    parts: list[str] = []
    parts.append(f"<h1>{e(manifest.title or manifest.name)}</h1>")
    parts.append(
        '<p class="pack-meta"><code>{name}</code> v{ver} · '
        '{author} · {license} · <span title="HMAC-SHA256 first-party '
        'signature verified at load time">signature verified ✓</span></p>'
        .format(
            name=e(manifest.name),
            ver=e(manifest.version),
            author=e(manifest.author or "unknown"),
            license=e(manifest.license or "unspecified"),
        )
    )
    if manifest.description:
        parts.append(f"<p>{e(manifest.description)}</p>")
    if manifest.tags:
        tags_html = "".join(
            f'<span class="pack-tag">{e(t)}</span>'
            for t in manifest.tags
        )
        parts.append(f'<p class="pack-tags">{tags_html}</p>')

    parts.append("<h2>What this pack does</h2><ul>")
    if add_patterns:
        # Group patterns by intent class for a one-line summary per class.
        from collections import Counter
        by_class = Counter(p.get("class", "?") for p in add_patterns)
        breakdown = ", ".join(
            f"{n} {cls.upper()}" for cls, n in sorted(by_class.items())
        )
        parts.append(
            f"<li>Adds <strong>{len(add_patterns)}</strong> block "
            f"pattern(s): {e(breakdown)}.</li>"
        )
    if disabled:
        parts.append(
            "<li>Disables default block classes: "
            + ", ".join(f"<code>{e(c)}</code>" for c in disabled)
            + ".</li>"
        )
    if allow_only:
        parts.append(
            "<li>Whitelist mode — allows only: "
            + ", ".join(f"<code>{e(c)}</code>" for c in allow_only)
            + ". Everything else is blocked.</li>"
        )
    if not (add_patterns or disabled or allow_only):
        parts.append("<li>Inherits the default policy unchanged.</li>")
    parts.append("</ul>")

    if tested:
        parts.append("<h2>Tested against</h2><ul>")
        for t in tested:
            parts.append(f"<li><code>{e(str(t))}</code></li>")
        parts.append("</ul>")

    parts.append(
        '<h2>Full policy</h2>'
        '<details><summary>Show raw policy JSON</summary>'
        f'<pre><code>{e(_json.dumps(pol, indent=2, sort_keys=True))}</code></pre>'
        '</details>'
    )
    parts.append(
        '<p style="margin-top:2rem"><a href="/dashboard/packs">'
        '← Back to Skill Packs</a> · '
        '<a href="/help/skill-packs">Skill Pack format reference</a></p>'
    )
    return "".join(parts)


@app.get("/help/packs/{name}", response_class=HTMLResponse)
def help_pack_page(request: Request, name: str):
    """Per-pack help page rendered from the verified manifest.

    Replaces the broken `homepage` URLs that every signed pack carries
    (`https://docs.orivael.dev/firewall/packs/<name>` — a host that
    isn't served). `_load_pack` enforces name validation, signature
    verification, and returns None for missing/tampered packs."""
    manifest = _load_pack(name)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"no pack named {name!r}",
        )
    html = _HELP_HTML_TEMPLATE.format(
        title=f"{manifest.title or manifest.name} — Skill Pack",
        nav=_help_render_nav("skill-packs"),
        body=_render_pack_help_body(manifest),
    )
    return HTMLResponse(content=html)


@app.post("/v1/guard/check")
async def guard_check(request: Request):
    """Authenticated intent classification of *input* prompts.

    Use this BEFORE forwarding a prompt to your LLM. Pair with
    `/v1/guard/output` to also screen the LLM's response before it
    reaches the end user.

    Header:  Authorization: Bearer axfw_<key>
    Body:    {"text": "<prompt to classify>"}
    Returns: {"verdict": "allow" | "block",
              "intent": {"class", "confidence", "signals", "signature"}}

    Returns 429 (with Retry-After) when a free-tier tenant has used
    their monthly quota. Paid tiers have no hard cap — Stripe metered
    billing handles overage.
    """
    return await _guard_classify(request, endpoint_label="/v1/guard/check")


@app.post("/v1/guard/output")
async def guard_output(request: Request):
    """Authenticated intent classification of *output* text.

    Use this AFTER your LLM produces a response, BEFORE sending it to
    the end user. Same verdict logic as `/v1/guard/check`; recorded
    under a separate endpoint label so dashboards can break out
    input-vs-output block rates.

    Primary use cases:
      - AI toys screening their own voice response before TTS
      - Customer-support bots ensuring no PII leaks in the reply
      - Compliance redaction (FDCPA, HIPAA, SEC) on model output

    Header / Body / Response: identical to /v1/guard/check.
    """
    return await _guard_classify(request, endpoint_label="/v1/guard/output")


async def _guard_classify(request: Request, *, endpoint_label: str) -> JSONResponse:
    """Shared guard-classify flow used by both /check and /output."""
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
        endpoint=endpoint_label,
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
    if BETA_MODE:
        raise HTTPException(
            403,
            f"Self-serve upgrades are disabled during the beta. "
            f"Contact {SALES_EMAIL} for pricing.",
        )
    if not billing.is_enabled():
        raise HTTPException(503, "Billing is not configured on this deployment")
    try:
        url = billing.create_checkout_session(t, tier)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    except Exception:
        log.exception(
            "Stripe checkout failed for tenant=%s tier=%s",
            t.tenant_id, tier,
        )
        raise HTTPException(502, "Billing provider error — please try again")
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
    except Exception:
        log.exception("Stripe portal failed for tenant=%s", t.tenant_id)
        raise HTTPException(502, "Billing provider error — please try again")
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
