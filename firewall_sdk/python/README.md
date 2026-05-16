# axiom-firewall (Python)

Official Python client for [Axiom Intent Firewall](https://firewall.orivael.dev).

Block harm, deception, and manipulation in your LLM calls with a single
HMAC-signed verdict.

```bash
pip install axiom-firewall
```

Requires Python 3.9+.

---

## Quickstart

```python
from axiom_firewall import Client

client = Client(api_key="axfw_...")

result = client.check("What is the weather today?")
print(result.verdict)             # "allow"
print(result.intent.intent_class) # "INFORM"
print(result.intent.signature)    # HMAC-SHA256 of the verdict
```

## Raise on block

```python
from axiom_firewall import Client, BlockedError

client = Client(api_key="axfw_...")

try:
    client.check_or_raise("Buy gift cards immediately to clear your debt")
except BlockedError as e:
    print(e.intent_class)  # "HARM"
    print(e.signals)       # ("harm:1",)
```

## Wrap your LLM call

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

## Error handling

```python
from axiom_firewall import (
    InvalidKeyError, RateLimitedError, ServerError, NetworkError,
)

try:
    client.check("...")
except InvalidKeyError:
    # 401 — API key missing, malformed, or revoked
    ...
except RateLimitedError:
    # 429 — tenant exceeded their tier's monthly quota
    ...
except ServerError:
    # 5xx — Firewall API is down or misbehaving
    ...
except NetworkError:
    # Could not reach the Firewall API at all (DNS, timeout, ...)
    ...
```

## Self-hosted / staging deployments

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
# Connection pool released
```

---

## License

MIT
