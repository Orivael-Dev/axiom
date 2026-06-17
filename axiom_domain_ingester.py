"""Document ingestion pipeline: folder watch → chunk → BM25 index → KnowledgeCookie → finetune event.

Three-stage pipeline
--------------------
1. **Ingest** — read supported files from a folder, split into overlapping
   character-based chunks, write each chunk as a .txt file under index_dir
   (idempotent via content-hash naming), and call
   ``KnowledgeCookieStore.record_hit()`` for every chunk so the retrieval
   system can track fragment popularity across sessions.

2. **Promote** — handled transparently by KnowledgeCookie: once a fragment's
   ``hit_count`` reaches FINETUNE_THRESHOLD (default 10), its
   ``finetune_emitted`` flag is set and ``_emit_finetune_event()`` appends a
   JSONL line to the configured finetune log.

3. **Emit** — the BM25 index built from index_dir feeds into domain pack
   building via ``export_index()``, which copies all chunk .txt files to an
   output directory consumed by the pack builder.

This file has no heavy dependencies: stdlib + axiom_knowledge_cookie +
axiom_research_retriever (lazy import inside build_retriever()).

CLI
---
    python3 -m axiom_domain_ingester ingest <folder> --domain legal --index-dir ./legal_index
    python3 -m axiom_domain_ingester watch  <folder> --domain legal --index-dir ./legal_index --poll 30
    python3 -m axiom_domain_ingester stats  --index-dir ./legal_index
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from axiom_knowledge_cookie import KnowledgeFragment, KnowledgeCookieStore
    from axiom_research_retriever import LocalRetriever

logger = logging.getLogger("axiom.domain_ingester")

# ---------------------------------------------------------------------------
# ChunkConfig
# ---------------------------------------------------------------------------

@dataclass
class ChunkConfig:
    """Character-based chunking parameters."""
    max_chars: int = 1600          # ~400 tokens at 4 chars/token
    overlap_chars: int = 160       # ~40 tokens overlap between chunks
    min_chars: int = 100           # skip chunks shorter than this
    supported_exts: List[str] = field(
        default_factory=lambda: [".txt", ".md", ".py", ".rst", ".csv"]
    )


# ---------------------------------------------------------------------------
# IngestedChunk
# ---------------------------------------------------------------------------

_ANCHOR_STOP: frozenset[str] = frozenset({
    "this", "that", "with", "from", "have", "been", "will", "would", "could",
    "should", "their", "there", "these", "those", "which", "where", "when",
    "what", "about", "into", "they", "more", "also", "some", "such", "each",
    "only", "both", "then", "than", "them", "were", "said", "does", "like",
    "other", "after", "over", "under", "between",
})

QUESTION_GENERATION_PROMPT = (
    "You are a retrieval expert. Given the following text segment, output exactly 3 "
    "specific questions that this text directly answers. Use precise domain vocabulary. "
    "Output ONLY the 3 questions, one per line, no numbers or bullets.\n\nText:\n{chunk}"
)


@dataclass
class IngestedChunk:
    """A single text chunk produced from an ingested document."""
    source_path: str    # absolute path of source file
    chunk_idx: int      # 0-based chunk number within the file
    content: str        # the chunk text
    char_count: int
    content_hash: str   # SHA256[:16] of content
    intent_type: str = "general"          # classified content type
    vocab_anchors: List[str] = field(default_factory=list)  # extracted domain vocabulary


# ---------------------------------------------------------------------------
# DomainIngester
# ---------------------------------------------------------------------------

class DomainIngester:
    """Pipeline that watches a folder, chunks documents, feeds a BM25 index,
    and records hits into KnowledgeCookie.

    Parameters
    ----------
    domain:          Short label for the knowledge domain (e.g. "legal").
    index_dir:       Directory where chunk .txt files are written.
    knowledge_store: Optional KnowledgeCookieStore for recording hits.
    finetune_log:    Optional path to a JSONL file for finetune-candidate events.
    chunk_config:    Chunking settings; defaults to ChunkConfig().
    session_id:      Session identifier passed to record_hit(); auto-generated
                     as a UUID4 if left empty.
    """

    def __init__(
        self,
        domain: str,
        index_dir: Path,
        knowledge_store: Optional["KnowledgeCookieStore"] = None,
        finetune_log: Optional[Path] = None,
        chunk_config: Optional[ChunkConfig] = None,
        session_id: str = "",
    ) -> None:
        self.domain = domain
        self.index_dir = Path(index_dir)
        self.knowledge_store = knowledge_store
        self.finetune_log = Path(finetune_log) if finetune_log else None
        self.chunk_config = chunk_config or ChunkConfig()
        self.session_id = session_id or str(uuid.uuid4())

        # Track content hashes seen this run to support idempotency
        self._seen_hashes: set[str] = set()

        self.index_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_file(self, path: Path) -> List[IngestedChunk]:
        """Read, chunk, index, and record hits for a single file.

        Parameters
        ----------
        path: Absolute or relative path to the source file.

        Returns a list of IngestedChunk objects written this call.
        Unsupported file extensions are skipped (returns []).
        """
        path = Path(path).resolve()
        if path.suffix.lower() not in self.chunk_config.supported_exts:
            logger.debug("Skipping unsupported extension: %s", path)
            return []

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return []

        chunks = self._chunk_text(text, str(path))
        ingested: List[IngestedChunk] = []

        for chunk in chunks:
            # Write chunk file (idempotent — skips if hash already on disk)
            self._write_chunk(chunk)

            # Always record the hit regardless of whether the file was new;
            # the cookie tracks retrieval frequency across sessions, not
            # just first-time ingestion.
            if self.knowledge_store is not None:
                frag = self.knowledge_store.record_hit(
                    content=chunk.content,
                    source_uri=f"{chunk.source_path}:chunk_{chunk.chunk_idx}",
                    session_id=self.session_id,
                )
                if frag.finetune_emitted and self.finetune_log:
                    self._emit_finetune_event(frag)

            ingested.append(chunk)

        return ingested

    def ingest_folder(self, folder: Path, *, glob: str = "**/*") -> Dict[str, int]:
        """Ingest all supported files under folder matching the glob pattern.

        Parameters
        ----------
        folder: Root folder to search.
        glob:   Glob pattern (default ``**/*`` — all files recursively).

        Returns a dict mapping ``str(path)`` to the number of chunks ingested.
        Files whose chunks are all already in the index are included with
        count 0 (idempotent).
        """
        folder = Path(folder).resolve()
        results: Dict[str, int] = {}

        for candidate in folder.glob(glob):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in self.chunk_config.supported_exts:
                continue

            chunks = self.ingest_file(candidate)
            new_chunks = [c for c in chunks if c.content_hash not in self._seen_hashes]
            # Mark all hashes seen
            for c in chunks:
                self._seen_hashes.add(c.content_hash)

            results[str(candidate)] = len(new_chunks)

        return results

    def watch_folder(self, folder: Path, *, poll_s: int = 30) -> None:
        """Block and periodically ingest new files from folder.

        Calls ``ingest_folder()`` every ``poll_s`` seconds. Tracks which
        content hashes have already been ingested across iterations to avoid
        reprocessing unchanged files. Press Ctrl-C to stop.

        Parameters
        ----------
        folder: Folder to watch.
        poll_s: Polling interval in seconds (default 30).
        """
        folder = Path(folder).resolve()
        logger.info(
            "Watching %s for domain=%r (poll every %ds). Ctrl-C to stop.",
            folder, self.domain, poll_s,
        )

        while True:
            try:
                results = self.ingest_folder(folder)
                new_files = {p: n for p, n in results.items() if n > 0}
                if new_files:
                    total_chunks = sum(new_files.values())
                    logger.info(
                        "Ingested %d new chunk(s) from %d file(s): %s",
                        total_chunks, len(new_files), list(new_files.keys()),
                    )
                else:
                    logger.debug("No new files detected in %s", folder)
                time.sleep(poll_s)
            except KeyboardInterrupt:
                logger.info("watch_folder stopped by user.")
                break

    def build_retriever(self) -> "LocalRetriever":
        """Build and return a LocalRetriever over the current index_dir.

        Raises ImportError (with a clear message) when axiom_research_retriever
        is not available.
        """
        try:
            from axiom_research_retriever import LocalRetriever  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "axiom_research_retriever is required to build a LocalRetriever. "
                "Ensure axiom_research_retriever.py is on the Python path."
            ) from exc

        retriever = LocalRetriever(roots=[self.index_dir])
        retriever.build()
        return retriever

    def export_index(self, output_dir: Path) -> int:
        """Copy all chunk .txt files from index_dir to output_dir.

        Parameters
        ----------
        output_dir: Destination directory (created if absent).

        Returns the number of files copied. Used by the domain pack builder
        to bundle a pre-built BM25 corpus into a pack.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for src in self.index_dir.glob("*.txt"):
            dest = output_dir / src.name
            shutil.copy2(src, dest)
            count += 1

        logger.info("Exported %d chunk file(s) to %s", count, output_dir)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str, source_path: str) -> List[IngestedChunk]:
        """Split text into overlapping character-based chunks.

        Tries to split on a sentence boundary (`. ` or newline) near the
        chunk boundary; falls back to a hard split when no boundary is found
        within a small look-ahead window.

        Parameters
        ----------
        text:        Full document text.
        source_path: Source file path string stored on each chunk.

        Returns a list of IngestedChunk objects (may be empty if text is too
        short after stripping).
        """
        cfg = self.chunk_config
        text = text.strip()
        if not text:
            return []

        chunks: List[IngestedChunk] = []
        start = 0
        chunk_idx = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + cfg.max_chars, text_len)

            # Try to find a sentence boundary near the end of the window
            if end < text_len:
                # Look back up to 15% of max_chars for `. ` or `\n`
                look_back = max(1, cfg.max_chars // 7)
                search_start = max(start, end - look_back)
                best_boundary = -1

                # Prefer paragraph break first, then sentence break
                for sep in ("\n\n", "\n", ". "):
                    pos = text.rfind(sep, search_start, end)
                    if pos > search_start:
                        best_boundary = pos + len(sep)
                        break

                if best_boundary > start:
                    end = best_boundary

            content = text[start:end].strip()

            if len(content) >= cfg.min_chars:
                content_hash = _sha256_prefix(content)
                chunk = IngestedChunk(
                    source_path=source_path,
                    chunk_idx=chunk_idx,
                    content=content,
                    char_count=len(content),
                    content_hash=content_hash,
                    intent_type=self._classify_intent(content),
                    vocab_anchors=self._extract_vocab_anchors(content),
                )
                chunks.append(chunk)
                chunk_idx += 1

            # Advance with overlap — next chunk starts overlap_chars before end
            if end >= text_len:
                break
            next_start = end - cfg.overlap_chars
            # Guard against infinite loop if overlap >= advance
            if next_start <= start:
                next_start = start + max(1, cfg.max_chars - cfg.overlap_chars)
            start = next_start

        return chunks

    def _write_chunk(self, chunk: IngestedChunk) -> Optional[Path]:
        """Write chunk content to index_dir/<content_hash>.txt.

        Idempotent — skips writing if the file already exists.  Always
        writes (or refreshes) the companion ``.meta.json`` sidecar.

        Returns the file path, or None if the chunk was already present (so
        callers can distinguish new vs cached writes).
        """
        dest = self.index_dir / f"{chunk.content_hash}.txt"
        if dest.exists():
            self._write_chunk_meta(chunk, self.domain)
            return None
        dest.write_text(chunk.content, encoding="utf-8")
        self._write_chunk_meta(chunk, self.domain)
        return dest

    def _classify_intent(self, text: str) -> str:
        """Rule-based intent classifier — no LLM required.

        Checks a prioritised list of keyword patterns against the text
        (case-insensitive) and returns the first matching label.
        Falls back to "general" when nothing matches.
        """
        lower = text.lower()

        definition_signals = ("means ", "is defined as", "refers to", "defined as", "is the process of")
        if any(sig in lower for sig in definition_signals):
            return "definition"

        procedure_signals = ("step 1", "step one", "first,", "then,", "how to",
                             "in order to", "procedure:", "follow these", "instructions:")
        if any(sig in lower for sig in procedure_signals):
            return "procedure"

        ruling_signals = ("held that", "the court", "ruled that", "decided that",
                          "judgment", "verdict", "opinion of")
        if any(sig in lower for sig in ruling_signals):
            return "ruling"

        warning_signals = ("warning:", "caution:", "must not", "shall not",
                           "prohibited", "danger:", "do not")
        if any(sig in lower for sig in warning_signals):
            return "warning"

        if re.search(r'\d+\s*(MHz|GHz|KB|MB|GB|ms|ns|V|A|W|°C|%|±)', text):
            return "specification"

        return "general"

    def _extract_vocab_anchors(
        self,
        text: str,
        *,
        domain: str = "general",
        max_anchors: int = 15,
    ) -> List[str]:
        """Extract domain vocabulary keywords from text.

        Simple frequency-based extraction — no LLM, no external deps.
        Returns up to ``max_anchors`` tokens sorted by descending frequency,
        then alphabetically for ties.
        """
        tokens = re.findall(r'[A-Za-z][A-Za-z0-9-]{3,}', text)
        lowered = [t.lower() for t in tokens]
        freq: Dict[str, int] = {}
        for tok in lowered:
            if tok not in _ANCHOR_STOP:
                freq[tok] = freq.get(tok, 0) + 1

        # Sort by frequency descending, then alphabetically for ties
        ranked = sorted(freq.keys(), key=lambda t: (-freq[t], t))
        return ranked[:max_anchors]

    def _write_chunk_meta(self, chunk: "IngestedChunk", domain: str) -> Path:
        """Write a JSON sidecar file alongside the ``.txt`` chunk.

        Idempotent: when the file already exists **and** ``intent_type``
        is not the generic fallback, re-writing is skipped.  Returns the
        sidecar path in all cases.
        """
        meta_path = self.index_dir / f"{chunk.content_hash}.meta.json"

        if meta_path.exists() and chunk.intent_type != "general":
            return meta_path

        meta: dict = {
            "content_hash":        chunk.content_hash,
            "source_path":         chunk.source_path,
            "chunk_idx":           chunk.chunk_idx,
            "domain":              domain,
            "intent_type":         chunk.intent_type,
            "vocab_anchors":       chunk.vocab_anchors,
            "synthetic_questions": [],
            "char_count":          chunk.char_count,
            "created_at":          _iso_now(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta_path

    def enrich_with_questions(
        self,
        chunk: "IngestedChunk",
        backend,
        *,
        domain: str = "general",
    ) -> List[str]:
        """Generate synthetic questions for this chunk (Doc-to-Question / HyDE ingestion).

        Updates the .meta.json sidecar with the generated questions.
        Returns the generated questions as a list of strings.
        Backend can be any object with a ``.complete(prompt, max_tokens)``
        or ``.generate(prompt)`` method.  Wraps in try/except — returns []
        on any error so the main pipeline is never interrupted.
        """
        prompt = QUESTION_GENERATION_PROMPT.format(chunk=chunk.content)
        try:
            try:
                raw: str = backend.complete(prompt, max_tokens=150)
            except (AttributeError, TypeError):
                raw = backend.generate(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.debug("enrich_with_questions failed for %s: %s", chunk.content_hash, exc)
            return []

        questions = [line.strip() for line in raw.splitlines() if line.strip()]

        meta_path = self.index_dir / f"{chunk.content_hash}.meta.json"
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            else:
                meta = {
                    "content_hash":        chunk.content_hash,
                    "source_path":         chunk.source_path,
                    "chunk_idx":           chunk.chunk_idx,
                    "domain":              domain,
                    "intent_type":         chunk.intent_type,
                    "vocab_anchors":       chunk.vocab_anchors,
                    "char_count":          chunk.char_count,
                    "created_at":          _iso_now(),
                }
            meta["synthetic_questions"] = questions
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not update meta sidecar for %s: %s", chunk.content_hash, exc)

        return questions

    def _emit_finetune_event(self, fragment: "KnowledgeFragment") -> None:
        """Append a finetune-candidate JSONL line to finetune_log.

        Called only when fragment.finetune_emitted is True.  The log is
        appended atomically by writing a single JSON line and flushing.
        """
        if self.finetune_log is None:
            return

        self.finetune_log.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event": "finetune_candidate",
            "content_hash": fragment.content_hash,
            "source_uri": fragment.source_uri,
            "hit_count": fragment.hit_count,
            "domain": self.domain,
            "timestamp": _iso_now(),
        }
        with self.finetune_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()

        logger.info(
            "Finetune event emitted for %s (hit_count=%d, domain=%s)",
            fragment.content_hash, fragment.hit_count, self.domain,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def from_domain_pack(
    manifest: "DomainPackManifest",  # quoted to avoid circular import
    store_base: Optional[Path] = None,
) -> DomainIngester:
    """Build a DomainIngester wired to an installed domain pack's index dir.

    Parameters
    ----------
    manifest:   A DomainPackManifest instance (from axiom_domain_pack or similar).
                Must expose ``.domain`` (str) and ``.index_dir`` (Path-like).
    store_base: Optional base directory for the KnowledgeCookieStore cookie
                file.  When None, the store uses its default path
                (``~/.axiom/knowledge.cookie.json``).

    Returns a fully configured DomainIngester ready for ingest_file() /
    ingest_folder() / watch_folder() calls.
    """
    from axiom_knowledge_cookie import KnowledgeCookieStore  # type: ignore[import]

    store = KnowledgeCookieStore(
        Path(store_base) / "knowledge.cookie.json" if store_base else None
    )

    index_dir = Path(manifest.index_dir)

    return DomainIngester(
        domain=manifest.domain,
        index_dir=index_dir,
        knowledge_store=store,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_prefix(text: str, length: int = 16) -> str:
    """Return the first ``length`` hex characters of SHA256(text)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_ingest(args) -> int:
    """Handle `ingest <folder>` subcommand."""
    from axiom_knowledge_cookie import KnowledgeCookieStore  # type: ignore[import]

    index_dir = Path(args.index_dir)
    store = KnowledgeCookieStore() if not args.no_cookie else None
    finetune_log = Path(args.finetune_log) if args.finetune_log else None

    ingester = DomainIngester(
        domain=args.domain,
        index_dir=index_dir,
        knowledge_store=store,
        finetune_log=finetune_log,
    )

    folder = Path(args.folder).resolve()
    if not folder.exists():
        print(f"Error: folder does not exist: {folder}")
        return 1

    results = ingester.ingest_folder(folder)

    total_files = len(results)
    total_chunks = sum(results.values())
    new_files = {p: n for p, n in results.items() if n > 0}

    print(f"Domain:      {args.domain}")
    print(f"Index dir:   {index_dir.resolve()}")
    print(f"Files seen:  {total_files}")
    print(f"New files:   {len(new_files)}")
    print(f"Chunks:      {total_chunks}")
    if new_files:
        print("\nNew files ingested:")
        for path, count in sorted(new_files.items()):
            print(f"  {path}  ({count} chunk(s))")
    return 0


def _cli_watch(args) -> int:
    """Handle `watch <folder>` subcommand."""
    from axiom_knowledge_cookie import KnowledgeCookieStore  # type: ignore[import]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    )

    index_dir = Path(args.index_dir)
    store = KnowledgeCookieStore() if not args.no_cookie else None
    finetune_log = Path(args.finetune_log) if args.finetune_log else None

    ingester = DomainIngester(
        domain=args.domain,
        index_dir=index_dir,
        knowledge_store=store,
        finetune_log=finetune_log,
    )

    folder = Path(args.folder).resolve()
    if not folder.exists():
        print(f"Error: folder does not exist: {folder}")
        return 1

    ingester.watch_folder(folder, poll_s=args.poll)
    return 0


def _cli_stats(args) -> int:
    """Handle `stats` subcommand."""
    index_dir = Path(args.index_dir)
    if not index_dir.exists():
        print(f"Index dir does not exist: {index_dir.resolve()}")
        return 1

    chunk_files = list(index_dir.glob("*.txt"))
    total_chars = 0
    for f in chunk_files:
        try:
            total_chars += f.stat().st_size
        except OSError:
            pass

    print(f"Index dir:     {index_dir.resolve()}")
    print(f"Chunk files:   {len(chunk_files)}")
    print(f"Total bytes:   {total_chars:,}")
    print(f"Avg chunk size: {(total_chars // len(chunk_files)) if chunk_files else 0:,} bytes")

    # Try to build a retriever and show its stats
    ingester = DomainIngester(domain=args.domain or "unknown", index_dir=index_dir)
    try:
        retriever = ingester.build_retriever()
        s = retriever.stats()
        print(f"BM25 indexed:  {s['indexed_files']} file(s), "
              f"{s['unique_tokens']} unique tokens, "
              f"avg len {s['avg_token_len']}")
    except ImportError as exc:
        print(f"(BM25 stats unavailable: {exc})")

    return 0


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="axiom_domain_ingester",
        description="Document ingestion pipeline: folder → BM25 index + KnowledgeCookie",
    )
    sub = ap.add_subparsers(dest="cmd")

    # ---- ingest ----
    p_ingest = sub.add_parser("ingest", help="Ingest a folder once and exit")
    p_ingest.add_argument("folder", help="Folder of documents to ingest")
    p_ingest.add_argument("--domain", default="general", help="Domain label (default: general)")
    p_ingest.add_argument("--index-dir", default="./axiom_index", help="BM25 chunk output dir")
    p_ingest.add_argument("--finetune-log", default=None, help="JSONL file for finetune events")
    p_ingest.add_argument("--no-cookie", action="store_true", help="Skip KnowledgeCookie recording")

    # ---- watch ----
    p_watch = sub.add_parser("watch", help="Watch a folder and ingest new files continuously")
    p_watch.add_argument("folder", help="Folder to watch")
    p_watch.add_argument("--domain", default="general", help="Domain label (default: general)")
    p_watch.add_argument("--index-dir", default="./axiom_index", help="BM25 chunk output dir")
    p_watch.add_argument("--poll", type=int, default=30, help="Poll interval in seconds (default: 30)")
    p_watch.add_argument("--finetune-log", default=None, help="JSONL file for finetune events")
    p_watch.add_argument("--no-cookie", action="store_true", help="Skip KnowledgeCookie recording")

    # ---- stats ----
    p_stats = sub.add_parser("stats", help="Show index statistics")
    p_stats.add_argument("--index-dir", default="./axiom_index", help="BM25 chunk output dir")
    p_stats.add_argument("--domain", default=None, help="Domain label (informational only)")

    args = ap.parse_args(argv)

    if args.cmd == "ingest":
        return _cli_ingest(args)
    elif args.cmd == "watch":
        return _cli_watch(args)
    elif args.cmd == "stats":
        return _cli_stats(args)
    else:
        ap.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
