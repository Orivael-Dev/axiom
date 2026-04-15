"""
AXIOM NIM Client
Thin wrapper around NVIDIA NIM inference (OpenAI-compatible API).
"""
import json
import os
import time

from openai import OpenAI


def _build_client() -> OpenAI:
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    base_url = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    if not api_key or api_key == "your_nvidia_api_key_here":
        raise EnvironmentError(
            "NVIDIA_API_KEY is not set. "
            "Edit .env and replace 'your_nvidia_api_key_here' with your real key from "
            "https://build.nvidia.com"
        )
    return OpenAI(api_key=api_key, base_url=base_url)


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def chat(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> str:
    """Single chat completion call. Returns the text content of the response."""
    client = get_client()
    resolved_model = model or os.environ.get(
        "AXIOM_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"
    )

    kwargs: dict = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    time.sleep(2)
    return response.choices[0].message.content or ""


def chat_json(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    temperature: float = 0.3,
) -> dict:
    """Chat completion that returns parsed JSON. Raises ValueError on parse failure."""
    raw = chat(
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        temperature=temperature,
        json_mode=True,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Attempt to extract JSON block if model wrapped it in markdown
        import re
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise ValueError(f"Model returned non-JSON output: {raw[:200]}") from exc
