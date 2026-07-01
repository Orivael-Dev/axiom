# MCP tools

Axiom ships an MCP (Model Context Protocol) server alongside the REST
firewall. Drop it into Claude Desktop, Claude Code, Cursor, or any
JSON-RPC stdio client and get 23 signed tools native to your editor —
no separate service to host, no extra API key to mint.

Every tool result body carries an `hmac_signature` field. Re-verify it
client-side with `axiom_signing.verify` under the `axiom-mcp-v1`
namespace to detect tampering between server and client.

## Why use it alongside the REST firewall?

The REST endpoints (`/v1/guard/check`, `/v1/guard/output`,
`/v1/bonded_pair/*`) are what your production traffic flows through.
The MCP tools are for everything around that:

- Lint a `.axiom` policy spec before deploying it
- Trace a prompt's full 3-phase reasoning to debug why something
  almost-blocked
- Run an N-branch QRF forecast on a borderline input
- Drive the OS Shield daemon during a tabletop ransomware drill
- Inspect / verify / route an `.AXM` container

You can call any tool from inside your editor without writing a curl,
without spinning up a worker, without leaving the file you're editing.

## The 23 tools

### Core (5)

| Tool | Description |
|---|---|
| `axiom_guard_check` | Check input against the constitutional boundary. Returns verdict + constitutional distance + signed manifest. |
| `axiom_lint` | Lint an `.axiom` spec file for authorship-time issues. Returns health score + per-issue list. |
| `axiom_trace` | Run the full 3-phase constitutional reasoning trace (preflight / mid_chain / final_synthesis) with magnitude + monotonicity gates. |
| `axiom_qrf` | Quantum-style reasoning forecast: N parallel branches with constitutional probability per outcome. |
| `axiom_status` | Get AXIOM stack status (version, guard runtime state, test count, patent count, training corpus size). |

### Patent emulators (8)

| Tool | Patent | Description |
|---|---|---|
| `axiom_validate` | ORVL-001 | `.axiom` language validator with optional strict mode. |
| `axiom_intent_gate_check` | ORVL-016 | Classify text + optional trajectory through the intent gate. Returns intent_class (INFORM / CLARIFY / REFUSE / HARM / DECEIVE / UNCERTAIN), confidence, signals, signature. |
| `axiom_cmaa_route` | ORVL-017 | Route a constitutional packet through the multi-agent orchestrator. HARM / DECEIVE refused before reaching the orchestrator; bonded-pair revocations short-circuit authority without rotating keys. |
| `axiom_cmaa_fleet` | ORVL-017 | Inspect fleet trust levels, suspended containers, review queue depth. |
| `axiom_shield` | ORVL-013 | Drive the OS Shield daemon — constitutional ransomware defence that stops attackers at the enumeration stage. Actions: `status`, `tick`, `restore`. |
| `axiom_phone_gate` | ORVL-019 | Run text through the Sovereign Phone constitutional coprocessor for BYOD and edge deployments. `out` gates outbound queries (PII redaction + intent pre-check); `in` gates inbound cloud responses (manipulation + privacy screening); `trajectory` scores a single utterance through the Hello Operator call-trajectory detector. |
| `axiom_axm` | ORVL-023 | Operate an `.AXM` container — successor-to-GGUF format treating models as living execution graphs with signed skill delegates and proof ledgers. Actions: `inspect`, `verify`, `route`. |
| `axiom_cpi` | ORVL-022 | Drive the Constitutional Physical Intelligence agent — toddler-reflex / supervisor / curriculum / examiner stack for robotics, prosthetics, vehicles. Actions: `stability`, `classify`, `simulate`, `pickup`, `status`. |

### AX OS building blocks (9)

| Tool | Description |
|---|---|
| `axiom_memory` | Constitutional memory (ORVL-015) — local-first recall over signed, compressed memory packets. Actions: `remember`, `recall`, `stats`. |
| `axiom_workspace` | Assemble an adaptive workspace from a goal — intent-gated pre-flight check, closest memory recall, signed WorkspaceContext. |
| `axiom_ledger` | Append-only signed audit log. Actions: `log` (record governance event), `list` (query with filters), `verify` (re-verify all rows). |
| `axiom_marketplace` | Signed-agent marketplace with live-revocable bonded authority. Actions: `verify`, `sandbox_install`, `review`, `approve`, `revoke`, `authority`. |
| `axiom_mkb` | Modular Constitutional Knowledge Blocks (ORVL-004) — parse `.axiom` specs into typed HMAC-signed blocks. Actions: `register`, `find`, `list`. |
| `axiom_cas` | Constitutional Adversarial Sandbox (ORVL-008) — blue-team detectors over attack payloads; fix proposals for weak regions. Actions: `defend`, `report`. |
| `axiom_crl` | Constitutional Reinforcement Learning reward (ORVL-011) — governance scores → signed scalar reward. Actions: `compute`, `score`. |
| `axiom_immune` | Constitutional Immune System (ORVL-012) — antibody detectors over a payload: guard-pattern, manifold-distance, HMAC violation, CANNOT_MUTATE, semantic similarity. |
| `axiom_fusion` | Fuse an EventToken's modality layers (text / audio / video / physics / governance) into a signed FusedIntent — each layer votes intent signals weighted by confidence. |

### Research Pipeline (1)

| Tool | Description |
|---|---|
| `axiom_research` | 9-agent constitutional research pipeline: hypothesis → literature → simulation → critic → safety → ethics → data → experiment → report. Safety and Ethics agents can HALT early if critical risks are detected. Returns per-step signed manifests. Uses the active NIM or Anthropic backend. |

## Install

### Prerequisites

```bash
git clone https://github.com/Orivael-Dev/axiom.git
cd axiom
pip install -r requirements.txt
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, or `%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "axiom": {
      "command": "python3",
      "args": ["/absolute/path/to/axiom/axiom_mcp_server.py"],
      "env": {
        "AXIOM_MASTER_KEY": "<your-64-hex-key>"
      }
    }
  }
}
```

Restart Claude Desktop. The 23 tools appear in the tool menu.

### Claude Code

Add to `.mcp.json` at the root of any project you want governed by
Axiom:

```json
{
  "mcpServers": {
    "axiom": {
      "command": "python3",
      "args": ["axiom_mcp_server.py"],
      "env": {
        "AXIOM_MASTER_KEY": "<your-64-hex-key>"
      }
    }
  }
}
```

Path is relative to the project root once you clone the repo into it
(or a sibling directory adjusted with an absolute path).

### Cursor

Cursor reads MCP servers from `~/.cursor/mcp.json` (global) or
`.cursor/mcp.json` (per-project). Same JSON shape as Claude Code, but
prefer an absolute path so Cursor can find the script from any
project root:

```json
{
  "mcpServers": {
    "axiom": {
      "command": "python3",
      "args": ["/absolute/path/to/axiom/axiom_mcp_server.py"],
      "env": {
        "AXIOM_MASTER_KEY": "<your-64-hex-key>"
      }
    }
  }
}
```

### Generic stdio

Any MCP client that speaks JSON-RPC 2.0 over stdio works. Spawn the
process with `AXIOM_MASTER_KEY` in the environment and read/write
line-delimited JSON-RPC on stdin/stdout:

```bash
AXIOM_MASTER_KEY=<hex> python3 axiom_mcp_server.py
```

## Hosted manifest

A machine-readable description of the server lives at
[`orivael-dev.github.io/axiom/mcp.json`](https://orivael-dev.github.io/axiom/mcp.json) —
23 tool entries with input schemas, four install snippets, signing
metadata. Curl it, grep the right block, paste into your client:

```bash
curl -s https://orivael-dev.github.io/axiom/mcp.json | jq .install.claude_code.snippet
```

The manifest is the same document the README links to.

## Signing & verification

Every tool result body carries an `hmac_signature` field. Verify
client-side:

```python
from axiom_signing import derive_key
import hmac, hashlib, json

key = derive_key(b"axiom-mcp-v1")
payload = {k: v for k, v in result.items() if k != "hmac_signature"}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
assert hmac.compare_digest(result["hmac_signature"], expected)
```

If the signature verifies, the response was produced by this server
and not modified in transit. If it doesn't, treat the response as
untrusted — do not act on it.

## Troubleshooting

- **"No module named 'openai'"** on `axiom_guard_check`: the
  classifier auto-fell-back to heuristic mode. Set `OPENAI_API_KEY`
  or `ANTHROPIC_API_KEY` if you want the LLM path; otherwise this is
  expected behaviour.
- **`AXIOM_MASTER_KEY` errors at boot**: the server now self-heals
  missing keys by generating a per-session ephemeral one. For
  production set the env var explicitly so signatures persist across
  server restarts.
- **Tools don't appear in Claude Desktop**: check the desktop log
  (`~/Library/Logs/Claude/mcp.log` on macOS). The most common cause
  is a wrong Python path on `command:` — try `which python3` and use
  the full path.

## Related

- [API reference](api-reference.md) — REST endpoints (`/v1/guard/*`).
- [Quickstart](quickstart.md) — sign up + make your first REST call.
- [Custom policies](custom-policies.md) — per-tenant block patterns.
- GitHub: [Orivael-Dev/axiom](https://github.com/Orivael-Dev/axiom) ·
  [`axiom_mcp_server.py`](https://github.com/Orivael-Dev/axiom/blob/main/axiom_mcp_server.py)
