"""Verified Answer Cache — query fingerprinting + hot-path promotion.

Tracks LLM responses by query fingerprint.  After a configurable number
of independent verifications, the answer is *promoted* to the hot-path:
the next identical (or near-identical) query is served directly from SQLite
without calling the LLM at all.

How it fits into the agent loop
--------------------------------
::

    cache = VerifiedAnswerCache()

    fp = cache.fingerprint(query)
    hot = cache.lookup(fp)          # None until promoted
    if hot:
        return hot                  # LLM never called

    answer = llm.generate(query)    # normal path
    cache.record(fp, answer)

    # — later, when user or eval marks it correct —
    cache.verify(fp)                # increments verified_hits; auto-promotes at threshold

Fingerprinting
--------------
1. Lowercase + strip punctuation.
2. Remove stop-words (``_STOPWORDS``).
3. Alphabetise remaining tokens (order-invariant).
4. Keep at most ``MAX_FINGERPRINT_TOKENS = 20``.
5. SHA-256 of the joined string → 64-char hex fingerprint.

Identical semantic questions with different word order yield the same key.
Context-dependent queries (per-user, temporal) pass a ``context_key``
discriminator that's appended before hashing:
``fingerprint("my balance", context_key="user:42")`` is distinct from
``fingerprint("my balance", context_key="user:99")``.

Promotion lifecycle
-------------------
::

    cold  → (record + verify × N)  → promoted (hot)
               ↑                         |
               └──── invalidate() ───────┘

COLD    : answer seen, not yet verified enough times
PROMOTED: answer frozen; LLM bypassed on next hit
EXPIRED : TTL exceeded; auto-demoted to cold on sweep

Integrity
---------
Every promoted answer is HMAC-SHA256 signed over
``fingerprint|answer_text|context_key|created_at`` so a tampered DB row
fails ``lookup()`` silently (returns None, falls back to LLM).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────────────

PROMOTION_THRESHOLD:    int   = int(os.environ.get("AXIOM_CACHE_THRESHOLD",   "5"))
DEFAULT_TTL_DAYS:       int   = int(os.environ.get("AXIOM_CACHE_TTL_DAYS",   "30"))
MAX_FINGERPRINT_TOKENS: int   = 20
DEFAULT_DB_PATH: Path = Path.home() / ".axiom" / "verified_answer_cache.db"

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "do", "of",
    "for", "and", "or", "but", "not", "be", "as", "by", "if", "so",
    "me", "my", "we", "our", "you", "your", "he", "she", "they", "them",
    "how", "what", "when", "where", "who", "why", "which", "can", "will",
    "does", "did", "has", "had", "have", "was", "were", "are", "am",
    "get", "go", "use", "also", "just", "up", "out", "with", "from",
    "into", "this", "that", "these", "those", "more", "than", "any",
    "all", "some", "about", "after", "before", "between", "other",
})

_PUNCT_RE  = re.compile(r"[^a-z0-9\s]")
_SPACE_RE  = re.compile(r"\s+")

# ── Fingerprint ───────────────────────────────────────────────────────────────

def fingerprint(query: str, *, context_key: str = "") -> str:
    """Deterministic, order-invariant fingerprint for ``query``.

    Parameters
    ----------
    query       : raw user query
    context_key : optional discriminator for context-dependent queries
                  (e.g. ``"user:42"`` or ``"date:2026-06"``).
                  Empty string (default) → context-free fingerprint.
    """
    cleaned = _PUNCT_RE.sub(" ", query.lower())
    tokens  = [t for t in _SPACE_RE.split(cleaned) if len(t) > 1 and t not in _STOPWORDS]
    tokens  = sorted(set(tokens))[:MAX_FINGERPRINT_TOKENS]
    payload = " ".join(tokens)
    if context_key:
        payload += "|" + context_key
    return hashlib.sha256(payload.encode()).hexdigest()


# ── HMAC signing ──────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    raw = os.environ.get("AXIOM_MASTER_KEY", "")
    if not raw:
        raise RuntimeError("AXIOM_MASTER_KEY not set")
    seed = bytes.fromhex(raw) if len(raw) == 64 else raw.encode()
    return hmac.new(seed, b"axiom-verified-answer-cache-v1", hashlib.sha256).digest()


def _sign(fp: str, answer: str, ctx: str, created_at: str) -> str:
    key     = _signing_key()
    payload = f"{fp}|{answer}|{ctx}|{created_at}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _verify_sig(fp: str, answer: str, ctx: str, created_at: str, sig: str) -> bool:
    expected = _sign(fp, answer, ctx, created_at)
    return hmac.compare_digest(expected, sig)


# ── Dataclass (for typed returns) ─────────────────────────────────────────────

@dataclass
class CachedAnswer:
    fingerprint:   str
    answer_text:   str
    hits:          int
    verified_hits: int
    promoted:      bool
    context_key:   str
    created_at:    str
    last_seen:     str
    ttl_days:      int
    signature:     str

    def is_expired(self) -> bool:
        try:
            created = datetime.fromisoformat(self.created_at)
            age_s   = (datetime.now(timezone.utc) - created).total_seconds()
            return age_s > self.ttl_days * 86_400
        except Exception:
            return False

    def is_valid(self) -> bool:
        """Returns True if the HMAC signature is intact."""
        try:
            return _verify_sig(
                self.fingerprint, self.answer_text,
                self.context_key, self.created_at, self.signature,
            )
        except Exception:
            return False


# ── SQLite schema ──────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS answer_cache (
    fingerprint    TEXT PRIMARY KEY,
    answer_text    TEXT NOT NULL,
    hits           INTEGER NOT NULL DEFAULT 0,
    verified_hits  INTEGER NOT NULL DEFAULT 0,
    promoted       INTEGER NOT NULL DEFAULT 0,
    context_key    TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    ttl_days       INTEGER NOT NULL DEFAULT 30,
    signature      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_promoted ON answer_cache (promoted);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_cached(row: tuple) -> CachedAnswer:
    fp, ans, hits, vhits, promoted, ctx, created, last, ttl, sig = row
    return CachedAnswer(
        fingerprint=fp, answer_text=ans,
        hits=hits, verified_hits=vhits,
        promoted=bool(promoted),
        context_key=ctx,
        created_at=created, last_seen=last,
        ttl_days=ttl, signature=sig,
    )


# ── Main class ────────────────────────────────────────────────────────────────

class VerifiedAnswerCache:
    """SQLite-backed, HMAC-signed query fingerprint → answer cache.

    Parameters
    ----------
    db_path            : path to the SQLite file (default: ``~/.axiom/verified_answer_cache.db``)
    promotion_threshold: number of independent ``verify()`` calls before an
                         answer is promoted to the hot-path (default: 5)
    default_ttl_days   : days before a promoted answer is considered stale
                         and returned to cold status (default: 30)
    """

    def __init__(
        self,
        db_path:             Path = DEFAULT_DB_PATH,
        promotion_threshold: int  = PROMOTION_THRESHOLD,
        default_ttl_days:    int  = DEFAULT_TTL_DAYS,
    ) -> None:
        self._db_path  = Path(db_path)
        self._threshold = promotion_threshold
        self._ttl_days  = default_ttl_days
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ── Public API ────────────────────────────────────────────────────────

    def lookup(self, fp: str) -> Optional[str]:
        """Return the frozen answer for ``fp`` if promoted and valid; else None.

        Increments ``hits`` on every call.  Returns None (falls through to LLM)
        when:
          - fingerprint not in DB
          - not yet promoted
          - TTL expired (entry is demoted back to cold)
          - HMAC signature invalid (tampered row)
        """
        row = self._conn.execute(
            "SELECT fingerprint, answer_text, hits, verified_hits, promoted, "
            "context_key, created_at, last_seen, ttl_days, signature "
            "FROM answer_cache WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if row is None:
            return None

        ca = _row_to_cached(row)

        # Expire stale entries silently — demote to cold.
        if ca.promoted and ca.is_expired():
            self._set_promoted(fp, False)
            return None

        # Signature check — reject tampered rows.
        if ca.promoted and not ca.is_valid():
            return None

        self._conn.execute(
            "UPDATE answer_cache SET hits = hits + 1, last_seen = ? "
            "WHERE fingerprint = ?",
            (_now(), fp),
        )
        self._conn.commit()

        return ca.answer_text if ca.promoted else None

    def record(
        self,
        fp:          str,
        answer_text: str,
        *,
        context_key: str = "",
        verified:    bool = False,
        ttl_days:    int  = 0,
    ) -> None:
        """Log an answer for ``fp``.

        If an entry already exists, the answer_text is updated only if the
        new answer differs (prevents thrashing on minor LLM response variation).
        Set ``verified=True`` to immediately count this as a verified hit.
        """
        ttl = ttl_days or self._ttl_days
        now = _now()

        existing = self._conn.execute(
            "SELECT answer_text, verified_hits FROM answer_cache WHERE fingerprint = ?",
            (fp,),
        ).fetchone()

        if existing is None:
            sig = _sign(fp, answer_text, context_key, now)
            self._conn.execute(
                "INSERT INTO answer_cache "
                "(fingerprint, answer_text, hits, verified_hits, promoted, "
                " context_key, created_at, last_seen, ttl_days, signature) "
                "VALUES (?, ?, 1, ?, 0, ?, ?, ?, ?, ?)",
                (fp, answer_text, 1 if verified else 0,
                 context_key, now, now, ttl, sig),
            )
        else:
            prev_answer, prev_vhits = existing
            new_vhits = prev_vhits + (1 if verified else 0)
            # Update answer if it changed (keeps the most recent verified version).
            if answer_text != prev_answer:
                sig = _sign(fp, answer_text, context_key, now)
                self._conn.execute(
                    "UPDATE answer_cache "
                    "SET answer_text = ?, verified_hits = ?, hits = hits + 1, "
                    "    last_seen = ?, signature = ?, promoted = 0 "
                    "WHERE fingerprint = ?",
                    (answer_text, new_vhits, now, sig, fp),
                )
            else:
                self._conn.execute(
                    "UPDATE answer_cache "
                    "SET verified_hits = ?, hits = hits + 1, last_seen = ? "
                    "WHERE fingerprint = ?",
                    (new_vhits, now, fp),
                )

        self._conn.commit()
        if verified:
            self._maybe_promote(fp)

    def verify(self, fp: str) -> bool:
        """Mark the stored answer for ``fp`` as verified.

        Increments ``verified_hits`` and auto-promotes when the threshold
        is reached.  Returns True if the fingerprint was found.
        """
        row = self._conn.execute(
            "SELECT verified_hits FROM answer_cache WHERE fingerprint = ?", (fp,)
        ).fetchone()
        if row is None:
            return False
        new_vhits = row[0] + 1
        self._conn.execute(
            "UPDATE answer_cache SET verified_hits = ?, last_seen = ? WHERE fingerprint = ?",
            (new_vhits, _now(), fp),
        )
        self._conn.commit()
        self._maybe_promote(fp)
        return True

    def promote(self, fp: str) -> bool:
        """Explicitly promote ``fp`` to the hot-path (bypasses threshold check).

        Returns False if the fingerprint is not found.
        """
        return self._set_promoted(fp, True)

    def invalidate(self, fp: str) -> bool:
        """Demote ``fp`` from the hot-path back to cold.

        Use when the underlying data changes and the frozen answer is stale.
        Resets ``verified_hits`` so the answer must be re-verified before
        re-promotion.  Returns False if the fingerprint is not found.
        """
        row = self._conn.execute(
            "SELECT 1 FROM answer_cache WHERE fingerprint = ?", (fp,)
        ).fetchone()
        if row is None:
            return False
        self._conn.execute(
            "UPDATE answer_cache SET promoted = 0, verified_hits = 0, last_seen = ? "
            "WHERE fingerprint = ?",
            (_now(), fp),
        )
        self._conn.commit()
        return True

    def sweep_expired(self) -> int:
        """Demote all promoted entries whose TTL has expired.

        Returns the number of entries demoted.  Call periodically (e.g. daily).
        """
        rows = self._conn.execute(
            "SELECT fingerprint, created_at, ttl_days FROM answer_cache WHERE promoted = 1"
        ).fetchall()
        demoted = 0
        for fp, created_str, ttl in rows:
            try:
                created = datetime.fromisoformat(created_str)
                age_s   = (datetime.now(timezone.utc) - created).total_seconds()
                if age_s > ttl * 86_400:
                    self._set_promoted(fp, False)
                    demoted += 1
            except Exception:
                pass
        return demoted

    def get(self, fp: str) -> Optional[CachedAnswer]:
        """Return the full ``CachedAnswer`` record for ``fp``, or None."""
        row = self._conn.execute(
            "SELECT fingerprint, answer_text, hits, verified_hits, promoted, "
            "context_key, created_at, last_seen, ttl_days, signature "
            "FROM answer_cache WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        return _row_to_cached(row) if row else None

    def stats(self) -> dict:
        """Return cache-wide statistics."""
        row = self._conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN promoted = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN promoted = 0 THEN 1 ELSE 0 END), "
            "SUM(hits), SUM(verified_hits) "
            "FROM answer_cache"
        ).fetchone()
        total, hot, cold, total_hits, total_verified = row
        total     = total     or 0
        hot       = hot       or 0
        cold      = cold      or 0
        total_hits     = total_hits     or 0
        total_verified = total_verified or 0
        return {
            "total_fingerprints": total,
            "promoted_hot":       hot,
            "cold_warm":          cold,
            "total_hits":         total_hits,
            "total_verified_hits": total_verified,
            "promotion_threshold": self._threshold,
            "db_path":            str(self._db_path),
        }

    def close(self) -> None:
        self._conn.close()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _maybe_promote(self, fp: str) -> None:
        row = self._conn.execute(
            "SELECT verified_hits, promoted FROM answer_cache WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if row and row[0] >= self._threshold and not row[1]:
            self._set_promoted(fp, True)

    def _set_promoted(self, fp: str, promoted: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE answer_cache SET promoted = ? WHERE fingerprint = ?",
            (1 if promoted else 0, fp),
        )
        self._conn.commit()
        return cur.rowcount > 0


# ── Module-level convenience functions ───────────────────────────────────────

_DEFAULT_CACHE: Optional[VerifiedAnswerCache] = None


def default_cache(db_path: Optional[Path] = None) -> VerifiedAnswerCache:
    """Lazy singleton; safe to call from anywhere without arguments."""
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = VerifiedAnswerCache(db_path or DEFAULT_DB_PATH)
    return _DEFAULT_CACHE


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="axiom-answer-cache",
        description="Inspect and manage the verified answer cache",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="print cache statistics")

    p_fp = sub.add_parser("fingerprint", help="compute fingerprint for a query")
    p_fp.add_argument("query")
    p_fp.add_argument("--context", default="")

    p_get = sub.add_parser("get", help="look up a fingerprint")
    p_get.add_argument("fingerprint")

    p_inv = sub.add_parser("invalidate", help="demote a fingerprint from hot-path")
    p_inv.add_argument("fingerprint")

    p_sweep = sub.add_parser("sweep", help="demote all expired entries")

    p_verify = sub.add_parser("verify", help="mark a fingerprint as verified")
    p_verify.add_argument("fingerprint")

    args = ap.parse_args(argv)
    cache = default_cache()

    if args.cmd == "stats":
        s = cache.stats()
        for k, v in s.items():
            print(f"{k:30s}: {v}")

    elif args.cmd == "fingerprint":
        fp = fingerprint(args.query, context_key=args.context)
        print(fp)

    elif args.cmd == "get":
        ca = cache.get(args.fingerprint)
        if ca is None:
            print("not found")
        else:
            print(json.dumps({
                "fingerprint":   ca.fingerprint,
                "promoted":      ca.promoted,
                "hits":          ca.hits,
                "verified_hits": ca.verified_hits,
                "ttl_days":      ca.ttl_days,
                "created_at":    ca.created_at,
                "last_seen":     ca.last_seen,
                "answer_preview": ca.answer_text[:120] + ("…" if len(ca.answer_text) > 120 else ""),
                "signature_ok":  ca.is_valid(),
            }, indent=2))

    elif args.cmd == "invalidate":
        ok = cache.invalidate(args.fingerprint)
        print("demoted" if ok else "not found")

    elif args.cmd == "sweep":
        n = cache.sweep_expired()
        print(f"swept {n} expired entries")

    elif args.cmd == "verify":
        ok = cache.verify(args.fingerprint)
        print("verified" if ok else "not found")

    cache.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
