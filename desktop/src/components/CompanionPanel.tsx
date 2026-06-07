import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { fadeSlide } from "../motion";
import { api } from "../api";
import { speak } from "../voice";
import type { VoiceSettings, PersonaToken } from "../types";

type Msg = { who: "you" | "aria"; text: string; refused?: boolean };

export function CompanionPanel() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [persona, setPersona] = useState<PersonaToken | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [voice, setVoice] = useState<VoiceSettings | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.getVoice().then(setVoice).catch(() => setVoice(null)); }, []);
  useEffect(() => { api.getPersona().then(setPersona).catch(() => setPersona(null)); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const speaking = !!voice?.enabled;

  async function toggleVoice() {
    const next = await api.setVoice({ enabled: !speaking }).catch(() => null);
    if (next) setVoice(next);
    if (speaking) window.speechSynthesis?.cancel();
  }

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setMsgs((m) => [...m, { who: "you", text: t }]);
    setText("");
    setBusy(true);
    try {
      const r = await api.companion(t);
      setMsgs((m) => [...m, { who: "aria", text: r.text, refused: r.refused }]);
      // gate + engine come from the server (authoritative); rate from local prefs
      if (r.voice_enabled) speak(r.text, r.voice_engine ?? voice?.engine ?? "browser", voice?.rate ?? 1);
    } catch {
      setMsgs((m) => [...m, { who: "aria", text: "I'm having trouble reaching you right now." }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div className="companion" variants={fadeSlide} initial="hidden" animate="visible">
      <div className="companion__head">
        {persona?.self_image
          ? <img className="companion__face" src={persona.self_image} alt={persona.name}
                 title={persona.image_caption || persona.name} />
          : <span className="companion__avatar" aria-hidden>✦</span>}
        <span className="companion__name">{persona?.name ?? "Aria"}</span>
        <button type="button" className="companion__mic" disabled
                title="Voice input coming soon" aria-label="Voice input (coming soon)">🎙️</button>
        <button
          type="button"
          className="companion__voice"
          onClick={toggleVoice}
          title={speaking ? "Voice on — tap to mute" : "Voice off — tap to enable"}
          aria-label={speaking ? "Mute voice" : "Enable voice"}
        >
          {speaking ? "🔊" : "🔇"}
        </button>
      </div>

      <div className="companion__log">
        {msgs.length === 0 && <div className="companion__empty muted">Say hello to Aria…</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`bubble bubble--${m.who} ${m.refused ? "bubble--refused" : ""}`}>
            {m.refused && <span className="bubble__shield" aria-hidden>🛡 </span>}
            {m.text}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="companion__bar">
        <input
          className="companion__input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Message Aria…"
        />
        <button className="btn btn--accent" onClick={send} disabled={busy} aria-label="Send">
          {busy ? "…" : "➤"}
        </button>
      </div>
    </motion.div>
  );
}
