# Packaging AX OS as a one-click desktop app

Goal: a single installer that launches the window **and** auto-starts the
service — no Python, no separate terminal.

## Architecture

```
AX OS.app  (Tauri)
 ├─ webview → React shell  → http://127.0.0.1:8800
 └─ sidecar: ax-os-service (frozen)   ← Tauri spawns on startup
                 └─ spawns sidecar: axiom-mcp (frozen)   ← the Axiom trust layer
```

Two PyInstaller binaries are bundled as Tauri **sidecars**:
- **`axiom-mcp`** — the Axiom MCP server (JSON-RPC over stdio).
- **`ax-os-service`** — the FastAPI service (`aui.server`). On startup it finds
  the sibling `axiom-mcp` binary and points the bridge at it via
  `AX_OS_MCP_BIN` (see `packaging/service_main.py`), and stores ledgers + a
  per-install signing key under the OS user-data dir.

The enabler in product code is tiny and boundary-safe: `bridge/client.py`
honours `AX_OS_MCP_BIN` (an env-named command) — it still never imports Axiom.

## Build

From the **ax-os repo root**, with Python + Rust installed and a clone of
**Axiom main**:

```bash
export AXIOM_REPO=/path/to/axiom        # Windows: $env:AXIOM_REPO = "..."
bash packaging/build.sh                 # Windows: ./packaging/build.ps1
cd desktop && npm run tauri build
```

`build.sh` freezes both binaries (PyInstaller) and stages them as
`desktop/src-tauri/bin/{axiom-mcp,ax-os-service}-<target-triple>` (the naming
Tauri sidecars require).

> PyInstaller misses lazily-imported modules — if the frozen `axiom-mcp`
> errors with `ModuleNotFoundError`, add the module to `hiddenimports` in
> `packaging/axiom-mcp.spec` and rebuild. `torch` is excluded on purpose
> (the AUI tool surface doesn't need it).

## Tauri wiring — add these (kept out of your working config on purpose)

**1. `desktop/src-tauri/tauri.conf.json`** — add `externalBin` to `bundle`:
```jsonc
"bundle": {
  "active": true,
  "externalBin": ["bin/axiom-mcp", "bin/ax-os-service"],
  "icon": [ /* ...your icon set... */ ]
}
```

**2. `desktop/src-tauri/Cargo.toml`** — add the shell plugin:
```toml
[dependencies]
tauri = { version = "2", features = [] }
tauri-plugin-shell = "2"
```

**3. `desktop/src-tauri/capabilities/default.json`** — allow spawning the
service sidecar:
```jsonc
{
  "identifier": "default",
  "windows": ["main"],
  "permissions": [
    "core:default",
    { "identifier": "shell:allow-execute",
      "allow": [{ "name": "bin/ax-os-service", "sidecar": true, "args": true }] }
  ]
}
```

**4. `desktop/src-tauri/src/main.rs`** — spawn the service on setup:
```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use tauri_plugin_shell::ShellExt;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // ax-os-service finds the sibling axiom-mcp binary itself.
            let _ = app.shell().sidecar("ax-os-service")?.spawn()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the AX OS desktop shell");
}
```

> The exact `tauri-plugin-shell` API and the capability schema move between
> Tauri 2.x point releases — if `npm run tauri dev` complains, check the
> plugin docs for your version (`Command::new_sidecar` vs `shell().sidecar()`)
> and adjust the four snippets above. The Python side (`service_main.py`,
> specs, build scripts) is stable.

## Fallback (no Tauri integration)

The frozen binaries are useful on their own — a **Python-free service**:
```bash
./dist/ax-os-service            # serves :8800, spawns ./dist/axiom-mcp beside it
```
Run the desktop app pointing at it with `VITE_AX_OS_API` (default localhost),
or just use the existing two-terminal flow with `npm run tauri dev`.

## Verify
- `python packaging/service_main.py` (dev) → service on :8800 with ledgers in
  your user-data dir.
- `pytest tests/test_bridge_command.py` → bridge command-resolution (AX_OS_MCP_BIN).
- After a packaged build: launch the app — the window opens and `/health`
  returns 200 with no terminal open.

---

## Voice (TTS) for the companion

Aria can speak her replies. Toggle it with the 🔊 button in the chat or in
**⚙ Settings → Voice**, then click **🔊 Test voice** to confirm.

### Browser engine — default, nothing to install
On **Windows (WebView2)** and **macOS (WKWebView)** the webview ships the Web
Speech API, so the default `browser` engine just works — fully on-device, no
server. If Settings shows *"✕ no on-device voice here — use Piper"* (some Linux
WebKitGTK builds), use the local Piper engine below.

### Local neural voice (Piper) — one command
AX OS speaks via an **OpenAI-compatible** `/v1/audio/speech` endpoint, so any
such server works. The simplest is **OpenedAI-speech**, which wraps Piper:

```bash
docker run -d -p 8000:8000 ghcr.io/matatonic/openedai-speech
```

Then in **⚙ Settings → Voice**: set **Engine = Piper** (the default
`base_url` is already `http://localhost:8000/v1`) and click **Test voice**.

docker-compose:

```yaml
services:
  tts:
    image: ghcr.io/matatonic/openedai-speech
    ports: ["8000:8000"]
    restart: unless-stopped
```

### Cloud voice (optional)
Set **Engine = Cloud**, `base_url = https://api.openai.com/v1`, an API key, and
a `voice` (e.g. `alloy`). Same code path — note the reply text leaves the
machine (the ☁ tradeoff).

### How it routes
`POST /tts` proxies `{base_url}/audio/speech` with
`{model, input, voice, response_format: wav, speed}` and returns base64 WAV the
webview plays. It **fails soft** — if the TTS server is down, Aria still replies
in text; nothing crashes. `base_url`, `voice`, `model`, and `rate` live in the
persisted settings (`AX_OS_SETTINGS`).
