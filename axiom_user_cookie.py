"""User context cookie — lightweight portable session state for LLMs.

Concept (from LLM_Cookies_Concept.pdf): instead of re-reading an entire
chat history on every prompt, store a signed snapshot of user preferences,
expertise, and active project context.  The snapshot is small, local,
user-owned, and portable across models.

Unlike dense vector embeddings (which are model-specific and unportable),
the cookie is structured JSON injected as a plain-text CONTEXT block.
Any model that speaks English can read it — the interoperability problem
the PDF identifies is sidestepped by staying in the text domain.

Storage: ~/.axiom/user.cookie.json  (default; overridable)
Format:  HMAC-SHA256-signed JSON, key derived from AXIOM_MASTER_KEY
Privacy: individual fields can be cleared without touching others;
         forget_all() deletes the file

Usage — Python API:
    from axiom_user_cookie import CookieStore, UserContextCookie

    # Create / update
    store = CookieStore()
    store.update(
        style="terse, no preamble",
        domain_expertise={"security": "expert", "legal": "beginner"},
        active_project="legal RAG benchmark",
        active_goals=["improve Hit@10", "reduce latency below 50ms"],
    )

    # Read and inject into an LLM call
    cookie = store.load()
    extra_context = cookie.to_extra_context()
    token = exo.invoke("research", query, extra_context=extra_context)

    # Forget specific fields (privacy)
    store.forget("active_project", "active_goals")

    # Nuclear option
    store.forget_all()

Usage — CLI:
    python3 -m axiom_user_cookie show
    python3 -m axiom_user_cookie set style "terse, no preamble"
    python3 -m axiom_user_cookie set-expertise security expert
    python3 -m axiom_user_cookie add-goal "improve Hit@10"
    python3 -m axiom_user_cookie forget active_project active_goals
    python3 -m axiom_user_cookie forget-all

Server wiring:
    export AXIOM_USER_COOKIE=~/.axiom/user.cookie.json
    # research server auto-loads and injects on every delegate call
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

# ── constants ─────────────────────────────────────────────────────────────────

DEFAULT_COOKIE_PATH = Path.home() / ".axiom" / "user.cookie.json"
_COOKIE_VERSION     = 1
_SIGNING_NS         = b"axiom-user-cookie-v1"

# Fields that survive forget() — metadata, never cleared individually
_IMMUTABLE_FIELDS = frozenset({"version", "created_at", "signature"})

# Fields that form the HMAC payload (everything except the sig itself)
_SIGNABLE_FIELDS = frozenset({
    "version", "created_at", "updated_at",
    "style", "response_format", "language",
    "domain_expertise", "active_project", "active_goals", "topics_blocked",
})


# ── Schema ────────────────────────────────────────────────────────────────────

@dataclass
class UserContextCookie:
    """Portable snapshot of user preferences and active context.

    Injected into LLM calls as a CONTEXT block — no vector embeddings,
    no model-specific encoding, works across every backend.
    """
    # Communication style
    style: str = ""
    # e.g. "terse, no preamble" | "verbose with examples" | "ELI5"

    response_format: str = ""
    # e.g. "markdown" | "plain text" | "json"

    language: str = "en"

    # Domain expertise — model adapts vocabulary and depth per domain
    domain_expertise: Dict[str, str] = field(default_factory=dict)
    # {"security": "expert", "legal": "beginner", "medical": "layperson"}

    # Active project context
    active_project: str = ""
    active_goals: List[str] = field(default_factory=list)

    # Privacy — topics the model should never surface
    topics_blocked: List[str] = field(default_factory=list)

    # Metadata
    version: int = _COOKIE_VERSION
    created_at: str = ""
    updated_at: str = ""

    # HMAC-SHA256 signature (excluded from the signing payload itself)
    signature: str = ""

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "UserContextCookie":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    # ── LLM injection ─────────────────────────────────────────────────────────

    def to_extra_context(self) -> dict:
        """Return a compact dict for the delegate_runtime CONTEXT block.

        Only non-empty fields are included; keys are prefixed with
        ``user_`` so they stand out from domain-specific context keys.
        """
        ctx: dict = {}
        if self.style:
            ctx["user_style"] = self.style
        if self.response_format:
            ctx["user_response_format"] = self.response_format
        if self.language and self.language != "en":
            ctx["user_language"] = self.language
        if self.domain_expertise:
            ctx["user_expertise"] = ", ".join(
                f"{d}={level}" for d, level in sorted(self.domain_expertise.items())
            )
        if self.active_project:
            ctx["active_project"] = self.active_project
        if self.active_goals:
            ctx["active_goals"] = "; ".join(self.active_goals)
        return ctx

    def to_system_prompt_prefix(self) -> str:
        """One-paragraph system-prompt prefix for backends that don't use extra_context."""
        parts: List[str] = []
        if self.style:
            parts.append(f"Communication style: {self.style}.")
        if self.domain_expertise:
            expertise_str = ", ".join(
                f"{d} ({level})" for d, level in sorted(self.domain_expertise.items())
            )
            parts.append(f"User expertise: {expertise_str}.")
        if self.active_project:
            parts.append(f"Active project: {self.active_project}.")
        if self.active_goals:
            parts.append(f"Current goals: {'; '.join(self.active_goals)}.")
        if self.response_format:
            parts.append(f"Preferred format: {self.response_format}.")
        if not parts:
            return ""
        return "[User context] " + " ".join(parts)

    # ── signing ───────────────────────────────────────────────────────────────

    def sign(self) -> "UserContextCookie":
        """Return a new cookie with the HMAC signature filled in."""
        payload = self._signable_payload()
        sig = _sign_payload(payload)
        return UserContextCookie(**{**asdict(self), "signature": sig})

    def verify(self) -> bool:
        """Return True if the HMAC signature matches the current fields."""
        if not self.signature:
            return False
        payload  = self._signable_payload()
        expected = _sign_payload(payload)
        return hmac_lib.compare_digest(expected, self.signature)

    def _signable_payload(self) -> dict:
        d = asdict(self)
        return {k: d[k] for k in sorted(_SIGNABLE_FIELDS)}


# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(_SIGNING_NS)


def _sign_payload(payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True,
                      ensure_ascii=True, separators=(",", ":")).encode()
    return hmac_lib.new(_signing_key(), data, hashlib.sha256).hexdigest()


# ── CookieStore ───────────────────────────────────────────────────────────────

class CookieStore:
    """Load, save, update, and delete a UserContextCookie on disk.

    Parameters
    ----------
    path : path to the JSON cookie file (default: ~/.axiom/user.cookie.json)
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_COOKIE_PATH

    # ── read ──────────────────────────────────────────────────────────────────

    def load(self) -> Optional["UserContextCookie"]:
        """Load and verify the cookie.  Returns None if missing or invalid."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        cookie = UserContextCookie.from_dict(data)
        if not cookie.verify():
            return None
        return cookie

    def load_or_empty(self) -> "UserContextCookie":
        """Return the stored cookie, or a fresh unsigned one if not found."""
        return self.load() or UserContextCookie()

    # ── write ─────────────────────────────────────────────────────────────────

    def save(self, cookie: "UserContextCookie") -> None:
        """Sign and write the cookie to disk."""
        signed = cookie.sign()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(signed), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update(self, **kwargs) -> "UserContextCookie":
        """Update fields and save.  Creates a new cookie if none exists.

        Nested dict fields (domain_expertise) are merged, not replaced.
        List fields (active_goals, topics_blocked) are replaced.
        """
        cookie = self.load_or_empty()
        now    = _iso_now()

        # Handle nested merge for domain_expertise
        if "domain_expertise" in kwargs and isinstance(kwargs["domain_expertise"], dict):
            merged = dict(cookie.domain_expertise)
            merged.update(kwargs.pop("domain_expertise"))
            kwargs["domain_expertise"] = merged

        d = asdict(cookie)
        for k, v in kwargs.items():
            if k in _IMMUTABLE_FIELDS:
                continue
            if k in d:
                d[k] = v

        if not d["created_at"]:
            d["created_at"] = now
        d["updated_at"] = now
        d["signature"]  = ""   # will be re-signed by save()

        updated = UserContextCookie.from_dict(d)
        self.save(updated)
        return self.load_or_empty()

    # ── privacy ───────────────────────────────────────────────────────────────

    def forget(self, *fields: str) -> "UserContextCookie":
        """Clear named fields.  Immutable fields (version, created_at) are skipped."""
        cookie = self.load_or_empty()
        d      = asdict(cookie)
        blank  = UserContextCookie()
        blank_d = asdict(blank)

        for f in fields:
            if f in _IMMUTABLE_FIELDS or f not in d:
                continue
            d[f] = blank_d[f]   # reset to the dataclass default

        d["updated_at"] = _iso_now()
        d["signature"]  = ""
        updated = UserContextCookie.from_dict(d)
        self.save(updated)
        return self.load_or_empty()

    def forget_domain(self, domain: str) -> "UserContextCookie":
        """Remove a single domain from domain_expertise."""
        cookie = self.load_or_empty()
        expertise = dict(cookie.domain_expertise)
        expertise.pop(domain, None)
        # Bypass update()'s merge logic — replace directly
        d = asdict(cookie)
        d["domain_expertise"] = expertise
        d["updated_at"] = _iso_now()
        d["signature"]  = ""
        self.save(UserContextCookie.from_dict(d))
        return self.load_or_empty()

    def forget_all(self) -> None:
        """Delete the cookie file entirely."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def add_goal(self, goal: str) -> "UserContextCookie":
        """Append a goal if not already present."""
        cookie = self.load_or_empty()
        goals  = list(cookie.active_goals)
        if goal not in goals:
            goals.append(goal)
        return self.update(active_goals=goals)

    def remove_goal(self, goal: str) -> "UserContextCookie":
        """Remove a goal by exact match."""
        cookie = self.load_or_empty()
        return self.update(active_goals=[g for g in cookie.active_goals if g != goal])


# ── factory ───────────────────────────────────────────────────────────────────

def from_env() -> Optional["UserContextCookie"]:
    """Load the user cookie from AXIOM_USER_COOKIE env var path.

    Returns None when the env var is unset or the cookie is missing/invalid.
    This is the server-side entry point — called once at startup.
    """
    path_str = os.environ.get("AXIOM_USER_COOKIE", "").strip()
    if not path_str:
        return None
    path = Path(path_str).expanduser()
    return CookieStore(path).load()


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_show(store: CookieStore) -> None:
    cookie = store.load()
    if cookie is None:
        print("No cookie found at", store.path)
        return
    d = asdict(cookie)
    d.pop("signature", None)
    print(json.dumps(d, indent=2, ensure_ascii=False))
    print(f"\n  Verified: {cookie.verify()}")
    print(f"  Path: {store.path}")
    if cookie.to_extra_context():
        print("\n  LLM context block:")
        for k, v in cookie.to_extra_context().items():
            print(f"    {k}: {v}")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Manage your Axiom user context cookie"
    )
    ap.add_argument("--path", default=None,
                    help=f"Cookie file path (default: {DEFAULT_COOKIE_PATH})")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("show",        help="Display current cookie")
    sub.add_parser("forget-all",  help="Delete the cookie file")

    p_set = sub.add_parser("set",  help="Set a top-level string field")
    p_set.add_argument("field",    help="Field name (style / response_format / language / active_project)")
    p_set.add_argument("value",    help="New value")

    p_exp = sub.add_parser("set-expertise", help="Set expertise level for a domain")
    p_exp.add_argument("domain",   help="Domain name (e.g. security, legal)")
    p_exp.add_argument("level",    help="Level (e.g. expert, intermediate, beginner, layperson)")

    p_forget_exp = sub.add_parser("forget-expertise", help="Remove a domain from expertise")
    p_forget_exp.add_argument("domain")

    p_goal = sub.add_parser("add-goal",    help="Append a goal")
    p_goal.add_argument("goal")

    p_rmgoal = sub.add_parser("remove-goal", help="Remove a goal")
    p_rmgoal.add_argument("goal")

    p_forget = sub.add_parser("forget", help="Clear one or more fields")
    p_forget.add_argument("fields", nargs="+")

    args = ap.parse_args(argv)
    store = CookieStore(args.path)

    if args.cmd == "show" or args.cmd is None:
        _cli_show(store)

    elif args.cmd == "set":
        store.update(**{args.field: args.value})
        print(f"  Set {args.field} = {args.value!r}")
        _cli_show(store)

    elif args.cmd == "set-expertise":
        store.update(domain_expertise={args.domain: args.level})
        print(f"  Set expertise: {args.domain} = {args.level}")

    elif args.cmd == "forget-expertise":
        store.forget_domain(args.domain)
        print(f"  Removed domain: {args.domain}")

    elif args.cmd == "add-goal":
        store.add_goal(args.goal)
        print(f"  Added goal: {args.goal!r}")

    elif args.cmd == "remove-goal":
        store.remove_goal(args.goal)
        print(f"  Removed goal: {args.goal!r}")

    elif args.cmd == "forget":
        store.forget(*args.fields)
        print(f"  Cleared: {', '.join(args.fields)}")

    elif args.cmd == "forget-all":
        store.forget_all()
        print(f"  Cookie deleted: {store.path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
