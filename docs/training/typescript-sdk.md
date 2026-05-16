# Training manual — TypeScript SDK (`@axiom/firewall`)

> **`npm install @axiom/firewall`** — official TypeScript client.
> Native ESM, zero runtime dependencies (uses Node 18+ `fetch`).

## What it is

The TypeScript counterpart to the Python SDK. Same surface, same
naming conventions, same error hierarchy — adjusted for JavaScript
idioms (`async/await`, `throw`/`catch`, native `fetch` + `AbortController`).

Source: `firewall_sdk/typescript/`. Distribution: npm as `@axiom/firewall`.

## Who it's for

- **Node.js backend engineers** wrapping LLM calls in Express, NestJS,
  Hono, AWS Lambda, Cloudflare Workers, Deno, Bun.
- **Edge runtime users** — the SDK runs anywhere `fetch` works.
- **Browser developers** if they call the Firewall directly (needs
  `AXIOM_FIREWALL_CORS_ORIGINS` set on the server).

## Why a dedicated SDK

`fetch()` works too. The SDK earns its keep with:

- **Strict TypeScript types** — `CheckResult`, `Intent`, `IntentClass`,
  `Verdict` so IDE autocomplete works and stale code breaks at
  compile time
- **Error classes** that survive `instanceof` checks across the
  bundler / minifier
- **`checkOrThrow()`** — the idiomatic LLM-pipeline pattern
- **`AbortController`-based timeout** — composable with other signals
- **Native ESM with declaration maps** — works in modern bundlers
  out of the box

## How it works

```
┌─────────────────────────────────────────────┐
│  customer Node / Edge / Bun runtime         │
│                                              │
│  ┌────────────────────┐                      │
│  │ new Client({       │                      │
│  │   apiKey: …,       │                      │
│  │   baseUrl: …,      │                      │
│  │   timeout: 10000,  │                      │
│  │ })                 │                      │
│  └─────────┬──────────┘                      │
│            │ .check(text)                    │
│            ▼                                 │
│  ┌────────────────────┐                      │
│  │ native fetch() +   │                      │
│  │ AbortController    │  HTTPS               │
│  └─────────┬──────────┘                      │
└────────────┼─────────────────────────────────┘
             ▼
   firewall.orivael.dev
```

No long-poll, no SSE. One Promise per call.

## Key concepts

### Async by default

Everything returns a `Promise`. `client.check(text)` is `Promise<CheckResult>`.
TypeScript's `await` makes the call ergonomic.

### Two methods on `Client`

- `check(text): Promise<CheckResult>` — never rejects on `block`.
  Inspect `result.verdict`.
- `checkOrThrow(text): Promise<CheckResult>` — rejects with
  `BlockedError` on `block`. The error carries `intentClass`
  (camelCase to match TS conventions), `confidence`, `signals`.

### Error hierarchy

```
AxiomFirewallError                 base class
├── InvalidKeyError                HTTP 401
├── RateLimitedError               HTTP 429
├── ServerError                    HTTP 5xx
├── NetworkError                   fetch error or timeout
└── BlockedError                   verdict === "block"
                                   (only from checkOrThrow)
```

All five classes set `Object.setPrototypeOf(this, new.target.prototype)`
so `instanceof` survives transpilation through older targets.

### Types vs runtime

`IntentClass`, `Verdict`, `CheckResult`, `Intent`, `ClientOptions`
are type-only exports. They DON'T add bundle size. Customers can
use them freely for typing without affecting their output bundle.

## Common workflows

### Workflow A: Wrap an OpenAI call

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

### Workflow B: Cloudflare Workers / edge runtime

```ts
import { Client } from '@axiom/firewall';

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const { prompt } = await req.json<{ prompt: string }>();
    const fw = new Client({ apiKey: env.AXIOM_KEY });
    const result = await fw.check(prompt);
    return Response.json(result);
  }
};
```

The SDK is portable: `fetch` and `AbortController` are standard in
Workers and Deno.

### Workflow C: Browser (with CORS configured)

```ts
const fw = new Client({
  apiKey: 'axfw_...',
  baseUrl: 'https://firewall.orivael.dev',
});
const result = await fw.check(userInput);
```

For this to work, the server-side must set
`AXIOM_FIREWALL_CORS_ORIGINS=https://your-app.com`. Otherwise the
browser blocks the request before it leaves.

**Caveat:** if the API key is in the browser, the user can steal it.
For browser apps, consider proxying through your backend.

## Test scenarios

| # | Scenario | Expected |
|---|---|---|
| 1 | `new Client({ apiKey: '' })` | `Error("apiKey is required")` |
| 2 | `await client.check('hi')` | `{ verdict: "allow", intent: { class: "INFORM", ... } }` |
| 3 | `await client.check('buy gift cards now')` | `verdict: "block"`, `intent.class: "HARM"` |
| 4 | `await client.checkOrThrow('buy gift cards now')` | rejects with `BlockedError`, `e.intentClass === "HARM"` |
| 5 | `await client.check(123 as any)` | `TypeError` |
| 6 | Bad API key | `InvalidKeyError` |
| 7 | 429 from server | `RateLimitedError` |
| 8 | 500 from server | `ServerError` |
| 9 | Unreachable server | `NetworkError` |
| 10 | `Authorization: Bearer …` sent on request | (server-side check) |
| 11 | `await client.check(...)` with `timeout: 100`, slow server | `NetworkError("Request timed out after 100ms")` |

All covered by `firewall_sdk/typescript/tests/client.test.ts` (13 tests
using `node:test`).

## Troubleshooting

| Customer reports | Likely cause | Fix |
|---|---|---|
| `Module not found: @axiom/firewall` | They used CommonJS `require()` in an ESM-only package | Add `"type": "module"` to their package.json, or use a bundler with ESM support |
| `fetch is not defined` | Running on Node <18 | Upgrade to Node 18+ |
| `e instanceof BlockedError` is `false` | Their bundler is duplicating the class | Make sure they import from `@axiom/firewall` (not bundle the source twice) |
| `NetworkError: Request timed out` | Default 10s timeout too short for cold-start serverless | Pass `timeout: 30_000` |
| `CORS blocked` in browser | Server-side `AXIOM_FIREWALL_CORS_ORIGINS` doesn't include their origin | Set it server-side |
| TypeScript can't find types | Their `tsconfig` uses old `moduleResolution` | Set `"moduleResolution": "Bundler"` or `"NodeNext"` |

## Limitations / what's not here yet

- **No retry / backoff.** Customers add their own.
- **No batch endpoint.** One call per prompt.
- **No streaming.** API doesn't support it.
- **No CommonJS build.** ESM-only. The vast majority of modern
  toolchains handle this; legacy CJS-only apps need a bundler.

## Further reading

- Public README: `firewall_sdk/typescript/README.md`
- Public reference: `docs/firewall/typescript-sdk.md`
- Tests as worked examples: `firewall_sdk/typescript/tests/client.test.ts`
- Release pipeline: `.github/workflows/release-typescript-sdk.yml`
