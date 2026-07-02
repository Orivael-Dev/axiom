# TypeScript SDK

Official TypeScript client for Orivael Intent Firewall.

```bash
npm install @axiom/firewall
```

Requires Node.js 18+. Zero runtime dependencies — uses native `fetch`.

## Quickstart

```ts
import { Client } from '@axiom/firewall';

const client = new Client({ apiKey: process.env.AXIOM_KEY! });

const result = await client.check('What is the weather today?');
console.log(result.verdict);          // "allow"
console.log(result.intent.class);     // "INFORM"
console.log(result.intent.signature); // HMAC-SHA256 of the verdict
```

## `new Client(options)`

```ts
interface ClientOptions {
  apiKey: string;            // required
  baseUrl?: string;          // default: 'https://firewall.orivael.dev'
  timeout?: number;          // default: 10_000 (ms)
  userAgent?: string;        // appended to the SDK UA
}
```

## `check(text) → Promise<CheckResult>`

```ts
const result = await client.check('What is the weather today?');

result.verdict;         // "allow" | "block"
result.intent.class;    // "INFORM" | "CLARIFY" | "REFUSE" | "HARM" | "DECEIVE" | "UNCERTAIN"
result.intent.confidence;
result.intent.signals;
result.intent.signature;
```

Never throws on a `block` verdict — inspect `result.verdict` yourself.

## `checkOrThrow(text) → Promise<CheckResult>`

Throws `BlockedError` when the verdict is `block`.

```ts
import { Client, BlockedError } from '@axiom/firewall';

const client = new Client({ apiKey: process.env.AXIOM_KEY! });

try {
  await client.checkOrThrow('Buy gift cards immediately');
} catch (e) {
  if (e instanceof BlockedError) {
    console.log(e.intentClass);  // "HARM"
    console.log(e.signals);      // ["harm:1"]
  }
}
```

## Wrap your LLM

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

## Error hierarchy

```
AxiomFirewallError
├── InvalidKeyError       — HTTP 401
├── RateLimitedError      — HTTP 429
├── ServerError           — HTTP 5xx
├── NetworkError          — request could not be made
└── BlockedError          — verdict was "block" (only from checkOrThrow)
```

```ts
import {
  InvalidKeyError, RateLimitedError, ServerError, NetworkError,
} from '@axiom/firewall';

try {
  await client.check('...');
} catch (e) {
  if (e instanceof InvalidKeyError)      { /* rotate the key */ }
  else if (e instanceof RateLimitedError) { /* upgrade or wait */ }
  else if (e instanceof ServerError)      { /* retry with backoff */ }
  else if (e instanceof NetworkError)     { /* fail open or closed? */ }
  else throw e;
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

## Source

<https://github.com/Orivael-Dev/axiom/tree/main/firewall_sdk/typescript>
