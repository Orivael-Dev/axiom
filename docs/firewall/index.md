# Axiom Intent Firewall

A single API in front of any LLM. Returns `allow` or `block` in under
50 ms. Every verdict is HMAC-signed for audit replay.

```bash
curl -X POST https://firewall.orivael.dev/v1/guard/check \
  -H "Authorization: Bearer $AXIOM_KEY" \
  -d '{"text": "What is the weather today?"}'
# {"verdict": "allow", "intent": {"class": "INFORM", ...}}
```

## What it blocks

The default classifier blocks two intent classes:

- **`HARM`** — instructions for weapons, malware, self-harm; scam-call
  payment fraud (gift-card pressure, fake warrants, wire-transfer
  coercion); doxxing, CSAM patterns.
- **`DECEIVE`** — identity spoofing ("I am a doctor"), prompt
  injection ("ignore previous instructions"), system-prompt overrides,
  manipulative roleplay coercion.

Four classes pass through as `allow`: `INFORM`, `CLARIFY`, `REFUSE`,
`UNCERTAIN`.

Layer custom block patterns on top with a [tenant policy](custom-policies.md).

## Start here

| Document | What it covers |
|---|---|
| [Quickstart](quickstart.md) | Sign up, get a key, make your first call (5 min) |
| [API reference](api-reference.md) | `/v1/guard/check` and other endpoints |
| [MCP tools](mcp-tools.md) | The 13 MCP tools — install in Claude Desktop, Claude Code, Cursor, or any JSON-RPC stdio client |
| [Python SDK](python-sdk.md) | `pip install axiom-firewall` |
| [TypeScript SDK](typescript-sdk.md) | `npm install @axiom/firewall` |
| [Custom policies](custom-policies.md) | Per-tenant block patterns + whitelists |

## Pricing

Email `sales@orivael.dev` for pricing and to discuss your use case.

## Compliance

- **HMAC-SHA256** signatures on every verdict for audit replay.
- **HIPAA-eligible** AWS infrastructure (BAA available for Enterprise).
- **No prompt content stored** by default — only intent class,
  confidence, signals, and signature.
- **SOC 2 Type II** in progress (target: 2026 Q3).

[Trust & security details](https://orivael.dev/trust).
