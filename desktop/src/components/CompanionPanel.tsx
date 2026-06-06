import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { fadeSlide } from "../motion";
import { api } from "../api";

type Msg = { who: "you" | "aria"; text: string; refused?: boolean };

export function CompanionPanel() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setMsgs((m) => [...m, { who: "you", text: t }]);
    setText("");
    setBusy(true);
    try {
      const r = await api.companion(t);
      setMsgs((m) => [...m, { who: "aria", text: r.text, refused: r.refused }]);
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
        <span className="companion__voice" title="Voice coming soon — text only for now" aria-label="text only">🔇</span>
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
