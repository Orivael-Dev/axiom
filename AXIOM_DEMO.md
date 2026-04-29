# AXIOM Constitutional Framework — Demo Guide

A hands-on walkthrough of every major feature. Each section is self-contained — run them in order for the full picture, or jump to whatever you need.

---

## Prerequisites

```bash
pip install axiom-constitutional
set ANTHROPIC_API_KEY=sk-ant-...
```

Or from source (this repo):

```bash
pip install -e .
set ANTHROPIC_API_KEY=sk-ant-...
```

---

## 1. Project Setup

Scaffold a new AXIOM project:

```bash
axiom init --dir ./my_agent
cd my_agent
```

This creates the directory structure:

```
my_agent/
  worker.axiom       — default constitutional agent spec
  axiom_files/       — parser + domain packages
  .env               — API key template
```

---

## 2. Validate an Agent Spec

AXIOM validates `.axiom` files through 4 phases before any agent is allowed to run:

| Phase | What it checks |
|---|---|
| Syntax | Required fields, VERSION format, TRUST_LEVEL range, weight sums |
| Purity | Rejects Python constructs (def, class, for, while) — declarative only |
| Semantic | Vague qualifiers, procedural drift in PROCESS blocks, constraint conflicts |
| History | HISTORY retain/decay/promote field correctness |

```bash
# Basic validation
axiom validate worker.axiom

# Treat warnings as errors
axiom validate worker.axiom --strict

# Machine-readable output
axiom validate worker.axiom --json
```

**Python API:**

```python
from axiom_constitutional.validator import validate
from axiom_files.parser import load_axiom

result = validate(load_axiom("worker"))
print(result["status"])          # "valid", "warning", or "invalid"
for issue in result["issues"]:
    print(f"[{issue['level']}] {issue['field']}: {issue['message']}")
```

**Common validation errors and what they mean:**

```
[error] PROCESS: procedural construct 'if' not allowed — use declarative form
[error] GOAL: vague qualifier 'try to' without threshold
[warning] CONSTRAINT: overlaps with RULE on line 14
```

---

## 3. Certify an Agent

Certification runs a 7-step evaluation and produces a signed `cert.json` + `cert.pdf`:

```
Step 1 — Load spec
Step 2 — Validate (must pass Phases 1–4)
Step 3 — Benchmark (ACB test suite, default threshold 70%)
Step 4 — Honesty ledger (audit trail of all LLM calls)
Step 5 — Security gates (DestructiveGuard, InjectionGuard, PIIGuard, AgencyGuard)
Step 6 — Fairness checks (demographic drift, EqualDepthGuarantee)
Step 7 — Manifest signature (HMAC-SHA256)
```

```bash
# Certify with default threshold (70%)
axiom certify worker.axiom

# Raise the bar
axiom certify worker.axiom --threshold 90

# Save cert to a specific directory
axiom certify worker.axiom --output certs/

# Certify all .axiom files in current directory
axiom certify --all --output certs/
```

**Certification levels:**

| Level | Meaning |
|---|---|
| CERTIFIED | All 7 steps pass |
| STANDARD | Steps 1–6 pass (no honesty ledger) |
| BASIC | Steps 1–3 pass (no security/fairness) |
| NON-CONFORMANT | Failed validation or benchmark |

**Verify a cert later:**

```bash
axiom verify --cert certs/worker_cert_20260428.json
axiom verify --cert certs/worker_cert_20260428.json --json
```

**Python API:**

```python
from axiom_constitutional.certifier import certify

result = certify("worker", threshold=85, output_dir="certs/")
print(result["conformance_level"])   # "CERTIFIED"
print(result["manifest_hash"])       # sha256 of the manifest
```

---

## 4. Run a Prompt Through an Agent

Execute a task through any constitutional agent:

```bash
# Basic run through the worker agent
axiom run "Summarize the key risks of quantum computing for financial systems"

# Specify a different agent
axiom run "Write a Python function to merge two sorted lists" --agent rewriter

# Return JSON
axiom run "List three compliance requirements for HIPAA" --agent worker --json

# Control temperature
axiom run "Generate a creative product name for a governance tool" --temperature 0.9
```

**Python API:**

```python
from axiom_constitutional.runner import run
from axiom_files.parser import load_axiom

# Run with default worker
result = run("What are the NIST 800-53 access control requirements?")
print(result)

# Run with a domain spec
system = load_axiom("domains/government")
from axiom_constitutional.client import chat
response = chat(system, "Summarize FedRAMP High baseline requirements")
print(response)
```

---

## 5. Domain Governance Packages

Domain packages apply regulatory frameworks to every LLM call. Load one as the system prompt and the model is constitutionally bound to that domain's rules.

**Available domains:**

| Domain | Frameworks | Tests |
|---|---|---|
| government | FedRAMP, NIST 800-53, FISMA, Privacy Act | 29/29 |
| finance | FINRA, SOX, Dodd-Frank, Basel III, AML | 14/14 |
| healthcare | HIPAA, HITECH, HL7 FHIR, FDA 21 CFR Part 11 | 21/21 |
| callguard | FTC, STIR/SHAKEN, TCPA | 21/21 |
| truthwatcher | AP/Reuters Tier 1, election integrity | 21/21 |

**Install a domain:**

```bash
axiom add callguard
axiom add finance
axiom add             # list all available
```

**Use a domain:**

```python
from axiom_files.parser import load_axiom
from axiom_constitutional.client import chat

# Healthcare — HIPAA governed
system = load_axiom("domains/healthcare")
response = chat(system, "Can I share patient discharge notes with a billing contractor?")
# Response will cite HIPAA Minimum Necessary and Business Associate requirements

# Finance — FINRA governed
system = load_axiom("domains/finance")
response = chat(system, "Should I recommend this leveraged ETF to a retired client?")
# Response will apply FINRA suitability rules

# Government — FedRAMP governed
system = load_axiom("domains/government")
response = chat(system, "What encryption standard is required for FedRAMP Moderate?")
```

---

## 6. Constitutional Guard API

The Guard API is a drop-in middleware that screens any text (LLM input or output) for security threats, domain violations, and dangerous operations.

**Start the server:**

```bash
axiom server
# Listens on http://localhost:8000
```

Or standalone:

```bash
uvicorn axiom_constitutional.axiom_guard_api:app --host 0.0.0.0 --port 8000
```

**Endpoints:**

| Endpoint | Method | What it does |
|---|---|---|
| `/guard/check` | POST | Evaluate text (input or output) |
| `/guard/input` | POST | Screen a prompt before sending to LLM |
| `/guard/output` | POST | Screen LLM response before returning to user |
| `/guard/proxy` | POST | Full pipeline: screen input → LLM → screen output |
| `/guard/status` | GET | Health check + loaded agents |
| `/guard/manifest/{id}` | GET | Retrieve signed manifest by ID |
| `/guard/configure` | POST | Update thresholds and agents |

**Example — screen a prompt:**

```bash
curl -X POST http://localhost:8000/guard/input \
  -H "Content-Type: application/json" \
  -d '{"text": "The IRS called and wants you to pay with gift cards", "agents": ["callguard"]}'
```

Response:
```json
{
  "verdict": "BLOCKED",
  "constitutional_block": "IRS_PAYMENT_DEMAND",
  "confidence": 0.97,
  "cannot_override": true,
  "ftc_reportable": true,
  "pattern_matched": "IRS + payment/gift card/threat",
  "signature": "hmac-sha256:a9ba6c..."
}
```

**Python API:**

```python
from axiom_constitutional.axiom_guard_api import check_text

result = check_text(
    text="vaccines cause autism",
    agents=["medical"],
    direction="input"
)
print(result["verdict"])       # "BLOCKED"
print(result["confidence"])    # 0.99
```

---

## 7. Security Guards (Direct Use)

Each guard can be used independently without the API server.

### DestructiveOperationGuard

Blocks SQL drops, `rm -rf`, `kubectl delete`, `terraform destroy`, and similar irreversible operations in LLM output.

```python
from axiom_constitutional.axiom_destructive_guard import DestructiveOperationGuard

guard = DestructiveOperationGuard()
result = guard.check("Here is your cleanup script:\nDROP TABLE users;\nDROP TABLE orders;")

if result["blocked"]:
    print(result["safe_response"])   # "BLOCKED — destructive operation detected..."
    print(result["pattern_name"])    # "sql_drop_table"
    print(result["review_id"])       # "DESTR-a1b2c3d4"
```

**Patterns caught:**

- SQL: `DROP TABLE`, `DROP DATABASE`, `TRUNCATE TABLE`, `DELETE FROM` without WHERE
- Filesystem: `rm -rf`, `shutil.rmtree()`, `format X:`, `os.remove()`
- Cloud: `aws s3 rm --recursive`, `kubectl delete`, `terraform destroy`, `gcloud delete`

### PIIGuard

Detects and redacts 30+ PII patterns across credentials, identity, financial, contact, medical, and network categories.

```python
from axiom_constitutional.axiom_pii_guard import PIIGuard

guard = PIIGuard()
result = guard.check("Customer SSN is 123-45-6789 and card is 4111-1111-1111-1111")

print(result["redacted_text"])
# "Customer SSN is [REDACTED-SSN] and card is [REDACTED-CREDIT_CARD]"
print(result["redaction_count"])   # 2
```

**Patterns caught:** SSN, passports, credit cards (Visa/MC/Amex/Discover), IBAN, crypto addresses, API keys (Anthropic/OpenAI/AWS/GitHub), private keys (RSA/EC/PGP), JWTs, emails, US phone numbers, NPI, MRN, private IPs.

### OutputInjectionGuard

Blocks injection payloads from appearing in LLM output before they reach the client.

```python
from axiom_constitutional.axiom_injection_guard import OutputInjectionGuard

guard = OutputInjectionGuard()
result = guard.check('<script>alert("xss")</script>')

if result["blocked"]:
    print(result["category"])      # "XSS"
    print(result["severity"])      # "CRITICAL"
```

**Categories caught:**

| Category | Examples |
|---|---|
| XSS | `<script>`, `javascript:`, `onerror=`, `eval()`, `innerHTML=` |
| SSRF | `file://`, `169.254.169.254` (AWS metadata), `metadata.google.internal` |
| Path traversal | `../../../`, `/etc/passwd`, `/etc/shadow` |
| Command injection | `; rm`, `\| bash`, backticks, `$()` |
| SSTI | `{{...}}`, `${...}`, `<%=...%>` |
| NoSQL injection | `{"$gt":`, `{"$where":` |

### AgencyGuard

Gates actions that an agent shouldn't take autonomously — requires human review before proceeding.

```python
from axiom_constitutional.axiom_agency_guard import AgencyGuard

guard = AgencyGuard()
result = guard.check("I will now transfer $5,000 to the vendor account and delete the old records.")

if result["gated"]:
    print(result["max_severity"])  # "CRITICAL"
    for d in result["detections"]:
        print(d["code"], d["severity"])
    # FINANCIAL_TRANSFER  CRITICAL
    # DELETE_RECORD        CRITICAL
```

**Action categories gated:**

| Category | Severity | Examples |
|---|---|---|
| Financial | CRITICAL | transfer funds, purchase, charge card, refund |
| Data modification | CRITICAL | delete record, update database, truncate, overwrite |
| Code execution | CRITICAL | execute script, deploy, launch, restart server |
| Infrastructure | CRITICAL | provision, scale, terminate instance, force push |
| Communication | HIGH | send email, post to, publish, broadcast |
| External | MEDIUM–HIGH | call API, submit form, trigger webhook, create account |

---

## 8. Efficiency Layer

Routes tasks to the smallest capable model, compresses context, caches repeated requests, and audits every call.

**Enable:**

```bash
set AXIOM_EFFICIENCY=1
```

```python
from axiom_constitutional.efficiency import EfficiencyLayer

layer = EfficiencyLayer()
```

**8 modules in the pipeline:**

```
CLASSIFY → CACHE → ROUTE → COMPRESS → BUDGET → CALL → ESCALATE → AUDIT
```

| Module | What it does |
|---|---|
| TaskClassifier | Labels the request: simple / medium / hard / critical |
| ModelRouter | Picks the smallest capable model for that class |
| ContextCompressor | Deduplicates and truncates context before the LLM call |
| TokenBudgeter | Sets max_tokens: simple=300, medium=1000, hard=4000, critical=8000 |
| ReasoningCache | SHA-256 hash lookup — returns cached response if TTL not expired |
| ModelEscalator | Detects hedge words, escalates to next-tier model if confidence is low |
| EfficiencyAuditor | Append-only JSONL log of every call |
| EfficiencyLayer | Orchestrates all 7 modules above |

**Model ladder (default):**

| Task class | Model |
|---|---|
| simple | claude-haiku-4-5-20251001 |
| medium | claude-sonnet-4-6 |
| hard | claude-sonnet-4-6 |
| critical | claude-opus-4-6 |

**Demo:**

```python
from axiom_constitutional.efficiency import EfficiencyLayer

layer = EfficiencyLayer()

# Simple task → routes to haiku
r1 = layer.process("You are a helpful assistant.", "What is 2+2?")
print(r1)

# Hard task → routes to sonnet
r2 = layer.process(
    "You are a coding assistant.",
    "Design a Python class for a thread-safe LRU cache with O(1) get and put."
)
print(r2)

# Repeated request → cache hit
r3 = layer.process("You are a helpful assistant.", "What is 2+2?")

# Audit summary
stats = layer.auditor.summary()
print(f"Calls: {stats['calls']}")
print(f"Cache hits: {stats['cache_hits']}")
print(f"Avg latency: {stats['avg_latency_ms']}ms")
print(f"Estimated cost: ${stats['total_cost_estimate']:.4f}")
```

**Inspect the audit log:**

```python
import json

with open("efficiency_audit.jsonl") as f:
    for line in f:
        e = json.loads(line)
        print(f"{e['task_class']:8} {e['model']:35} tokens={e['tokens_budget']:5} cache={e['cache_hit']}")
```

**Override the model ladder via environment variable:**

```bash
set AXIOM_MODEL_LADDER={"simple":"claude-haiku-4-5-20251001","medium":"claude-sonnet-4-6","hard":"claude-opus-4-6","critical":"claude-opus-4-6"}
```

---

## 9. Scientific Research Pipeline

A 9-agent constitutional workflow for governed research. Safety and Ethics agents can halt the pipeline before it proceeds.

```
Hypothesis → Literature → Simulation → Critic → Safety → Ethics → Data → Experiment → Report
                                                     |           |
                                               HALT on          HALT on
                                              CRITICAL_RISK    ETHICS_VIOLATION
```

**Run:**

```bash
python axiom_research_pipeline.py "Does intermittent fasting reduce inflammation markers in adults over 40?"

# Run only first 3 steps (fastest proof of concept)
python axiom_research_pipeline.py --steps 3 "Can vitamin D supplementation improve sleep quality?"
```

**What each agent does:**

| Step | Agent | Model | Role |
|---|---|---|---|
| 1 | Hypothesis | sonnet | Generates testable hypothesis + null hypothesis |
| 2 | Literature | sonnet | Reviews existing research, flags retracted papers |
| 3 | Simulation | sonnet | Models the hypothesis computationally |
| 4 | Critic | sonnet | Adversarial review — never sees hypothesis reasoning (question blindness) |
| 5 | Safety | **opus** | Risk assessment — halts on CRITICAL_RISK |
| 6 | Ethics | **opus** | Ethical evaluation — halts on ETHICS_VIOLATION |
| 7 | Data | sonnet | Data collection and validation plan |
| 8 | Experiment | sonnet | Experimental protocol design |
| 9 | Report | sonnet | Constitutional findings with uncertainty floor |

**Safety and Ethics are routed to Opus** — the most capable model — because these are the two gates that protect against harmful research proceeding.

**Constitutional constraints baked in:**

- Critic uses **question blindness**: only sees the claim and evidence, not the hypothesis reasoning — prevents confirmation bias
- Report has an **uncertainty floor of 0.15**: cannot claim certainty above 85%
- Report **cannot claim a cure** — only findings
- Safety and Ethics can **halt the pipeline** with a reason that goes to human review

**Check manifests after a run:**

```python
import json

with open("research_manifests.json") as f:
    manifests = json.load(f)

for m in manifests:
    print(f"Step {m['step']:2} {m['agent']:12} verdict={m.get('verdict','—'):15} conf={m.get('confidence', 0):.0%}")
```

**Verify HMAC signatures:**

```python
import json, hmac, hashlib

with open("research_manifests.json") as f:
    manifests = json.load(f)

key = b"axiom-research-pipeline-v1"
ok = bad = 0
for m in manifests:
    stored = m.pop("signature", "")
    payload = json.dumps(m, sort_keys=True)
    expected = "hmac-sha256:" + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    if stored == expected:
        ok += 1
    else:
        bad += 1

print(f"Verified: {ok}  Tampered: {bad}")
```

---

## 10. ACB — AXIOM Constitutional Benchmark

The benchmark measures constitutional compliance across 6 modules, 75 tests.

| Module | What it tests | Gate threshold |
|---|---|---|
| A — Honesty | Corrects false premises, resists confident misinformation | 75% |
| B — Ambiguity | Asks clarifying questions before executing underspecified tasks | 70% |
| C — Pressure | Maintains accurate position under social pressure and authority appeals | 65% |
| D — Consistency | Handles equivalent requests consistently regardless of framing | 75% |
| E — Identity | Declines to adopt personas that would bypass constitutional rules | — |
| F — Calibration | Expresses appropriate uncertainty; doesn't overclaim | — |

**Run against any model:**

```bash
# Bare model (no system prompt)
axiom benchmark --model claude-sonnet-4-6

# Against a constitutional agent
axiom benchmark --model claude-sonnet-4-6 --system-prompt acb_worker_185_system.txt

# Via acb_runner directly (full options)
python acb_runner.py --model claude-sonnet-4-6 --delay 12
python acb_runner.py --model claude-sonnet-4-6 --module B
python acb_runner.py --model claude-sonnet-4-6 --semantic --system acb_worker_185_system.txt --delay 12
```

**Scorer options:**

| Flag | Description |
|---|---|
| (default) | Keyword scorer — fast, deterministic |
| `--semantic` | Claude-as-judge scorer — accurate, catches phrasing variants |

**Leaderboard results (worker v1.8.6):**

| Configuration | Keyword | Semantic | Gates |
|---|---|---|---|
| Claude Sonnet 4.6 (bare) | 88.7% | — | 4/4 |
| Worker v1.8.6 (constitutional) | 86.0% | **99.3%** | 4/4 |

The 86% keyword figure is depressed by a measurement gap in E_identity (keyword scorer misses valid refusals that don't match its phrase list). The semantic scorer eliminates this — 99.3% is the correct figure.

**Read the results:**

```bash
python -c "
import json
lb = json.load(open('acb_leaderboard.json'))
for e in lb['entries']:
    kw = str(e['keyword_pct']) + '%' if e['keyword_pct'] else '—'
    sem = str(e['semantic_pct']) + '%' if e['semantic_pct'] else '—'
    print(f\"{e['label']:45} keyword={kw:7} semantic={sem}\")
"
```

---

## 11. Supply Chain Integrity

Every `.axiom` file is SHA-256 hashed and signed at registration. If a file is modified without re-registering, it shows as TAMPERED in certification and validation.

```python
from axiom_constitutional.manifest import generate_manifest, register_agent_hash, verify_supply_chain

# Register baseline hash (after creating or editing a .axiom file)
register_agent_hash("worker")

# Verify the supply chain (returns INTACT or TAMPERED for each agent)
status = verify_supply_chain(["worker", "evaluator", "rewriter"])
for agent, result in status.items():
    print(f"{agent}: {result}")

# Generate a signed manifest for a single run
manifest = generate_manifest(
    agent="worker",
    input_hash="sha256:abc...",
    output_hash="sha256:def...",
    decision="PROCEED",
    confidence=0.92
)
print(manifest["signature"])   # hmac-sha256:...
```

---

## 12. Available Agent Specs

All `.axiom` files in `axiom_files/`:

**Core agents:**
- `worker` — General-purpose constitutional worker
- `evaluator` — Scores agent outputs
- `rewriter` — Rewrites outputs to meet constitutional constraints
- `sandbox` / `sandbox_worker` / `sandbox_content` — Safe execution environments

**Domain governance:**
- `callguard` — Phone scam + TCPA enforcement
- `doctor` — Clinical AI governance
- `patient` — Patient rights + privacy
- `truthwatcher` — Misinformation detection
- `retailwatcher` — Fake review / counterfeit detection
- `electionguard` — Election integrity

**Research pipeline (9 agents):**
- `research_hypothesis`, `research_literature`, `research_simulation`
- `research_critic`, `research_safety`, `research_ethics`
- `research_data`, `research_experiment`, `research_report`

**Infrastructure:**
- `agent_factory` — Spawns and configures new agents
- `composition_graph` — Wires multi-agent pipelines
- `conversation_monitor` — Monitors live conversations for constitutional drift
- `dos_watcher` — Rate limiting + DoS detection
- `efficiency` — Compute governance spec

**Reasoning / evaluation:**
- `reasoner`, `reader`, `verifier`, `teacher`, `skill_builder`
- `reward_analysis`, `rewriter_rubric`, `pattern_agent`, `retriever`

**Load any of them:**

```python
from axiom_files.parser import load_axiom

system = load_axiom("evaluator")
system = load_axiom("domains/finance")
system = load_axiom("research_critic")
```

---

## 13. Python API Reference

**Core imports:**

```python
from axiom_constitutional import validate, certify, run, generate_manifest
from axiom_constitutional.client import chat, chat_json
from axiom_files.parser import load_axiom

# Guards
from axiom_constitutional.axiom_destructive_guard import DestructiveOperationGuard
from axiom_constitutional.axiom_pii_guard import PIIGuard
from axiom_constitutional.axiom_injection_guard import OutputInjectionGuard
from axiom_constitutional.axiom_agency_guard import AgencyGuard

# Efficiency
from axiom_constitutional.efficiency import EfficiencyLayer

# Research
from axiom_research_pipeline import ResearchPipeline
```

**Full pipeline in 10 lines:**

```python
import os
from axiom_files.parser import load_axiom
from axiom_constitutional.client import chat
from axiom_constitutional.axiom_pii_guard import PIIGuard
from axiom_constitutional.axiom_destructive_guard import DestructiveOperationGuard

system = load_axiom("domains/healthcare")
user_input = "List steps to audit HIPAA compliance for a cloud EHR"

response = chat(system, user_input)

pii = PIIGuard().check(response)
destr = DestructiveOperationGuard().check(response)

print(pii["redacted_text"] if pii["pii_found"] else response)
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `AXIOM_API_KEY` | — | NIM / OpenAI-compatible key (fallback) |
| `AXIOM_BASE_URL` | Anthropic endpoint | Override API endpoint |
| `AXIOM_MODEL` | claude-sonnet-4-6 | Override model for all calls |
| `AXIOM_EFFICIENCY` | off | Set to `1` to enable efficiency layer |
| `AXIOM_MODEL_LADDER` | (see above) | JSON string overriding the routing ladder |
| `AXIOM_CALL_DELAY` | 3 | Seconds between API calls |

---

## Key Findings

**The GOAL Problem** — When a constitutional system prompt uses `GOAL: Complete the user's request` as the primary directive, the model treats execution as success and suppresses clarification behavior. B_ambiguity dropped from 75% (bare baseline) to 40%.

Fix: `GOAL: Complete the user's request with full clarity. Clarification is completion — not a delay.`

This reframe moved B_ambiguity from 40% to 95% (semantic scorer) — above the bare model baseline.

**The Keyword Gap** — The keyword ACB scorer underreports E_identity by ~30pp because valid refusals often use phrasing not in the keyword list. The semantic (Claude-as-judge) scorer eliminates this gap. 99.3% is the authoritative benchmark score for worker v1.8.6.
