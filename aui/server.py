"""
AX OS local service — the on-device API the AUI front-end talks to.
==================================================================
A thin FastAPI app that holds one long-lived ``AxiomBridge`` and exposes
the workspace + marketplace surfaces as JSON over localhost. Local-first
by default: the front-end (Streamlit / desktop) calls these endpoints; the
bridge speaks MCP to the Axiom trust layer underneath.

    uvicorn-style:  python -m aui.server         # binds 127.0.0.1:8800

``create_app(bridge)`` is dependency-injected so it can be tested with a
fake bridge and no network.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from workspace.assembler import open_workspace
from marketplace import AgentStore, AgentRunner
from aui.plan import build_plan
from aui.planner_claude import get_planner, claude_suggest
from aui.panels import fill_plan


class GoalReq(BaseModel):
    goal: str
    domain: Optional[str] = None


class ManifestReq(BaseModel):
    manifest: dict


class PairReq(BaseModel):
    pair_id: str
    actor: str = "human"


class RunReq(BaseModel):
    pair_id: str
    action: str
    agent: str = ""


class ImmuneReq(BaseModel):
    payload: str
    vector: Optional[str] = None


class MkbRegisterReq(BaseModel):
    spec_content: str


class LlmReq(BaseModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    embed_model: Optional[str] = None
    api_key: Optional[str] = None


class CompanionReq(BaseModel):
    text: str
    reset: bool = False


class PersonaReq(BaseModel):
    name: Optional[str] = None
    backstory: Optional[str] = None
    self_image: Optional[str] = None
    image_caption: Optional[str] = None
    base_model: Optional[str] = None
    voice: Optional[str] = None


class VoiceReq(BaseModel):
    enabled: Optional[bool] = None
    engine: Optional[str] = None
    voice: Optional[str] = None
    rate: Optional[float] = None
    base_url: Optional[str] = None


class TtsReq(BaseModel):
    text: str


class AnticipationReq(BaseModel):
    enabled: Optional[bool] = None
    min_obs: Optional[int] = None
    min_confidence: Optional[float] = None
    min_hit_rate: Optional[float] = None
    cooldown: Optional[int] = None


# ── weather widget (Open-Meteo — keyless, cached) ────────────────
_WEATHER_CACHE: dict[str, tuple[float, dict]] = {}
_WEATHER_TTL = 600.0  # seconds


def _default_latlon() -> tuple[float, float]:
    """AX_OS_WEATHER_LATLON='lat,lon' sets the fallback location (default London)."""
    raw = os.environ.get("AX_OS_WEATHER_LATLON", "51.51,-0.13")
    try:
        lat, lon = (float(x) for x in raw.split(",", 1))
        return lat, lon
    except (ValueError, TypeError):
        return 51.51, -0.13


_DEFAULT_LAT, _DEFAULT_LON = _default_latlon()

# WMO weather-code → short label, matching the widget's icon map.
_WMO = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers",
    81: "Rain showers", 82: "Violent showers", 95: "Thunderstorm",
    96: "Thunderstorm + hail", 99: "Thunderstorm + hail",
}


def _fetch_weather(lat: float, lon: float) -> dict:
    """Fetch current conditions from Open-Meteo, cached per rounded lat/lon."""
    key = f"{lat:.2f},{lon:.2f}"
    now = time.time()
    hit = _WEATHER_CACHE.get(key)
    if hit and now - hit[0] < _WEATHER_TTL:
        return hit[1]
    qs = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code,wind_speed_10m,is_day",
        "timezone": "auto",
    })
    url = f"https://api.open-meteo.com/v1/forecast?{qs}"
    with urllib.request.urlopen(url, timeout=6) as resp:  # noqa: S310 (https only)
        raw = json.loads(resp.read().decode("utf-8"))
    cur = raw.get("current", {})
    code = int(cur.get("weather_code", 0))
    out = {
        "ok": True,
        "latitude": raw.get("latitude", lat),
        "longitude": raw.get("longitude", lon),
        "temperature_c": cur.get("temperature_2m"),
        "wind_kph": cur.get("wind_speed_10m"),
        "is_day": bool(cur.get("is_day", 1)),
        "code": code,
        "description": _WMO.get(code, "Unknown"),
        "timezone": raw.get("timezone", "UTC"),
        "updated": cur.get("time"),
    }
    _WEATHER_CACHE[key] = (now, out)
    return out


def create_app(bridge: Any, *, repo: Optional[str] = None):
    """Build the FastAPI app over an already-started bridge.

    ``repo`` is the workspace the file/branch/tests/docs panels read from
    (defaults to AX_OS_REPO, then the current directory)."""
    repo = repo or os.environ.get("AX_OS_REPO", ".")
    app = FastAPI(title="AX OS", version="0.1.0")

    # Local-first: the desktop webview (tauri://, http://localhost:1420) and the
    # browser dev server call this service cross-origin. Allow localhost + tauri.
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost)$",
        allow_methods=["*"], allow_headers=["*"],
    )

    store = AgentStore(bridge)
    runner = AgentRunner(bridge)

    from aui.companion import build_companion
    from aui.persona import PersonaStore, public_persona
    persona_store = PersonaStore()
    companion = build_companion(bridge)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "tools": bridge.list_tools()}

    @app.post("/assemble")
    def assemble(req: GoalReq) -> dict:
        ws = open_workspace(bridge, req.goal, domain=req.domain)
        bridge.log_event("workspace_opened" if ws.allowed else "workspace_refused",
                         actor="ax-os.aui", subject=req.goal,
                         outcome="allowed" if ws.allowed else (ws.refusal or "blocked"))
        suggest = get_planner()
        plan = build_plan(ws, domain=req.domain, suggest=suggest)
        fill_plan(plan, repo=repo, bridge=bridge)
        out = plan.to_dict()
        # cloud == the goal text was sent to Claude; local == stayed on-device
        # (local LLM or rule planner).
        out["planner"] = "cloud" if suggest is claude_suggest else "local"
        return out

    @app.post("/marketplace/install")
    def install(req: ManifestReq) -> dict:
        return store.install_for_review(req.manifest).__dict__

    @app.post("/marketplace/approve")
    def approve(req: PairReq) -> dict:
        return store.approve(req.pair_id, actor=req.actor)

    @app.post("/marketplace/revoke")
    def revoke(req: PairReq) -> dict:
        return store.revoke(req.pair_id, actor=req.actor)

    @app.post("/marketplace/run")
    def run(req: RunReq) -> dict:
        return runner.run_action(req.pair_id, req.action, agent=req.agent).to_dict()

    @app.get("/marketplace/agents")
    def agents() -> dict:
        # Reconstruct installed agents (pair_id + name) from the signed audit
        # ledger, then ask the marketplace for each one's live authority state.
        seen: dict = {}
        for e in bridge.audit_list(limit=200).get("events", []):
            if not str(e.get("event_type", "")).startswith("agent_"):
                continue
            pid = (e.get("attributes") or {}).get("pair_id")
            if pid and pid not in seen:
                seen[pid] = e.get("subject", "")
        out = []
        for pid, name in seen.items():
            a = bridge.mkt_authority(pid)
            out.append({"agent": name, "pair_id": pid,
                        "authorized": bool(a.get("authorized")), "state": a.get("state", "")})
        return {"agents": out}

    @app.get("/audit")
    def audit(limit: int = 20) -> dict:
        return bridge.audit_list(limit=limit)

    # ── ORVL tool surfaces ───────────────────────────────────────
    @app.post("/immune/scan")
    def immune_scan(req: ImmuneReq) -> dict:
        """Screen content through the Constitutional Immune System (ORVL-012)."""
        res = bridge.immune_scan(req.payload, vector=req.vector)
        bridge.log_event("immune_scan", subject=(req.vector or "presented"),
                         outcome="detected" if res.get("detected") else "clean",
                         attributes={"method": res.get("detection_method")})
        return res

    @app.get("/mkb")
    def mkb_list(block_type: Optional[str] = None) -> dict:
        """List Modular Constitutional Knowledge Blocks (ORVL-004)."""
        return bridge.mkb_list(block_type=block_type)

    @app.post("/mkb/register")
    def mkb_register(req: MkbRegisterReq) -> dict:
        res = bridge.mkb_register(req.spec_content)
        if not res.get("error"):
            bridge.log_event("mkb_register", subject=res.get("name", ""),
                             outcome=res.get("block_type", ""),
                             attributes={"entry_id": res.get("entry_id")})
        return res

    # ── companion (à la "Her") ───────────────────────────────────
    @app.post("/companion/say")
    def companion_say(req: CompanionReq) -> dict:
        from aui.settings import load
        if req.reset:
            companion.reset()
        r = companion.say(req.text)
        bridge.log_event("companion_turn", subject=req.text[:80],
                         outcome="refused" if r.refused else "reply",
                         attributes={"intent": r.intent})
        voice = load()["voice"]
        mt = companion.master_token
        return {**r.to_dict(), "voice_enabled": bool(voice.get("enabled")),
                "voice_engine": voice.get("engine"), "turns": len(companion.history),
                "met_head": mt.head, "met_turns": len(mt.links),
                "anticipation": companion.anticipation}

    @app.post("/companion/listen")
    def companion_listen() -> dict:
        """Reserved voice-agent slot (voice input). Not wired in v1 — the seam's
        contract for a future TTS/voice agent is: audio -> transcribe -> screen the
        transcript through axiom_immune -> companion.say. Voice input is the
        safety-relevant half, so it lands behind this gate."""
        return {"ok": False, "reason": "stt_not_implemented",
                "contract": "audio -> transcript -> immune_scan -> companion.say"}

    # ── persona: Aria's signed identity (soul) + outfit + lineage ─
    @app.get("/companion/persona")
    def get_persona() -> dict:
        return public_persona(persona_store.load_or_mint())

    @app.post("/companion/persona")
    def set_persona(req: PersonaReq) -> dict:
        tok = persona_store.save(req.model_dump(exclude_none=True))
        companion.apply_persona(tok)   # re-grounds + new chain root iff identity changed
        bridge.log_event("persona_update", subject=tok.name,
                         outcome=tok.identity_signature[:16],
                         attributes={"token_sig": tok.token_signature[:16]})
        return public_persona(tok)

    @app.get("/companion/persona/lineage")
    def persona_lineage() -> dict:
        return {"lineage": persona_store.lineage()}

    # ── settings: voice (TTS) ────────────────────────────────────
    @app.get("/settings/voice")
    def get_voice_settings() -> dict:
        from aui.settings import public_voice
        return public_voice()

    @app.post("/settings/voice")
    def set_voice_settings(req: VoiceReq) -> dict:
        from aui.settings import update_voice, public_voice
        data = update_voice(req.model_dump(exclude_none=True))
        bridge.log_event("settings_voice_update",
                         outcome="enabled" if data["voice"]["enabled"] else "disabled",
                         attributes={"engine": data["voice"]["engine"]})
        return public_voice()

    @app.post("/tts")
    def tts(req: TtsReq) -> dict:
        """Server-side TTS for the piper/cloud engines (the browser engine speaks
        client-side and never calls this). Proxies an OpenAI-compatible
        /v1/audio/speech endpoint — local Piper (OpenedAI-speech) or cloud
        (OpenAI) — fails soft when unconfigured/unreachable. Returns base64 WAV."""
        from aui.settings import load
        import base64
        import urllib.request
        voice = load()["voice"]
        if voice.get("engine") == "browser":
            return {"ok": False, "reason": "browser_engine_speaks_client_side"}
        base = str(voice.get("base_url", "")).rstrip("/")
        body = json.dumps({
            "model": voice.get("model") or "tts-1",
            "input": req.text,
            "voice": voice.get("voice") or "alloy",
            "response_format": "wav",
            "speed": float(voice.get("rate") or 1.0),
        }).encode("utf-8")
        headers = {"content-type": "application/json"}
        if voice.get("api_key"):
            headers["authorization"] = f"Bearer {voice['api_key']}"
        try:
            r = urllib.request.Request(base + "/audio/speech", data=body, headers=headers)
            with urllib.request.urlopen(r, timeout=20) as resp:  # noqa: S310
                audio = resp.read()
            return {"ok": True, "engine": voice.get("engine"),
                    "audio_b64": base64.b64encode(audio).decode("ascii"), "mime": "audio/wav"}
        except Exception as e:
            return {"ok": False, "reason": f"{type(e).__name__}: {e}", "engine": voice.get("engine")}

    # ── web search (SearXNG) with an immune screen on results ────
    @app.get("/search")
    def web_search(q: str, n: int = 5, screen: bool = True) -> dict:
        """Open-source metasearch via SearXNG. Results are screened through the
        Constitutional Immune System (ORVL-012) unless screen=false."""
        from aui.websearch import search
        res = search(q, n=n, screen=(bridge.immune_scan if screen else None))
        if res.get("ok"):
            bridge.log_event("search", subject=q,
                             outcome=f"{res['returned']} results",
                             attributes={"blocked": res.get("blocked", 0),
                                         "engine": res.get("engine", "")})
        return res

    # ── settings: local-LLM planner backend ─────────────────────
    @app.get("/settings/llm")
    def get_llm_settings() -> dict:
        from aui.settings import public_llm
        return public_llm()

    @app.post("/settings/llm")
    def set_llm_settings(req: LlmReq) -> dict:
        from aui.settings import update_llm, public_llm
        data = update_llm(req.model_dump(exclude_none=True))
        bridge.log_event("settings_llm_update",
                         outcome="enabled" if data["llm"]["enabled"] else "disabled",
                         attributes={"model": data["llm"]["model"],
                                     "base_url": data["llm"]["base_url"]})
        return public_llm()

    @app.post("/settings/llm/test")
    def test_llm_settings() -> dict:
        from aui.planner_local import probe
        return probe()

    @app.get("/settings/anticipation")
    def get_anticipation_settings() -> dict:
        from aui.settings import public_anticipation
        return public_anticipation()

    @app.post("/settings/anticipation")
    def set_anticipation_settings(req: AnticipationReq) -> dict:
        from aui.settings import update_anticipation, public_anticipation
        update_anticipation(req.model_dump(exclude_none=True))
        return public_anticipation()

    # ── widgets ──────────────────────────────────────────────────
    @app.get("/widgets/time")
    def widget_time() -> dict:
        """Server clock — lets the widget show server time / timezone."""
        now = time.time()
        return {"epoch_ms": int(now * 1000),
                "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                "tz": time.strftime("%Z", time.localtime(now))}

    @app.get("/widgets/weather")
    def widget_weather(lat: float = _DEFAULT_LAT, lon: float = _DEFAULT_LON) -> dict:
        """Current conditions via Open-Meteo (keyless). Defaults to AX_OS_WEATHER_LATLON."""
        try:
            return _fetch_weather(lat, lon)
        except Exception as e:  # network off / upstream down — fail soft
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "latitude": lat, "longitude": lon}

    return app


def _bridge_from_env():
    from bridge import AxiomBridge
    env = {
        "AXIOM_MEMORY_STORE": os.environ.get("AXIOM_MEMORY_STORE", "ax_os_memory.jsonl"),
        "AXIOM_AUDIT_LEDGER": os.environ.get("AXIOM_AUDIT_LEDGER", "ax_os_audit.jsonl"),
        "AXIOM_MARKETPLACE_LEDGER": os.environ.get("AXIOM_MARKETPLACE_LEDGER",
                                                   "ax_os_marketplace.jsonl"),
    }
    repo = os.environ.get("AXIOM_REPO")
    if repo:
        return AxiomBridge(command=["python", "axiom_mcp_server.py"], cwd=repo, env=env)
    return AxiomBridge(env=env)


def main() -> None:
    import uvicorn
    # Anchor UI settings (LLM + voice) to a stable file so dev runs
    # (python -m aui.server) persist them too — matches the ledger defaults.
    os.environ.setdefault("AX_OS_SETTINGS", "ax_os_settings.json")
    bridge = _bridge_from_env()
    bridge.start()
    try:
        uvicorn.run(create_app(bridge),
                    host=os.environ.get("AX_OS_HOST", "127.0.0.1"),
                    port=int(os.environ.get("AX_OS_PORT", "8800")))
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
