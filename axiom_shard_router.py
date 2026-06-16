"""Federated FTS5 shard router — parallel dispatch across knowledge shards.

Architecture (Tier 3 of the Edge RAG FTS5 Technical Brief):

  Instead of one monolithic CVE database, ShardRouter holds N per-domain
  FTS5 shards (cve.db, bugs.db, runbooks.db, datasheets.db, …).  For
  identifier queries the pattern is matched → single shard → cache hot-path
  (~0 ms) or FTS5 (~3 ms).  For free-text queries all shards are queried in
  parallel and results are merged by BM25 rank.

  Total RAM    : just the active query rows — no in-memory HNSW graphs.
  Total disk   : sum of all .db files; can be hundreds of GB on NVMe.
  Concurrency  : WAL mode means readers never block writers and vice-versa.

Identifier patterns shipped by default (extend via ShardConfig):
  CVE-YEAR-N    → security vulnerabilities
  BUG-N         → internal bug reports
  ERR-*/FAULT-* → industrial / OT error codes
  P0000         → OBD-II automotive diagnostic codes
  ICD-*         → medical diagnostic codes
  TSB-*         → technical service bulletins
  FINRA/GDPR/ISO → regulatory references
  ECONNRESET/OOM/SIGABRT → software runbooks

RAGBundle — signed, portable appliance packaging:
  RAGBundle.pack(shards, output_path)  → creates a signed .rag.axm zip
  RAGBundle.verify(bundle_path)        → verifies HMAC + per-file SHA-256
  RAGBundle.unpack(bundle_path, dest)  → verify + extract to a directory

Usage:
    from axiom_cve_retriever import CVERetriever, CachedCVERetriever
    from axiom_verified_answer_cache import VerifiedAnswerCache
    from axiom_shard_router import ShardRouter, ShardConfig, DEFAULT_SHARD_PATTERNS

    cve_r = CachedCVERetriever(CVERetriever("cve.db"), VerifiedAnswerCache("cve.cache.db"))
    router = ShardRouter([
        ShardConfig("cve", DEFAULT_SHARD_PATTERNS["cve"], cve_r),
    ])
    sources = router.query("what is CVE-2021-44228?", k=5)

Environment-variable wiring:
    AXIOM_SHARD_CVE=/data/cve.db           # activates CVE shard
    AXIOM_SHARD_BUGS=/data/bugs.db         # activates bugs shard
    AXIOM_SHARD_CVE_CACHE=/data/cve.cache  # optional: custom cache path
    AXIOM_RAG_BUNDLE=/data/bundle.rag.axm  # load all shards from a bundle
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Tuple

from axiom_research_retriever import RetrievedSource


# ── Default identifier patterns (Tier 2 domain extensions) ────────────────────

DEFAULT_SHARD_PATTERNS: Dict[str, re.Pattern] = {
    "cve":        re.compile(r"CVE-\d{4}-\d+",                   re.IGNORECASE),
    "bugs":       re.compile(r"\bBUG-\d+\b",                     re.IGNORECASE),
    "errors":     re.compile(r"\b(?:ERR|FAULT|ERROR)-[\w\d]+\b", re.IGNORECASE),
    "obd":        re.compile(r"\bP\d{4}\b"),
    "medical":    re.compile(r"\bICD-?[\dA-Z][A-Z0-9.]{1,6}\b", re.IGNORECASE),
    "legal":      re.compile(
                      r"\b(?:"
                      r"\d+\s+(?:U\.S\.C|CFR|F\.\d|F\dth|U\.S)\b"  # federal citations
                      r"|[A-Z][a-z]+ v\. [A-Z][a-z]+"               # case names
                      r"|\d{4} WL \d+"                               # Westlaw
                      r"|\d{4} U\.S\. LEXIS"                         # LexisNexis
                      r"|(?:FINRA|SEC|GDPR|ISO|HIPAA|CCPA)\s+[\w./:\-]+"  # regulatory
                      r")",
                      re.IGNORECASE,
                  ),
    "runbooks":   re.compile(
                      r"\b(?:ECONNRESET|OOM[-_]KILLER|SIGABRT|SIGSEGV|SIGKILL)\b",
                      re.IGNORECASE,
                  ),
    "regulatory": re.compile(
                      r"\b(?:FINRA|GDPR|ISO)\s+[\w./:\-]+\b",
                      re.IGNORECASE,
                  ),
    "tsb":        re.compile(r"\bTSB-[\d-]+\b",                  re.IGNORECASE),
}


@dataclass
class ShardConfig:
    """Configuration for a single FTS5 knowledge shard.

    Parameters
    ----------
    domain    : short human label, e.g. "cve", "bugs", "runbooks"
    pattern   : compiled regex; a query matching this pattern routes here first
    retriever : CachedCVERetriever (or any object with .answer()/.retrieve()/.stats())
    """
    domain:    str
    pattern:   re.Pattern
    retriever: object   # CachedCVERetriever — typed loosely to avoid circular import


# ── Core router ───────────────────────────────────────────────────────────────

class ShardRouter:
    """Fan-out to N FTS5 shards; merge results by BM25 rank.

    Pattern-matched queries hit a single shard (cache hot-path included).
    Free-text queries are dispatched in parallel to every shard.
    """

    def __init__(
        self,
        shards: List[ShardConfig],
        *,
        max_workers: int = 8,
    ) -> None:
        self._shards     = shards
        self._max_workers = max_workers

    # ── routing ───────────────────────────────────────────────────────────────

    def route(self, query: str) -> Optional[ShardConfig]:
        """Return the first shard whose pattern matches `query`, or None."""
        for shard in self._shards:
            if shard.pattern.search(query):
                return shard
        return None

    # ── public query API ──────────────────────────────────────────────────────

    def query(
        self,
        query: str,
        *,
        k: int = 5,
        reranker=None,
    ) -> List[RetrievedSource]:
        """Dispatch query to the best shard and return up to k results.

        Identifier queries (pattern matched) → single shard, cache aware.
        Free-text queries → query_parallel() across all shards.

        Parameters
        ----------
        reranker : optional SPLADEReranker — applied after retrieval when set
        """
        shard = self.route(query)
        if shard is not None:
            hits = self._query_single(query, shard, k=k)
        else:
            hits = self.query_parallel(query, k=k)

        if reranker is not None and hits:
            hits = reranker.rerank(query, hits, top_n=k)
        return hits

    def query_parallel(
        self,
        query: str,
        *,
        k: int = 5,
    ) -> List[RetrievedSource]:
        """Fan out to ALL shards in parallel, merge top-k by score."""
        if not self._shards:
            return []
        all_hits: List[RetrievedSource] = []
        lock = threading.Lock()

        def _fetch(shard: ShardConfig) -> None:
            try:
                hits = shard.retriever.retrieve(query, k=k)
                with lock:
                    all_hits.extend(hits)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=min(self._max_workers,
                                                len(self._shards))) as pool:
            futs = [pool.submit(_fetch, s) for s in self._shards]
            for f in as_completed(futs):
                f.result()   # propagate any unexpected exceptions

        # Merge: sort by score desc, de-duplicate by uri, return top-k
        seen: set[str] = set()
        merged: List[RetrievedSource] = []
        for hit in sorted(all_hits, key=lambda h: -h.score):
            if hit.uri not in seen:
                seen.add(hit.uri)
                merged.append(hit)
            if len(merged) >= k:
                break
        return merged

    def stats(self) -> dict:
        return {
            "shards": [
                {"domain": s.domain, **s.retriever.stats()}
                for s in self._shards
            ]
        }

    # ── private ───────────────────────────────────────────────────────────────

    def _query_single(
        self,
        query: str,
        shard: ShardConfig,
        *,
        k: int,
    ) -> List[RetrievedSource]:
        """Use the cache-aware answer() path for identifier queries."""
        try:
            text, from_cache = shard.retriever.answer(query)
        except Exception:
            text, from_cache = None, False

        if text:
            # Derive the matched identifier for the title
            m = shard.pattern.search(query)
            title = m.group(0).upper() if m else shard.domain.upper()
            kind  = f"cache · {shard.domain}" if from_cache else f"fts5 · {shard.domain}"
            return [
                RetrievedSource(
                    title    = title,
                    uri      = f"fts5://{shard.domain}/{title}",
                    kind     = kind,
                    score    = 1.0,
                    snippet  = text[:600],
                    provider = f"{shard.domain}-cache" if from_cache else f"{shard.domain}-fts5",
                )
            ]

        # answer() returned None (no FTS5 hit) — fall back to retrieve()
        return shard.retriever.retrieve(query, k=k)

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> Optional["ShardRouter"]:
        """Build from AXIOM_SHARD_<DOMAIN> env vars.

        Returns None when no shard env vars are set (server degrades
        gracefully to the BM25 LocalRetriever path).

        Supported vars:
            AXIOM_SHARD_CVE=/data/cve.db
            AXIOM_SHARD_CVE_CACHE=/data/cve.cache.db   (optional)
            AXIOM_SHARD_BUGS=/data/bugs.db
            ...
        """
        from axiom_cve_retriever import CVERetriever, CachedCVERetriever
        from axiom_verified_answer_cache import VerifiedAnswerCache

        shards: List[ShardConfig] = []
        for domain, pattern in DEFAULT_SHARD_PATTERNS.items():
            env_key = f"AXIOM_SHARD_{domain.upper()}"
            db_path = os.environ.get(env_key, "").strip()
            if not db_path:
                continue
            cache_key  = f"AXIOM_SHARD_{domain.upper()}_CACHE"
            cache_path = os.environ.get(cache_key, "").strip() or \
                         str(Path(db_path).with_suffix(".cache.db"))
            try:
                retriever = CachedCVERetriever(
                    CVERetriever(db_path),
                    VerifiedAnswerCache(db_path=cache_path),
                )
                shards.append(ShardConfig(domain, pattern, retriever))
            except Exception as exc:
                import logging
                logging.getLogger("axiom.shard_router").warning(
                    "shard %s skipped: %s", domain, exc
                )

        return cls(shards) if shards else None


# ── RAGBundle — signed portable appliance packaging ───────────────────────────

def _signing_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-rag-bundle-v1")


def _sign_payload(payload: dict) -> str:
    import hmac as hmac_lib
    data = json.dumps(payload, sort_keys=True,
                      ensure_ascii=True, separators=(",", ":")).encode()
    return hmac_lib.new(_signing_key(), data, hashlib.sha256).hexdigest()


def _verify_payload(payload: dict, signature: str) -> bool:
    import hmac as hmac_lib
    expected = _sign_payload(payload)
    return hmac_lib.compare_digest(expected, signature)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class RAGBundle:
    """Signed, portable packaging for FTS5 shards + verified answer caches.

    ``axm index-pack`` uses this to create a single .rag.axm archive that
    can ship to a Jetson Orin Nano / Raspberry Pi 5 / air-gapped rack server
    and be verified before the first query runs.

    Bundle format (zip):
        rag_manifest.json   ← HMAC-signed shard manifest
        shards/
            <domain>.db
            <domain>.cache.db   (optional)

    The manifest payload is HMAC-SHA256 signed with a key derived from
    AXIOM_MASTER_KEY via ``axiom_signing.derive_key``.  Any bit-flip in
    the db files OR the manifest breaks verification.

    Usage:
        RAGBundle.pack(
            [("cve", Path("cve.db"), Path("cve.cache.db")),
             ("bugs", Path("bugs.db"), None)],
            Path("bundle.rag.axm"),
        )
        ok, info = RAGBundle.verify(Path("bundle.rag.axm"))
        dest_dir = RAGBundle.unpack(Path("bundle.rag.axm"), Path("/opt/axiom/shards"))
    """

    MANIFEST_NAME = "rag_manifest.json"
    SHARDS_DIR    = "shards"

    # ── pack ──────────────────────────────────────────────────────────────────

    @classmethod
    def pack(
        cls,
        shards: List[Tuple[str, Path, Optional[Path]]],
        output_path: Path,
        *,
        compresslevel: int = 6,
    ) -> dict:
        """Create a signed .rag.axm bundle.

        Parameters
        ----------
        shards        : list of (domain, db_path, cache_path_or_None)
        output_path   : destination .rag.axm file
        compresslevel : zip compression level 0-9 (default 6)

        Returns a dict with fingerprint and per-shard sizes.
        """
        output_path = Path(output_path)
        manifest_shards: List[dict] = []
        file_map: List[Tuple[str, Path]] = []   # (zip_name, local_path)

        for domain, db_path, cache_path in shards:
            db_path = Path(db_path)
            if not db_path.exists():
                raise FileNotFoundError(f"shard db not found: {db_path}")
            db_zip  = f"{cls.SHARDS_DIR}/{domain}.db"
            db_hash = _sha256_path(db_path)
            entry: dict = {
                "domain":   domain,
                "db_file":  db_zip,
                "db_sha256": db_hash,
                "db_bytes": db_path.stat().st_size,
            }
            file_map.append((db_zip, db_path))

            if cache_path is not None:
                cache_path = Path(cache_path)
                if cache_path.exists():
                    cache_zip  = f"{cls.SHARDS_DIR}/{domain}.cache.db"
                    entry["cache_file"]   = cache_zip
                    entry["cache_sha256"] = _sha256_path(cache_path)
                    entry["cache_bytes"]  = cache_path.stat().st_size
                    file_map.append((cache_zip, cache_path))

            manifest_shards.append(entry)

        payload = {"version": "1", "shards": manifest_shards}
        signature = _sign_payload(payload)
        manifest  = {**payload, "signature": signature}
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        fingerprint = _sha256_bytes(manifest_bytes)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            output_path, "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compresslevel,
        ) as zf:
            zf.writestr(cls.MANIFEST_NAME, manifest_bytes)
            for zip_name, local_path in file_map:
                zf.write(local_path, zip_name)

        return {
            "fingerprint": fingerprint,
            "output":      str(output_path),
            "size_bytes":  output_path.stat().st_size,
            "shards":      [s["domain"] for s in manifest_shards],
        }

    # ── verify ────────────────────────────────────────────────────────────────

    @classmethod
    def verify(cls, bundle_path: Path) -> Tuple[bool, dict]:
        """Verify the bundle's HMAC signature and per-shard SHA-256 hashes.

        Returns (True, info_dict) on success, (False, error_dict) on failure.
        Does NOT extract files — reads directly from the zip.
        """
        bundle_path = Path(bundle_path)
        try:
            with zipfile.ZipFile(bundle_path, "r") as zf:
                manifest_bytes = zf.read(cls.MANIFEST_NAME)
                manifest = json.loads(manifest_bytes)
                signature = manifest.pop("signature", "")
                payload   = manifest   # {version, shards}
                if not _verify_payload(payload, signature):
                    return False, {"error": "manifest HMAC signature mismatch"}

                errors: List[str] = []
                for shard in payload.get("shards", []):
                    for key in ("db_file", "cache_file"):
                        zname = shard.get(key)
                        sha_key = key.replace("_file", "_sha256")
                        expected_sha = shard.get(sha_key)
                        if zname is None or expected_sha is None:
                            continue
                        data = zf.read(zname)
                        actual_sha = _sha256_bytes(data)
                        if actual_sha != expected_sha:
                            errors.append(
                                f"{zname}: sha256 mismatch "
                                f"(expected {expected_sha[:12]}… "
                                f"got {actual_sha[:12]}…)"
                            )

            if errors:
                return False, {"error": "file hash mismatch", "details": errors}
            fingerprint = _sha256_bytes(manifest_bytes)
            return True, {
                "fingerprint": fingerprint,
                "shards":      [s["domain"] for s in payload.get("shards", [])],
                "verified":    True,
            }

        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            return False, {"error": str(exc)}

    # ── unpack ────────────────────────────────────────────────────────────────

    @classmethod
    def unpack(
        cls,
        bundle_path: Path,
        dest_dir: Path,
        *,
        verify: bool = True,
    ) -> Path:
        """Verify and extract shards to ``dest_dir``.

        Returns the destination directory path.  Raises RuntimeError if
        verification fails (tampered bundle) when ``verify=True``.
        """
        bundle_path = Path(bundle_path)
        dest_dir    = Path(dest_dir)

        if verify:
            ok, info = cls.verify(bundle_path)
            if not ok:
                raise RuntimeError(f"RAGBundle verification failed: {info}")

        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(dest_dir)

        return dest_dir

    # ── load ShardRouter from bundle ──────────────────────────────────────────

    @classmethod
    def load_router(
        cls,
        bundle_path: Path,
        *,
        extract_dir: Optional[Path] = None,
        verify: bool = True,
    ) -> "ShardRouter":
        """Unpack bundle and return a ready ShardRouter.

        ``extract_dir`` defaults to a temporary directory that persists for
        the process lifetime (safe for single-process appliances).
        """
        if extract_dir is None:
            extract_dir = Path(tempfile.mkdtemp(prefix="axiom_rag_"))

        dest = cls.unpack(bundle_path, extract_dir, verify=verify)

        from axiom_cve_retriever import CVERetriever, CachedCVERetriever
        from axiom_verified_answer_cache import VerifiedAnswerCache

        with zipfile.ZipFile(bundle_path, "r") as zf:
            manifest = json.loads(zf.read(cls.MANIFEST_NAME))

        shards: List[ShardConfig] = []
        for s in manifest.get("shards", []):
            domain   = s["domain"]
            pattern  = DEFAULT_SHARD_PATTERNS.get(domain, re.compile(re.escape(domain), re.I))
            db_file  = dest / s["db_file"]
            retriever = CachedCVERetriever(
                CVERetriever(str(db_file)),
                VerifiedAnswerCache(
                    db_path=str(dest / s["cache_file"])
                    if s.get("cache_file")
                    else str(db_file.with_suffix(".cache.db"))
                ),
            )
            shards.append(ShardConfig(domain, pattern, retriever))

        return ShardRouter(shards)
