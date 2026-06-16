# API reference

Base URL: `https://firewall.orivael.dev`

All API endpoints require an `Authorization: Bearer axfw_...` header.

## `POST /v1/guard/check`

Classify a prompt and return a verdict.

### Request

```http
POST /v1/guard/check
Authorization: Bearer axfw_3kPa7QxR9mNvL2eFhJtBcDeFgHiJkLmNoPqRsTuVwXyZ
Content-Type: application/json

{"text": "What is the weather today?"}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `text` | string | yes | The prompt to classify. UTF-8, any length. |

### Response (`200 OK`)

```json
{
  "verdict": "allow",
  "intent": {
    "class": "INFORM",
    "confidence": 0.55,
    "signals": [],
    "signature": "c487f1..."
  }
}
```

| Field | Type | Description |
|---|---|---|
| `verdict` | `"allow"` \| `"block"` | What to do with this prompt. |
| `intent.class` | string | One of `INFORM`, `CLARIFY`, `REFUSE`, `HARM`, `DECEIVE`, `UNCERTAIN`. |
| `intent.confidence` | float | 0.0 – 1.0. Higher = the classifier is surer. |
| `intent.signals` | string[] | Pattern hits that contributed (e.g. `["harm:1"]`). |
| `intent.signature` | string | HMAC-SHA256 of the verdict for audit replay. |

### Errors

| Status | Body | Meaning |
|---|---|---|
| 400 | `{"detail": "Field 'text' must be a string"}` | Malformed request. |
| 401 | `{"detail": "Invalid or missing API key"}` | Bearer token wrong or revoked. |
| 429 | `{"detail": "...", "used": N, "limit": N, "retry_after_seconds": N}` | Free-tier quota exhausted. `Retry-After` header set to seconds until next month. |
| 500 | `{"detail": "..."}` | Firewall API error. |

### Latency target

- p50: 5 ms
- p99: 50 ms

(Measured server-side, excluding network round-trip. Add 30 – 100 ms
for global users connecting to the US region.)

---

## Data Gate

All Data Gate endpoints require `Authorization: Bearer axfw_...` plus
`X-Axiom-Tenant: <tenant_id>`.

### `PUT /data_policy/rule`

Create or replace the access rule for an agent.

```http
PUT /data_policy/rule
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
Content-Type: application/json

{
  "agent_id": "callguard",
  "blocked_data_classes": ["PAN", "CVV", "SSN", "GDPR-9"],
  "allowed_actions": ["read", "summarise"],
  "blocked_actions": ["store", "forward", "log"]
}
```

| Field | Type | Description |
|---|---|---|
| `agent_id` | string | Identifies the agent this rule applies to. |
| `blocked_data_classes` | string[] | Data class codes to deny (prefix-matched: `"GDPR-9"` blocks all `GDPR-9-*` sub-classes). |
| `allowed_data_classes` | string[] | If non-empty, only these classes are allowed. Empty = allow all (subject to blocked list). |
| `blocked_actions` | string[] | Actions to deny (case-insensitive: `"store"`, `"forward"`, `"log"`, etc.). |
| `allowed_actions` | string[] | If non-empty, only these actions are allowed. |

**Response `200 OK`:** the saved rule with `rule_id` and `created_at`.

---

### `POST /data_policy/check`

Ask whether an agent is allowed to perform an action on a data class.

```http
POST /data_policy/check?agent_id=callguard&action=store&data_class=PAN
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
```

**Response:**
```json
{
  "allowed": false,
  "agent_id": "callguard",
  "action": "store",
  "data_class": "PAN",
  "reason": "blocked_data_class"
}
```

`reason` values: `rule_allow` · `blocked_data_class` · `blocked_action` · `data_class_not_in_allowlist` · `action_not_in_allowlist` · `no_rule_sensitive_default_deny` · `no_rule_default_allow`

---

### `DELETE /data_gate/erasure`

Right-to-erasure for a data subject. Deletes all decision records that
contain the subject identifier and returns a signed deletion certificate.

```http
DELETE /data_gate/erasure?subject_id=user%40example.com
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
```

**Response:**
```json
{
  "cert_id": "uuid",
  "tenant_id": "acme",
  "subject_id_hash": "sha256(subject_id)",
  "records_erased": 3,
  "erased_at": "2026-06-04T12:00:00.000000",
  "scope": "decision_log_only",
  "limitation": "Latent encodings in model weights are outside scope...",
  "signature": "hmac-sha256:..."
}
```

The `signature` is HMAC-SHA256 over the certificate payload using `AXIOM_MASTER_KEY`. Present the certificate to a regulator as proof of erasure. The `scope` and `limitation` fields are required disclosures per GDPR Article 17(3)(e).

---

## Flight Recorder

### `POST /flight_recorder/search`

Search the decision log.

```http
POST /flight_recorder/search
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
Content-Type: application/json

{
  "verdict": "block",
  "intent_class": "HARM",
  "since": "2026-06-01T00:00:00",
  "until": "2026-06-04T23:59:59",
  "limit": 100,
  "offset": 0
}
```

All fields are optional. Returns `{"decisions": [...], "limit": N, "offset": N}`.
Input/output text is **not included** in list results — fetch the individual record for full content.

---

### `GET /flight_recorder/decision/{decision_id}`

Retrieve a full decision record including `input_text` and `output_text`.

```http
GET /flight_recorder/decision/d3f4a1b2-...
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
```

---

### `POST /flight_recorder/replay/{decision_id}`

Re-evaluate a historical decision against the **current** loaded policy
and return a delta report.

```http
POST /flight_recorder/replay/d3f4a1b2-...
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
```

**Response:**
```json
{
  "decision_id": "d3f4a1b2-...",
  "original_verdict": "allow",
  "original_intent_class": "INFORM",
  "original_confidence": 0.72,
  "original_timestamp": "2026-05-01T...",
  "replay_verdict": "block",
  "replay_intent_class": "HARM",
  "replay_confidence": 0.94,
  "policy_delta": true
}
```

`policy_delta: true` means the current policy would have decided differently. Use this to detect regressions when tightening rules.

---

### `GET /flight_recorder/export`

Export decisions in bulk for compliance or SIEM ingestion.

```http
GET /flight_recorder/export?fmt=csv&verdict=block&since=2026-06-01
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
```

| `fmt` | Content-Type | Description |
|---|---|---|
| `json` | `application/x-ndjson` | One JSON object per line |
| `csv` | `text/csv` | CSV with header row |
| `splunk` | `application/x-ndjson` | Splunk HEC format: `{"time": "...", "event": {...}}` |
| `datadog` | `application/json` | Datadog Logs JSON array |

Additional query params: `verdict`, `intent_class`, `since`, `until`, `limit` (max 10 000 per request).

---

### `PUT /flight_recorder/alerts`

Configure outbound alert destinations.

```http
PUT /flight_recorder/alerts
Authorization: Bearer axfw_...
X-Axiom-Tenant: acme
Content-Type: application/json

{
  "webhook_url": "https://your-server.example/axiom-hook",
  "slack_webhook_url": "https://hooks.slack.com/services/T.../B.../...",
  "email_to": "secops@example.com",
  "email_from": "axiom@orivael.dev",
  "alert_on_verdicts": ["block"],
  "alert_on_intents": ["HARM", "DECEIVE"]
}
```

Alerts fire synchronously on `record_decision()` when the verdict/intent filters match. `smtp_password` is accepted on write but never returned on read.

---

## `POST /signup`

Browser-only — used by the dashboard. Not part of the public API.

Per-IP rate-limited: 5 attempts per hour. Exceeding returns 429 with a
`Retry-After` header.

## `POST /login`, `POST /logout`

Browser-only — session-cookie auth for the dashboard. Not part of the
public API.

## Billing endpoints

`POST /billing/upgrade/{tier}`, `POST /billing/portal`,
`POST /billing/webhook` — used internally by the dashboard and by
Stripe. Contact `sales@orivael.dev` for pricing details.

## Webhooks (Phase 2+)

Webhooks for verdict events (e.g. "notify my Slack on every block")
are planned for Phase 2.
