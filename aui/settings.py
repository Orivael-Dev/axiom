"""
AX OS runtime settings — small JSON-backed config the service + planner read.
=============================================================================
Currently holds the local-LLM planner config. Persisted to AX_OS_SETTINGS
(default ``ax_os_settings.json``) so a choice survives restarts. Read on every
``get_planner()`` call, so toggling the local LLM takes effect immediately.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

_LOCK = threading.Lock()

_DEFAULT_LLM = {
    "enabled": False,
    "base_url": "http://localhost:11434/v1",  # Ollama's OpenAI-compatible API
    "model": "llama3.2",
    "api_key": "",
}

_DEFAULT_VOICE = {
    "enabled": False,
    "engine": "browser",                       # browser (Web Speech) | piper | cloud
    "voice": "alloy",                          # OpenAI-style voice name
    "model": "tts-1",                          # TTS model id
    "rate": 1.0,                               # 0.5–2.0 (sent as 'speed')
    "base_url": "http://localhost:8000/v1",    # OpenAI-compatible TTS (Piper via OpenedAI-speech)
    "api_key": "",                             # for cloud TTS (e.g. OpenAI)
}


def _path() -> str:
    return os.environ.get("AX_OS_SETTINGS", "ax_os_settings.json")


def load() -> dict:
    """Full settings dict, defaults merged in (always has an 'llm' block)."""
    data: dict[str, Any] = {}
    p = _path()
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    llm = {**_DEFAULT_LLM, **(data.get("llm") or {})}
    voice = {**_DEFAULT_VOICE, **(data.get("voice") or {})}
    return {**data, "llm": llm, "voice": voice}


def update_llm(patch: dict) -> dict:
    """Apply a partial LLM-config update and persist. Returns the full config."""
    with _LOCK:
        data = load()
        llm = data["llm"]
        for k in ("enabled", "base_url", "model", "api_key"):
            if k in patch and patch[k] is not None:
                llm[k] = patch[k]
        data["llm"] = llm
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        return data


def public_llm() -> dict:
    """LLM config safe to send to the UI — secrets redacted to a boolean."""
    llm = dict(load()["llm"])
    api_key = llm.pop("api_key", "")
    llm["api_key_set"] = bool(api_key)
    return llm


def update_voice(patch: dict) -> dict:
    """Apply a partial voice-config update and persist. Returns the full config."""
    with _LOCK:
        data = load()
        voice = data["voice"]
        for k in ("enabled", "engine", "voice", "model", "rate", "base_url", "api_key"):
            if k in patch and patch[k] is not None:
                voice[k] = patch[k]
        data["voice"] = voice
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        return data


def public_voice() -> dict:
    """Voice (TTS) config for the UI — cloud secret redacted to a boolean."""
    voice = dict(load()["voice"])
    api_key = voice.pop("api_key", "")
    voice["api_key_set"] = bool(api_key)
    return voice
