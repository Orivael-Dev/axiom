import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { fadeSlide } from "../motion";
import { api } from "../api";
import type { VoiceSettings } from "../types";

type Msg = { who: "you" | "aria"; text: string; refused?: boolean };

// Speak a reply. Browser engine uses the Web Speech API (fully client-side,
// no server); piper/cloud engines fetch audio from the /tts route and play it.
function speak(text: string, voice: VoiceSettings | null) {
  const engine = voice?.engine ?? "browser";
  if (engine === "browser") {
    if (!("speechSynthesis" in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.rate = voice?.rate || 1;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    return;
  }
  api.tts(text)
    .then((r) => {
      if (r.ok && r.audio_b64) {
        new Audio(`data:${r.mime ?? "audio/wav"};base64,${r.audio_b64}`).play().catch(() => {});
      }
    })
    .catch(() => {});
}

export function CompanionPanel() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [voice, setVoice] = useState<VoiceSettings | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.getVoice().then(setVoice).catch(() => setVoice(null)); }, []);
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
      if (r.voice_enabled) speak(r.text, voice);
    } catch {
      setMsgs((m) => [...m, { who: "aria", text: "I'm having trouble reaching you right now." }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div className="companion" variants={fadeSlide} initial="hidden" animate="visible">
      <div className="companion__head">
        <span className="companion__avatar" aria-hidden>✦</span>
        <span className="companion__name">Aria</span>
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
