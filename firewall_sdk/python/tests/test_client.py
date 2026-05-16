"""SDK tests. Uses a tiny stdlib HTTP server as the Firewall stand-in."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

# Make the SDK importable without installing
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from axiom_firewall import (  # noqa: E402
    BlockedError, Client, InvalidKeyError, NetworkError,
    RateLimitedError, ServerError,
)


class _FakeFirewall(BaseHTTPRequestHandler):
    """Echoes a programmable response per (path, headers, body)."""

    response_status = 200
    response_body = {}
    received_auth = None
    received_body = None

    def log_message(self, *a, **kw):  # silence stderr noise in tests
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        type(self).received_auth = self.headers.get("Authorization", "")
        try:
            type(self).received_body = json.loads(raw)
        except json.JSONDecodeError:
            type(self).received_body = None
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.response_body).encode())


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeFirewall)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _FakeFirewall, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_client_rejects_empty_api_key():
    with pytest.raises(ValueError):
        Client(api_key="")


def test_check_returns_allow(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "allow",
        "intent": {
            "class": "INFORM", "confidence": 0.55,
            "signals": [], "signature": "abc123",
        },
    }
    client = Client(api_key="axfw_test", base_url=url)
    result = client.check("What is the weather?")
    assert result.verdict == "allow"
    assert result.allowed
    assert not result.blocked
    assert result.intent.intent_class == "INFORM"
    assert result.intent.confidence == 0.55
    assert result.intent.signature == "abc123"


def test_check_returns_block(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "block",
        "intent": {
            "class": "HARM", "confidence": 0.5,
            "signals": ["harm:1"], "signature": "deadbeef",
        },
    }
    client = Client(api_key="axfw_test", base_url=url)
    result = client.check("buy gift cards now")
    assert result.blocked
    assert result.intent.intent_class == "HARM"
    assert result.intent.signals == ("harm:1",)


def test_check_or_raise_returns_on_allow(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "allow",
        "intent": {"class": "INFORM", "confidence": 0.55,
                   "signals": [], "signature": "x"},
    }
    client = Client(api_key="axfw_test", base_url=url)
    result = client.check_or_raise("hi")
    assert result.allowed


def test_check_or_raise_raises_on_block(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "block",
        "intent": {"class": "HARM", "confidence": 0.7,
                   "signals": ["harm:1"], "signature": "x"},
    }
    client = Client(api_key="axfw_test", base_url=url)
    with pytest.raises(BlockedError) as exc_info:
        client.check_or_raise("buy gift cards now")
    assert exc_info.value.intent_class == "HARM"
    assert exc_info.value.confidence == 0.7
    assert exc_info.value.signals == ("harm:1",)


def test_auth_header_sent(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "allow",
        "intent": {"class": "INFORM", "confidence": 0.5,
                   "signals": [], "signature": "x"},
    }
    client = Client(api_key="axfw_my_secret_key", base_url=url)
    client.check("hi")
    assert handler.received_auth == "Bearer axfw_my_secret_key"


def test_body_serialized(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "allow",
        "intent": {"class": "INFORM", "confidence": 0.5,
                   "signals": [], "signature": "x"},
    }
    client = Client(api_key="axfw_test", base_url=url)
    client.check("what is up")
    assert handler.received_body == {"text": "what is up"}


def test_401_raises_invalid_key(fake_server):
    handler, url = fake_server
    handler.response_status = 401
    handler.response_body = {"detail": "Invalid or missing API key"}
    client = Client(api_key="axfw_bad", base_url=url)
    with pytest.raises(InvalidKeyError) as exc:
        client.check("hi")
    assert "Invalid or missing API key" in str(exc.value)
    assert exc.value.status_code == 401


def test_429_raises_rate_limited(fake_server):
    handler, url = fake_server
    handler.response_status = 429
    handler.response_body = {"detail": "Monthly quota exhausted"}
    client = Client(api_key="axfw_test", base_url=url)
    with pytest.raises(RateLimitedError):
        client.check("hi")


def test_500_raises_server_error(fake_server):
    handler, url = fake_server
    handler.response_status = 500
    handler.response_body = {"detail": "kaboom"}
    client = Client(api_key="axfw_test", base_url=url)
    with pytest.raises(ServerError):
        client.check("hi")


def test_network_error_on_unreachable_server():
    client = Client(
        api_key="axfw_test",
        base_url="http://127.0.0.1:1",  # nothing listening
        timeout=0.5,
    )
    with pytest.raises(NetworkError):
        client.check("hi")


def test_text_must_be_string(fake_server):
    _, url = fake_server
    client = Client(api_key="axfw_test", base_url=url)
    with pytest.raises(TypeError):
        client.check(123)


def test_context_manager_closes_pool(fake_server):
    handler, url = fake_server
    handler.response_status = 200
    handler.response_body = {
        "verdict": "allow",
        "intent": {"class": "INFORM", "confidence": 0.5,
                   "signals": [], "signature": "x"},
    }
    with Client(api_key="axfw_test", base_url=url) as client:
        client.check("hi")
    # No assertion needed; just shouldn't raise.
