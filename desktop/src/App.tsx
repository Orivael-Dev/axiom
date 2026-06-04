import { useState } from "react";
import { api, BASE } from "./api";
import type { WorkspacePlan, AuditTrail } from "./types";
import { PanelCard } from "./components/PanelCard";

const DOMAINS = ["(auto)", "general", "dev", "financial", "music", "medical"];

export default function App() {
  const [goal, setGoal] = useState("work on the launch demo branch");
  const [domain, setDomain] = useState("(auto)");
  const [plan, setPlan] = useState<WorkspacePlan | null>(null);
  const [trail, setTrail] = useState<AuditTrail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function openWorkspace() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.assemble(goal, domain === "(auto)" ? undefined : domain);
      setPlan(p);
      setTrail(await api.audit(10).catch(() => null));
    } catch (e) {
      setError(
        `Could not reach the AX OS service at ${BASE}. ` +
          `Start it with: python -m aui.server  (${e instanceof Error ? e.message : e})`
      );
      setPlan(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="app__bar">
        <h1>AX OS</h1>
        <span className="app__tag">state a goal — the workspace assembles, safety checked first</span>
      </header>

      <section className="intent">
        <input
          className="intent__goal"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && openWorkspace()}
          placeholder="What are you working on?"
        />
        <select value={domain} onChange={(e) => setDomain(e.target.value)}>
          {DOMAINS.map((d) => (
            <option key={d}>{d}</option>
          ))}
        </select>
        <button onClick={openWorkspace} disabled={busy}>
          {busy ? "Assembling…" : "Open workspace"}
        </button>
      </section>

      {error && <div className="banner banner--error">{error}</div>}

      {plan && !plan.allowed && (
        <div className="banner banner--blocked">
          Goal refused by the intent gate — no workspace assembled.
        </div>
      )}

      {plan && (
        <>
          <div className="scene">scene: <strong>{plan.scene}</strong></div>
          <div className="grid">
            {plan.panels.map((p, i) => (
              <PanelCard key={i} panel={p} />
            ))}
          </div>

          {trail && (
            <details className="audit" open>
              <summary>
                Signed audit trail · {trail.count} events ·{" "}
                {trail.all_verified ? "✅ all verified" : "🔴 tamper detected"}
              </summary>
              <ul>
                {[...trail.events].reverse().map((e, i) => (
                  <li key={i}>
                    <code>{e.event_type}</code> → {e.outcome || "-"}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <footer className="sig">signed {plan.signature.slice(0, 24)}…</footer>
        </>
      )}
    </div>
  );
}
