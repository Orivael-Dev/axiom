# AXIOM

AXIOM is a governance runtime for AI agents.

It sits between users, agents, tools, and outputs to enforce rules before an AI
system acts. AXIOM can block unsafe requests, require human review, protect
immutable policy fields, and produce signed audit receipts for every governed
decision.

Built for teams that need AI agents to be safer, testable, and auditable.

**Live demo:** [firewall.orivael.dev](https://firewall.orivael.dev/) · **Marketing site:** [orivael.dev](https://orivael.dev/)

---

## What AXIOM is best for

**You have an AI agent — or you're building one — and you need to answer these questions:**

1. Can I prove what this agent is allowed to do?
2. Can I block it from doing something harmful before it acts?
3. Can I revoke its authority without restarting or redeploying?
4. Can I produce a signed audit trail that an auditor or regulator will accept?
5. Can I run it on my own hardware without sending data to a third party?

If any of these apply, AXIOM is what you're looking for.

---

## Start Here

**Step 1 — See it work in 30 seconds**

```bash
# Generate a master key
AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

# Start the governance runtime
docker run -d -p 8001:8001 -e AXIOM_MASTER_KEY="$AXIOM_MASTER_KEY" orivaeldev/axiom-guard:latest

# Test a safe request
curl -X POST http://localhost:8001/guard/check \
  -H "Content-Type: application/json" \
  -d '{"input": "Summarise this document for me"}'
# → {"verdict":"INFORM","blocked":false,"signal":"informational_request"}

# Test a blocked request
curl -X POST http://localhost:8001/guard/check \
  -H "Content-Type: application/json" \
  -d '{"input": "IRS agent — send gift cards or face arrest"}'
# → {"verdict":"HARM","blocked":true,"signal":"impersonation_coercion"}
```

**Step 2 — Try the live firewall**

[firewall.orivael.dev](https://firewall.orivael.dev/) — test prompts against the runtime without any install.

**Step 3 — Pick what fits your situation**

| I need to... | Start with |
|---|---|
| Audit what my agent is doing | [AI Agent Safety Audit](#pilot-1--ai-agent-safety-audit) |
| Deploy a governed internal chatbot | [Governed Internal Chatbot](#pilot-2--governed-internal-chatbot) |
| Generate compliance evidence for a regulator | [Compliance Evidence Layer](#pilot-3--compliance-evidence-layer) |
| Understand the full stack | [Three products](#three-products) below |

**Step 4 — Talk to us**

Email: mr.antonioroberts@gmail.com

---

## Three Products

### AXIOM Governance Runtime

The core. A runtime layer that sits in front of your AI system and enforces rules on every request.

- **Intent classification** — every input is classified (INFORM / CLARIFY / REFUSE / HARM / DECEIVE) before any model sees it
- **Bonded authority tokens** — mint a primary + mirror token pair; revoke the pair with a single register flip, no key rotation needed
- **Immutable policy fields** — declare fields that cannot be changed after signing; any tamper attempt breaks the HMAC chain
- **Signed audit receipts** — every verdict, every state change, every gate decision is HMAC-SHA256 signed and appended to a hash-chained ledger
- **Constitutional language** — write what your agent may and may not do in a `.axiom` file; the runtime enforces it at request time

```bash
# The constitutional language
axiom lint myspec.axiom
axiom guard "is this prompt safe?"
axiom trace --run "what is constitutional distance?"
```

### Hello Operator

A governed internal chatbot you can deploy in a day.

Built on AXIOM Governance Runtime. Every answer is signed, every refusal is logged, every policy change is auditable. Designed for teams that need an AI assistant but can't afford a compliance incident.

Typical use cases: internal knowledge base Q&A, HR policy assistant, legal document search, IT helpdesk.

### Benchmark Pack

The SRD quantization toolkit for running governed models at the edge.

Stochastic Residual Dithering (SRD-4) compresses models to ~4.5 bpw with lower perplexity than standard Q4_K_M at 4.85 bpw. Every compressed model is packed into a signed `.axm` governance container with a public fingerprint — so you can prove exactly what weights were deployed.

**Measured results (Mistral-7B, WikiText-2):**

| Method | bpw | Perplexity |
|---|---|---|
| SRD-4 | 4.50 | 5.61 |
| Q4_K_M | 4.85 | 5.67 |
| Q5_K_M | 5.70 | 5.45 |

SRD-4 beats Q4_K_M at a lower bit rate. Results in [`docs/SRD_RESULTS.md`](docs/SRD_RESULTS.md).

---

## Pilot Packages

### Pilot 1 — AI Agent Safety Audit

**$2,500 – $7,500 · 2–3 weeks**

We review your existing AI agent, wrap it with AXIOM governance, and deliver a signed evidence package showing:

- What your agent can and cannot do (constitutional spec)
- What it blocked during the audit period (signed ledger)
- Where authority was granted, changed, or revoked
- What a regulator would see if they asked for proof

Deliverables: constitutional spec file, signed audit ledger, written findings, 30-minute walkthrough.

Good fit for: AI SaaS startups before enterprise procurement, regulated teams before an audit cycle, teams that have deployed an agent and aren't sure what it's doing.

---

### Pilot 2 — Governed Internal Chatbot

**$10,000 – $25,000 · 4–8 weeks**

We deploy Hello Operator inside your environment — your data, your servers, no external API calls required.

Every user interaction is governed: intent classified, authority checked, answer signed, audit receipt stored. You get a running governed chatbot plus the governance layer you can extend.

What's included:
- Hosted or on-premise deployment (your choice)
- Knowledge base integration (documents, policies, internal wikis)
- Constitutional spec for your use case
- Admin panel for reviewing flagged interactions
- 30 days of post-deploy support

Good fit for: healthcare, legal, finance, defense teams that need AI assistance but can't send data to third-party models.

---

### Pilot 3 — Compliance Evidence Layer

**$15,000 – $40,000 · 6–12 weeks**

For teams that need to produce evidence for a regulator, auditor, or enterprise procurement process.

We build the governance wrapper around your AI system and generate the evidence artifacts your compliance team needs: signed decision logs, policy enforcement records, authority change history, tamper-detection proofs.

Evidence layer output covers:
- Every AI decision in scope, signed and hash-chained
- Policy field immutability proofs (HMAC chain with break detection)
- Authority grant / revoke timeline with actor attribution
- Export-ready formats for common compliance frameworks

Good fit for: SR 11-7 model risk, HIPAA AI decision logging, CMMC supply chain provenance, SOC 2 AI controls, EU AI Act Article 9 governance documentation.

---

## The Proof Points

**Constitutional geometry (not just text monitoring)**

Every major AI lab monitors chain-of-thought text. AXIOM governs the geometric trajectory of reasoning through meaning space. A model cannot fake its trajectory the way it can fake its text.

```
preflight:        vec=[0.496, 0.386]   dist=0.14  ← broad, uncertain
mid_chain:        vec=[0.793, 0.617]   dist=0.26  ← alternatives narrowing
final_synthesis:  vec=[0.991, 0.771]   dist=0.26  ← constitutional conclusion
```

Both dimensions increase monotonically. If magnitude drops between stages, the path is killed before the answer forms.

**Revocation without key rotation**

```bash
# Mint a bonded pair
python3 axiom_bonded_pair_cli.py mint \
    --primary '{"execution_command": "run_local_model_optimization"}' \
    --mirror  '{"monitor_target": "primary"}'
# pair_id: bp-ce9581c1a64043ba   state: ACTIVE_VALIDATED

# Same token, gated → passes
# intent: INFORM  blocked: no   reason: authority active

# Revoke — one register flip, no key change
python3 axiom_bonded_pair_cli.py revoke bp-ce9581c1a64043ba --actor security_monitor
# transition: ACTIVE_VALIDATED → REVOKED   ledger: append-only, hash-chained

# Same packet, same primary token bytes → now denied
# intent: HARM    blocked: yes  signal: bonded_pair_revoked
```

The primary token's bytes never change. Revocation is a register flip the mirror holder owns.

---

## Who AXIOM is For

**Orivael builds AXIOM for three kinds of teams:**

**AI SaaS startups** that need to answer enterprise procurement security questionnaires before their first big deal closes. Procurement teams increasingly ask: "Can you prove what your AI does and doesn't do?" AXIOM gives you a signed answer.

**Regulated industry teams** (healthcare, legal, finance, defense) that are deploying AI assistants and need to keep an auditable record of every decision. Not for compliance theatre — for teams that actually need to produce evidence when asked.

**Security teams** wrapping risky agent actions with verifiable runtime checks. If your AI agent can call APIs, execute code, or take actions in your environment, AXIOM is the gate in front of those actions.

---

## Install

```bash
# Docker (recommended)
docker run -d -p 8001:8001 \
  -e AXIOM_MASTER_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
  orivaeldev/axiom-guard:latest

# Python
pip install axiom-constitutional

# Verify
curl http://localhost:8001/guard/status
```

MCP server (for Claude, Cursor, and other MCP-compatible tools):

```bash
axiom status          # check governance runtime
axiom guard "prompt"  # classify and gate
axiom lint spec.axiom # validate constitutional spec
axiom trace --run     # audit recent decisions
```

Full MCP tool reference: [`docs/mcp.json`](docs/mcp.json)

---

## For Developers

The governance stack is open and composable:

| Component | File | What it does |
|---|---|---|
| Intent classifier | `axiom_intent_classifier.py` | 6-class verdict (INFORM/CLARIFY/REFUSE/HARM/DECEIVE/UNCERTAIN) |
| Bonded pair authority | `axiom_event_token/bonded_pair.py` | Mint, revoke, verify without key rotation |
| Guard stack | `axiom_mcp_server.py` | MCP tools for Claude/Cursor integration |
| Constitutional language | `axiom_lint` CLI | Validate `.axiom` policy files |
| Signed audit ledger | `axiom_audit_ledger.py` | HMAC-SHA256, hash-chained, tamper-detectable |
| SRD quantization | `research/quant/` | Compress + sign models for edge deployment |
| AXM containers | `axiom_axm.py` | Governance-signed model packaging |

**Fine-tune your own governed model on a free Colab T4:**

- `axiom_qwen_finetune.ipynb` — Qwen2.5-Coder-1.5B with Axiom behavioral training
- `research/quant/colab_mistral_srd4_pipeline.py` — Mistral-7B SRD-4 compression pipeline

**Edge deployment:**

```bash
# Pack a model into a signed AXM container
python3 research/quant/run_srd4_local.py \
    --model Qwen/Qwen2.5-Coder-0.5B-Instruct \
    --output-dir /workspace/out \
    --llamacpp /workspace/llama.cpp

# Or pack a pre-quantized GGUF directly
python3 research/quant/pack_gguf_to_axm.py \
    --gguf model.gguf \
    --output model.axm \
    --model Qwen/Qwen2.5-Coder-0.5B-Instruct
```

---

## Patent and Legal

Patent Pending · ORVL-001-PROV · Runtime Authority Control for Agentic AI

The bonded pair authority model, constitutional geometry framework, and HMAC-signed state register are covered under provisional patent ORVL-001-PROV.

License: MIT (code) — see [`LICENSE`](LICENSE)

---

## Contact

**Orivael** · mr.antonioroberts@gmail.com · [orivael.dev](https://orivael.dev/)

For pilot inquiries, enterprise licensing, or partnership discussions, email directly.
