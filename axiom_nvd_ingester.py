"""Background JSONL tail watcher for live NVD CVE ingestion.

Watches a local JSONL file for new lines and appends them to a live
CVERetriever FTS5 index without triggering a full rebuild. WAL mode
means concurrent readers are never blocked during ingestion.

NVD publishes ~50 new CVEs/day. A separate cron job (or manual download)
updates the local JSONL; this module picks up new lines and keeps the
appliance current with zero maintenance windows.

JSONL row format — same as build_from_jsonl() expects:
    {"User": "what is CVE-2021-44228?", "Assistant": "Log4Shell is ..."}

Usage:
    from axiom_cve_retriever import CVERetriever
    from axiom_nvd_ingester import NVDIngester

    r = NVDIngester(CVERetriever("cve_fts5.db"))
    r.tail_jsonl(Path("/data/nvd_updates.jsonl"))   # returns immediately

    # The background thread keeps running; call .stop() to shut it down.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from axiom_cve_retriever import CVERetriever, CVE_PATTERN


class NVDIngester:
    """Tail a local NVD JSONL file and append new rows to a CVERetriever.

    Parameters
    ----------
    retriever : CVERetriever
        The live FTS5 index to append to.
    poll_s : int
        Seconds between file checks (default 30).
    batch_size : int
        Max rows per INSERT transaction (default 500).
    """

    def __init__(
        self,
        retriever: CVERetriever,
        *,
        poll_s: int = 30,
        batch_size: int = 500,
    ) -> None:
        self._retriever  = retriever
        self._poll_s     = poll_s
        self._batch_size = batch_size
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── public API ────────────────────────────────────────────────────────

    def tail_jsonl(
        self,
        path: Path,
        *,
        daemon: bool = True,
    ) -> threading.Thread:
        """Start a background thread that tails `path` for new CVE rows.

        Returns the thread so the caller can join() it on shutdown.
        Calling tail_jsonl() a second time on the same instance raises
        RuntimeError — create a new NVDIngester for each file.
        """
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("NVDIngester already running; call stop() first")
        self._stop_event.clear()
        path = Path(path).resolve()
        t = threading.Thread(
            target=self._run_tail,
            args=(path,),
            daemon=daemon,
            name=f"nvd-ingester:{path.name}",
        )
        t.start()
        self._thread = t
        return t

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def ingest_file(self, path: Path) -> int:
        """One-shot: ingest all rows in `path`. Returns count inserted.

        Useful for manual catch-up runs or tests. Does not start a thread.
        """
        path = Path(path).resolve()
        entries = self._parse_jsonl(path, byte_offset=0)
        return self._retriever.insert_batch(entries)

    # ── background loop ───────────────────────────────────────────────────

    def _run_tail(self, path: Path) -> None:
        byte_offset = self._resume_offset(path)
        while not self._stop_event.is_set():
            try:
                new_offset, entries = self._read_new(path, byte_offset)
                if entries:
                    inserted = self._retriever.insert_batch(entries)
                    print(
                        f"[nvd-ingester] {path.name}: "
                        f"+{inserted} rows  offset={new_offset}"
                    )
                byte_offset = new_offset
            except Exception as exc:  # pragma: no cover
                print(f"[nvd-ingester] error: {exc}")
            self._stop_event.wait(self._poll_s)

    def _resume_offset(self, path: Path) -> int:
        """Start from the end of the file so we only pick up new lines."""
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _read_new(
        self,
        path: Path,
        byte_offset: int,
    ) -> Tuple[int, List[Tuple[str, str, str]]]:
        """Read lines added after `byte_offset`. Returns (new_offset, entries)."""
        try:
            size = path.stat().st_size
        except OSError:
            return byte_offset, []
        if size <= byte_offset:
            return byte_offset, []
        entries: List[Tuple[str, str, str]] = []
        with path.open("rb") as fh:
            fh.seek(byte_offset)
            pending = b""
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()   # incomplete last line
                for raw in lines:
                    entry = self._parse_line(raw)
                    if entry:
                        entries.append(entry)
                        if len(entries) >= self._batch_size:
                            new_offset = fh.tell() - len(pending)
                            return new_offset, entries
            new_offset = fh.tell() - len(pending)
        return new_offset, entries

    @staticmethod
    def _parse_line(raw: bytes) -> Optional[Tuple[str, str, str]]:
        line = raw.strip()
        if not line:
            return None
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        q = row.get("User", "")
        a = row.get("Assistant", "")
        if not q and not a:
            return None
        m = CVE_PATTERN.search(q) or CVE_PATTERN.search(a)
        cve_id = m.group(0).upper() if m else ""
        return cve_id, q, a

    @staticmethod
    def _parse_jsonl(
        path: Path,
        byte_offset: int = 0,
    ) -> List[Tuple[str, str, str]]:
        entries: List[Tuple[str, str, str]] = []
        with path.open("rb") as fh:
            fh.seek(byte_offset)
            for raw in fh:
                entry = NVDIngester._parse_line(raw)
                if entry:
                    entries.append(entry)
        return entries
