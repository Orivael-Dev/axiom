"""SLM backends for modular event-token delegates.

Two real backends + a chain wrapper, picked by a delegate's
`backend_chain` field or an env override. Each backend speaks the
same `SLMBackend` protocol so delegate runtime never branches on
backend identity.

Environment:
  NVIDIA_NIM_API_KEY   — required for NIMBackend
  NIM_MODEL            — default "meta/llama-3.1-8b-instruct"
  NIM_BASE_URL         — default "https://integrate.api.nvidia.com/v1"
  OLLAMA_URL           — default "http://localhost:11434"
  OLLAMA_MODEL         — default "llama3.2:3b"
  AXIOM_BACKEND        — override: "nim" | "local" | "local,nim"

The local backend lifts its HTTP body from
`axiom_terminus._ollama_generate` — same proven shape, no
duplication of crypto or signing logic.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, Sequence

import requests


# ── Backend result ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackendResult:
    """One generation's transport-level facts."""
    text:           str
    input_tokens:   int
    output_tokens:  int
    latency_ms:     int
    backend:        str
    model:          str


class BackendError(RuntimeError):
    """Generation failed (timeout, connection error, non-2xx, malformed)."""


# ── Protocol ──────────────────────────────────────────────────────────────


class SLMBackend(Protocol):
    """Minimum contract every backend implements."""
    name: str
    model: str

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int,
        timeout_s: float = 60.0,
    ) -> BackendResult: ...


# ── NVIDIA NIM (hosted, OpenAI-compatible) ────────────────────────────────


class NIMBackend:
    """NVIDIA NIM API — OpenAI-compatible chat completions endpoint.

    Free tier available at build.nvidia.com (read NVIDIA_NIM_API_KEY
    from env). Token counts are reported by the API in the `usage`
    field, so no client-side estimation is needed.
    """
    name: str = "nim"

    def __init__(
        self,
        *,
        api_key:  Optional[str] = None,
        model:    Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("NVIDIA_NIM_API_KEY")
        if not key:
            raise BackendError(
                "NIMBackend requires NVIDIA_NIM_API_KEY (or api_key=)"
            )
        self._api_key  = key
        self.model     = model or os.environ.get(
            "NIM_MODEL", "meta/llama-3.1-8b-instruct"
        )
        self._base_url = (base_url or os.environ.get(
            "NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )).rstrip("/")

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int,
        timeout_s: float = 60.0,
    ) -> BackendResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "max_tokens":  int(max_output_tokens),
            "temperature": 0.3,
            "stream":      False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }
        t0 = time.monotonic()
        try:
            resp = requests.post(
                f"{self._base_url}/chat/completions",
                json=body, headers=headers, timeout=timeout_s,
            )
        except requests.RequestException as e:
            raise BackendError(f"NIM transport: {e}") from e
        latency_ms = int((time.monotonic() - t0) * 1000)
        if not resp.ok:
            raise BackendError(f"NIM HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
        except (ValueError, KeyError, IndexError) as e:
            raise BackendError(f"NIM malformed response: {e}") from e
        return BackendResult(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
            backend=self.name,
            model=self.model,
        )


# ── Local nano SLM (Ollama on Orin Nano or any host) ──────────────────────


class LocalNanoBackend:
    """Local Ollama-served SLM — body matches axiom_terminus._ollama_generate.

    Ollama returns `prompt_eval_count` + `eval_count` in its
    response, which we surface as input/output token counts.
    """
    name: str = "local"

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        url:   Optional[str] = None,
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
        self._url  = (url or os.environ.get(
            "OLLAMA_URL", "http://localhost:11434"
        )).rstrip("/")

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int,
        timeout_s: float = 60.0,
    ) -> BackendResult:
        body = {
            "model":  self.model,
            "prompt": f"{system}\n\n{prompt}",
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": int(max_output_tokens),
            },
        }
        t0 = time.monotonic()
        try:
            resp = requests.post(
                f"{self._url}/api/generate",
                json=body, timeout=timeout_s,
            )
        except requests.RequestException as e:
            raise BackendError(f"Ollama transport: {e}") from e
        latency_ms = int((time.monotonic() - t0) * 1000)
        if not resp.ok:
            raise BackendError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise BackendError(f"Ollama malformed JSON: {e}") from e
        return BackendResult(
            text=data.get("response", ""),
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            latency_ms=latency_ms,
            backend=self.name,
            model=self.model,
        )


# ── Chained fallback ──────────────────────────────────────────────────────


# ── DeepSeek API (hosted, OpenAI-compatible) ────────────────────────────


class DeepSeekBackend(NIMBackend):
    """DeepSeek's hosted chat-completions API — OpenAI-compatible.

    Default model `deepseek-chat` (DeepSeek-V3). Use `deepseek-reasoner`
    for R1. Get a key at platform.deepseek.com; pricing is roughly
    $0.14/M input + $0.28/M output at time of writing — usually
    cheaper than running the distilled models locally for low-volume
    use, while still keeping the per-call latency low because the
    endpoint is hosted.

    Reads from env:
      DEEPSEEK_API_KEY    — required (sk-...)
      DEEPSEEK_MODEL      — default "deepseek-chat"
      DEEPSEEK_BASE_URL   — default "https://api.deepseek.com/v1"
    """
    name: str = "deepseek"

    def __init__(
        self,
        *,
        api_key:  Optional[str] = None,
        model:    Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise BackendError(
                "DeepSeekBackend requires DEEPSEEK_API_KEY (or api_key=)"
            )
        # Skip parent __init__ (NIMBackend mandates NVIDIA key) — set
        # fields directly with DeepSeek defaults.
        self._api_key = key
        self.model = model or os.environ.get(
            "DEEPSEEK_MODEL", "deepseek-chat"
        )
        self._base_url = (base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )).rstrip("/")


# ── Bring-your-own OpenAI-compatible endpoint ───────────────────────────


class CustomBackend(NIMBackend):
    """Any OpenAI-compatible chat-completions endpoint.

    Pointed by three env vars (or constructor kwargs) — the SAME shape
    `axiom_constitutional.client` already understands, so the
    Exoskeleton + Medical Research tabs and the Prompt Evolution +
    DSL tabs end up reading the SAME LLM:

      AXIOM_BASE_URL   — required (e.g. https://openrouter.ai/api/v1)
      AXIOM_API_KEY    — required (anything non-empty for vLLM /
                         LM Studio)
      AXIOM_MODEL      — required (model identifier the endpoint
                         understands)

    The wire format must match OpenAI's standard chat-completions
    shape — `{choices: [{message: {content: ...}}], usage: {...}}`.
    99% of providers do; if yours doesn't, write a dedicated SLMBackend
    subclass instead.
    """
    name: str = "custom"

    def __init__(
        self,
        *,
        api_key:  Optional[str] = None,
        model:    Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("AXIOM_API_KEY")
        if not key:
            raise BackendError(
                "CustomBackend requires AXIOM_API_KEY (or api_key=). "
                "Use any non-empty string for endpoints that don't "
                "validate the key (e.g. LM Studio, vLLM)."
            )
        url = base_url or os.environ.get("AXIOM_BASE_URL")
        if not url:
            raise BackendError(
                "CustomBackend requires AXIOM_BASE_URL (or base_url=). "
                "Example: https://openrouter.ai/api/v1"
            )
        mdl = model or os.environ.get("AXIOM_MODEL")
        if not mdl:
            raise BackendError(
                "CustomBackend requires AXIOM_MODEL (or model=). "
                "Use the model identifier your endpoint expects."
            )
        self._api_key  = key
        self._base_url = url.rstrip("/")
        self.model     = mdl


class ChainedBackend:
    """Try each backend in order; first success wins.

    Falls through on BackendError only. The actual serving backend's
    `name` is preserved in the BackendResult so callers can record
    which one paid the bill.
    """
    name: str = "chain"

    def __init__(self, backends: Sequence[SLMBackend]) -> None:
        if not backends:
            raise ValueError("ChainedBackend needs at least one backend")
        self._backends = list(backends)
        self.model = "+".join(b.model for b in backends)

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int,
        timeout_s: float = 60.0,
    ) -> BackendResult:
        last_err: Optional[BackendError] = None
        for b in self._backends:
            try:
                return b.generate(
                    system=system, prompt=prompt,
                    max_output_tokens=max_output_tokens,
                    timeout_s=timeout_s,
                )
            except BackendError as e:
                last_err = e
                continue
        raise BackendError(
            f"all {len(self._backends)} backends failed; last: {last_err}"
        )


# ── Factory: env → backend ────────────────────────────────────────────────


_BACKEND_FACTORIES = {
    "nim":      lambda: NIMBackend(),
    "local":    lambda: LocalNanoBackend(),
    "deepseek": lambda: DeepSeekBackend(),
    "custom":   lambda: CustomBackend(),
}


def make_backend(chain: Iterable[str]) -> SLMBackend:
    """Build a backend from a chain spec like ('local',) or ('local','nim').

    Unknown names raise BackendError.
    """
    names = [n.strip().lower() for n in chain if n and n.strip()]
    if not names:
        raise BackendError("empty backend chain")
    built: List[SLMBackend] = []
    for n in names:
        if n not in _BACKEND_FACTORIES:
            raise BackendError(f"unknown backend: {n}")
        built.append(_BACKEND_FACTORIES[n]())
    if len(built) == 1:
        return built[0]
    return ChainedBackend(built)


def default_backend() -> SLMBackend:
    """Resolve the default backend from environment.

    AXIOM_BACKEND="nim"           → NIMBackend
    AXIOM_BACKEND="local"         → LocalNanoBackend
    AXIOM_BACKEND="deepseek"      → DeepSeekBackend
    AXIOM_BACKEND="local,deepseek"→ ChainedBackend (try local first,
                                                    fall back to DeepSeek)
    AXIOM_BACKEND="local,nim"     → ChainedBackend([local, nim])
    unset                         → "local" if OLLAMA_URL appears
                                    reachable in env;
                                    else "deepseek" if DEEPSEEK_API_KEY
                                    is set;
                                    else "nim"
    """
    spec = os.environ.get("AXIOM_BACKEND")
    if spec:
        return make_backend(spec.split(","))
    if os.environ.get("OLLAMA_URL") or not (
        os.environ.get("NVIDIA_NIM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    ):
        return LocalNanoBackend()
    if os.environ.get("DEEPSEEK_API_KEY"):
        return DeepSeekBackend()
    return NIMBackend()
