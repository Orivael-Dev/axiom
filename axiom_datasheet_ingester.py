"""Incremental datasheet ingester for the BM25 local retriever.

Extracts text from PDF datasheets (and any supported text file), chunks
each document into page-sized pieces, caches chunks as .txt files on
disk, and appends them to a live `LocalRetriever` without triggering a
full IDF rebuild.

Why BM25 for datasheets:
  Datasheets are keyword-dense — part numbers (STM32F103C8T6), voltage
  ratings (3.3 V), pinout labels — that are best found by exact keyword
  match. BM25 returns accurate results for identifier-style queries with
  no embedding model, no GPU, and no cloud dependency.

Usage:
    from axiom_research_retriever import LocalRetriever
    from axiom_datasheet_ingester import DatasheetIngester

    r = LocalRetriever(roots=[Path("docs")])
    r.build()

    ing = DatasheetIngester(retriever=r)
    ing.ingest_folder(Path("/mnt/datasheets"))   # 100 MB of PDFs

    # Immediately searchable — no rebuild triggered
    results = r.retrieve("STM32F103 flash", k=5)

CLI:
    python3 -m axiom_datasheet_ingester ingest --folder /mnt/datasheets
    python3 -m axiom_datasheet_ingester watch  --folder /mnt/datasheets
    python3 -m axiom_datasheet_ingester stats
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from axiom_research_retriever import LocalRetriever, _tokenize, _MAX_BYTES

_DEFAULT_CACHE_DIR = Path.home() / ".axiom" / "datasheet_chunks"
_DEFAULT_CHUNK_TOKENS = 400      # roughly one datasheet page
_DEFAULT_OVERLAP_TOKENS = 40     # 10% overlap keeps context at page boundaries
_SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".rst"}


def _extract_pdf_text(path: Path) -> Optional[str]:
    """Extract plain text from a PDF. Requires pypdf or pdfminer.six."""
    # Try pypdf first (faster, pure-Python, no C deps)
    try:
        import pypdf   # type: ignore
        reader = pypdf.PdfReader(str(path))
        pages = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages.append(t)
        return "\n\n".join(pages) if pages else None
    except ImportError:
        pass

    # Fallback: pdfminer.six
    try:
        from pdfminer.high_level import extract_text as pm_extract  # type: ignore
        text = pm_extract(str(path))
        return text if text and text.strip() else None
    except ImportError:
        pass

    return None   # no PDF library available


def _chunk_by_tokens(
    text: str,
    max_tokens: int = _DEFAULT_CHUNK_TOKENS,
    overlap: int = _DEFAULT_OVERLAP_TOKENS,
) -> List[str]:
    """Split text into chunks of at most max_tokens BM25 tokens, with overlap."""
    tokens = _tokenize(text)
    if not tokens:
        return []

    # Use a simple approach: find token boundaries in the original text,
    # then slice the raw text by character position.
    # For datasheets we don't need sentence-boundary splitting — token-count
    # chunks are fine because queries target identifiers, not prose.
    words = text.split()
    chunks: List[str] = []
    step = max(1, max_tokens - overlap)
    i = 0
    while i < len(words):
        chunk_words = words[i : i + max_tokens]
        chunks.append(" ".join(chunk_words))
        i += step
    return chunks


class DatasheetIngester:
    """Extract text from datasheets, chunk, cache on disk, add to retriever.

    Parameters
    ----------
    retriever      : the LocalRetriever instance to populate
    cache_dir      : where chunk .txt files are stored (default ~/.axiom/datasheet_chunks)
    chunk_tokens   : approximate tokens per chunk (one datasheet page ≈ 400)
    overlap_tokens : token overlap between adjacent chunks for context continuity
    """

    def __init__(
        self,
        retriever: LocalRetriever,
        cache_dir: Path = _DEFAULT_CACHE_DIR,
        chunk_tokens: int = _DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = _DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        self._retriever     = retriever
        self._cache_dir     = Path(cache_dir)
        self._chunk_tokens  = chunk_tokens
        self._overlap_tokens = overlap_tokens
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Index file: {source_path: {mtime, chunk_paths[]}}
        self._index_path = self._cache_dir / "ingester_index.json"
        self._index: Dict[str, dict] = self._load_index()

    # ── index persistence ─────────────────────────────────────────────────

    def _load_index(self) -> Dict[str, dict]:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text())
            except Exception:
                pass
        return {}

    def _save_index(self) -> None:
        self._index_path.write_text(json.dumps(self._index, indent=2))

    # ── chunk cache helpers ───────────────────────────────────────────────

    def _doc_cache_dir(self, source_path: Path) -> Path:
        digest = hashlib.sha256(str(source_path).encode()).hexdigest()[:16]
        d = self._cache_dir / digest
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _source_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # ── text extraction ───────────────────────────────────────────────────

    def _extract_text(self, path: Path) -> Optional[str]:
        ext = path.suffix.lower()
        if ext == ".pdf":
            return _extract_pdf_text(path)
        try:
            if path.stat().st_size > _MAX_BYTES * 10:   # 2.5 MB cap for plain text
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # ── public API ────────────────────────────────────────────────────────

    def ingest_file(self, path: Path) -> int:
        """Ingest one file into the retriever. Returns chunk count (0 if skipped).

        Idempotent: skips the file if it has already been ingested at the
        same mtime. Call `ingest_file(path)` again after updating a file
        and the new content will replace the old chunks in the cache.
        """
        path = path.resolve()
        mtime = self._source_mtime(path)
        key   = str(path)

        # Check if already ingested at this mtime
        entry = self._index.get(key)
        if entry and entry.get("mtime") == mtime:
            # Already cached — load chunk files into retriever if not present
            chunk_paths = [Path(p) for p in entry.get("chunk_paths", [])]
            existing = [cp for cp in chunk_paths if cp.exists()]
            if existing:
                self._retriever.add_documents(existing)
                return len(existing)

        text = self._extract_text(path)
        if not text or not text.strip():
            return 0

        chunks = _chunk_by_tokens(text, self._chunk_tokens, self._overlap_tokens)
        if not chunks:
            return 0

        cache_d = self._doc_cache_dir(path)

        # Remove stale chunk files if mtime changed
        for old in cache_d.glob("chunk_*.txt"):
            old.unlink(missing_ok=True)

        chunk_paths: List[Path] = []
        for i, chunk in enumerate(chunks):
            cp = cache_d / f"chunk_{i:04d}.txt"
            cp.write_text(chunk, encoding="utf-8")
            chunk_paths.append(cp)

        # Update index
        self._index[key] = {
            "mtime":       mtime,
            "chunk_paths": [str(p) for p in chunk_paths],
            "source":      str(path),
            "chunks":      len(chunks),
        }
        self._save_index()

        added = self._retriever.add_documents(chunk_paths)
        return added

    def ingest_folder(
        self,
        folder: Path,
        glob: str = "**/*",
    ) -> Dict[str, int]:
        """Ingest all supported files under `folder`. Returns {path: chunk_count}.

        Idempotent — files at the same mtime are skipped.
        """
        folder = folder.resolve()
        results: Dict[str, int] = {}
        for path in sorted(folder.glob(glob)):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _SUPPORTED_EXTS:
                continue
            n = self.ingest_file(path)
            results[str(path)] = n
        return results

    def watch_folder(
        self,
        folder: Path,
        glob: str = "**/*",
        poll_s: int = 30,
    ) -> None:
        """Poll `folder` for new or updated files and ingest them.

        Runs in the foreground — call from a background thread or process.
        Ctrl-C to stop.
        """
        folder = folder.resolve()
        print(f"[datasheet-ingester] watching {folder}  poll={poll_s}s")
        while True:
            results = self.ingest_folder(folder, glob=glob)
            new = sum(1 for n in results.values() if n > 0)
            if new:
                print(f"[datasheet-ingester] ingested {new} file(s)")
            time.sleep(poll_s)

    def stats(self) -> dict:
        """Return ingestion statistics from the on-disk index."""
        total_chunks = sum(e.get("chunks", 0) for e in self._index.values())
        return {
            "indexed_sources": len(self._index),
            "total_chunks":    total_chunks,
            "cache_dir":       str(self._cache_dir),
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="axiom-datasheet-ingester",
        description="Incrementally ingest datasheets into the BM25 local retriever",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest a folder of datasheets")
    p_ingest.add_argument("--folder", "-f", required=True, type=Path)
    p_ingest.add_argument("--glob", default="**/*")
    p_ingest.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE_DIR)
    p_ingest.add_argument("--chunk-tokens", type=int, default=_DEFAULT_CHUNK_TOKENS)

    p_watch = sub.add_parser("watch", help="poll a folder and ingest new files")
    p_watch.add_argument("--folder", "-f", required=True, type=Path)
    p_watch.add_argument("--poll", type=int, default=30, help="seconds between polls")
    p_watch.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE_DIR)

    p_stats = sub.add_parser("stats", help="show ingestion statistics")
    p_stats.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE_DIR)

    args = ap.parse_args(argv)

    if args.cmd == "stats":
        ingester = DatasheetIngester(
            retriever=LocalRetriever(roots=[]),
            cache_dir=args.cache_dir,
        )
        s = ingester.stats()
        print(f"indexed sources : {s['indexed_sources']}")
        print(f"total chunks    : {s['total_chunks']}")
        print(f"cache dir       : {s['cache_dir']}")
        return 0

    # Build retriever from the cache dir so existing chunks are loaded
    from axiom_research_retriever import default_retriever as _dr
    try:
        retriever_obj = _dr()
        # Unwrap MultiProviderRetriever if present
        if hasattr(retriever_obj, "_providers"):
            for prov in retriever_obj._providers:
                if hasattr(prov, "_retriever"):
                    retriever_obj = prov._retriever
                    break
        if not isinstance(retriever_obj, LocalRetriever):
            retriever_obj = LocalRetriever(roots=[])
    except Exception:
        retriever_obj = LocalRetriever(roots=[])

    ingester = DatasheetIngester(
        retriever=retriever_obj,
        cache_dir=args.cache_dir,
        chunk_tokens=getattr(args, "chunk_tokens", _DEFAULT_CHUNK_TOKENS),
    )

    if args.cmd == "ingest":
        results = ingester.ingest_folder(args.folder, glob=args.glob)
        total_chunks = sum(results.values())
        total_files  = sum(1 for n in results.values() if n > 0)
        print(f"ingested {total_files} file(s) → {total_chunks} chunks")
        return 0

    if args.cmd == "watch":
        ingester.watch_folder(args.folder, poll_s=args.poll)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
