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
