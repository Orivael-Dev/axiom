"""Public pack registry HTTP server — packs.orivael.dev.

Endpoints (all read-only for v1; publishing comes in week 7+):

  GET /                          landing + JSON index
  GET /v1/packs                  list all available packs
  GET /v1/packs/{name}           pack metadata + latest version
  GET /v1/packs/{name}/{version} the exact manifest for that version
  GET /healthz                   liveness probe
  GET /readyz                    readiness — does the packs/ dir exist?

Manifests served from disk under AXIOM_PACKS_DIR (default ./packs).
Each pack lives at packs/<name>/pack.json. Multi-version is supported
via packs/<name>/<version>/pack.json — the unversioned pack.json is
treated as "latest".

Run:
  uvicorn axiom_packs.server:app --port 8005 --host 0.0.0.0

This service is intentionally separate from the Firewall dashboard
(different blast radius, different scaling, can run anywhere). The
Firewall dashboard fetches from it via the AXIOM_FIREWALL_REGISTRY_URL
env var (wired up in Phase 2 week 8).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from axiom_firewall.skill_pack import (
    SkillPackManifest, verify_first_party,
)

PACKS_DIR = Path(os.environ.get("AXIOM_PACKS_DIR", "packs")).resolve()

# CORS — registry is meant to be read from anywhere, including browsers.
# Set AXIOM_PACKS_CORS_ORIGINS to lock down if you don't want that.
_cors_raw = os.environ.get("AXIOM_PACKS_CORS_ORIGINS", "*").strip()
CORS_ORIGINS: list[str] = (
    [o.strip() for o in _cors_raw.split(",") if o.strip()] if _cors_raw else []
)

log = logging.getLogger("axiom_packs")
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s:    %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    log.info("axiom-packs registry starting")
    log.info("  packs dir:    %s", PACKS_DIR)
    log.info("  cors origins: %s", CORS_ORIGINS or "(none)")
    if not PACKS_DIR.exists():
        log.warning("PACKS_DIR does not exist — registry will serve no packs")
    yield
    log.info("axiom-packs registry shutting down")


app = FastAPI(
    title="Axiom Skill Pack Registry",
    lifespan=_lifespan,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
        max_age=3600,
    )


class _RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response


app.add_middleware(_RequestIdMiddleware)


# ─── Pack discovery ─────────────────────────────────────────────────────


def _safe_name(name: str) -> bool:
    """Defense against path traversal."""
    return bool(name) and "/" not in name and ".." not in name and name == name.strip()


def _load_manifest(path: Path) -> Optional[SkillPackManifest]:
    if not path.is_file():
        return None
    try:
        manifest = SkillPackManifest.parse(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        log.warning("failed to load %s: %s", path, e)
        return None
    if not verify_first_party(manifest):
        log.warning("rejecting unsigned pack %s", path)
        return None
    return manifest


def _list_packs() -> list[SkillPackManifest]:
    if not PACKS_DIR.exists():
        return []
    out = []
    for entry in sorted(PACKS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        manifest = _load_manifest(entry / "pack.json")
        if manifest is not None:
            out.append(manifest)
    return out


def _get_pack(name: str, version: Optional[str] = None) -> Optional[SkillPackManifest]:
    if not _safe_name(name):
        return None
    if version is not None and not _safe_name(version):
        return None
    pack_dir = PACKS_DIR / name
    if not pack_dir.is_dir():
        return None

    if version is None:
        return _load_manifest(pack_dir / "pack.json")

    # Versioned path: packs/<name>/<version>/pack.json
    versioned = pack_dir / version / "pack.json"
    if versioned.is_file():
        return _load_manifest(versioned)

    # Fall back to the unversioned manifest if its version matches.
    latest = _load_manifest(pack_dir / "pack.json")
    if latest is not None and latest.version == version:
        return latest
    return None


def _manifest_summary(m: SkillPackManifest) -> dict:
    """Compact metadata for index listings — excludes the policy body."""
    return {
        "name":           m.name,
        "title":          m.title,
        "description":    m.description,
        "version":        m.version,
        "author":         m.author,
        "license":        m.license,
        "homepage":       m.homepage,
        "tags":           list(m.tags),
        "tested_against": list(m.tested_against),
    }


# ─── Routes ─────────────────────────────────────────────────────────────


_LANDING_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Axiom Skill Pack Registry — packs.orivael.dev</title>
<style>
body {font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
      margin: 3rem auto; padding: 0 1rem; color: #e6e1cf; background: #0a0e14;}
h1 {color: #ffb454;}
code {background: #131820; padding: 0.15rem 0.4rem; border-radius: 3px;}
a {color: #ffb454;}
pre {background: #131820; padding: 1rem; border-radius: 6px; overflow-x: auto;}
</style></head><body>
<h1>Axiom Skill Pack Registry</h1>
<p>Public read-only mirror for Axiom Intent Firewall Skill Packs.</p>
<p>Index: <a href="/v1/packs">/v1/packs</a></p>
<pre>$ curl https://packs.orivael.dev/v1/packs
$ curl https://packs.orivael.dev/v1/packs/fdcpa
$ curl https://packs.orivael.dev/v1/packs/fdcpa/0.1.0</pre>
<p>Docs: <a href="https://docs.orivael.dev/firewall/skill-packs">docs.orivael.dev/firewall/skill-packs</a></p>
</body></html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing():
    return HTMLResponse(_LANDING_HTML)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return JSONResponse({"status": "ok"})


@app.get("/readyz", include_in_schema=False)
def readyz():
    if not PACKS_DIR.exists():
        return JSONResponse(
            {"status": "unready", "error": f"packs dir not found: {PACKS_DIR}"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse({"status": "ready", "pack_count": len(_list_packs())})


@app.get("/v1/packs")
def packs_index():
    return JSONResponse({
        "format_version": "1.0",
        "packs": [_manifest_summary(m) for m in _list_packs()],
    })


@app.get("/v1/packs/{name}")
def pack_latest(name: str):
    manifest = _get_pack(name)
    if manifest is None:
        raise HTTPException(404, f"Pack {name!r} not found")
    return JSONResponse(manifest.to_dict())


@app.get("/v1/packs/{name}/{version}")
def pack_versioned(name: str, version: str):
    manifest = _get_pack(name, version=version)
    if manifest is None:
        raise HTTPException(
            404, f"Pack {name!r} version {version!r} not found"
        )
    return JSONResponse(manifest.to_dict())
