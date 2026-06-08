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
    "embed_model": "",                        # e.g. nomic-embed-text → enables latent curiosity
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


_DEFAULT_VISION = {
    "enabled": False,
    "base_url": "http://localhost:11434/v1",   # Ollama's OpenAI-compatible API
    "model": "moondream",                      # a tiny local VLM — Aria's eyes
    "api_key": "",
}


_DEFAULT_LOCATION = {
    "name": "London",          # human label — set it / "ask" for a place by name
    "lat": 51.51,
    "lon": -0.13,
    "timezone": "Europe/London",
}


_DEFAULT_ANTICIPATION = {
    "enabled": True,
    "min_obs": 3,            # transitions to observe before acting
    "min_confidence": 0.6,   # predictor confidence floor
    "min_hit_rate": 0.6,     # proven accuracy floor
    "cooldown": 3,           # turns between proactive moves
}


def _ax_os_home() -> str:
    """Stable per-user state dir so settings survive a restart regardless of the
    directory AX OS was launched from. Override with AX_OS_HOME."""
    base = os.environ.get("AX_OS_HOME") or os.path.join(
        os.path.expanduser("~"), ".ax_os")
    os.makedirs(base, exist_ok=True)
    return base


_LEGACY_SETTINGS = "ax_os_settings.json"   # the old CWD-relative default


def _migrate_legacy(dest: str) -> None:
    """One-time, non-destructive copy of a legacy CWD-relative settings file into
    the stable home, so upgrading from the old default isn't seen as a reset.
    Skips if the home file already exists, no legacy file is present, or
    AX_OS_NO_MIGRATE is set (tests)."""
    if os.environ.get("AX_OS_NO_MIGRATE") or os.path.exists(dest):
        return
    try:
        if os.path.isfile(_LEGACY_SETTINGS):
            import shutil
            shutil.copy2(_LEGACY_SETTINGS, dest)
    except OSError:
        pass


def _path() -> str:
    # An explicit file wins; otherwise a stable home — not a CWD-relative file
    # that "resets" when the service starts from a different directory.
    explicit = os.environ.get("AX_OS_SETTINGS")
    if explicit:
        return explicit
    dest = os.path.join(_ax_os_home(), "settings.json")
    _migrate_legacy(dest)
    return dest


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
    vision = {**_DEFAULT_VISION, **(data.get("vision") or {})}
    location = {**_DEFAULT_LOCATION, **(data.get("location") or {})}
    antic = {**_DEFAULT_ANTICIPATION, **(data.get("anticipation") or {})}
    return {**data, "llm": llm, "voice": voice, "vision": vision,
            "location": location, "anticipation": antic}


def update_llm(patch: dict) -> dict:
    """Apply a partial LLM-config update and persist. Returns the full config."""
    with _LOCK:
        data = load()
        llm = data["llm"]
        for k in ("enabled", "base_url", "model", "embed_model", "api_key"):
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


def update_vision(patch: dict) -> dict:
    """Apply a partial vision-config update and persist. Returns the full config."""
    with _LOCK:
        data = load()
        vision = data["vision"]
        for k in ("enabled", "base_url", "model", "api_key"):
            if k in patch and patch[k] is not None:
                vision[k] = patch[k]
        data["vision"] = vision
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        return data


def public_vision() -> dict:
    """Vision (VLM) config for the UI — secret redacted to a boolean."""
    vision = dict(load()["vision"])
    api_key = vision.pop("api_key", "")
    vision["api_key_set"] = bool(api_key)
    return vision


def update_location(patch: dict) -> dict:
    """Set the location used for time & weather (name + lat/lon + timezone).
    Returns the full config."""
    with _LOCK:
        data = load()
        loc = data["location"]
        for k in ("name", "lat", "lon", "timezone"):
            if k in patch and patch[k] is not None:
                loc[k] = patch[k]
        data["location"] = loc
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        return data


def public_location() -> dict:
    """Location for the UI / widgets (no secrets)."""
    return dict(load()["location"])


def update_anticipation(patch: dict) -> dict:
    """Apply a partial anticipation-config update and persist."""
    with _LOCK:
        data = load()
        antic = data["anticipation"]
        for k in ("enabled", "min_obs", "min_confidence", "min_hit_rate", "cooldown"):
            if k in patch and patch[k] is not None:
                antic[k] = patch[k]
        data["anticipation"] = antic
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        return data


def public_anticipation() -> dict:
    return dict(load()["anticipation"])
