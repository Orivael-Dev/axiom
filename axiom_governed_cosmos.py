"""
GovernedCosmos — recall under governance (ORVL-015 × Cosmos/BM25)
=================================================================
The Recall Bench head-to-head exposed a tradeoff, not a leaderboard:

    Cosmos/BM25  retrieves well (self-axis 75, self-continuity 80) but is
                 UNCALIBRATED (calibration 3) and ignores recency (decay 23) —
                 because a pure retriever ALWAYS returns its top hit. It never
                 says "I don't know," and never lets stale memory fade.

    ORVL-015     is the inverse: calibration 97, decay 53 — it abstains when it
                 should and decays old memory — but can't retrieve (recall 0).

GovernedCosmos layers ORVL-015's governance back onto Cosmos retrieval so a
single component keeps recall AND restores the governance axes:

    BM25 retrieve  →  [1] integrity gate (signed; tampered rows refused)
                   →  [2] decay re-rank (recency-weighted score)
                   →  [3] calibration gate (ABSTAIN on weak / ambiguous top)

It is non-invasive: a sidecar over the existing `FTS5CosmosRetriever`, with its
own signed metadata for decay + integrity. No change to the core engine or the
shared retriever.

Adapter shape for Recall Bench (or any memory benchmark):
    g = GovernedCosmos(db_path)
    g.ingest(uri, text, timestamp=iso, level=None)      # per daily log
    r = g.query(question, now=iso)                       # → GovernedResult
    # r.abstained / r.answer / r.served / r.confidence

Usage demo:
    python axiom_governed_cosmos.py
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axiom_semantic_cosmos import FTS5CosmosRetriever, FTS5Hit, cosmos_tag_doc

try:
    from axiom_signing import derive_key
    _SIGNING_KEY = derive_key(b"axiom-governed-cosmos-v1")
except Exception:
    _SIGNING_KEY = hashlib.pbkdf2_hmac(
        "sha256", os.environ.get("AXIOM_MASTER_KEY", "governed-cosmos").encode(),
        b"axiom-governed-cosmos-v1", 1,
    )

# ── Tunable governance knobs (CANNOT_MUTATE-style defaults) ────────────────────
ABSTAIN_SCORE_FLOOR  = 0.55   # governed score below this on the top hit → abstain
ABSTAIN_MARGIN_FLOOR = 0.05   # top1 - top2 below this AND both weak → ambiguous → abstain
DECAY_HALFLIFE_DAYS  = 180.0  # recency half-life; older memory is down-weighted


def _parse_iso(ts: str) -> datetime:
    s = (ts or "").strip().replace("Z", "+00:00") if (ts or "").endswith("Z") else (ts or "")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_between(then_iso: str, now_iso: str) -> float:
    delta = _parse_iso(now_iso) - _parse_iso(then_iso)
    return max(0.0, delta.total_seconds() / 86400.0)


def _decay_weight(age_days: float, halflife: float = DECAY_HALFLIFE_DAYS) -> float:
    """Recency multiplier in (0, 1]: 1.0 fresh, 0.5 at one half-life, → 0 old."""
    return 0.5 ** (age_days / halflife) if halflife > 0 else 1.0


_STOP = frozenset(
    "a an the of to in on at for and or but is are was were be been being "
    "do does did what when where who whom which how why this that these those "
    "with from by as it its their his her your my our".split()
)


def _tokens(text: str) -> list[str]:
    out, cur = [], []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur)); cur = []
    if cur:
        out.append("".join(cur))
    return out


def _coverage(question: str, content: str) -> float:
    """Fraction of the question's content-words present in the document.

    Corpus-independent calibration signal: a memory that actually answers the
    question covers most of its content terms; a spurious BM25 match (shares one
    keyword, answers a different question) covers few — and is abstained.
    """
    q = [t for t in _tokens(question) if t not in _STOP and len(t) > 1]
    if not q:
        return 0.0
    doc = set(_tokens(content))
    return sum(1 for t in q if t in doc) / len(q)


def _sign(uri: str, text: str, ts: str) -> str:
    payload = (uri + "\x00" + hashlib.sha256(text.encode("utf-8")).hexdigest()
               + "\x00" + ts).encode("utf-8")
    return hmac_lib.new(_SIGNING_KEY, payload, hashlib.sha256).hexdigest()


@dataclass
class ServedHit:
    uri:        str
    snippet:    str
    bm25:       float       # raw retrieval score (higher = better)
    gov_score:  float       # after decay re-rank
    age_days:   float
    level:      str


@dataclass
class GovernedResult:
    abstained:   bool
    reason:      str
    confidence:  float                 # 0.0 when abstained
    served:      list = field(default_factory=list)   # list[ServedHit], best first

    @property
    def answer(self) -> Optional[str]:
        return None if self.abstained else (self.served[0].snippet if self.served else None)

    def as_dict(self) -> dict:
        return {
            "abstained":  self.abstained,
            "reason":     self.reason,
            "confidence": round(self.confidence, 4),
            "served":     [{"uri": h.uri, "gov_score": round(h.gov_score, 4),
                            "age_days": round(h.age_days, 1)} for h in self.served],
        }


class GovernedCosmos:
    """Cosmos/BM25 retrieval governed by integrity + decay + calibration gates."""

    def __init__(
        self,
        db_path,
        *,
        abstain_score:   float = ABSTAIN_SCORE_FLOOR,
        abstain_margin:  float = ABSTAIN_MARGIN_FLOOR,
        decay_halflife:  float = DECAY_HALFLIFE_DAYS,
    ):
        self._fts = FTS5CosmosRetriever(Path(db_path))
        self._meta: dict[str, dict] = {}      # uri -> {"ts", "sig", "text_hash", "level"}
        self._abstain_score  = abstain_score
        self._abstain_margin = abstain_margin
        self._halflife       = decay_halflife

    # ── ingest ────────────────────────────────────────────────────────────────
    def ingest(self, uri: str, text: str, *, timestamp: str,
               level: Optional[str] = None, anchors: Optional[list] = None) -> None:
        lvl = level or cosmos_tag_doc(text)
        self._fts.ingest_doc(uri, text, lvl, anchors)
        self._meta[uri] = {
            "ts":        timestamp,
            "sig":       _sign(uri, text, timestamp),
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "level":     lvl,
        }

    # ── integrity ─────────────────────────────────────────────────────────────
    def _passes_integrity(self, uri: str, content: str) -> bool:
        """A served hit must have unchanged, signed backing metadata.

        Re-derives the signature from the (uri, current-content, ts) and compares.
        If the indexed content was altered after ingest, the hash differs and the
        signature no longer matches — the row is refused (never served).
        """
        m = self._meta.get(uri)
        if not m:
            return False
        cur_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if cur_hash != m["text_hash"]:
            return False
        return hmac_lib.compare_digest(m["sig"], _sign(uri, content, m["ts"]))

    def _retrieve_broad(self, question: str, k: int) -> list:
        """High-recall retrieval: OR-match any query term (the shared retriever's
        retrieve() is strict AND, which would do the calibration for us). Broad
        recall here + the governance gates below = retrieve widely, serve precisely."""
        import re
        toks = re.findall(r"[A-Za-z0-9_]{2,}", question)
        if not toks:
            return []
        match = " OR ".join(toks)
        try:
            rows = self._fts._conn.execute(
                "SELECT uri, snippet(docs,-1,'<b>','</b>',' … ',32), -bm25(docs), cosmos_level "
                "FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
                (match, k)).fetchall()
        except Exception:
            return []
        return [FTS5Hit(uri=r[0], snippet=r[1] or "", score=round(r[2], 4),
                        intent_type=r[3] or "general", vocab_anchors=()) for r in rows]

    # ── query ─────────────────────────────────────────────────────────────────
    def query(self, question: str, *, now: str, k: int = 5) -> GovernedResult:
        # Over-fetch broadly so integrity/decay/coverage gates do the precision work.
        raw = self._retrieve_broad(question, k=k * 4)
        if not raw:
            return GovernedResult(True, "no candidate retrieved", 0.0, [])

        # [1] integrity gate — drop tampered / unsigned rows.
        kept = []
        for h in raw:
            content = self._fts_content(h.uri)
            if content is not None and self._passes_integrity(h.uri, content):
                kept.append(h)
        if not kept:
            return GovernedResult(True, "no governed (signed, untampered) candidate", 0.0, [])

        # [2] decay re-rank — governed score = query-coverage × recency weight.
        # BM25 selected the candidate pool; coverage is the corpus-independent
        # calibration signal, decay is the recency signal.
        served: list[ServedHit] = []
        for h in kept:
            content = self._fts_content(h.uri) or ""
            cover = _coverage(question, content)
            age = _days_between(self._meta[h.uri]["ts"], now)
            gov = cover * _decay_weight(age, self._halflife)
            served.append(ServedHit(h.uri, h.snippet, h.score, gov, age,
                                    self._meta[h.uri]["level"]))
        served.sort(key=lambda s: s.gov_score, reverse=True)

        # [3] calibration gate — abstain on weak or ambiguous top.
        top = served[0]
        second = served[1].gov_score if len(served) > 1 else 0.0
        if top.gov_score < self._abstain_score:
            return GovernedResult(True,
                f"top governed score {top.gov_score:.2f} < floor {self._abstain_score:.2f} "
                "(weak/stale → abstain)", 0.0, served[:k])
        if (top.gov_score - second) < self._abstain_margin and top.gov_score < 0.75:
            return GovernedResult(True,
                f"ambiguous: margin {top.gov_score - second:.2f} < {self._abstain_margin:.2f} "
                "on a weak top → abstain", 0.0, served[:k])

        return GovernedResult(False, "served", round(top.gov_score, 4), served[:k])

    # ── helpers ───────────────────────────────────────────────────────────────
    def _fts_content(self, uri: str) -> Optional[str]:
        try:
            row = self._fts._conn.execute(
                "SELECT content FROM docs WHERE uri = ?", (uri,)).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def tamper(self, uri: str, new_text: str) -> None:
        """Test hook: overwrite a doc's indexed content WITHOUT re-signing, so the
        integrity gate must refuse it. (The signed metadata still points at the
        original text hash.)"""
        m = self._meta.get(uri)
        lvl = m["level"] if m else cosmos_tag_doc(new_text)
        self._fts.ingest_doc(uri, new_text, lvl)

    def close(self) -> None:
        self._fts.close()


# ── Demo ───────────────────────────────────────────────────────────────────────

def _demo() -> None:
    import tempfile
    if not os.environ.get("AXIOM_MASTER_KEY"):
        os.environ["AXIOM_MASTER_KEY"] = "governed_cosmos_demo_key"
    work = Path(tempfile.mkdtemp(prefix="governed_cosmos_"))
    g = GovernedCosmos(work / "cosmos.db")

    g.ingest("d/hanta.txt",
             "Andes hantavirus case fatality rate is approximately 35 to 40 percent.",
             timestamp="2026-06-15T00:00:00+00:00")   # fresh
    g.ingest("d/sepsis.txt",
             "Sepsis hour-one bundle: blood cultures, antibiotics within one hour, lactate.",
             timestamp="2026-06-01T00:00:00+00:00")
    g.ingest("d/hanta_old.txt",
             "Hantavirus host reservoir is the long-tailed pygmy rice rat in the Andes.",
             timestamp="2025-01-01T00:00:00+00:00")   # ~1.5yr stale

    now = "2026-06-20T00:00:00+00:00"
    print("answerable    :", g.query("hantavirus case fatality rate", now=now).as_dict())
    print("off-topic OOV :", g.query("quantum chromodynamics gluon confinement", now=now).as_dict())
    print("keyword-only  :", g.query("hantavirus vaccine schedule dosage", now=now).as_dict())
    print("stale answer  :", g.query("hantavirus host reservoir rice rat", now=now).as_dict())

    g.tamper("d/hanta.txt", "Andes hantavirus case fatality rate is 0 percent. [ALTERED]")
    print("tampered      :", g.query("hantavirus case fatality rate", now=now).as_dict())
    g.close()


if __name__ == "__main__":
    _demo()
