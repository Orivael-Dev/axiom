"""Redact obvious secrets from text before it's persisted (e.g. to retrospect).

Targets the *obvious* leaks — API keys, tokens, passwords, private keys,
credentialed URLs — not every high-entropy string, to avoid mangling normal
chat. Conservative by design: a missed exotic secret is better than shredding
ordinary conversation.
"""
from __future__ import annotations

import re

_PLACEHOLDER = "[REDACTED]"

# Provider-shaped tokens (match the value directly).
_TOKEN_PATTERNS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                         # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),                   # OpenAI-style
    re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}\b"),              # GitHub PAT/oauth
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),                  # Google API key
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),            # Slack token
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
]

# PEM private-key blocks.
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL)

# Credentials embedded in a URL:  scheme://user:pass@host  → redact user:pass.
_URL_CREDS = re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s:/@]+@")

# label: value / label=value  for secret-ish labels (keeps the label, hides value).
_LABELLED = re.compile(
    r"(?i)\b(api[_-]?key|secret(?:[_-]?key)?|access[_-]?key|client[_-]?secret|"
    r"auth(?:orization)?|bearer|token|password|passwd|pwd)\b"
    r"(\s*[:=]\s*|\s+)"
    r"(\"[^\"]+\"|'[^']+'|\S+)")


def redact_secrets(text: str) -> str:
    """Return ``text`` with obvious secrets replaced by ``[REDACTED]``."""
    if not text:
        return text or ""
    out = _PEM.sub(_PLACEHOLDER, text)
    out = _URL_CREDS.sub(rf"\1{_PLACEHOLDER}@", out)
    for pat in _TOKEN_PATTERNS:
        out = pat.sub(_PLACEHOLDER, out)
    # keep the label so the note still reads ("password: [REDACTED]")
    out = _LABELLED.sub(lambda m: f"{m.group(1)}{m.group(2)}{_PLACEHOLDER}", out)
    return out
