# Hello Operator

**A governed internal chatbot you can deploy in a day.**

Built on the AXIOM Governance Runtime. Every message is checked before the model
sees it, every answer is HMAC-signed and appended to a hash-chained ledger, every
refusal is logged, and every blocked or flagged interaction fires a notification.

This is the chatbot teams reach for when they need an AI assistant but can't
afford a compliance incident — internal knowledge base Q&A, HR policy assistant,
legal document search, IT helpdesk.

---

## Quick start

```bash
./hello_operator/setup.sh          # installs deps, generates a key, writes config
python3 hello_operator/server.py   # starts the operator
# open http://localhost:8800
```

Setup is safe to re-run — it never overwrites an existing key or config.

Without `ANTHROPIC_API_KEY` the operator runs in **stub mode** (governance is
fully live; answers are placeholders). Add the key to `.env` for live responses:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```

---

## What governs each message

```
user message
   │
   ▼
1. Intent gate    IntentClassifier (ORVL-016)
   │              HARM / DECEIVE  → hard block, signed refusal, BLOCK notification
   ▼
2. Policy guard   check_constitutional() with your configured agents
   │              known scam / unsafe patterns → hard block (cannot override)
   ▼
3. Model          Claude answers (or stub)
   │              low intent confidence / SUSPICIOUS → answered + FLAG notification
   ▼
4. Sign + ledger  answer manifest HMAC-signed, appended to hash-chained ledger
   ▼
5. Notify         BLOCK / FLAG events fan out to console / file / webhook / UI
```

Two layers do the blocking; both are HMAC-signed, and the ledger is hash-chained
so any tampering with a past entry breaks the chain.

---

## Notification system

Configured in `config.json → notifications`. A notification fires on every
blocked or flagged message and goes to **every enabled channel at once**:

| Channel  | What it does |
|----------|--------------|
| `console` | prints to stderr (always useful in a terminal) |
| `file`    | appends JSON to `~/.axiom/hello_operator_notifications.jsonl` |
| `stream`  | pushes to the live UI panel over Server-Sent Events |
| `webhook` | POSTs to a URL — Slack- and Discord-compatible payloads |

```json
"notifications": {
  "console": true,
  "file": true,
  "stream": true,
  "min_severity": "FLAG",
  "webhook": { "url": "https://hooks.slack.com/services/...", "kind": "slack" }
}
```

`min_severity` gates what fires: `INFO` < `FLAG` < `APPROVAL` < `BLOCK`. Set it
to `BLOCK` to be paged only on hard blocks, or `FLAG` to also see review items.

For Discord, set `"kind": "discord"` and use a Discord webhook URL — the payload
switches to a rich embed automatically.

---

## Configuration

`config.json` (created from `config.example.json` by setup):

| Field | Meaning |
|-------|---------|
| `operator_name` | shown in the UI header |
| `model` | Claude model id for answers |
| `system_prompt` | the operator's role and scope |
| `policy_agents` | which `check_constitutional` agents are active (`callguard`, `medical`, `truthwatcher`, `retailwatcher`, …) |
| `flag_confidence_below` | intent confidence under this → answer is flagged for review |
| `notifications` | channels + `min_severity` + optional webhook |

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat` | `{message, session_id}` → governed answer + signed manifest |
| `GET`  | `/notifications/stream` | SSE feed of governance notifications |
| `GET`  | `/audit?limit=N` | recent hash-chained ledger entries |
| `GET`  | `/config` | public config + governance/channel status |
| `GET`  | `/health` | liveness |
| `GET`  | `/` | the chat UI |

---

## Audit trail

Every interaction — answered, flagged, or blocked — is written to
`~/.axiom/hello_operator_ledger.jsonl` with:

- HMAC-SHA256 signature over the manifest
- `prev_hash` / `entry_hash` forming a tamper-evident chain
- intent class + confidence, policy verdict, message/answer hashes (not raw text)

Pull the recent trail any time:

```bash
curl localhost:8800/audit?limit=20 | python3 -m json.tool
```
