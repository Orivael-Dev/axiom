"""SLM backends for modular event-token delegates.

Two real backends + a chain wrapper, picked by a delegate's
`backend_chain` field or an env override. Each backend speaks the
same `SLMBackend` protocol so delegate runtime never branches on
backend identity.

Environment:
  NVIDIA_NIM_API_KEY   — required for NIMBackend
  NIM_MODEL            — default "meta/llama-3.1-8b-instruct"
  NIM_BASE_URL         — default "https://integrate.api.nvidia.com/v1"
  TRTLLM_URL           — local trtllm-serve endpoint (enables TRTLLMBackend)
  TRTLLM_MODEL         — model served by trtllm-serve
  OLLAMA_URL           — default "http://localhost:11434"
  OLLAMA_MODEL         — default "llama3.2:3b"
  AXIOM_BACKEND        — override: "nim" | "local" | "trtllm" | "local,nim"

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
    prefill_ms:     int = 0   # Ollama prompt_eval_duration / 1e6; 0 = not reported


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


# ── NVIDIA TensorRT-LLM (local trtllm-serve) ─────────────────────────────


class TRTLLMBackend(NIMBackend):
    """NVIDIA TensorRT-LLM local inference — drop-in for NIMBackend.

    ``trtllm-serve`` exposes the same OpenAI-compatible ``/v1/chat/completions``
    API as NVIDIA NIM, so the wire format is identical. The only differences
    are the endpoint URL, no API-key requirement, and the added
    ``generate_batch()`` method for parallel QRF branch execution.

    Quick-start (one command, no Dockerfile needed)::

        trtllm-serve meta/llama-3.1-8b-instruct --port 8000
        export TRTLLM_URL=http://localhost:8000/v1
        export TRTLLM_MODEL=meta/llama-3.1-8b-instruct
        export AXIOM_BACKEND=trtllm

    Drop-in switch: any code that currently calls ``NIMBackend.generate()``
    works unchanged with ``TRTLLMBackend.generate()``. The ``name`` field
    in ``BackendResult`` changes to ``"trtllm"`` so the exoskeleton ledger
    and RouterPolicy distinguish the two cleanly.

    **QRF parallel branches** — ``generate_batch(prompts, system=...)``::

        backend = TRTLLMBackend()
        results = backend.generate_batch(
            ["Branch A hypothesis", "Branch B hypothesis", ...],
            system=domain_system_prompt,
        )

    All prompts share the same ``system`` string.  TRT-LLM's in-flight
    batching schedules them on the GPU in one cycle; branches that share
    the same prefix (= the system prompt) reuse the same KV-cache blocks,
    so the shared prefix is prefilled exactly once regardless of branch
    count — the same saving the Trifecta preamble cache delivers, but
    native on GPU.
    """

    name: str = "trtllm"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model:    Optional[str] = None,
    ) -> None:
        url = (base_url or os.environ.get("TRTLLM_URL", "http://localhost:8000/v1")).rstrip("/")
        mdl = model    or os.environ.get("TRTLLM_MODEL", "meta/llama-3.1-8b-instruct")
        # Pass api_key="none" — trtllm-serve doesn't require auth on localhost.
        # NIMBackend.__init__ only checks for presence, not validity.
        super().__init__(api_key="none", base_url=url, model=mdl)

    # generate() is inherited from NIMBackend unchanged — same wire format.

    def generate_batch(
        self,
        prompts: List[str],
        *,
        system:            str   = "",
        max_output_tokens: int   = 256,
        timeout_s:         float = 60.0,
    ) -> List[BackendResult]:
        """Fire N prompts simultaneously against TRT-LLM.

        Uses a ``ThreadPoolExecutor`` so all branch HTTP requests are
        in-flight at the same time.  TRT-LLM's in-flight batcher then
        schedules them together on the GPU.  Branches sharing the same
        ``system`` string reuse the same KV-cache blocks — the shared
        prefix is computed once.

        Failed branches return an empty ``BackendResult`` (text="") rather
        than raising, so the caller always gets ``len(prompts)`` results
        and can filter on ``result.text``.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: List[Optional[BackendResult]] = [None] * len(prompts)
        with ThreadPoolExecutor(max_workers=len(prompts)) as pool:
            futures = {
                pool.submit(
                    self.generate,
                    system=system,
                    prompt=p,
                    max_output_tokens=max_output_tokens,
                    timeout_s=timeout_s,
                ): i
                for i, p in enumerate(prompts)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except BackendError:
                    results[idx] = BackendResult(
                        text="", input_tokens=0, output_tokens=0,
                        latency_ms=0, backend=self.name, model=self.model,
                    )
        # Return in original prompt order (futures resolve out-of-order)
        return [r for r in results if r is not None]


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
        # /api/chat sends system and user as named roles so Ollama can cache
        # the system tokens independently across requests.  keep_alive holds
        # the model (and its KV state) in memory between calls.
        body = {
            "model":      self.model,
            "messages":   [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "stream":     False,
            "keep_alive": "10m",
            "options":    {
                "temperature": 0.3,
                "num_predict": int(max_output_tokens),
            },
        }
        t0 = time.monotonic()
        try:
            resp = requests.post(
                f"{self._url}/api/chat",
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
        # prompt_eval_duration is nanoseconds; convert to ms for cache-hit detection
        prefill_ms = int(data.get("prompt_eval_duration", 0) / 1_000_000)
        return BackendResult(
            text=data.get("message", {}).get("content", ""),
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            latency_ms=latency_ms,
            backend=self.name,
            model=self.model,
            prefill_ms=prefill_ms,
        )


# ── Chained fallback ──────────────────────────────────────────────────────


# ── Subquadratic SubQ (hosted, OpenAI-compatible, 12M context) ───────────


class SubQBackend(NIMBackend):
    """Subquadratic SubQ API — 12M-token SSA context window.

    SubQ uses Subquadratic Selective Attention (SSA) internally: for each
    query token it selects the k most relevant key positions and computes
    exact attention only over those, giving O(n·k) rather than O(n²).
    From the API caller's perspective it is a standard OpenAI-compatible
    chat-completions endpoint — the SSA is transparent.

    The headline difference vs. every other backend: the context window is
    12 000 000 tokens.  This means a ``CosmosContextBuilder``-packed system
    prompt containing the full galaxy + planet + star + constellation layers
    (~100 documents) fits comfortably without chunking.  SubQ's SSA then
    selects which cross-document token relationships actually matter —
    exactly the application-layer equivalent of what the Semantic Cosmos
    model describes as "gravity pulling toward verified mass."

    Environment:
      SUBQ_API_KEY   — required (your subq.ai API key)
      SUBQ_MODEL     — default "subq-12m"
      SUBQ_BASE_URL  — default "https://api.subq.ai/v1"

    Reads from:
      AXIOM_BACKEND=subq            → auto-selected by default_backend()
      AXIOM_BACKEND_MEDICAL=subq    → route medical domain to SubQ
    """

    name: str = "subq"
    CONTEXT_WINDOW_TOKENS: int = 12_000_000

    def __init__(
        self,
        *,
        api_key:  Optional[str] = None,
        model:    Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("SUBQ_API_KEY")
        if not key:
            raise BackendError(
                "SubQBackend requires SUBQ_API_KEY (get one at subq.ai). "
                "Set AXIOM_BACKEND=subq and SUBQ_API_KEY=<key>."
            )
        self._api_key  = key
        self.model     = model or os.environ.get("SUBQ_MODEL", "subq-12m")
        self._base_url = (
            base_url
            or os.environ.get("SUBQ_BASE_URL", "https://api.subq.ai/v1")
        ).rstrip("/")


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
    "trtllm":   lambda: TRTLLMBackend(),
    "deepseek": lambda: DeepSeekBackend(),
    "custom":   lambda: CustomBackend(),
    "subq":     lambda: SubQBackend(),
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


# ── Per-domain routing ──────────────────────────────────────────────────
#
# The research console lets the user pick a domain (medical, finance,
# security, hr, supply_chain, general). Until now those all hit the
# same backend, which meant "Medical" picked the same off-the-shelf
# model as "Security" — the domain only affected QRF reasoning, not
# which LLM answered. DomainRoutedBackend fixes that: it holds a
# {domain: backend} map + a default fallback and dispatches based on
# a contextvar set by the request handler.
#
# Configure via env vars discovered by `default_backend()`:
#
#   AXIOM_BACKEND_MEDICAL=custom
#   AXIOM_BASE_URL_MEDICAL=https://medical-llm/v1
#   AXIOM_API_KEY_MEDICAL=...
#   AXIOM_MODEL_MEDICAL=meditron-70b
#
#   AXIOM_BACKEND_SECURITY=custom
#   AXIOM_BASE_URL_SECURITY=https://coder/v1
#   AXIOM_API_KEY_SECURITY=...
#   AXIOM_MODEL_SECURITY=qwen2.5-coder-32b
#
# Any domain without its own override falls through to the default
# (AXIOM_BACKEND / AXIOM_BASE_URL / etc.). The exoskeleton's signed
# token receipt still records which actual backend served the request
# because BackendResult carries `backend` + `model` per call.

import contextvars

# Request-scoped domain. The research server's `_run_research` sets
# this via `with domain_context(req.domain): exo.invoke(...)`. When
# DomainRoutedBackend.generate() runs inside that context it picks
# the right per-domain backend. Outside any context, falls through
# to the default.
_current_domain: "contextvars.ContextVar[Optional[str]]" = (
    contextvars.ContextVar("axiom_current_domain", default=None)
)


class _DomainContext:
    """Context manager that sets+restores the request-scoped domain.

    Use as `with domain_context("medical"): ...`. The contextvar API
    is also async-safe — fine inside FastAPI request handlers.
    """
    def __init__(self, domain: Optional[str]):
        self._domain = (domain or "").strip().lower() or None
        self._token: Optional[contextvars.Token] = None

    def __enter__(self):
        self._token = _current_domain.set(self._domain)
        return self

    def __exit__(self, *_exc):
        if self._token is not None:
            _current_domain.reset(self._token)


def domain_context(domain: Optional[str]) -> _DomainContext:
    """Set the request-scoped domain for the duration of a `with` block.

    Idiomatic use:

        with domain_context(req.domain):
            token = exo.invoke(delegate_name, req.query)

    Inside the block, any DomainRoutedBackend call will dispatch to
    the per-domain backend (or fall through to the default).
    """
    return _DomainContext(domain)


def current_domain() -> Optional[str]:
    """Return the request-scoped domain (lowercased) or None."""
    return _current_domain.get()


class DomainRoutedBackend:
    """Dispatch backend calls to the right per-domain LLM.

    Holds a `{domain: backend}` map + a default fallback. Reads the
    request-scoped domain from the `_current_domain` contextvar set
    by `domain_context()`. A request without a matching per-domain
    backend (or no contextvar set) goes to the default.
    """
    name: str = "domain-routed"

    def __init__(
        self,
        default: SLMBackend,
        per_domain: Optional[dict] = None,
    ) -> None:
        if default is None:
            raise ValueError("DomainRoutedBackend requires a default backend")
        self._default = default
        # Normalise keys to lowercase so dispatch is case-insensitive.
        self._per_domain = {
            k.lower(): v for k, v in (per_domain or {}).items() if v is not None
        }
        # Surface the routing summary as the .model string so the
        # /api/health endpoint can show what's wired without leaking
        # the full backend map.
        parts = [f"default={default.model}"]
        for d, b in sorted(self._per_domain.items()):
            parts.append(f"{d}={b.model}")
        self.model = " · ".join(parts)

    def _resolve(self) -> SLMBackend:
        d = current_domain()
        if d and d in self._per_domain:
            return self._per_domain[d]
        return self._default

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int,
        timeout_s: float = 60.0,
    ) -> BackendResult:
        return self._resolve().generate(
            system=system, prompt=prompt,
            max_output_tokens=max_output_tokens,
            timeout_s=timeout_s,
        )


# Domains the research console supports. Used by default_backend() to
# discover AXIOM_BACKEND_<DOMAIN> env vars without scanning the whole
# environment. Keep in sync with _DOMAIN_LABELS in
# axiom_research_server.py.
ROUTED_DOMAINS = ("general", "medical", "finance", "security", "hr",
                  "supply_chain")


def _build_domain_backend(domain: str) -> Optional[SLMBackend]:
    """Build a backend from AXIOM_*_<DOMAIN> env vars, or None if no
    per-domain override is configured.

    Looks for AXIOM_BACKEND_<DOMAIN>; if set, builds that backend with
    domain-suffixed config env vars falling back to the bare-named
    ones when a suffix is missing.

    Shadows these env vars so backend constructors (which read the
    bare names) see the per-domain values:
      OpenAI-shape (custom, deepseek, nim):
        AXIOM_API_KEY / AXIOM_BASE_URL / AXIOM_MODEL
      Ollama (local):
        OLLAMA_MODEL / OLLAMA_URL
      NIM (specifically NVIDIA NIM):
        NVIDIA_NIM_API_KEY / NIM_BASE_URL / NIM_MODEL
        — every NIM dimension is independently per-domain shadowable
        so an operator can wire separate NIM endpoints with separate
        keys (e.g. a security-tuned NIM at one tenant and a
        medically-tuned NIM at another).

    Use:
      AXIOM_BACKEND_MEDICAL=local
      OLLAMA_MODEL_MEDICAL=meditron:70b      # different local model
      OLLAMA_URL_MEDICAL=http://gpu-box:11434   # or different Ollama host

      AXIOM_BACKEND_SECURITY=nim
      NIM_MODEL_SECURITY=qwen2.5-coder-32b
      NVIDIA_NIM_API_KEY_SECURITY=nvapi-...   # separate key OK
      NIM_BASE_URL_SECURITY=https://my-nim/v1  # separate endpoint OK
    """
    suffix = domain.upper()
    spec = os.environ.get(f"AXIOM_BACKEND_{suffix}")
    if not spec:
        return None

    # Temporarily shadow the bare env vars with the domain-suffixed
    # ones so each backend's __init__ reads the right values.
    shadowed_keys = (
        "AXIOM_API_KEY", "AXIOM_BASE_URL", "AXIOM_MODEL",
        "OLLAMA_MODEL", "OLLAMA_URL",
        "NIM_MODEL", "NIM_BASE_URL", "NVIDIA_NIM_API_KEY",
    )
    original = {k: os.environ.get(k) for k in shadowed_keys}
    try:
        for k in shadowed_keys:
            v = os.environ.get(f"{k}_{suffix}")
            if v is not None:
                os.environ[k] = v
        return make_backend(spec.split(","))
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _custom_backend_env_complete() -> bool:
    """Are all three CustomBackend env vars set?

    AXIOM_BASE_URL + AXIOM_API_KEY + AXIOM_MODEL together constitute
    an explicit opt-in to a user-configured OpenAI-compatible endpoint
    — used by default_backend() to prefer CustomBackend over the
    auto-detected fallbacks (NIM / Ollama / DeepSeek) when the user
    has clearly configured one but forgot AXIOM_BACKEND=custom.
    """
    return all(
        os.environ.get(k, "").strip()
        for k in ("AXIOM_BASE_URL", "AXIOM_API_KEY", "AXIOM_MODEL")
    )


def default_backend() -> SLMBackend:
    """Resolve the default backend from environment.

    AXIOM_BACKEND="nim"           → NIMBackend (hosted NVIDIA NIM API)
    AXIOM_BACKEND="trtllm"        → TRTLLMBackend (local trtllm-serve)
    AXIOM_BACKEND="local"         → LocalNanoBackend (Ollama)
    AXIOM_BACKEND="deepseek"      → DeepSeekBackend
    AXIOM_BACKEND="custom"        → CustomBackend
    AXIOM_BACKEND="subq"          → SubQBackend (12M-token SSA context)
    AXIOM_BACKEND="local,trtllm"  → ChainedBackend (Ollama → TRT-LLM)
    AXIOM_BACKEND="trtllm,nim"    → ChainedBackend (local → hosted fallback)
    AXIOM_BACKEND="local,nim"     → ChainedBackend([local, nim])
    unset                         → SubQBackend   if SUBQ_API_KEY set;
                                    CustomBackend if AXIOM_BASE_URL +
                                    AXIOM_API_KEY + AXIOM_MODEL are
                                    all set;
                                    TRTLLMBackend if TRTLLM_URL is set
                                    (local GPU takes priority over Ollama);
                                    LocalNanoBackend if OLLAMA_URL set or
                                    no cloud key present;
                                    DeepSeekBackend if DEEPSEEK_API_KEY;
                                    else NIMBackend

    The CustomBackend auto-detection is the fix for the silent-NIM-
    fallback bug: previously, configuring AXIOM_BASE_URL/AXIOM_API_KEY/
    AXIOM_MODEL did nothing unless you ALSO set AXIOM_BACKEND=custom,
    and if NVIDIA_NIM_API_KEY was set in the same env (common on dev
    boxes) the resolver picked NIM and ignored your custom endpoint.

    Per-domain overrides (any subset of ROUTED_DOMAINS):
      AXIOM_BACKEND_MEDICAL=custom + AXIOM_BASE_URL_MEDICAL=...
      AXIOM_BACKEND_SECURITY=custom + AXIOM_BASE_URL_SECURITY=...
      etc.

    When ANY AXIOM_BACKEND_<DOMAIN> is set, the resolver wraps the
    default in a DomainRoutedBackend so calls under `domain_context()`
    dispatch to the per-domain backend.
    """
    spec = os.environ.get("AXIOM_BACKEND")
    if spec:
        base = make_backend(spec.split(","))
    elif os.environ.get("SUBQ_API_KEY"):
        base = SubQBackend()
    elif _custom_backend_env_complete():
        base = CustomBackend()
    elif os.environ.get("TRTLLM_URL"):
        base = TRTLLMBackend()
    elif os.environ.get("OLLAMA_URL") or not (
        os.environ.get("NVIDIA_NIM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    ):
        base = LocalNanoBackend()
    elif os.environ.get("DEEPSEEK_API_KEY"):
        base = DeepSeekBackend()
    else:
        base = NIMBackend()

    # Build any per-domain overrides; wrap in DomainRoutedBackend if
    # at least one is set, otherwise return the plain default.
    per_domain: dict = {}
    for d in ROUTED_DOMAINS:
        b = _build_domain_backend(d)
        if b is not None:
            per_domain[d] = b
    if per_domain:
        return DomainRoutedBackend(default=base, per_domain=per_domain)
    return base
