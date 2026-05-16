"""HTTP client for the Axiom Firewall API.

Uses urllib3 (already a transitive dependency in most environments) so
the SDK has a minimal install footprint and zero async runtime
dependency.
"""
from __future__ import annotations

import json
from typing import Optional

import urllib3

from . import __version__
from .errors import (
    AxiomFirewallError, BlockedError, InvalidKeyError,
    NetworkError, RateLimitedError, ServerError,
)
from .models import CheckResult

DEFAULT_BASE_URL = "https://firewall.orivael.dev"
DEFAULT_TIMEOUT_SECONDS = 10.0


class Client:
    """Axiom Firewall client.

    Args:
        api_key: Your `axfw_...` API key from the dashboard.
        base_url: Override for self-hosted or staging deployments.
        timeout: Per-request timeout in seconds.
        user_agent: Optional UA suffix appended to the default SDK UA.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        ua = f"axiom-firewall-python/{__version__}"
        if user_agent:
            ua = f"{ua} {user_agent}"
        self._pool = urllib3.PoolManager(
            timeout=urllib3.Timeout(total=self.timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": ua,
            },
        )

    # ─── Public API ──────────────────────────────────────────────────

    def check(self, text: str) -> CheckResult:
        """Classify `text` and return the verdict + intent.

        Never raises on block — inspect `result.verdict`. Use
        `check_or_raise` for the raise-on-block convenience.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        body = self._post("/v1/guard/check", {"text": text})
        return CheckResult.from_dict(body)

    def check_or_raise(self, text: str) -> CheckResult:
        """Like `check`, but raises `BlockedError` if verdict == 'block'."""
        result = self.check(text)
        if result.blocked:
            raise BlockedError(
                intent_class=result.intent.intent_class,
                confidence=result.intent.confidence,
                signals=result.intent.signals,
            )
        return result

    # ─── HTTP transport ──────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self._pool.request(
                "POST", url, body=json.dumps(body).encode("utf-8"),
            )
        except urllib3.exceptions.HTTPError as e:
            raise NetworkError(f"Failed to reach {url}: {e}") from e

        if resp.status == 200:
            try:
                return json.loads(resp.data)
            except json.JSONDecodeError as e:
                raise ServerError(
                    f"Server returned non-JSON body: {e}", resp.status,
                ) from e

        detail = self._extract_detail(resp.data)
        if resp.status == 401:
            raise InvalidKeyError(detail, resp.status)
        if resp.status == 429:
            raise RateLimitedError(detail, resp.status)
        if resp.status >= 500:
            raise ServerError(detail, resp.status)
        raise AxiomFirewallError(detail, resp.status)

    @staticmethod
    def _extract_detail(data: bytes) -> str:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return data.decode("utf-8", errors="replace")
        if isinstance(parsed, dict) and "detail" in parsed:
            return str(parsed["detail"])
        return json.dumps(parsed)

    def close(self) -> None:
        """Release pooled connections. Safe to call repeatedly."""
        self._pool.clear()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
