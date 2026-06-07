import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "motion/react";
import { fadeSlide } from "../motion";
import { api } from "../api";
import { speak, hasBrowserVoice } from "../voice";
import type {
  Weather, LlmSettings, LlmProbe, VoiceSettings, AnticipationSettings,
  PersonaToken, PersonaLineageEntry,
} from "../types";
import type { Theme } from "../theme";

type Panel = "clock" | "weather" | "settings";

// WMO weather-code → emoji. Day/night swap for clear-ish codes.
function icon(code: number | undefined, isDay: boolean): string {
  if (code == null) return "·";
  if (code === 0) return isDay ? "☀️" : "🌙";
  if (code <= 2) return isDay ? "🌤️" : "☁️";
  if (code === 3) return "☁️";
  if (code <= 48) return "🌫️";
  if (code <= 57) return "🌦️";
  if (code <= 67) return "🌧️";
  if (code <= 77) return "❄️";
  if (code <= 82) return "🌧️";
  if (code <= 86) return "🌨️";
  return "⛈️";
}

function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function useWeather(): { wx: Weather | null; loading: boolean } {
  const [wx, setWx] = useState<Weather | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    async function load(lat?: number, lon?: number) {
      try {
        const w = await api.weather(lat, lon);
        if (alive) setWx(w);
      } catch {
        if (alive) setWx({ ok: false, latitude: 0, longitude: 0 });
      } finally {
        if (alive) setLoading(false);
      }
    }
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => load(pos.coords.latitude, pos.coords.longitude),
        () => load(),
        { timeout: 5000, maximumAge: 600000 },
      );
    } else {
      load();
    }
    const id = setInterval(() => load(), 600000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return { wx, loading };
}

// ── overlay shell: one clicked widget, fullscreen via body portal ──────────
function Overlay(props: { title: string; onClose: () => void; children: ReactNode }) {
  return createPortal(
    <AnimatePresence>
      <motion.div
        className="widget-overlay"
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        onClick={props.onClose} role="dialog" aria-modal="true"
      >
        <motion.div
          className="widget-overlay__card"
          initial={{ scale: 0.92, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 24 }}
          onClick={(e) => e.stopPropagation()}
        >
          <button className="widget-overlay__close" onClick={props.onClose} aria-label="Close">×</button>
          {props.children}
        </motion.div>
      </motion.div>
    </AnimatePresence>,
    document.body,
  );
}

function ClockView({ now }: { now: Date }) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <>
      <div className="widget-overlay__clock">
        {now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
      </div>
      <div className="widget-overlay__date">
        {now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}
      </div>
      <div className="widget-overlay__tz">{tz}</div>
    </>
  );
}

function WeatherView({ wx }: { wx: Weather | null }) {
  const ok = !!(wx && wx.ok && wx.temperature_c != null);
  if (!ok) return <div className="widget-overlay__date muted">weather unavailable{wx?.error ? ` — ${wx.error}` : ""}</div>;
  return (
    <>
      <div className="widget-overlay__wx-icon widget-overlay__wx-icon--hero">{icon(wx!.code, wx!.is_day ?? true)}</div>
      <div className="widget-overlay__clock">{Math.round(wx!.temperature_c!)}°</div>
      <div className="widget-overlay__date">{wx!.description}</div>
      <div className="widget-overlay__tz">
        wind {Math.round(wx!.wind_kph ?? 0)} kph · {wx!.timezone ?? ""}
      </div>
    </>
  );
}

function SettingsView({ theme, onTheme }: { theme: Theme; onTheme: (t: Theme) => void }) {
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [probe, setProbe] = useState<LlmProbe | null>(null);
  const [busy, setBusy] = useState(false);
  const [voice, setVoice] = useState<VoiceSettings | null>(null);
  const [antic, setAntic] = useState<AnticipationSettings | null>(null);
  const [persona, setPersona] = useState<PersonaToken | null>(null);
  const [lineage, setLineage] = useState<PersonaLineageEntry[]>([]);

  useEffect(() => { api.getLlm().then(setLlm).catch(() => setLlm(null)); }, []);
  useEffect(() => { api.getVoice().then(setVoice).catch(() => setVoice(null)); }, []);
  useEffect(() => { api.getAnticipation().then(setAntic).catch(() => setAntic(null)); }, []);
  useEffect(() => {
    api.getPersona().then(setPersona).catch(() => setPersona(null));
    api.getPersonaLineage().then((r) => setLineage(r.lineage)).catch(() => setLineage([]));
  }, []);

  async function savePersona(patch: Partial<PersonaToken>) {
    const next = await api.setPersona(patch).catch(() => null);
    if (next) {
      setPersona(next);
      api.getPersonaLineage().then((r) => setLineage(r.lineage)).catch(() => {});
    }
  }

  function onImageFile(file?: File) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => savePersona({ self_image: String(reader.result) });
    reader.readAsDataURL(file);
  }

  async function saveVoice(patch: Partial<VoiceSettings>) {
    const next = await api.setVoice(patch).catch(() => null);
    if (next) setVoice(next);
  }

  async function saveAntic(patch: Partial<AnticipationSettings>) {
    const next = await api.setAnticipation(patch).catch(() => null);
    if (next) setAntic(next);
  }

  async function save(patch: Partial<LlmSettings> & { api_key?: string }) {
    setBusy(true);
    try {
      const next = await api.setLlm(patch);
      setLlm(next);
      if (patch.api_key) setApiKey("");
    } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true);
    try { setProbe(await api.testLlm()); } finally { setBusy(false); }
  }

  return (
    <div className="settings">
      <h2 className="settings__title">Settings</h2>

      <section className="settings__group">
        <div className="settings__label">Persona (Aria)</div>
        <p className="settings__hint">
          Her signed identity. Name, backstory and self-image are the <em>soul</em>
          (changing them re-roots her); base model and voice are the <em>outfit</em>.
        </p>
        <div className="settings__persona-head">
          {persona?.self_image
            ? <img className="settings__face" src={persona.self_image} alt={persona.name} />
            : <span className="settings__face settings__face--empty">✦</span>}
          <label className="btn">
            Set image
            <input type="file" accept="image/*" style={{ display: "none" }}
                   onChange={(e) => onImageFile(e.target.files?.[0])} />
          </label>
        </div>
        <label className="settings__field">
          <span>Name</span>
          <input value={persona?.name ?? ""}
                 onChange={(e) => setPersona(persona ? { ...persona, name: e.target.value } : persona)}
                 onBlur={(e) => savePersona({ name: e.target.value })} />
        </label>
        <label className="settings__field">
          <span>Backstory</span>
          <textarea rows={4} value={persona?.backstory ?? ""}
                    onChange={(e) => setPersona(persona ? { ...persona, backstory: e.target.value } : persona)}
                    onBlur={(e) => savePersona({ backstory: e.target.value })} />
        </label>
        <label className="settings__field">
          <span>Self-image caption <span className="muted">(grounds the text model)</span></span>
          <input value={persona?.image_caption ?? ""}
                 placeholder="a calm woman with violet eyes"
                 onChange={(e) => setPersona(persona ? { ...persona, image_caption: e.target.value } : persona)}
                 onBlur={(e) => savePersona({ image_caption: e.target.value })} />
        </label>
        <label className="settings__field">
          <span>Base model <span className="muted">(~1B; the outfit)</span></span>
          <input value={persona?.base_model ?? ""}
                 placeholder="llama3.2:1b"
                 onChange={(e) => setPersona(persona ? { ...persona, base_model: e.target.value } : persona)}
                 onBlur={(e) => savePersona({ base_model: e.target.value })} />
        </label>
        {persona && (
          <p className="settings__hint">
            identity <code>{persona.identity_signature.slice(0, 12)}…</code> · token{" "}
            <code>{persona.token_signature.slice(0, 12)}…</code>
          </p>
        )}
        {lineage.length > 1 && (
          <details className="settings__lineage">
            <summary>identity lineage · {lineage.length}</summary>
            <ul>
              {lineage.map((l, i) => (
                <li key={i}>
                  <code>{(l.identity_signature || "").slice(0, 10)}…</code>
                  {l.current ? " · current" : ` · ${l.at}`}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="settings__group">
        <div className="settings__label">Theme</div>
        <div className="settings__themes">
          {(["studio", "calm"] as Theme[]).map((t) => (
            <button
              key={t}
              className={`settings__theme ${theme === t ? "is-active" : ""}`}
              onClick={() => onTheme(t)}
            >
              {t === "studio" ? "🌙 Studio" : "☀ Calm"}
            </button>
          ))}
        </div>
      </section>

      <section className="settings__group">
        <div className="settings__row">
          <div className="settings__label">Local LLM</div>
          <label className="settings__switch">
            <input
              type="checkbox"
              checked={!!llm?.enabled}
              disabled={busy || !llm}
              onChange={(e) => save({ enabled: e.target.checked })}
            />
            <span>{llm?.enabled ? "on" : "off"}</span>
          </label>
        </div>
        <p className="settings__hint">
          OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp, vLLM). When on,
          it lays out the workspace instead of Claude.
        </p>

        <label className="settings__field">
          <span>Base URL</span>
          <input
            value={llm?.base_url ?? ""}
            placeholder="http://localhost:11434/v1"
            onChange={(e) => setLlm(llm ? { ...llm, base_url: e.target.value } : llm)}
            onBlur={(e) => llm && save({ base_url: e.target.value })}
          />
        </label>

        <label className="settings__field">
          <span>Model</span>
          <input
            value={llm?.model ?? ""}
            placeholder="llama3.2"
            onChange={(e) => setLlm(llm ? { ...llm, model: e.target.value } : llm)}
            onBlur={(e) => llm && save({ model: e.target.value })}
          />
        </label>

        <label className="settings__field">
          <span>Embedding model <span className="muted">(optional — latent curiosity)</span></span>
          <input
            value={llm?.embed_model ?? ""}
            placeholder="nomic-embed-text"
            onChange={(e) => setLlm(llm ? { ...llm, embed_model: e.target.value } : llm)}
            onBlur={(e) => llm && save({ embed_model: e.target.value })}
          />
        </label>

        <label className="settings__field">
          <span>API key {llm?.api_key_set ? "(set)" : "(optional)"}</span>
          <div className="settings__keyrow">
            <input
              type="password" value={apiKey} placeholder="—"
              onChange={(e) => setApiKey(e.target.value)}
            />
            <button className="btn" disabled={busy || !apiKey} onClick={() => save({ api_key: apiKey })}>
              Save
            </button>
          </div>
        </label>

        <div className="settings__testrow">
          <button className="btn btn--accent" disabled={busy} onClick={test}>
            {busy ? "…" : "Test connection"}
          </button>
          {probe && (
            <span className={`settings__probe ${probe.ok ? "is-ok" : "is-bad"}`}>
              {probe.ok
                ? `✓ reachable${probe.model_present ? ` · ${probe.model} found` : ` · ${probe.model} not pulled`}`
                : `✕ ${probe.error ?? "unreachable"}`}
            </span>
          )}
        </div>
      </section>

      <section className="settings__group">
        <div className="settings__row">
          <div className="settings__label">Voice (Aria)</div>
          <label className="settings__switch">
            <input
              type="checkbox"
              checked={!!voice?.enabled}
              disabled={!voice}
              onChange={(e) => saveVoice({ enabled: e.target.checked })}
            />
            <span>{voice?.enabled ? "on" : "off"}</span>
          </label>
        </div>
        <p className="settings__hint">
          Speak Aria's replies. Browser uses on-device OS voices; Piper/cloud
          stream audio from a TTS server. Voice input (STT) is coming.
        </p>
        <label className="settings__field">
          <span>Engine</span>
          <select
            value={voice?.engine ?? "browser"}
            onChange={(e) => saveVoice({ engine: e.target.value })}
          >
            <option value="browser">Browser (on-device)</option>
            <option value="piper">Piper (local server)</option>
            <option value="cloud">Cloud</option>
          </select>
        </label>
        <div className="settings__testrow">
          <button
            className="btn btn--accent"
            onClick={() => speak("Hi, I'm Aria. This is how I sound.",
                                 voice?.engine ?? "browser", voice?.rate ?? 1)}
          >
            🔊 Test voice
          </button>
          {!hasBrowserVoice() && (voice?.engine ?? "browser") === "browser" && (
            <span className="settings__probe is-bad">✕ no on-device voice here — use Piper</span>
          )}
        </div>
      </section>

      <section className="settings__group">
        <div className="settings__row">
          <div className="settings__label">Anticipation</div>
          <label className="settings__switch">
            <input
              type="checkbox"
              checked={!!antic?.enabled}
              disabled={!antic}
              onChange={(e) => saveAntic({ enabled: e.target.checked })}
            />
            <span>{antic?.enabled ? "on" : "off"}</span>
          </label>
        </div>
        <p className="settings__hint">
          When Aria has seen enough of your conversation to predict it reliably,
          she starts acting on it (offering to look things up, slowing down). Tune
          how much trust she needs first.
        </p>
        {([
          ["min_obs", "Observations before acting", 1, 50, 1],
          ["min_confidence", "Confidence floor", 0, 1, 0.05],
          ["min_hit_rate", "Accuracy floor", 0, 1, 0.05],
          ["cooldown", "Turns between nudges", 1, 20, 1],
        ] as const).map(([key, label, min, max, step]) => (
          <label className="settings__field" key={key}>
            <span>{label}: <strong>{antic ? antic[key] : "—"}</strong></span>
            <input
              type="range" min={min} max={max} step={step}
              value={antic ? Number(antic[key]) : min}
              disabled={!antic}
              onChange={(e) => setAntic(antic ? { ...antic, [key]: Number(e.target.value) } : antic)}
              onMouseUp={(e) => saveAntic({ [key]: Number((e.target as HTMLInputElement).value) })}
            />
          </label>
        ))}
      </section>
    </div>
  );
}

export function StatusStrip({ theme, onTheme }: { theme: Theme; onTheme: (t: Theme) => void }) {
  const now = useClock();
  const { wx, loading } = useWeather();
  const [open, setOpen] = useState<Panel | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(null); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  const hasWx = !!(wx && wx.ok && wx.temperature_c != null);
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  return (
    <>
      <motion.div className="statusstrip" variants={fadeSlide} initial="hidden" animate="visible">
        <button type="button" className="widget widget--weather" onClick={() => setOpen("weather")} title="Weather">
          {loading ? (
            <span className="muted">weather…</span>
          ) : hasWx ? (
            <>
              <span className="widget__wx-icon">{icon(wx!.code, wx!.is_day ?? true)}</span>
              <span className="widget__temp">{Math.round(wx!.temperature_c!)}°</span>
              <span className="widget__sub">{wx!.description}</span>
            </>
          ) : (
            <span className="muted" title={wx?.error ?? ""}>weather —</span>
          )}
        </button>

        <button type="button" className="widget widget--clock" onClick={() => setOpen("clock")} title="Clock">
          <span className="widget__time">{time}</span>
          <span className="widget__sub">
            {now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
          </span>
        </button>

        <button type="button" className="widget widget--settings" onClick={() => setOpen("settings")} title="Settings"
                aria-label="Settings">
          <span className="widget__gear">⚙</span>
        </button>
      </motion.div>

      {open === "clock" && <Overlay title="Clock" onClose={() => setOpen(null)}><ClockView now={now} /></Overlay>}
      {open === "weather" && <Overlay title="Weather" onClose={() => setOpen(null)}><WeatherView wx={wx} /></Overlay>}
      {open === "settings" && (
        <Overlay title="Settings" onClose={() => setOpen(null)}>
          <SettingsView theme={theme} onTheme={onTheme} />
        </Overlay>
      )}
    </>
  );
}
