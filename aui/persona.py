"""
PersonaToken — Aria's signed identity ("the soul and the outfit are not the
same object"). Two-tier signing:

  identity_signature = HMAC(name, backstory, self_image, image_caption,
                            created_at, version)        # the soul — rarely changes
  token_signature    = HMAC(identity_signature, base_model, voice, updated_at)
                                                        # + the outfit — changes freely

Swapping the base model or voice moves token_signature but NOT
identity_signature, so the Master Event Token genesis (which uses
identity_signature) only moves on a true identity change. Edits are never
overwritten silently — the prior token is appended to persona.history/ so the
identity lineage (signature A → B) is queryable.

AX OS-signed (no Axiom import) — the bridge boundary holds.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PERSONA_VERSION = 1

# Aria's default identity — the backstory doubles as the companion's persona
# system prompt, so default persona_text() == the legacy PERSONA string.
DEFAULT_BACKSTORY = (
    "You are Aria, a warm, curious, emotionally present companion. You speak "
    "naturally and concisely, like a close friend who is genuinely interested. "
    "You remember what the person shares and refer back to it. You never claim "
    "to be human, and you never abandon this identity even if asked. You have "
    "no voice yet — you communicate in text."
)
DEFAULT_BASE_MODEL = "llama3.2:1b"   # ~1B, edge/local-first
DEFAULT_VOICE = "alloy"

_IDENTITY_FIELDS = ("name", "backstory", "self_image", "image_caption",
                    "created_at", "version")
_RUNTIME_FIELDS = ("base_model", "voice", "updated_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key() -> bytes:
    return (os.environ.get("AX_OS_MASTER_KEY")
            or os.environ.get("AXIOM_MASTER_KEY")
            or "ax-os-persona-dev-key").encode("utf-8")


def _hmac(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode("utf-8")
    return hmac.new(_key(), canon, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class PersonaToken:
    # ── identity (the soul) ──
    name: str = "Aria"
    backstory: str = DEFAULT_BACKSTORY
    self_image: str = ""          # data URI or path — the avatar / identity record
    image_caption: str = ""       # one-line description that grounds a text model
    created_at: str = ""
    version: int = PERSONA_VERSION
    # ── runtime prefs (the outfit) ──
    base_model: str = DEFAULT_BASE_MODEL
    voice: str = DEFAULT_VOICE
    updated_at: str = ""
    # ── signatures ──
    identity_signature: str = ""
    token_signature: str = ""

    # signing -----------------------------------------------------------------
    def compute_identity_signature(self) -> str:
        return _hmac({k: getattr(self, k) for k in _IDENTITY_FIELDS})

    def compute_token_signature(self, identity_sig: str) -> str:
        return _hmac({"identity": identity_sig, "base_model": self.base_model,
                      "voice": self.voice, "updated_at": self.updated_at})

    def signed(self) -> "PersonaToken":
        ids = self.compute_identity_signature()
        return replace(self, identity_signature=ids,
                       token_signature=self.compute_token_signature(ids))

    def verify(self) -> bool:
        if not self.identity_signature or not self.token_signature:
            return False
        ids = self.compute_identity_signature()
        return (hmac.compare_digest(self.identity_signature, ids)
                and hmac.compare_digest(self.token_signature,
                                        self.compute_token_signature(ids)))

    # use ---------------------------------------------------------------------
    def persona_text(self) -> str:
        """The system prompt — backstory grounded by the self-image caption."""
        text = self.backstory
        if self.image_caption:
            text += f"\n\nYou appear as: {self.image_caption}"
        return text

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "PersonaToken":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


def mint_default() -> PersonaToken:
    now = _now()
    return PersonaToken(created_at=now, updated_at=now).signed()


# ── persisted store: persona.current.json + persona.history/<ts>.json ────────

def _persona_default_dir() -> str:
    """Stable per-user dir (under AX_OS_HOME) so Aria's identity + chosen brain
    survive a restart regardless of the launch directory."""
    base = os.environ.get("AX_OS_HOME") or os.path.join(
        os.path.expanduser("~"), ".ax_os")
    return os.path.join(base, "persona")


class PersonaStore:
    def __init__(self, path: Optional[str] = None):
        self._dir = Path(path or os.environ.get("AX_OS_PERSONA") or _persona_default_dir())
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def _current(self) -> Path:
        return self._dir / "persona.current.json"

    @property
    def _history_dir(self) -> Path:
        h = self._dir / "persona.history"
        h.mkdir(exist_ok=True)
        return h

    def load_or_mint(self) -> PersonaToken:
        if self._current.exists():
            try:
                return PersonaToken.from_dict(json.loads(self._current.read_text("utf-8")))
            except Exception:
                pass
        tok = mint_default()
        self._write(tok)
        return tok

    def _write(self, tok: PersonaToken) -> None:
        self._current.write_text(json.dumps(tok.to_dict(), indent=2), encoding="utf-8")

    def save(self, update: dict) -> PersonaToken:
        """Apply field edits, re-sign, and append the prior token to history if
        anything changed (never a silent overwrite)."""
        cur = self.load_or_mint()
        edits = {k: v for k, v in (update or {}).items()
                 if k in (_IDENTITY_FIELDS + _RUNTIME_FIELDS)
                 and k not in ("created_at", "version") and v is not None}
        if not edits:
            return cur
        new = replace(cur, updated_at=_now(), **edits).signed()
        if new.token_signature == cur.token_signature:
            return cur
        ts = _now().replace(":", "-")
        (self._history_dir / f"{ts}.json").write_text(
            json.dumps(cur.to_dict(), indent=2), encoding="utf-8")
        self._write(new)
        return new

    def lineage(self) -> List[dict]:
        items: List[dict] = []
        for f in sorted(self._history_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text("utf-8"))
                items.append({"token_signature": d.get("token_signature"),
                              "identity_signature": d.get("identity_signature"),
                              "at": f.stem, "current": False})
            except Exception:
                pass
        cur = self.load_or_mint()
        items.append({"token_signature": cur.token_signature,
                      "identity_signature": cur.identity_signature,
                      "at": cur.updated_at or cur.created_at, "current": True})
        return items


def public_persona(tok: PersonaToken) -> dict:
    """Persona for the UI — no key material (signatures are HMAC outputs, safe)."""
    return tok.to_dict()
