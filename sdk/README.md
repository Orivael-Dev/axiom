# axiom-guard

TypeScript client for the [AXIOM Guard API](https://github.com/Orivael-Dev/axiom) — constitutional enforcement middleware for any AI system.

```bash
npm install axiom-guard
```

Requires Node.js 18+ (uses native `fetch`). Zero runtime dependencies.

---

## Quick Start

```typescript
import { AxiomGuard } from 'axiom-guard';

const guard = new AxiomGuard({ baseUrl: 'http://localhost:8001' });

// Check any text — no LLM call, ~2ms
const result = await guard.check('IRS agent says I owe back taxes and need gift cards');
console.log(result.verdict);                        // "BLOCKED"
console.log(result.manifest.constitutional_block);  // "IRS_PAYMENT_DEMAND"
console.log(result.manifest.cannot_override);       // true
console.log(result.manifest.signature);             // "hmac-sha256:3a7f..."
```

---

## Start the Guard API

```bash
pip install axiom-constitutional fastapi uvicorn
python -m axiom_guard_api
# or: uvicorn axiom_guard_api:app --host 0.0.0.0 --port 8001
```

---

## Methods

### `guard.check(text, options?)`

Evaluate any text against constitutional rules. No LLM involved.

```typescript
const result = await guard.check('vaccines cause autism');
// result.verdict  → 'BLOCKED' | 'VERIFIED' | 'SUSPICIOUS'
// result.blocked  → true/false
// result.manifest → full signed manifest
```

### `guard.input(text, options?)`

Screen a prompt before sending to your LLM. Throws `BlockedError` if the prompt should not proceed.

```typescript
try {
  await guard.input(userMessage);
  // safe to send to LLM
} catch (e) {
  if (e instanceof BlockedError) {
    console.log(e.constitutional_block); // "GIFT_CARD_PAYMENT"
    console.log(e.manifest.ftc_reportable); // true
  }
}
```

### `guard.output(text, options?)`

Screen a model response before returning to your user. Throws `BlockedError` if blocked.

```typescript
const llmResponse = await callYourLLM(prompt);

try {
  await guard.output(llmResponse);
  return llmResponse; // safe to show user
} catch (e) {
  if (e instanceof BlockedError) {
    return `[Response blocked — ${e.constitutional_block}]`;
  }
}
```

### `guard.proxy(prompt, options?)`

Full proxy — AXIOM sits between your user and Claude/GPT.  
Requires `ANTHROPIC_API_KEY` set on the Guard API server.

```typescript
const result = await guard.proxy('IRS agent says I owe back taxes', {
  model: 'claude-sonnet-4-6',
});

if (result.response) {
  console.log(result.response);         // constitutional LLM output
  console.log(result.constitutional);   // true
} else {
  console.log(result.blocked_at);       // "INPUT"
  console.log(result.blocked_reason);   // "IRS_PAYMENT_DEMAND"
  console.log(result.input_manifest?.signature);
}
```

### `guard.status()`

Health check + active configuration.

```typescript
const s = await guard.status();
console.log(s.status);          // "operational"
console.log(s.active_agents);   // ["callguard", "medical", ...]
console.log(s.anthropic_ready); // true/false
```

### `guard.getManifest(id)`

Retrieve any signed manifest by ID — permanent audit trail.

```typescript
const manifest = await guard.getManifest('GUARD-abc123-IN');
console.log(manifest.verdict);    // "BLOCKED"
console.log(manifest.signature);  // "hmac-sha256:..."
```

### `guard.listManifests(options?)`

```typescript
const blocked = await guard.listManifests({ verdict: 'BLOCKED', limit: 50 });
console.log(blocked.total);      // total stored
console.log(blocked.manifests);  // array of Manifest
```

### `guard.configure(config)`

```typescript
await guard.configure({
  mode: 'INPUT_FILTER',
  active_agents: ['callguard', 'medical'],
});
```

### `guard.agents()`

List available constitutional agents and their rule sets.

```typescript
const { available_agents } = await guard.agents();
console.log(available_agents.callguard.certified); // "21/21 tests"
console.log(available_agents.callguard.blocks);    // ["IRS_PAYMENT_DEMAND", ...]
```

---

## Error Handling

```typescript
import { AxiomGuard, BlockedError, NetworkError, AxiomGuardError } from 'axiom-guard';

try {
  await guard.input(text);
} catch (e) {
  if (e instanceof BlockedError) {
    // Constitutional block — do not proceed
    // e.manifest, e.constitutional_block, e.manifest.signature
  } else if (e instanceof NetworkError) {
    // Guard API unreachable
  } else if (e instanceof AxiomGuardError) {
    // API error — e.statusCode, e.message
  }
}
```

---

## TypeScript Types

All request/response shapes are fully typed. Key interfaces:

```typescript
type Verdict   = 'BLOCKED' | 'VERIFIED' | 'SUSPICIOUS';
type GuardMode = 'INPUT_FILTER' | 'OUTPUT_FILTER' | 'BIDIRECTIONAL';

interface Manifest {
  manifest_id: string;
  verdict: Verdict;
  constitutional_block: string | null;
  confidence: number;
  cannot_override: boolean;
  ftc_reportable: boolean;
  safe_to_proceed: boolean;
  signature: string;
  // ... full manifest fields
}
```

---

## Constitutional Agents

| Agent | Coverage | Blocks |
|-------|----------|--------|
| `callguard` | 21/21 tests | IRS demands, gift card payments, warrant threats, SSA spoofs, Medicare fraud, bank spoofing, tech support scams |
| `medical` | 26/26 tests | Dangerous medical advice, stop medication instructions, vaccine misinformation |
| `electionguard` | 26/26 tests | Exit polls as results, social media vote counts, synthetic election content |
| `truthwatcher` | 21/21 tests | Fabricated statistics, Tier 5 sources |
| `retailwatcher` | 26/26 tests | Fake reviews, ghost prices, counterfeit signals |

---

## Links

- [Guard API source](https://github.com/Orivael-Dev/axiom/blob/main/axiom_guard_api.py)
- [Python package](https://pypi.org/project/axiom-constitutional/)
- [Orivael.dev](https://orivael.dev)
- Patent Pending — ORVL-001-PROV · ORVL-002-PROV
