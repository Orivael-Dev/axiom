"""WeasyPrint-backed PDF generator.

Render a Jinja2 HTML template into a sealed PDF. The PDF body is
HMAC-signed at the caller's discretion (the signature lives in the
returned tuple alongside the bytes).
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from axiom_signing import derive_key

REPORTS_SIGNING_NAMESPACE = b"axiom-report-v1"

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _stars(n: int) -> str:
    """Render an integer 1-5 as a row of filled / hollow star characters.

    WeasyPrint's font fallback handles the unicode reliably.
    """
    n = max(0, min(int(n), 5))
    return "★" * n + "☆" * (5 - n)


_env.globals["stars"] = _stars


def render_pdf(template_name: str, context: dict[str, Any]) -> tuple[bytes, str]:
    """Render `templates/<template_name>` with `context` → (pdf_bytes, signature).

    The signature is HMAC-SHA256 over the rendered PDF bytes, namespaced
    under `axiom-report-v1`. Callers should persist BOTH the bytes and
    the signature; later verification re-hashes the bytes and compares.
    """
    from weasyprint import HTML  # lazy import: WeasyPrint pulls in C libs

    context.setdefault("generated_at", datetime.utcnow().isoformat() + "Z")
    context.setdefault("report_version", __version__)
    context.setdefault("static_dir", str(_STATIC_DIR))

    template = _env.get_template(template_name)
    html_str = template.render(**context)

    pdf_bytes = HTML(string=html_str, base_url=str(_BASE_DIR)).write_pdf()
    signature = _sign(pdf_bytes)
    return (pdf_bytes, signature)


def verify_pdf(pdf_bytes: bytes, signature: str) -> bool:
    """Constant-time check that the PDF was produced by this signing key."""
    expected = _sign(pdf_bytes)
    return hmac.compare_digest(signature, expected)


def _sign(pdf_bytes: bytes) -> str:
    key = derive_key(REPORTS_SIGNING_NAMESPACE)
    return hmac.new(key, pdf_bytes, hashlib.sha256).hexdigest()


# Re-export the package version so callers can stamp the report.
from . import __version__  # noqa: E402
