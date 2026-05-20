"""Unit tests for axiom_event_token.backends — no network calls.

NIM + local backend HTTP shapes are validated via mocked requests;
ChainedBackend fallthrough is checked with stub backends.
"""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_event_token", "axiom_signing")):
            sys.modules.pop(mod, None)
    yield


# ─── NIMBackend ─────────────────────────────────────────────────────────


def test_nim_requires_api_key(isolated, monkeypatch):
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    from axiom_event_token.backends import NIMBackend, BackendError
    with pytest.raises(BackendError, match="NVIDIA_NIM_API_KEY"):
        NIMBackend()


def test_nim_builds_openai_compatible_body(isolated):
    from axiom_event_token.backends import NIMBackend
    b = NIMBackend(api_key="test-key", model="meta/llama-3.1-8b-instruct")
    fake_resp = MagicMock(ok=True)
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "answer"}}],
        "usage":   {"prompt_tokens": 42, "completion_tokens": 7},
    }
    with patch("axiom_event_token.backends.requests.post",
               return_value=fake_resp) as mp:
        r = b.generate(system="sys", prompt="prompt", max_output_tokens=100)
    mp.assert_called_once()
    call_url, = mp.call_args.args
    body = mp.call_args.kwargs["json"]
    headers = mp.call_args.kwargs["headers"]
    assert call_url.endswith("/chat/completions")
    assert body["model"] == "meta/llama-3.1-8b-instruct"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "prompt"}
    assert body["max_tokens"] == 100
    assert headers["Authorization"] == "Bearer test-key"
    assert r.text == "answer"
    assert r.input_tokens == 42
    assert r.output_tokens == 7
    assert r.backend == "nim"
    assert r.model == "meta/llama-3.1-8b-instruct"


def test_nim_http_error_raises(isolated):
    from axiom_event_token.backends import NIMBackend, BackendError
    b = NIMBackend(api_key="k")
    fake = MagicMock(ok=False, status_code=429, text="rate limited")
    with patch("axiom_event_token.backends.requests.post", return_value=fake):
        with pytest.raises(BackendError, match="429"):
            b.generate(system="s", prompt="p", max_output_tokens=10)


def test_nim_transport_error_raises(isolated):
    from axiom_event_token.backends import NIMBackend, BackendError
    import requests as _r
    b = NIMBackend(api_key="k")
    with patch("axiom_event_token.backends.requests.post",
               side_effect=_r.ConnectTimeout("boom")):
        with pytest.raises(BackendError, match="transport"):
            b.generate(system="s", prompt="p", max_output_tokens=10)


# ─── LocalNanoBackend ───────────────────────────────────────────────────


def test_local_parses_ollama_response_shape(isolated):
    from axiom_event_token.backends import LocalNanoBackend
    b = LocalNanoBackend(model="llama3.2:3b", url="http://orin:11434")
    fake = MagicMock(ok=True)
    fake.json.return_value = {
        "response":          "hello back",
        "prompt_eval_count": 18,
        "eval_count":        4,
    }
    with patch("axiom_event_token.backends.requests.post",
               return_value=fake) as mp:
        r = b.generate(system="be brief", prompt="say hi",
                       max_output_tokens=50)
    body = mp.call_args.kwargs["json"]
    assert body["model"] == "llama3.2:3b"
    assert "be brief" in body["prompt"]
    assert "say hi" in body["prompt"]
    assert body["options"]["num_predict"] == 50
    assert r.text == "hello back"
    assert r.input_tokens == 18
    assert r.output_tokens == 4
    assert r.backend == "local"
    assert r.model == "llama3.2:3b"


def test_local_http_error_raises(isolated):
    from axiom_event_token.backends import LocalNanoBackend, BackendError
    b = LocalNanoBackend(url="http://orin:11434")
    fake = MagicMock(ok=False, status_code=500, text="server")
    with patch("axiom_event_token.backends.requests.post", return_value=fake):
        with pytest.raises(BackendError, match="500"):
            b.generate(system="s", prompt="p", max_output_tokens=10)


# ─── ChainedBackend ─────────────────────────────────────────────────────


class _StubBackend:
    """In-test stub. Raises on first call if `raises` set."""
    def __init__(self, name, *, text="ok", raises=None):
        self.name = name
        self.model = f"stub-{name}"
        self._text = text
        self._raises = raises
        self.calls = 0

    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        self.calls += 1
        if self._raises:
            raise self._raises
        from axiom_event_token.backends import BackendResult
        return BackendResult(
            text=self._text, input_tokens=1, output_tokens=1,
            latency_ms=1, backend=self.name, model=self.model,
        )


def test_chained_uses_first_success(isolated):
    from axiom_event_token.backends import ChainedBackend
    a = _StubBackend("a", text="from-a")
    b = _StubBackend("b", text="from-b")
    chain = ChainedBackend([a, b])
    r = chain.generate(system="s", prompt="p", max_output_tokens=1)
    assert r.text == "from-a"
    assert r.backend == "a"
    assert a.calls == 1
    assert b.calls == 0


def test_chained_falls_through_on_failure(isolated):
    from axiom_event_token.backends import ChainedBackend, BackendError
    a = _StubBackend("a", raises=BackendError("down"))
    b = _StubBackend("b", text="from-b")
    chain = ChainedBackend([a, b])
    r = chain.generate(system="s", prompt="p", max_output_tokens=1)
    assert r.text == "from-b"
    assert r.backend == "b"
    assert a.calls == 1
    assert b.calls == 1


def test_chained_all_fail_raises(isolated):
    from axiom_event_token.backends import ChainedBackend, BackendError
    a = _StubBackend("a", raises=BackendError("a-down"))
    b = _StubBackend("b", raises=BackendError("b-down"))
    chain = ChainedBackend([a, b])
    with pytest.raises(BackendError, match="all 2"):
        chain.generate(system="s", prompt="p", max_output_tokens=1)


def test_chained_rejects_empty(isolated):
    from axiom_event_token.backends import ChainedBackend
    with pytest.raises(ValueError):
        ChainedBackend([])


# ─── default_backend() env resolution ───────────────────────────────────


def test_make_backend_single_local(isolated, monkeypatch):
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    from axiom_event_token.backends import make_backend, LocalNanoBackend
    b = make_backend(["local"])
    assert isinstance(b, LocalNanoBackend)


def test_make_backend_chain(isolated, monkeypatch):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "test-key")
    from axiom_event_token.backends import make_backend, ChainedBackend
    b = make_backend(["local", "nim"])
    assert isinstance(b, ChainedBackend)


def test_make_backend_unknown(isolated):
    from axiom_event_token.backends import make_backend, BackendError
    with pytest.raises(BackendError, match="unknown"):
        make_backend(["unknown_backend"])


def test_default_backend_respects_axiom_backend(isolated, monkeypatch):
    monkeypatch.setenv("AXIOM_BACKEND", "local")
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    from axiom_event_token.backends import default_backend, LocalNanoBackend
    assert isinstance(default_backend(), LocalNanoBackend)


def test_default_backend_picks_local_when_no_nim_key(isolated, monkeypatch):
    monkeypatch.delenv("AXIOM_BACKEND", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    from axiom_event_token.backends import default_backend, LocalNanoBackend
    assert isinstance(default_backend(), LocalNanoBackend)


# ─── DeepSeekBackend ─────────────────────────────────────────────────


def test_deepseek_requires_api_key(isolated, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from axiom_event_token.backends import DeepSeekBackend, BackendError
    with pytest.raises(BackendError, match="DEEPSEEK_API_KEY"):
        DeepSeekBackend()


def test_deepseek_defaults(isolated, monkeypatch):
    """Defaults: model=deepseek-chat, base_url=api.deepseek.com/v1."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    from axiom_event_token.backends import DeepSeekBackend
    b = DeepSeekBackend()
    assert b.name == "deepseek"
    assert b.model == "deepseek-chat"
    assert b._base_url == "https://api.deepseek.com/v1"


def test_deepseek_explicit_overrides_env(isolated, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    from axiom_event_token.backends import DeepSeekBackend
    b = DeepSeekBackend(
        api_key="sk-explicit",
        model="deepseek-reasoner",
        base_url="https://example.test/v1",
    )
    assert b._api_key == "sk-explicit"
    assert b.model == "deepseek-reasoner"
    assert b._base_url == "https://example.test/v1"


def test_make_backend_recognises_deepseek(isolated, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from axiom_event_token.backends import (
        make_backend, DeepSeekBackend,
    )
    b = make_backend(["deepseek"])
    assert isinstance(b, DeepSeekBackend)


def test_make_backend_chains_local_deepseek(isolated, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from axiom_event_token.backends import (
        make_backend, ChainedBackend,
        LocalNanoBackend, DeepSeekBackend,
    )
    b = make_backend(["local", "deepseek"])
    assert isinstance(b, ChainedBackend)
    assert isinstance(b._backends[0], LocalNanoBackend)
    assert isinstance(b._backends[1], DeepSeekBackend)


def test_default_backend_picks_deepseek_when_key_set_no_local(
    isolated, monkeypatch,
):
    """Unset AXIOM_BACKEND + OLLAMA_URL + NVIDIA_NIM_API_KEY,
    but DEEPSEEK_API_KEY set → DeepSeekBackend."""
    monkeypatch.delenv("AXIOM_BACKEND", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from axiom_event_token.backends import (
        default_backend, DeepSeekBackend,
    )
    assert isinstance(default_backend(), DeepSeekBackend)


def test_default_backend_local_still_wins_when_ollama_url_set(
    isolated, monkeypatch,
):
    """Local always wins when OLLAMA_URL is set, even if DeepSeek
    key is also configured — the user has signaled local intent."""
    monkeypatch.delenv("AXIOM_BACKEND", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from axiom_event_token.backends import (
        default_backend, LocalNanoBackend,
    )
    assert isinstance(default_backend(), LocalNanoBackend)
