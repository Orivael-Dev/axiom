"""
Local-LLM workspace planner (OpenAI-compatible / Ollama).
=========================================================
Same job as ``planner_claude.claude_suggest`` — pick the workspace panels for
a goal — but against a local OpenAI-compatible endpoint (Ollama, LM Studio,
vLLM, llama.cpp server). Configured via ``aui.settings`` (the ⚙ widget).
stdlib-only (urllib), graceful fallback to the rule-based planner on any error
so AX OS stays local-first and never hard-fails on a model call.
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import List, Optional

from aui.plan import plan_panels
from aui.planner_claude import PANEL_VOCAB, SYSTEM, _validate

_INSTRUCT = (
    SYSTEM
    + '\n\nReturn ONLY a JSON object of the form {"panels": ["kind", ...]} '
    + "using kinds from the allowed list. No prose, no code fences."
)


def _extract_panels(content: str) -> List[str]:
    """Pull the panels list out of a model reply that may include prose/fences."""
    m = re.search(r"\{.*\}", content, re.DOTALL)
    raw = m.group(0) if m else content
    try:
        return json.loads(raw).get("panels", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def _post(cfg: dict, path: str, body: dict, timeout: float) -> dict:
    base = str(cfg["base_url"]).rstrip("/")
    headers = {"content-type": "application/json"}
    if cfg.get("api_key"):
        headers["authorization"] = f"Bearer {cfg['api_key']}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _call_local(cfg: dict, goal: str, domain: Optional[str]) -> List[str]:
    out = _post(cfg, "/chat/completions", {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _INSTRUCT},
            {"role": "user", "content": f"Goal: {goal}\nDomain: {domain or '(infer from the goal)'}"},
        ],
        "temperature": 0,
        "stream": False,
    }, timeout=20)
    content = out["choices"][0]["message"]["content"]
    return _extract_panels(content)


def local_suggest(goal: str, domain: Optional[str] = None) -> List[str]:
    """suggest hook for build_plan, backed by the configured local LLM."""
    from aui.settings import load
    cfg = load()["llm"]
    if not cfg.get("enabled"):
        return plan_panels(goal, domain)
    try:
        panels = _call_local(cfg, goal, domain)
    except Exception:
        return plan_panels(goal, domain)
    return _validate(panels, goal, domain)


def probe() -> dict:
    """Ping the configured endpoint's /models so the ⚙ UI can show connectivity."""
    from aui.settings import load
    cfg = load()["llm"]
    base = str(cfg["base_url"]).rstrip("/")
    headers = {}
    if cfg.get("api_key"):
        headers["authorization"] = f"Bearer {cfg['api_key']}"
    try:
        req = urllib.request.Request(base + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
            out = json.loads(resp.read().decode("utf-8"))
        models = [m.get("id") for m in out.get("data", []) if m.get("id")]
        return {"ok": True, "models": models[:40],
                "model_present": cfg["model"] in models, "model": cfg["model"]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "base_url": base, "model": cfg["model"]}
