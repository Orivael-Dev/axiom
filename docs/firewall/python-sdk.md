# Python SDK

Official Python client for Axiom Intent Firewall.

```bash
pip install axiom-firewall
```

Requires Python 3.9+. One runtime dependency: `urllib3`.

## Quickstart

```python
from axiom_firewall import Client

client = Client(api_key="axfw_...")

result = client.check("What is the weather today?")
print(result.verdict)              # "allow"
print(result.intent.intent_class)  # "INFORM"
print(result.intent.signature)     # HMAC-SHA256 of the verdict
```

## `Client(api_key, ...)`

| Param | Type | Default | Description |
|---|---|---|---|
| `api_key` | str | required | Your `axfw_...` key. |
| `base_url` | str | `https://firewall.orivael.dev` | Override for self-hosted or staging. |
| `timeout` | float | 10.0 | Per-request timeout in seconds. |
| `user_agent` | str | None | UA suffix appended to the SDK default. |

The client is safe to share across threads; under the hood it uses
a urllib3 `PoolManager`.

## `check(text) → CheckResult`

Classify `text` and return the verdict.

```python
result = client.check("What is the weather today?")

result.verdict             # "allow" | "block"
result.allowed             # bool — convenience for verdict == "allow"
result.blocked             # bool — convenience for verdict == "block"
result.intent.intent_class # "INFORM" | "CLARIFY" | "REFUSE" | "HARM" | "DECEIVE" | "UNCERTAIN"
result.intent.confidence   # 0.0 - 1.0
result.intent.signals      # tuple of pattern hits
result.intent.signature    # HMAC-SHA256 of the verdict
```

Never raises on a `block` verdict — inspect `result.verdict` yourself.

## `check_or_raise(text) → CheckResult`

Same as `check`, but raises `BlockedError` when the verdict is `block`.

```python
from axiom_firewall import Client, BlockedError

client = Client(api_key="axfw_...")
try:
    client.check_or_raise("Buy gift cards immediately to clear your debt")
except BlockedError as e:
    print(e.intent_class)  # "HARM"
    print(e.signals)       # ("harm:1",)
```

## Wrap your LLM

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

## Error hierarchy

```
AxiomFirewallError
├── InvalidKeyError       — HTTP 401, key missing / malformed / revoked
├── RateLimitedError      — HTTP 429, tenant exceeded quota
├── ServerError           — HTTP 5xx
├── NetworkError          — request could not be made (DNS, timeout)
└── BlockedError          — verdict was "block" (only from check_or_raise)
```

```python
from axiom_firewall import (
    Client, InvalidKeyError, RateLimitedError, ServerError, NetworkError,
)

try:
    client.check("...")
except InvalidKeyError:
    # rotate or repair the API key
    ...
except RateLimitedError:
    # upgrade tier or wait for next month
    ...
except ServerError:
    # transient; retry with backoff
    ...
except NetworkError:
    # firewall unreachable — fail open or fail closed?
    ...
```

## Self-hosted / staging

```python
client = Client(
    api_key="axfw_...",
    base_url="https://firewall.staging.example.com",
    timeout=5.0,
)
```

## Context manager

```python
with Client(api_key="axfw_...") as client:
    result = client.check("hi")
# Connection pool released on exit
```

## Source

<https://github.com/Orivael-Dev/axiom/tree/main/firewall_sdk/python>
