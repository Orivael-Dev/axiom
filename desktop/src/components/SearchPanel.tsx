import { useState } from "react";
import { motion } from "motion/react";
import { fadeSlide } from "../motion";
import { api } from "../api";
import type { SearchResults } from "../types";

export function SearchPanel() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<SearchResults | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!q.trim()) return;
    setBusy(true);
    try {
      setRes(await api.search(q, 6));
    } catch {
      setRes({ ok: false, query: q, engine: "", error: "search unavailable" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div className="search" variants={fadeSlide} initial="hidden" animate="visible">
      <div className="search__bar">
        <span className="search__icon" aria-hidden>🔎</span>
        <input
          className="search__input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="Search the open web…"
        />
        <button className="btn btn--accent" onClick={run} disabled={busy} aria-label="Search">
          {busy ? "…" : "🔎"}
        </button>
      </div>

      {res && !res.ok && (
        <div className="search__error">⚠ {res.error ?? "search unavailable"}</div>
      )}

      {res && res.ok && (
        <>
          <div className="search__meta">
            🔎 {res.returned ?? 0}
            {(res.blocked ?? 0) > 0 ? <> · <span className="search__shield">🛡 {res.blocked} filtered</span></> : null}
          </div>

          {(res.answers ?? []).map((a, i) => (
            <div key={`ans-${i}`} className="search__answer">💡 {String(a)}</div>
          ))}

          {(res.returned ?? 0) === 0 && (res.answers ?? []).length === 0 && (
            <div className="search__error muted">No results — try different words.</div>
          )}

          <ul className="search__list">
            {(res.results ?? []).map((h, i) => (
              <li key={i} className={`search__hit ${h.blocked ? "is-blocked" : ""}`}>
                {h.blocked ? (
                  <div className="search__blocked" title={`filtered by the immune system · ${h.detection_method ?? ""}`}>
                    <span className="search__shield">🛡</span>
                    <span className="search__hit-title">{h.title || h.url}</span>
                    <span className="search__tag">{h.detection_method || "filtered"}</span>
                  </div>
                ) : (
                  <>
                    <a className="search__hit-title" href={h.url} target="_blank" rel="noreferrer">
                      {h.title || h.url} <span className="search__ext" aria-hidden>↗</span>
                    </a>
                    {h.engine && <span className="search__engine" title={h.engine}>🌐 {h.engine}</span>}
                    {h.content && <p className="search__snippet">{h.content}</p>}
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </motion.div>
  );
}
