"""Axiom Flight Recorder — per-tenant immutable decision log.

Records every guard decision with full input/output context, supports
search/filter queries, replay against current policy, compliance exports
(JSON/CSV/SIEM), and outbound alerts (webhook/email/Slack).

Architecture:
  Storage   — per-tenant SQLite `decisions` table (see db.py)
  Indexing  — composite indexes on (intent_class, timestamp) and (verdict, timestamp)
  Export    — JSON lines, CSV, Splunk HEC JSON, Datadog Logs JSON
  Alerts    — POST webhook, email (SMTP), Slack incoming webhook

Usage (from axiom_guard_api.py):
    from axiom_firewall.flight_recorder import record_decision, AlertConfig

    record_decision(tenant_id, {
        "decision_id": str(uuid.uuid4()),
        "api_key_id": key_id,
        "endpoint": "/guard/check",
        "verdict": "block",
        "intent_class": "HARM",
        "confidence": 0.97,
        "latency_ms": 42.0,
        "input_text": prompt,
        "output_text": None,
        "pattern_matched": "meth_synthesis",
        "constitutional_block": True,
        "ftc_reportable": True,
        "manifest_id": manifest_id,
        "signature": sig,
    })
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional
from urllib import request as urllib_request
from urllib.error import URLError

from .db import (
    get_decision,
    insert_decision,
    query_decisions,
    init_tenant_db,
    _conn,
    _tenant_path,
)

LOG = logging.getLogger("axiom.flight_recorder")


# ── Alert configuration ───────────────────────────────────────────────────

@dataclass
class AlertConfig:
    """Outbound alert destinations for a tenant."""
    webhook_url: Optional[str] = None       # POST JSON payload to this URL
    slack_webhook_url: Optional[str] = None  # Slack incoming webhook URL
    email_to: Optional[str] = None           # SMTP to address
    email_from: Optional[str] = None
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    # Only fire alerts for these verdicts (empty = all verdicts)
    alert_on_verdicts: list[str] = field(default_factory=lambda: ["block"])
    # Only fire alerts for these intent classes (empty = all)
    alert_on_intents: list[str] = field(default_factory=list)

    def should_alert(self, verdict: str, intent_class: str) -> bool:
        verdict_match = (
            not self.alert_on_verdicts
            or verdict in self.alert_on_verdicts
        )
        intent_match = (
            not self.alert_on_intents
            or intent_class in self.alert_on_intents
        )
        return verdict_match and intent_match


# In-memory per-tenant alert config store (backed by tenant DB in production)
_alert_configs: dict[str, AlertConfig] = {}


def set_alert_config(tenant_id: str, cfg: AlertConfig) -> None:
    _alert_configs[tenant_id] = cfg


def get_alert_config(tenant_id: str) -> AlertConfig:
    return _alert_configs.get(tenant_id, AlertConfig())


# ── Core record function ──────────────────────────────────────────────────

def record_decision(tenant_id: str, decision: dict) -> str:
    """Persist a decision and dispatch alerts if configured.

    Returns the decision_id.
    """
    if "decision_id" not in decision or not decision["decision_id"]:
        decision["decision_id"] = str(uuid.uuid4())
    decision["tenant_id"] = tenant_id
    if "timestamp" not in decision:
        decision["timestamp"] = datetime.utcnow().isoformat()

    insert_decision(decision)

    cfg = get_alert_config(tenant_id)
    if cfg.should_alert(decision.get("verdict", ""), decision.get("intent_class", "")):
        _dispatch_alerts(tenant_id, decision, cfg)

    return decision["decision_id"]


# ── Search ────────────────────────────────────────────────────────────────

def search_decisions(
    tenant_id: str,
    *,
    verdict: Optional[str] = None,
    intent_class: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Query the decision log with filters.

    Returns {"decisions": [...], "total": N, "limit": L, "offset": O}.
    """
    rows = query_decisions(
        tenant_id,
        verdict=verdict,
        intent_class=intent_class,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    # Scrub full input/output text from list view (reduce payload size)
    for r in rows:
        r.pop("input_text", None)
        r.pop("output_text", None)

    return {
        "decisions": rows,
        "limit": limit,
        "offset": offset,
    }


def fetch_decision(tenant_id: str, decision_id: str) -> Optional[dict]:
    """Return the full decision record including input/output text."""
    return get_decision(tenant_id, decision_id)


# ── Replay ────────────────────────────────────────────────────────────────

def replay_decision(
    tenant_id: str,
    decision_id: str,
    current_classifier=None,
) -> dict:
    """Re-evaluate a logged decision against the current policy.

    Returns a delta report: original verdict vs current verdict.
    If current_classifier is None, the function returns the original
    decision plus a placeholder noting that live re-evaluation requires
    the classifier to be passed in.
    """
    original = get_decision(tenant_id, decision_id)
    if not original:
        return {"error": f"decision_id not found: {decision_id}"}

    result: dict = {
        "decision_id": decision_id,
        "original_verdict": original["verdict"],
        "original_intent_class": original["intent_class"],
        "original_confidence": original["confidence"],
        "original_timestamp": original["timestamp"],
    }

    if current_classifier is None:
        result["replay_verdict"] = None
        result["replay_note"] = (
            "Pass current_classifier to replay_decision() for live re-evaluation."
        )
        return result

    input_text = original.get("input_text") or ""
    try:
        replay_result = current_classifier.classify(input_text)
        result["replay_verdict"] = "block" if replay_result.intent_class in ("HARM", "DECEIVE") else "allow"
        result["replay_intent_class"] = replay_result.intent_class
        result["replay_confidence"] = replay_result.confidence
        result["policy_delta"] = (
            original["verdict"] != result["replay_verdict"]
        )
    except Exception as exc:
        result["replay_error"] = str(exc)

    return result


# ── Compliance export ─────────────────────────────────────────────────────

def export_decisions(
    tenant_id: str,
    fmt: str = "json",
    *,
    verdict: Optional[str] = None,
    intent_class: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 10_000,
) -> tuple[str, str]:
    """Export decisions in the requested format.

    Returns (content: str, content_type: str).

    Formats:
      json     — JSON lines (one record per line)
      csv      — CSV with header row
      splunk   — Splunk HEC event JSON (one per line, wrapped in {"event":...})
      datadog  — Datadog Logs JSON array
    """
    rows = query_decisions(
        tenant_id,
        verdict=verdict,
        intent_class=intent_class,
        since=since,
        until=until,
        limit=limit,
        offset=0,
    )

    fmt = fmt.lower()

    if fmt == "csv":
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return buf.getvalue(), "text/csv"

    if fmt == "splunk":
        lines = []
        for r in rows:
            lines.append(json.dumps({"time": r.get("timestamp", ""), "event": r}))
        return "\n".join(lines), "application/x-ndjson"

    if fmt == "datadog":
        payload = []
        for r in rows:
            payload.append({
                "ddsource": "axiom-guard",
                "ddtags": f"tenant:{tenant_id},verdict:{r.get('verdict','')}",
                "hostname": "axiom",
                "service": "axiom-guard",
                "message": json.dumps(r),
            })
        return json.dumps(payload), "application/json"

    # Default: JSON lines
    lines = [json.dumps(r) for r in rows]
    return "\n".join(lines), "application/x-ndjson"


# ── Alert dispatch ────────────────────────────────────────────────────────

def _dispatch_alerts(tenant_id: str, decision: dict, cfg: AlertConfig) -> None:
    """Non-blocking best-effort alert dispatch. Logs errors, never raises."""
    payload = {
        "tenant_id": tenant_id,
        "decision_id": decision.get("decision_id"),
        "verdict": decision.get("verdict"),
        "intent_class": decision.get("intent_class"),
        "confidence": decision.get("confidence"),
        "pattern_matched": decision.get("pattern_matched"),
        "constitutional_block": decision.get("constitutional_block"),
        "timestamp": decision.get("timestamp"),
    }

    if cfg.webhook_url:
        _send_webhook(cfg.webhook_url, payload)

    if cfg.slack_webhook_url:
        _send_slack(cfg.slack_webhook_url, payload)

    if cfg.email_to and cfg.email_from:
        _send_email(cfg, payload)


def _send_webhook(url: str, payload: dict) -> None:
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=5):
            pass
    except (URLError, Exception) as exc:
        LOG.warning("Webhook delivery failed to %s: %s", url, exc)


def _send_slack(url: str, payload: dict) -> None:
    verdict = payload.get("verdict", "unknown")
    intent = payload.get("intent_class", "unknown")
    ts = payload.get("timestamp", "")
    text = (
        f":shield: *Axiom Guard* — `{verdict}` | intent: `{intent}` | "
        f"tenant: `{payload.get('tenant_id','')}` | `{ts}`"
    )
    if payload.get("pattern_matched"):
        text += f"\nPattern: `{payload['pattern_matched']}`"
    _send_webhook(url, {"text": text})


def _send_email(cfg: AlertConfig, payload: dict) -> None:
    try:
        subject = (
            f"[Axiom Guard] {payload.get('verdict','').upper()} — "
            f"{payload.get('intent_class','unknown')} detected"
        )
        body = json.dumps(payload, indent=2)
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = cfg.email_from
        msg["To"] = cfg.email_to

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as s:
            if cfg.smtp_user and cfg.smtp_password:
                s.starttls()
                s.login(cfg.smtp_user, cfg.smtp_password)
            s.sendmail(cfg.email_from, [cfg.email_to], msg.as_string())
    except Exception as exc:
        LOG.warning("Email alert failed to %s: %s", cfg.email_to, exc)
