# Training manual — Axiom Data Gate

> **The governed-data layer.** Classify, redact, and gate every piece
> of data before an AI agent reads, remembers, or exports it.

## What it is

Data Gate answers one question at every data boundary: **is this agent
allowed to do this action with this class of data?**

Three things it does:

1. **Classify** — scan any text for regulated data (HIPAA, GDPR Art. 9,
   PCI DSS, credentials) and return a list of detected classes.
2. **Gate** — check an `(agent_id, action, data_class)` triple against
   the tenant's per-agent access rules and return `allow` or `deny`.
3. **Redact** — replace classified fields with typed placeholders
   (`[SSN REDACTED]`, `[PAN REDACTED]`, `[HEALTH CONDITION REDACTED]`)
   before the text reaches the model.

Everything is HMAC-signed. Every verdict is auditable.

---

## Who buys this

- **Chief Privacy Officers / DPOs** at banks, healthcare systems,
  government contractors — anyone with a regulator asking "what did
  your AI do with personal data?"
- **Engineering teams** adding AI features to products that already
  handle PII — they want a guardrail at the data layer, not just the
  prompt layer.

**Pain it solves:** LLMs accept whatever you pass them. Without Data
Gate, an AI agent can read PAN card numbers, GDPR special-category
health data, and internal HR records — and then store them in vector
memory where they accumulate indefinitely.

---

## Regulatory coverage

| Framework | What's covered | Code |
|---|---|---|
| HIPAA Safe Harbor | 18 PHI identifiers (45 CFR 164.514) | `SAFE_HARBOR_PATTERNS` in `axiom_redact.py` |
| GDPR Article 9 | 9 special categories: race, religion, trade union, health conditions, genetic, biometric, sexual orientation, criminal, political | `GDPR_PATTERNS` |
| PCI DSS | PAN, CVV, card expiry, track data, PIN, cardholder name | `PCI_PATTERNS` |
| Financial (general) | Visa/MC/Amex/Discover cards, IBAN, routing numbers | `FINANCIAL_PATTERNS` |
| Credentials | API keys, AWS keys, GitHub tokens, private keys, passwords | `CREDENTIAL_PATTERNS` |
| Healthcare (domain) | Diagnosis, medication, ICD codes | `DOMAIN_PATTERNS["healthcare"]` |
| Legal (domain) | Case numbers, bar numbers | `DOMAIN_PATTERNS["legal"]` |

**Total: 54 compiled regex patterns** across all frameworks.

---

## Core concepts

### RedactionEngine — three modes

```python
from axiom_redact import RedactionEngine
engine = RedactionEngine()

# REDACT: replace with placeholders
result = engine.process("SSN: 123-45-6789", mode="REDACT")
# → "SSN: [SSN REDACTED]"

# DETECT: find without modifying
result = engine.process(text, mode="DETECT")
# → {"detections": [{"type": "SSN", "category": "HIPAA-7", "count": 1}], ...}

# BLOCK: refuse if any regulated data found
result = engine.process(text, mode="BLOCK")
# → {"blocked": True, "detections": [...]}
```

Every result carries `audit_id` and `signature` (HMAC-SHA256). The
original text is **never stored**.

### Per-agent access rules

An `AgentAccessRule` says: agent X may perform these actions on these
data classes, and is blocked from these.

```python
from axiom_firewall.data_policy import is_allowed, save_agent_rule, AgentAccessRule

rule = AgentAccessRule(
    rule_id="...",
    agent_id="callguard",
    blocked_data_classes=["PAN", "CVV", "GDPR-9"],  # prefix match: blocks all GDPR-9-*
    allowed_actions=["read", "summarise"],
    blocked_actions=["store", "forward"],
)
save_agent_rule("acme_tenant", rule)

verdict = is_allowed("acme_tenant", "callguard", "store", "PAN")
# PolicyVerdict(allowed=False, reason="blocked_data_class")
```

**Safe defaults when no rule exists:**
- PCI, GDPR-9, biometric, criminal data → **DENY**
- Everything else → allow

### Memory gate

`BlockRegistry` in `axiom_mkb.py` accepts an optional `gate_fn`. When
supplied, every write (`register()`) and read (`find()`) is gated:

```python
from axiom_mkb import BlockRegistry
from axiom_firewall.data_policy import is_allowed

def gate(agent_id, action, data_class):
    return is_allowed("acme", agent_id, action, data_class).allowed

registry = BlockRegistry(hmac_key, gate_fn=gate)
# register(block, agent_id="callguard")  → PermissionError if denied
# find("name", agent_id="untrusted")     → None if denied (no enumeration)
```

---

## REST API

All endpoints require `Authorization: Bearer axfw_...` and
`X-Axiom-Tenant: <tenant_id>`.

| Endpoint | What it does |
|---|---|
| `PUT /data_policy/rule` | Create or replace an agent access rule |
| `GET /data_policy/rules` | List all rules for the tenant |
| `GET /data_policy/rule/{agent_id}` | Get rule for a specific agent |
| `DELETE /data_policy/rule/{agent_id}` | Delete a rule |
| `POST /data_policy/check?agent_id=X&action=Y&data_class=Z` | Is agent X allowed to do Y on Z? |
| `POST /guard/redact` | Redact/detect/block PII in free text |
| `DELETE /data_gate/erasure?subject_id=X` | Right-to-erasure with signed cert |

Full request/response schemas: [`docs/firewall/api-reference.md`](../firewall/api-reference.md).

---

## Right-to-erasure

GDPR Article 17 / CCPA § 1798.105 erasure requests are handled by
`erase_subject_data(tenant_id, subject_id)`:

1. Substring-scans the tenant's `decisions` table for the subject identifier.
2. Deletes matching rows.
3. Returns a **signed deletion certificate**:
   ```json
   {
     "cert_id": "uuid",
     "subject_id_hash": "sha256(subject_id)",
     "records_erased": 3,
     "erased_at": "2026-06-04T...",
     "scope": "decision_log_only",
     "limitation": "Latent encodings in model weights are outside scope...",
     "signature": "hmac-sha256:..."
   }
   ```

**Important:** the `scope` and `limitation` fields are required
disclosures. The certificate proves what was erased; it also honestly
states that model-weight-level erasure requires retraining. Present
this to the regulator verbatim — do not omit the limitation.

For vector-store erasure: `PgVectorConnector.delete_by_subject(subject_id)`.

---

## Integration patterns

### Pattern 1 — Pre-flight check (recommended for new builds)

```python
import httpx

def call_llm_with_gate(agent_id: str, prompt: str, tenant_id: str):
    # 1. Classify input
    r = httpx.post("/guard/redact",
                   json={"text": prompt, "mode": "DETECT"},
                   headers={"Authorization": f"Bearer {API_KEY}",
                            "X-Axiom-Tenant": tenant_id})
    detections = r.json()["detections"]

    # 2. Check each detected class against agent's policy
    for d in detections:
        check = httpx.post(
            f"/data_policy/check",
            params={"agent_id": agent_id, "action": "read",
                    "data_class": d["type"]},
            headers={"Authorization": f"Bearer {API_KEY}",
                     "X-Axiom-Tenant": tenant_id}
        ).json()
        if not check["allowed"]:
            return {"blocked": True, "reason": check["reason"],
                    "data_class": d["type"]}

    # 3. Optionally redact before sending to LLM
    redacted_prompt = httpx.post("/guard/redact",
                                 json={"text": prompt, "mode": "REDACT"},
                                 headers=...).json()["redacted"]
    return call_llm(redacted_prompt)
```

### Pattern 2 — Memory proxy (for agents with vector stores)

Wrap `BlockRegistry` with a `gate_fn` that calls `is_allowed()`:

```python
registry = BlockRegistry(hmac_key, gate_fn=lambda aid, act, dc:
    is_allowed(tenant_id, aid, act, dc).allowed)
```

The agent code is unchanged; Data Gate is invisible at the call site.

---

## Limitations to communicate clearly

1. **Pattern-based, not semantic.** The redaction engine uses regexes.
   A document that says "the patient's condition is serious" won't
   trigger `HEALTH_CONDITION` unless it matches a specific pattern
   (like "HIV" or "diagnosed with cancer"). Context-free paraphrases
   pass through undetected.

2. **Model weights are out of scope.** Right-to-erasure clears the
   structured decision log and vector store. It does **not** remove
   data from model weights — that requires retraining. The deletion
   certificate says this explicitly.

3. **Per-agent rules, not row-level security.** Rules are
   `(agent_id, action, data_class)` — they don't scope down to "agent
   X can read SSN for user Y but not user Z." Row-level filtering is
   a v2 concern.

---

## Where the code lives

| What | File |
|---|---|
| Pattern library (all 54 patterns) | `axiom_redact.py` |
| Per-agent policy engine | `axiom_firewall/data_policy.py` |
| Memory gate | `axiom_mkb.py` `BlockRegistry(gate_fn=...)` |
| Right-to-erasure | `axiom_firewall/db.erase_subject_data()` |
| pgvector connector | `axiom_firewall/pgvector_connector.py` |
| REST endpoints | `axiom_guard_api.py` `/data_policy/*` and `/data_gate/*` |

---

## See also

- [`docs/firewall/api-reference.md`](../firewall/api-reference.md) — full request/response schemas
- [`docs/training/flight_recorder.md`](flight_recorder.md) — the complementary audit-log product
- [`AXIOM_DATA_GOVERNANCE.md`](../../AXIOM_DATA_GOVERNANCE.md) — EU AI Act Art. 10 compliance context
