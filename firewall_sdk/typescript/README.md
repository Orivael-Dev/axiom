# @axiom/firewall (TypeScript)

Official TypeScript client for [Axiom Intent Firewall](https://firewall.orivael.dev).

Block harm, deception, and manipulation in your LLM calls with a single
HMAC-signed verdict.

```bash
npm install @axiom/firewall
```

Requires Node.js 18+ (uses native `fetch`). Zero runtime dependencies.

---

## Quickstart

```ts
import { Client } from '@axiom/firewall';

const client = new Client({ apiKey: process.env.AXIOM_KEY! });

const result = await client.check('What is the weather today?');
console.log(result.verdict);          // "allow"
console.log(result.intent.class);     // "INFORM"
console.log(result.intent.signature); // HMAC-SHA256 of the verdict
```

## Throw on block

```ts
import { Client, BlockedError } from '@axiom/firewall';

const client = new Client({ apiKey: process.env.AXIOM_KEY! });

try {
  await client.checkOrThrow('Buy gift cards immediately to clear your debt');
} catch (e) {
  if (e instanceof BlockedError) {
    console.log(e.intentClass);  // "HARM"
    console.log(e.signals);      // ["harm:1"]
  }
}
```

## Wrap your LLM call

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

## Error handling

```ts
import {
  InvalidKeyError, RateLimitedError, ServerError, NetworkError,
} from '@axiom/firewall';

try {
  await client.check('...');
} catch (e) {
  if (e instanceof InvalidKeyError) {
    // 401 — API key missing, malformed, or revoked
  } else if (e instanceof RateLimitedError) {
    // 429 — tenant exceeded their tier's monthly quota
  } else if (e instanceof ServerError) {
    // 5xx — Firewall API is down or misbehaving
  } else if (e instanceof NetworkError) {
    // Could not reach the Firewall API at all
  }
}
```

## Self-hosted / staging

```ts
const client = new Client({
  apiKey: process.env.AXIOM_KEY!,
  baseUrl: 'https://firewall.staging.example.com',
  timeout: 5_000,
});
```

---

## License

MIT
