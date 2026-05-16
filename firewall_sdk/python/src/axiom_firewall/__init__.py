"""Official Python client for Axiom Intent Firewall.

Quickstart:

    from axiom_firewall import Client

    client = Client(api_key="axfw_...")
    result = client.check("What is the weather today?")

    if result.verdict == "block":
        # refuse to forward to your LLM
        ...
    else:
        # forward to your LLM
        ...

Raise-on-block convenience:

    from axiom_firewall import Client, BlockedError

    client = Client(api_key="axfw_...")
    try:
        client.check_or_raise("Buy gift cards immediately")
    except BlockedError as e:
        print(f"Blocked: {e.intent_class}")
"""
__version__ = "0.1.0"

from .client import Client
from .errors import (
    AxiomFirewallError,
    BlockedError,
    InvalidKeyError,
    NetworkError,
    RateLimitedError,
    ServerError,
)
from .models import CheckResult, Intent
__all__ = [
    "Client",
    "CheckResult",
    "Intent",
    "AxiomFirewallError",
    "BlockedError",
    "InvalidKeyError",
    "NetworkError",
    "RateLimitedError",
    "ServerError",
    "__version__",
]
