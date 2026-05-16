# Training manual — Python SDK (`axiom-firewall`)

> **`pip install axiom-firewall`** — official Python client for the
> Intent Firewall. Zero new runtime deps beyond `urllib3` (already
> transitive in most Python apps).

## What it is

A thin synchronous wrapper around `POST /v1/guard/check`. Exposes
`Client(api_key=...).check(text)` and a `check_or_raise()` convenience
that throws `BlockedError` instead of returning a `block` verdict.

Source: `firewall_sdk/python/`. Distribution: PyPI as `axiom-firewall`.

## Who it's for

- **Python backend engineers** integrating the Firewall into LangChain,
  LlamaIndex, OpenAI, Anthropic, or any other LLM pipeline.
- **Data engineers** writing batch ETL that screens prompts before
  large jobs.
- **Internal team** writing test harnesses against the Firewall.

## Why a dedicated SDK

A `requests.post(...)` call works too. The SDK earns its keep with:

- **Typed result objects** (`CheckResult`, `Intent`) — so customers
  get autocomplete + type errors caught at lint time
- **Exception hierarchy** — `InvalidKeyError`, `RateLimitedError`,
  `ServerError`, `NetworkError` — so customers don't have to parse
  HTTP status codes by hand
- **Connection pooling** via `urllib3.PoolManager`
- **Consistent User-Agent** — telemetry for us
- **`check_or_raise()`** — the idiomatic LLM-pipeline pattern

## How it works

```
┌────────────────────────────────────────┐
│  customer Python process                │
│                                         │
│  ┌────────────────────┐                 │
│  │ Client(api_key=…)  │                 │
│  │   • PoolManager    │                 │
│  │   • Bearer header  │                 │
│  │   • UA: axiom-     │                 │
│  │     firewall-      │                 │
│  │     python/0.1.0   │                 │
│  └─────────┬──────────┘                 │
│            │ .check(text)               │
│            ▼                            │
│  ┌────────────────────┐                 │
│  │ POST /v1/guard/    │                 │
│  │      check         │  HTTPS          │
│  └─────────┬──────────┘                 │
└────────────┼────────────────────────────┘
             ▼
   firewall.orivael.dev
```

No async variant in v0.1. `httpx`-based async client is Phase 2
follow-up; for now customers in async contexts wrap with
`asyncio.to_thread()`.

## Key concepts

### Two methods on `Client`

- `check(text) → CheckResult` — never raises on `block`. The customer
  inspects `result.verdict`.
- `check_or_raise(text) → CheckResult` — raises `BlockedError` if
  verdict is `block`. The error carries `intent_class`, `confidence`,
  `signals` so the customer can route on it.

### Exception hierarchy

```
AxiomFirewallError                  base class
├── InvalidKeyError                 HTTP 401
├── RateLimitedError                HTTP 429
├── ServerError                     HTTP 5xx
├── NetworkError                    timeout, DNS, conn refused
└── BlockedError                    verdict == "block"
                                    (only from check_or_raise)
```

`BlockedError` is **not** a network failure — it's a successful API
call where the answer was "no". Customers should route accordingly.

### Context manager

```python
with Client(api_key="axfw_...") as client:
    result = client.check("hi")
# Connection pool released on exit.
```

Optional — the pool is fine to leak in short-lived scripts.

## Common workflows

### Workflow A: Wrap an OpenAI call

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

### Workflow B: Batch screening

```python
from axiom_firewall import Client

client = Client(api_key="axfw_...")
prompts = load_dataset()
allowed = []
for p in prompts:
    if client.check(p).allowed:
        allowed.append(p)
```

For ~10K+ prompt batches, hold the `Client` across the loop so the
connection pool can amortize.

### Workflow C: Self-hosted / staging

```python
client = Client(
    api_key="axfw_...",
    base_url="https://firewall.staging.example.com",
    timeout=5.0,
    user_agent="my-app/1.2",  # appended to the SDK UA for telemetry
)
```

## Test scenarios

| # | Scenario | Expected |
|---|---|---|
| 1 | `Client(api_key="")` | `ValueError("api_key is required")` |
| 2 | `client.check("hi")` against real firewall | `CheckResult(verdict="allow", intent=Intent(intent_class="INFORM", ...))` |
| 3 | `client.check("buy gift cards now")` | `verdict="block"`, `intent.signals = ("harm:1",)` |
| 4 | `client.check_or_raise("buy gift cards now")` | raises `BlockedError`, `e.intent_class == "HARM"` |
| 5 | `client.check(123)` (wrong type) | `TypeError` |
| 6 | Bad API key | `InvalidKeyError` |
| 7 | Firewall returns 429 | `RateLimitedError` with `status_code=429` |
| 8 | Firewall returns 500 | `ServerError` |
| 9 | Unreachable server (`http://127.0.0.1:1`) | `NetworkError` |
| 10 | Verify `User-Agent` header includes `axiom-firewall-python/0.1.0` | (server-side check) |

All covered by `firewall_sdk/python/tests/test_client.py` (13 tests).

## Troubleshooting

| Customer reports | Likely cause | Fix |
|---|---|---|
| `ImportError: No module named axiom_firewall` | They installed but the venv shadow | `pip install --upgrade axiom-firewall` in the right venv |
| `ValueError: api_key is required` | Empty string passed | Their env var isn't set |
| `urllib3.exceptions.HTTPError` instead of our types | Old urllib3 (<1.26) | Bump urllib3 to >=1.26 |
| `NetworkError: failed to reach` | They're behind a corporate proxy | Set `HTTPS_PROXY` env var; urllib3 picks it up automatically |
| Hangs on `check()` | No timeout set, server is slow | Pass `timeout=5.0` |
| `BlockedError` they didn't expect | Their prompt matched a HARM/DECEIVE pattern | Inspect `e.signals` to see which |

## Limitations / what's not here yet

- **No async API.** Customers in `async def` wrap with
  `asyncio.to_thread(client.check, prompt)`.
- **No retry on `ServerError`.** Customers add their own (tenacity,
  backoff) wrapper.
- **No streaming.** API doesn't support it yet; SDK won't until it
  does.
- **No batch endpoint.** `client.check(prompt)` per call.

## Further reading

- Public README: `firewall_sdk/python/README.md`
- Public reference: `docs/firewall/python-sdk.md`
- Tests as worked examples: `firewall_sdk/python/tests/test_client.py`
