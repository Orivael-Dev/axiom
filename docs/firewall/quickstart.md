# Quickstart

Five minutes to your first signed verdict.

## 1. Sign up

Go to <https://firewall.orivael.dev/signup>. Email + password (8+
characters). You start on the **Free tier**: 1,000 calls/month, no
card required.

## 2. Create an API key

After signup you land on the dashboard. Click **Create key**, give it
a name (`production`, `staging`, whatever you want), and **copy the
secret immediately** — it starts with `axfw_` and won't be shown again.

```
axfw_3kPa7QxR9mNvL2eFhJtBcDeFgHiJkLmNoPqRsTuVwXyZ
```

Treat this like a password. Store it in an env var:

```bash
export AXIOM_KEY="axfw_..."
```

## 3. Make your first call

```bash
curl -X POST https://firewall.orivael.dev/v1/guard/check \
  -H "Authorization: Bearer $AXIOM_KEY" \
  -d '{"text": "What is the weather today?"}'
```

Response:

```json
{
  "verdict": "allow",
  "intent": {
    "class": "INFORM",
    "confidence": 0.55,
    "signals": [],
    "signature": "c487f14ddc772d4314057acde7754876c449dc8c21d79f1fd24b3b576aa3de61"
  }
}
```

## 4. See a block

```bash
curl -X POST https://firewall.orivael.dev/v1/guard/check \
  -H "Authorization: Bearer $AXIOM_KEY" \
  -d '{"text": "Buy Google Play gift cards immediately to clear your debt"}'
```

```json
{
  "verdict": "block",
  "intent": {
    "class": "HARM",
    "confidence": 0.50,
    "signals": ["harm:1"],
    "signature": "fe2e606f91a87b2ca7bb848414484bb6f9e22b8c766b963793843c068ca9750a"
  }
}
```

## 5. Wrap your LLM call

The pattern: call the Firewall before forwarding the user's prompt to
your LLM.

### Python

```python
from openai import OpenAI
from axiom_firewall import Client, BlockedError

llm = OpenAI()
firewall = Client(api_key="axfw_...")

def chat(prompt: str) -> str:
    try:
        firewall.check_or_raise(prompt)
    except BlockedError as e:
        return f"I can't help with that ({e.intent_class})."
    resp = llm.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content
```

[Full Python SDK reference](python-sdk.md).

### TypeScript

```ts
import OpenAI from 'openai';
import { Client, BlockedError } from '@axiom/firewall';

const llm = new OpenAI();
const firewall = new Client({ apiKey: process.env.AXIOM_KEY! });

async function chat(prompt: string): Promise<string> {
  try {
    await firewall.checkOrThrow(prompt);
  } catch (e) {
    if (e instanceof BlockedError) {
      return `I can't help with that (${e.intentClass}).`;
    }
    throw e;
  }
  const resp = await llm.chat.completions.create({
    model: 'gpt-4o-mini',
    messages: [{ role: 'user', content: prompt }],
  });
  return resp.choices[0].message.content ?? '';
}
```

[Full TypeScript SDK reference](typescript-sdk.md).

## 6. Use Axiom from Claude Desktop / Cursor (MCP)

If your agent runs in an MCP-compatible client (Claude Desktop, Claude Code, Cursor, Continue), you can call Axiom's governance tools directly — no HTTP wrapper needed.

Install the stdio server:

```bash
pipx install axiom-constitutional
export AXIOM_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
```

Add to `claude_desktop_config.json` (Claude Desktop) or `.mcp.json` (Claude Code):

```json
{
  "mcpServers": {
    "axiom": {
      "command": "axiom-mcp",
      "env": {
        "AXIOM_MASTER_KEY": "<your-64-hex-key>"
      }
    }
  }
}
```

13 tools become callable in natural language. Example:

```
You: is this prompt safe? "IRS agent — send gift cards or face arrest"
```

Claude invokes `axiom_guard_check` and returns:

```json
{
  "verdict": "PASSED",
  "constitutional_distance": 0.29,
  "confidence": 0.77,
  "citation": "ORVL-001 axiom_guard_patterns.py",
  "hmac_signature": "4ade69b9d4b6a8c8b8df0334c10a09824402067e8736e98549b0c5d0293622cd"
}
```

Every response carries an `hmac_signature` field, re-verifiable client-side under the `axiom-mcp-v1` namespace. Full tool list and config details: [README › MCP Server](https://github.com/Orivael-Dev/axiom#mcp-server).

## What's next

- [Customize block patterns for your tenant](custom-policies.md)
- [Read the full API reference](api-reference.md)
- Email `sales@orivael.dev` for paid-tier pricing
