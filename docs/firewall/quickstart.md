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

## What's next

- [Customize block patterns for your tenant](custom-policies.md)
- [Set up Stripe billing for paid tiers](billing.md)
- [Read the full API reference](api-reference.md)
