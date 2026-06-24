"""RAG Knowledge Cookie — general-purpose cross-session knowledge fragment store.

Concept: retrieved knowledge fragments are cached locally, signed, and promoted
to "hot knowledge" once they recur across enough distinct sessions.  Hot fragments
are then injected into every LLM call as extra context, giving the model persistent
awareness of frequently-encountered facts without re-running retrieval.

BLT trade-off:
  Bloat:   each fragment adds ~300–800 bytes to the cookie file.
  Latency: inject cost is O(hot_fragments) on every call — keep max_fragments≤5
           unless the session is long.
  Tokens:  5 hot fragments × avg 300 tokens = 1,500 tokens always prepended;
           use to_extra_context() sparingly.

Storage: ~/.axiom/knowledge.cookie.json  (default; overridable)
Format:  HMAC-SHA256-signed JSON, key derived from AXIOM_MASTER_KEY
Privacy: purge() removes stale fragments; forget_all() deletes the file

Usage — Python API:
    from axiom_knowledge_cookie import KnowledgeCookieStore

    store = KnowledgeCookieStore()
    store.record_hit(
        content="Section 12.3: liability is capped at $1 M USD",
        source_uri="legal/contracts.db:chunk_42",
        session_id="sess-abc123",
    )
    cookie = store.promote_and_save()
    extra_context = cookie.to_extra_context()   # inject into LLM call

Usage — CLI:
    python3 -m axiom_knowledge_cookie show
    python3 -m axiom_knowledge_cookie stats
    python3 -m axiom_knowledge_cookie forget-all

Server wiring:
    export AXIOM_KNOWLEDGE_COOKIE=~/.axiom/knowledge.cookie.json
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

DEFAULT_COOKIE_PATH = Path.home() / ".axiom" / "knowledge.cookie.json"
_COOKIE_VERSION     = 1
_SIGNING_NS         = b"axiom-knowledge-cookie-v1"

# Max session IDs kept per fragment (caps file growth)
_MAX_SESSIONS_LIST  = 20


# ── KnowledgeFragment ────────────────────────────────────────────────────────

@dataclass
class KnowledgeFragment:
    """A single retrieved text fragment tracked across sessions.

    ``content_hash`` (SHA256[:16]) is the stable key used in the cookie dict.
    ``sessions_list`` is capped at _MAX_SESSIONS_LIST entries; ``sessions_seen``
    is the authoritative cross-session count.
    """
    content: str         # the actual text (e.g. a retrieved passage)
    source_uri: str      # e.g. "legal/contracts.db:chunk_42"
    content_hash: str    # SHA256[:16] of content, used as dict key

    hit_count: int = 0          # times this fragment was retrieved in any session
    sessions_seen: int = 0      # distinct sessions this fragment appeared in
    verified_count: int = 0     # times the LLM cited this as useful (future)

    first_seen: str = ""        # ISO timestamp
    last_seen: str = ""         # ISO timestamp

    promoted: bool = False          # True when sessions_seen >= PROMOTE_THRESHOLD
    finetune_emitted: bool = False  # True when hit_count >= FINETUNE_THRESHOLD

    signature: str = ""             # HMAC-SHA256 of the fragment

    # Internal session tracking — not exposed to callers; capped list
    sessions_list: List[str] = field(default_factory=list)

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeFragment":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# ── KnowledgeCookie ──────────────────────────────────────────────────────────

@dataclass
class KnowledgeCookie:
    """Cross-session store of knowledge fragments and their promotion state.

    ``fragments`` is the primary store: content_hash -> KnowledgeFragment.
    ``hot_knowledge`` is derived — recomputed by promote() — not stored.
    """
    PROMOTE_THRESHOLD:  int = 3   # sessions_seen before moving to hot_knowledge
    FINETUNE_THRESHOLD: int = 10  # hit_count before marking finetune_emitted

    # content_hash -> KnowledgeFragment
    fragments: Dict[str, KnowledgeFragment] = field(default_factory=dict)

    # Hot fragments (promoted=True), ordered by hit_count desc — derived, not stored
    hot_knowledge: List[KnowledgeFragment] = field(default_factory=list)

    # Metadata
    version: int = _COOKIE_VERSION
    created_at: str = ""
    updated_at: str = ""
    session_count: int = 0   # total distinct sessions this cookie has seen

    # HMAC-SHA256 signature (excluded from the signing payload itself)
    signature: str = ""

    # ── core operations ───────────────────────────────────────────────────────

    def record_hit(
        self,
        content: str,
        source_uri: str,
        *,
        session_id: str,
    ) -> KnowledgeFragment:
        """Add or update a fragment, increment counters, track sessions.

        If the fragment's content_hash is new, creates a fresh KnowledgeFragment.
        If session_id has not been seen before for this fragment, increments
        sessions_seen and appends session_id (capped at _MAX_SESSIONS_LIST).
        Always increments hit_count and updates last_seen.

        Parameters
        ----------
        content:    The retrieved text.
        source_uri: Stable identifier for the source (e.g. "db:chunk_42").
        session_id: Caller-supplied session identifier for cross-session tracking.

        Returns the (possibly new) KnowledgeFragment.
        """
        content_hash = _sha256_prefix(content)
        now = _iso_now()

        if content_hash not in self.fragments:
            frag = KnowledgeFragment(
                content=content,
                source_uri=source_uri,
                content_hash=content_hash,
                first_seen=now,
                last_seen=now,
            )
            self.fragments[content_hash] = frag
        else:
            frag = self.fragments[content_hash]

        # Always bump hit_count
        frag.hit_count += 1
        frag.last_seen  = now

        # Cross-session tracking — only count a session once per fragment
        if session_id not in frag.sessions_list:
            frag.sessions_seen += 1
            if len(frag.sessions_list) < _MAX_SESSIONS_LIST:
                frag.sessions_list.append(session_id)

        # Mark finetune_emitted once threshold is crossed
        if frag.hit_count >= self.FINETUNE_THRESHOLD:
            frag.finetune_emitted = True

        return frag

    def promote(self) -> None:
        """Scan fragments and (re)build hot_knowledge.

        Any fragment with sessions_seen >= PROMOTE_THRESHOLD is marked
        promoted=True and included in hot_knowledge (sorted by hit_count desc).
        This method is idempotent.
        """
        hot: List[KnowledgeFragment] = []
        for frag in self.fragments.values():
            if frag.sessions_seen >= self.PROMOTE_THRESHOLD:
                frag.promoted = True
                hot.append(frag)
        self.hot_knowledge = sorted(hot, key=lambda f: f.hit_count, reverse=True)

    # ── LLM injection ─────────────────────────────────────────────────────────

    def to_extra_context(self, max_fragments: int = 5) -> dict:
        """Return a dict with a ``hot_knowledge`` key for delegate_runtime.

        The value is the top N hot fragments' content joined by ``\\n---\\n``.
        Returns an empty dict when there are no promoted fragments.

        Parameters
        ----------
        max_fragments: Maximum number of hot fragments to include (default 5).
        """
        top = self.hot_knowledge[:max_fragments]
        if not top:
            return {}
        joined = "\n---\n".join(f.content for f in top)
        return {"hot_knowledge": joined}

    def to_prompt_prefix(self, max_fragments: int = 5) -> str:
        """Return a plain-text prefix for backends without extra_context support.

        Format::

            [Hot knowledge]
            <fragment 1>
            ---
            <fragment 2>
            ...

        Returns an empty string when there are no promoted fragments.
        """
        top = self.hot_knowledge[:max_fragments]
        if not top:
            return ""
        joined = "\n---\n".join(f.content for f in top)
        return f"[Hot knowledge]\n{joined}"

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON.

        ``hot_knowledge`` is derived and intentionally excluded — it is
        recomputed by promote() on load.
        """
        return {
            "version":       self.version,
            "created_at":    self.created_at,
            "updated_at":    self.updated_at,
            "session_count": self.session_count,
            "signature":     self.signature,
            "fragments": {
                k: v.to_dict() for k, v in self.fragments.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeCookie":
        """Deserialise from a plain dict (as loaded from JSON).

        Calls promote() to rebuild hot_knowledge from stored fragments.
        """
        raw_frags = d.get("fragments", {})
        fragments = {
            k: KnowledgeFragment.from_dict(v)
            for k, v in raw_frags.items()
        }
        cookie = cls(
            version=d.get("version", _COOKIE_VERSION),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            session_count=d.get("session_count", 0),
            signature=d.get("signature", ""),
            fragments=fragments,
        )
        cookie.promote()
        return cookie

    # ── signing ───────────────────────────────────────────────────────────────

    def sign(self) -> "KnowledgeCookie":
        """Return a new cookie with the HMAC signature filled in."""
        self.promote()   # sync promoted flags before signing so payload matches stored state
        payload = self._signable_payload()
        sig = _sign_payload(payload)
        d = self.to_dict()
        d["signature"] = sig
        return KnowledgeCookie.from_dict(d)

    def verify(self) -> bool:
        """Return True if the HMAC signature matches the current fields."""
        if not self.signature:
            return False
        payload  = self._signable_payload()
        expected = _sign_payload(payload)
        return hmac_lib.compare_digest(expected, self.signature)

    def _signable_payload(self) -> dict:
        """Build the canonical payload dict used for signing.

        All fields except ``signature`` and ``hot_knowledge`` (derived) are
        included.  Fragment signatures are excluded from the cookie-level
        signature to avoid double-signing.
        """
        d = self.to_dict()
        d.pop("signature", None)
        # Include fragments but strip per-fragment signatures from cookie payload
        frags_clean = {}
        for k, frag_dict in d.get("fragments", {}).items():
            f = dict(frag_dict)
            f.pop("signature", None)
            frags_clean[k] = f
        d["fragments"] = frags_clean
        return d


# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(_SIGNING_NS)


def _sign_payload(payload: dict) -> str:
    data = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()
    return hmac_lib.new(_signing_key(), data, hashlib.sha256).hexdigest()


# ── KnowledgeCookieStore ─────────────────────────────────────────────────────

class KnowledgeCookieStore:
    """Load, save, and manage a KnowledgeCookie on disk.

    Parameters
    ----------
    path : path to the JSON cookie file (default: ~/.axiom/knowledge.cookie.json)
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_COOKIE_PATH

    # ── read ──────────────────────────────────────────────────────────────────

    def load(self) -> Optional[KnowledgeCookie]:
        """Load and verify the cookie.  Returns None if missing or tampered."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        cookie = KnowledgeCookie.from_dict(data)
        if not cookie.verify():
            return None
        return cookie

    def load_or_empty(self) -> KnowledgeCookie:
        """Return the stored cookie, or a fresh unsigned one if not found."""
        return self.load() or KnowledgeCookie()

    # ── write ─────────────────────────────────────────────────────────────────

    def save(self, cookie: KnowledgeCookie) -> None:
        """Sign and write the cookie to disk."""
        signed = cookie.sign()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(signed.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── convenience helpers ───────────────────────────────────────────────────

    def record_hit(
        self,
        content: str,
        source_uri: str,
        *,
        session_id: str,
    ) -> KnowledgeFragment:
        """Load the cookie, record a hit, save, and return the fragment.

        This is the primary write entry point for RAG pipelines.  The cookie
        is loaded from disk on every call so concurrent writers don't clobber
        each other (last-write-wins; no advisory locking).
        """
        cookie = self.load_or_empty()
        now = _iso_now()

        if not cookie.created_at:
            cookie.created_at = now
        cookie.updated_at = now

        frag = cookie.record_hit(content, source_uri, session_id=session_id)
        self.save(cookie)
        return frag

    def promote_and_save(self) -> KnowledgeCookie:
        """Run promote() then save.  Returns the updated cookie.

        Call this at the end of a session (or periodically) to build/refresh
        the hot_knowledge list.
        """
        cookie = self.load_or_empty()
        now = _iso_now()
        if not cookie.created_at:
            cookie.created_at = now
        cookie.updated_at = now
        cookie.promote()
        self.save(cookie)
        return self.load_or_empty()

    def purge(self, *, older_than_days: int = 90) -> int:
        """Remove fragments not seen in ``older_than_days`` days.

        Returns the number of fragments removed.  Saves the cookie after
        purging even if nothing was removed (no-op save is harmless).
        """
        cookie = self.load_or_empty()
        cutoff_ts = time.time() - older_than_days * 86_400

        before = len(cookie.fragments)
        to_remove = []
        for key, frag in cookie.fragments.items():
            last = _parse_iso(frag.last_seen)
            if last is not None and last < cutoff_ts:
                to_remove.append(key)

        for key in to_remove:
            del cookie.fragments[key]

        removed = len(to_remove)
        if removed:
            cookie.updated_at = _iso_now()
            cookie.promote()
        self.save(cookie)
        return removed

    def forget_all(self) -> None:
        """Delete the cookie file entirely."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ── factory ───────────────────────────────────────────────────────────────────

def from_env() -> Optional[KnowledgeCookie]:
    """Load the knowledge cookie from AXIOM_KNOWLEDGE_COOKIE env var path.

    Returns None when the env var is unset, the file is missing, or the
    HMAC signature does not match (tamper detection).
    This is the server-side entry point — called once at startup.
    """
    path_str = os.environ.get("AXIOM_KNOWLEDGE_COOKIE", "").strip()
    if not path_str:
        return None
    path = Path(path_str).expanduser()
    return KnowledgeCookieStore(path).load()


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_prefix(text: str, length: int = 16) -> str:
    """Return the first ``length`` hex chars of SHA256(text)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _parse_iso(ts: str) -> Optional[float]:
    """Parse an ISO timestamp (``YYYY-MM-DDTHH:MM:SSZ``) to a POSIX float.

    Returns None if the string is empty or unparseable.
    """
    if not ts:
        return None
    try:
        t = time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return float(time.mktime(t))
    except (ValueError, OverflowError):
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_show(store: KnowledgeCookieStore) -> None:
    cookie = store.load()
    if cookie is None:
        print("No cookie found at", store.path)
        return
    d = cookie.to_dict()
    d.pop("signature", None)
    print(json.dumps(d, indent=2, ensure_ascii=False))
    print(f"\n  Verified: {cookie.verify()}")
    print(f"  Path: {store.path}")
    ctx = cookie.to_extra_context()
    if ctx:
        print("\n  LLM context (hot_knowledge preview):")
        preview = ctx["hot_knowledge"][:200]
        print(f"    {preview!r}{'...' if len(ctx['hot_knowledge']) > 200 else ''}")


def _cli_stats(store: KnowledgeCookieStore) -> None:
    cookie = store.load()
    if cookie is None:
        print("No cookie found at", store.path)
        return
    total   = len(cookie.fragments)
    hot     = len(cookie.hot_knowledge)
    finetune_ready = sum(
        1 for f in cookie.fragments.values() if f.finetune_emitted
    )
    total_hits = sum(f.hit_count for f in cookie.fragments.values())
    print(f"Path:              {store.path}")
    print(f"Version:           {cookie.version}")
    print(f"Created:           {cookie.created_at}")
    print(f"Last updated:      {cookie.updated_at}")
    print(f"Session count:     {cookie.session_count}")
    print(f"Total fragments:   {total}")
    print(f"Hot fragments:     {hot}  (sessions_seen >= {cookie.PROMOTE_THRESHOLD})")
    print(f"Finetune-ready:    {finetune_ready}  (hit_count >= {cookie.FINETUNE_THRESHOLD})")
    print(f"Total hits:        {total_hits}")
    print(f"Verified (HMAC):   {cookie.verify()}")
    if hot:
        print(f"\nTop {min(hot, 5)} hot fragments by hit count:")
        for frag in cookie.hot_knowledge[:5]:
            preview = frag.content[:80].replace("\n", " ")
            ellipsis = "..." if len(frag.content) > 80 else ""
            print(
                f"  [{frag.content_hash}]  hits={frag.hit_count}"
                f"  sessions={frag.sessions_seen}"
                f"  src={frag.source_uri}"
            )
            print(f"    {preview}{ellipsis}")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Manage the Axiom RAG Knowledge Cookie"
    )
    ap.add_argument(
        "--path", default=None,
        help=f"Cookie file path (default: {DEFAULT_COOKIE_PATH})",
    )
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("show",       help="Display the full cookie JSON")
    sub.add_parser("stats",      help="Show fragment and promotion statistics")
    sub.add_parser("forget-all", help="Delete the cookie file entirely")

    args = ap.parse_args(argv)
    store = KnowledgeCookieStore(args.path)

    if args.cmd == "show" or args.cmd is None:
        _cli_show(store)

    elif args.cmd == "stats":
        _cli_stats(store)

    elif args.cmd == "forget-all":
        store.forget_all()
        print(f"  Cookie deleted: {store.path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
