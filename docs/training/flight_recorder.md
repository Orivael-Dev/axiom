# Training manual — Axiom Flight Recorder

> **The black box.** An immutable, searchable, replayable record of
> every AI decision. Required whenever a regulator can ask "what did
> your AI tell that user on March 4th?"

## What it is

Every AI action that passes through the Axiom guard stack produces a
**decision record**: who asked, what they asked, what the model said,
what the guard classified it as, and whether it was allowed or blocked.
Flight Recorder stores these records in a per-tenant immutable log,
indexes them for fast search, and provides:

- **Search** — filter by verdict, intent class, time range
- **Detail view** — full input/output text for any historical decision
- **Replay** — re-evaluate a past decision against the *current* policy
  to detect regressions
- **Export** — bulk compliance export in JSON, CSV, Splunk HEC, Datadog Logs
- **Alerts** — real-time webhook/email/Slack on block events

---

## Who buys this

- **SecOps / SRE** — "show me every HARM-class block in the last 30 days"
- **Compliance ops** — "export our AI decisions for the FFIEC exam"
- **Legal** — "what did the model tell this customer on this date?"
- **Product** — "did tightening this rule cause false positives?"

**The core differentiator:** Datadog, LangSmith, and Pangea log AI
calls too. None produce HMAC-signed manifests. AXIOM's decisions are
tamper-evident — the signature breaks if the record is altered. That
is the only claim that survives adversarial proceedings.

---

## Architecture

```
Incoming API call
       │
       ▼
axiom_guard_api.py  ──── classify (intent, PII, policy) ────►  verdict
       │
       ▼
flight_recorder.record_decision(tenant_id, decision_payload)
       │
       ▼
per-tenant SQLite   decisions table
(tenants/<id>.db)   indexes: (intent_class, timestamp)
                             (verdict, timestamp)
```

Storage is SQLite-per-tenant. The schema is in `axiom_firewall/db.py`;
the recorder logic is in `axiom_firewall/flight_recorder.py`.

---

## Core API

### Recording a decision

Calls happen automatically when traffic flows through
`POST /guard/proxy`, `POST /guard/check`, or `POST /v1/chat/completions`.
You can also call the recorder directly:

```python
from axiom_firewall.flight_recorder import record_decision

decision_id = record_decision("acme_tenant", {
    "api_key_id": "key_abc",
    "endpoint": "/guard/check",
    "verdict": "block",
    "intent_class": "HARM",
    "confidence": 0.97,
    "latency_ms": 42.0,
    "input_text": "the user's prompt",
    "output_text": None,            # None for input-only checks
    "pattern_matched": "meth_synthesis",
    "constitutional_block": True,
    "ftc_reportable": True,
})
```

### Searching

```python
from axiom_firewall.flight_recorder import search_decisions

results = search_decisions(
    "acme_tenant",
    verdict="block",
    intent_class="HARM",
    since="2026-06-01T00:00:00",
    limit=50,
)
# → {"decisions": [...], "limit": 50, "offset": 0}
# input_text / output_text are NOT included in list results
```

List results exclude full text to keep payload sizes manageable.
Use `fetch_decision(tenant_id, decision_id)` to get the full record.

### Replay

```python
from axiom_firewall.flight_recorder import replay_decision
from axiom_intent_classifier import IntentClassifier

result = replay_decision(
    "acme_tenant",
    decision_id="d3f4...",
    current_classifier=IntentClassifier(),
)
# {
#   "original_verdict": "allow",
#   "replay_verdict": "block",
#   "policy_delta": True,   ← current policy would have blocked this
# }
```

`policy_delta: True` means tightening a rule would have caught a past
event you missed. Use this to audit rule changes before deploying them.

### Export

```python
from axiom_firewall.flight_recorder import export_decisions

content, content_type = export_decisions(
    "acme_tenant",
    fmt="splunk",           # "json" | "csv" | "splunk" | "datadog"
    verdict="block",
    since="2026-06-01",
    limit=10_000,
)
# content_type = "application/x-ndjson"
# content = one Splunk HEC event per line
```

---

## REST endpoints

All require `Authorization: Bearer axfw_...` and `X-Axiom-Tenant: <tenant_id>`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/flight_recorder/search` | Filtered search (JSON body with filters) |
| `GET` | `/flight_recorder/decision/{id}` | Full record including input/output text |
| `POST` | `/flight_recorder/replay/{id}` | Policy delta report against current classifier |
| `GET` | `/flight_recorder/export?fmt=csv&...` | Bulk export — 4 formats |
| `PUT` | `/flight_recorder/alerts` | Set alert destinations |
| `GET` | `/flight_recorder/alerts` | Get current alert config |

Full schemas: [`docs/firewall/api-reference.md`](../firewall/api-reference.md).

---

## Configuring alerts

```python
from axiom_firewall.flight_recorder import set_alert_config, AlertConfig

set_alert_config("acme_tenant", AlertConfig(
    webhook_url="https://your-server/axiom-hook",
    slack_webhook_url="https://hooks.slack.com/services/...",
    email_to="secops@acme.com",
    email_from="axiom@orivael.dev",
    smtp_host="smtp.mailgun.org",
    smtp_port=587,
    smtp_user="postmaster@mg.acme.com",
    smtp_password="...",
    alert_on_verdicts=["block"],
    alert_on_intents=["HARM", "DECEIVE"],
))
```

Alerts fire synchronously within `record_decision()`. Delivery errors
are logged but never re-raised — a failed alert does not prevent the
decision from being recorded.

**Slack alert format:**
```
🛡 Axiom Guard — `block` | intent: `HARM` | tenant: `acme_tenant` | 2026-06-04T...
Pattern: `meth_synthesis`
```

---

## Integration patterns

### Pattern 1 — Proxy mode (easiest)

Point your LLM client at AXIOM's OpenAI-compatible drop-in:

```python
import openai
client = openai.OpenAI(
    base_url="https://firewall.orivael.dev/v1",
    api_key="axfw_...",
)
# Every call is automatically classified and recorded.
```

Zero code changes to your application. Every decision lands in Flight
Recorder automatically.

### Pattern 2 — Sidecar (for custom agent stacks)

Emit decision events from your existing orchestrator:

```python
import httpx

def after_llm_call(prompt, response, agent_id, tenant_id):
    # Fire-and-forget — don't block the user response on logging
    httpx.post(
        "https://firewall.orivael.dev/flight_recorder/record",
        json={
            "agent_id": agent_id,
            "input_text": prompt,
            "output_text": response,
            "endpoint": "custom_agent",
        },
        headers={"Authorization": f"Bearer {API_KEY}",
                 "X-Axiom-Tenant": tenant_id},
    )
```

### Pattern 3 — SDK (coming in v2)

Python and TypeScript SDKs will wrap the HTTP calls; Pattern 1/2 work
today without an SDK.

---

## What "tamper-evident" actually means

Each decision record carries a `signature` field:
```
HMAC-SHA256(AXIOM_MASTER_KEY, canonical_json_of_decision)
```

When a regulator asks you to produce a decision record, `signature`
lets them verify the record hasn't been altered since it was written.
The verify step requires your `AXIOM_MASTER_KEY` — share it with your
own legal team but not with the regulator directly (they get the
signed record; you get to prove authenticity on request).

This is why the HMAC chain is a stronger claim than "we have a database
log." Mutable database rows can be edited after the fact; a broken HMAC
signature cannot be silently fixed.

---

## Regulatory framing by industry

| Industry | Regulatory hook | What Flight Recorder provides |
|---|---|---|
| Banking | FFIEC IT Examination Handbook §II, OCC interpretive letters | Tamper-evident record of every AI credit/fraud decision; replay capability for examination |
| Insurance | NAIC Model Bulletin on AI, state market-conduct exams | Decision log with classification and confidence for each claim or underwriting action |
| Healthcare | HIPAA § 164.312(b) audit controls | Append-only log of every AI access to PHI (combined with Data Gate) |
| EU / GDPR | EU AI Act Article 12 (automatic recording of events) | HMAC-signed append-only log; `signature` satisfies "tamper-evident" per recital 47 |
| HR tech | NYC Local Law 144, EEOC guidance | Full audit trail for hiring AI decisions; export for bias audits |

---

## Limitations to communicate clearly

1. **No time-series UI yet.** The API surface is complete; the
   dashboard is a snapshot view (`docs/axiom_dashboard.html`), not a
   scrollable timeline. V2 ships the UI.

2. **SQLite is single-writer.** The per-tenant SQLite approach handles
   Phase 1 load (< 10M decisions/tenant). At > 100M decisions/mo the
   migration path is Postgres with `tenant_id` partitioning.

3. **Replay requires the current classifier.** `replay_decision()` with
   `current_classifier=None` returns a stub. Pass a live
   `IntentClassifier` instance for real re-evaluation. When the model
   the original decision ran on has been retired, replay uses the
   current model — the delta report flags this.

4. **Input/output text is stored verbatim.** If the input contains PII,
   it lands in the decisions table. Combine with Data Gate's
   `REDACT` mode as a pre-processing step to strip PII before it
   reaches the recorder.

---

## Where the code lives

| What | File |
|---|---|
| Core recorder (record, search, replay, export, alerts) | `axiom_firewall/flight_recorder.py` |
| `decisions` table schema + CRUD | `axiom_firewall/db.py` |
| REST endpoint wiring | `axiom_guard_api.py` `/flight_recorder/*` |
| Alert dispatch (webhook, Slack, SMTP) | `axiom_firewall/flight_recorder._dispatch_alerts()` |

---

## See also

- [`docs/firewall/api-reference.md`](../firewall/api-reference.md) — full request/response schemas
- [`docs/training/data_gate.md`](data_gate.md) — the governed-data layer (pair with Flight Recorder)
- [`docs/training/firewall.md`](firewall.md) — the intent classification layer
