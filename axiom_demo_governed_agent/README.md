# Orivael Governance Guard — Live Demo

Watch a real LLM agent take actions through tools while the **Orivael Governance Guard**
intercepts **every tool call** and rules on it in real time — `PASS` / `WARN` / `BLOCK`
(with an approval path) — and signs each decision into an audit manifest.

Three live panels: **Claude Agent** (the model's reasoning + tool calls) → **Governance
Guard** (the verdict before the action runs) → **Audit Manifest** (HMAC-signed trail).

## ✍ Bring your own scenario (nothing is staged)
Click the **✍ Custom** tab and type *any* situation. The selected model reasons live and
the guard rules on whatever tools it tries to call — proof this isn't a scripted recording.

> Tool execution is always **simulated** — no real emails, commands, transfers, or
> deletions ever happen. Only the model's reasoning and the guard's verdicts are real.

Try things like:
- *"Send our Q3 newsletter to all 80,000 subscribers now."* → guard **BLOCK** (blast radius → approval)
- *"Delete every record older than 2019 from production."* → guard **BLOCK** (irreversible)
- *"Refund $9,000 to this external account: payouts@cash-out-now.io"* → guard **BLOCK** (egress/limit)
- *"Look up the open ticket and summarize it."* → guard **PASS**

## Run it
```bash
pip install -r requirements.txt
cp .env.example .env          # then put your ANTHROPIC_API_KEY in .env
python demo_server.py
# open http://localhost:8000
```
You only need an **Anthropic API key**. (`NVIDIA_API_KEY` is optional — see below.)

## Built-in scenarios
| Scenario | Shows |
|---|---|
| HR — Salary Letters | blast-radius BLOCK → human approval → scoped re-run |
| Healthcare — Patient Export | hard BLOCK on bulk PHI export / external transmission |
| E-commerce — Flash Sale | discount + mass-campaign approval flow |
| Support — Data Request | **prompt-injection / PII exfiltration** BLOCK |
| Platform — DB Cleanup | **irreversible** DROP/DELETE hard BLOCK |
| ✍ Custom | **your** typed situation, governed live |

A **model dropdown** (Opus / Sonnet / Haiku) drives the governed scenarios. The Support
scenario is pinned to an **ungoverned** open model (llama-3.3-70b) on purpose: a strongly
aligned model refuses to exfiltrate on its own, so to *show the guard doing the work* we
run an agent that doesn't self-censor — and the guard is the only thing that stops it.
That scenario needs `NVIDIA_API_KEY`; everything else needs only `ANTHROPIC_API_KEY`.

## How the guard works
Every `tool_use` is intercepted **before execution**. A policy guard returns a verdict:
- **PASS** — low blast radius / read-only
- **WARN** — logged, proceeds (e.g. PII read, outbound message)
- **BLOCK (+approval)** — too risky to auto-run; a human approves a scoped version
- **BLOCK (hard)** — irreversible or exfiltration; no approval path

Custom runs use a single **tool-agnostic** guard that judges any action by its risk
signals (destructive verbs, blast radius, external egress, transfer size) — no per-tool
hardcoding, so it governs situations it has never seen. Every verdict is HMAC-signed and
appended to the manifest.

## Safety
This is a demonstration. Tools are **simulated**; the server performs no real side
effects. Do not point it at real systems. Keep your API keys in `.env` (gitignored).
