"""
Hello Operator — Notification System
=====================================
Pluggable notifier for governance events. When the operator blocks a message,
flags one for review, or needs human approval, every configured channel fires.

Channels (configured in config.json → "notifications"):
  console  — print to stdout (always available)
  file     — append signed JSON to ~/.axiom/hello_operator_notifications.jsonl
  webhook  — POST to a URL; Slack- and Discord-compatible payload shape
  stream   — in-memory queue the UI subscribes to over SSE

Every notification carries the HMAC signature of the governance verdict it
reports, so a downstream system can verify it was not forged.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

_FILE_PATH = Path.home() / ".axiom" / "hello_operator_notifications.jsonl"


class Severity(str, Enum):
    INFO  = "INFO"     # routine — answered cleanly
    FLAG  = "FLAG"     # suspicious / low confidence — answered with a warning
    BLOCK = "BLOCK"    # refused — message never reached the model
    APPROVAL = "APPROVAL"  # needs a human decision before proceeding


# Visual mapping for console + webhook
_SEV_ICON  = {"INFO": "ℹ", "FLAG": "⚠", "BLOCK": "⛔", "APPROVAL": "✋"}
_SEV_COLOR = {"INFO": 0x67E8F9, "FLAG": 0xFDE68A, "BLOCK": 0xFCA5A5, "APPROVAL": 0xA78BFA}


class Notification:
    def __init__(
        self,
        severity:    Severity,
        title:       str,
        message:     str,
        session_id:  str = "",
        intent_class: str = "",
        signature:   str = "",
        manifest_id: str = "",
        extra:       Optional[dict] = None,
    ):
        self.severity     = severity if isinstance(severity, Severity) else Severity(severity)
        self.title        = title
        self.message      = message
        self.session_id   = session_id
        self.intent_class = intent_class
        self.signature    = signature
        self.manifest_id  = manifest_id
        self.extra        = extra or {}
        self.timestamp    = datetime.now(timezone.utc).isoformat()

    def as_dict(self) -> dict:
        return {
            "severity":     self.severity.value,
            "title":        self.title,
            "message":      self.message,
            "session_id":   self.session_id,
            "intent_class": self.intent_class,
            "signature":    self.signature,
            "manifest_id":  self.manifest_id,
            "timestamp":    self.timestamp,
            **self.extra,
        }


class NotificationHub:
    """Fans a notification out to every enabled channel.

    Channels are enabled via the config dict, e.g.:
        {
          "console": true,
          "file":    true,
          "webhook": {"url": "https://hooks.slack.com/services/...", "kind": "slack"},
          "stream":  true,
          "min_severity": "FLAG"
        }
    """

    _LEVEL_ORDER = {"INFO": 0, "FLAG": 1, "APPROVAL": 2, "BLOCK": 3}

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.console_on = cfg.get("console", True)
        self.file_on    = cfg.get("file", True)
        self.stream_on  = cfg.get("stream", True)
        self.webhook    = cfg.get("webhook")  # {"url":..., "kind":"slack"|"discord"|"raw"}
        self.min_sev    = str(cfg.get("min_severity", "INFO")).upper()
        self.file_path  = Path(cfg.get("file_path", str(_FILE_PATH)))
        if self.file_on:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory subscribers (UI SSE connections)
        self._subscribers: list[asyncio.Queue] = []

    # ── Subscription (for the SSE stream) ─────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def notify(self, n: Notification) -> None:
        if self._LEVEL_ORDER.get(n.severity.value, 0) < self._LEVEL_ORDER.get(self.min_sev, 0):
            return

        payload = n.as_dict()

        if self.console_on:
            self._console(n)
        if self.file_on:
            self._file(payload)
        if self.stream_on:
            self._stream(payload)
        if self.webhook and self.webhook.get("url"):
            await self._webhook_send(n)

    # ── Channels ──────────────────────────────────────────────────────────────

    def _console(self, n: Notification) -> None:
        icon = _SEV_ICON.get(n.severity.value, "•")
        line = f"  {icon} [{n.severity.value}] {n.title} — {n.message}"
        if n.session_id:
            line += f"  (session={n.session_id})"
        print(line, file=sys.stderr, flush=True)

    def _file(self, payload: dict) -> None:
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except OSError:
            pass

    def _stream(self, payload: dict) -> None:
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    async def _webhook_send(self, n: Notification) -> None:
        if not _HTTPX_AVAILABLE:
            return
        kind = (self.webhook.get("kind") or "raw").lower()
        url  = self.webhook["url"]
        body = self._format_webhook(n, kind)
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(url, json=body)
        except Exception:
            pass  # notifications must never break the chat path

    def _format_webhook(self, n: Notification, kind: str) -> dict:
        icon = _SEV_ICON.get(n.severity.value, "•")
        text = f"{icon} *{n.severity.value}* — {n.title}\n{n.message}"
        if n.session_id:
            text += f"\nsession: `{n.session_id}`"
        if n.signature:
            text += f"\nsig: `{n.signature[:24]}…`"

        if kind == "slack":
            return {"text": text}
        if kind == "discord":
            return {
                "embeds": [{
                    "title":       f"{icon} {n.severity.value} — {n.title}",
                    "description": n.message,
                    "color":       _SEV_COLOR.get(n.severity.value, 0x8898BB),
                    "fields": [
                        {"name": "Session",      "value": n.session_id or "—",   "inline": True},
                        {"name": "Intent",       "value": n.intent_class or "—", "inline": True},
                        {"name": "Signature",    "value": (n.signature[:24] + "…") if n.signature else "—", "inline": False},
                    ],
                    "timestamp": n.timestamp,
                }]
            }
        # raw
        return n.as_dict()

    # ── History (for UI initial load) ─────────────────────────────────────────

    def recent(self, limit: int = 50) -> list[dict]:
        if not self.file_path.exists():
            return []
        out = []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return []
        return out[-limit:]
