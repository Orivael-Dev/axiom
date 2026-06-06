import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Agent } from "../../types";

// Interactive AX Store: lists installed agents (with live bonded-authority
// state from the signed ledger) and drives approve / revoke through the
// marketplace endpoints. Self-fetching so it stays in sync after actions.
export function AgentCard() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function refresh() {
    try {
      setAgents((await api.agents()).agents);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  useEffect(() => { void refresh(); }, []);

  async function act(a: Agent) {
    setBusy(a.pair_id);
    try {
      if (a.authorized) await api.revoke(a.pair_id);
      else await api.approve(a.pair_id);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (err) return <p className="panel__empty">store unavailable: {err}</p>;
  if (!agents) return <p className="panel__empty">loading agents…</p>;
  if (agents.length === 0) return <p className="panel__empty">no installed agents yet</p>;

  return (
    <div className="agents">
      {agents.map((a) => {
        const terminal = a.state === "REVOKED" || a.state === "EXPIRED";
        return (
          <div className="agent" key={a.pair_id}>
            <span className={`agent__dot ${a.authorized ? "" : "agent__dot--off"}`} />
            <span className="agent__name">{a.agent || a.pair_id}</span>
            <span className="agent__tag">
              {a.authorized ? "authorized" : a.state.toLowerCase().replace("_", " ")}
            </span>
            <div className="agent__actions">
              <button className="btn btn--ghost"
                      onClick={() => setOpen(open === a.pair_id ? null : a.pair_id)}>
                Review
              </button>
              <button className="btn btn--accent" disabled={busy === a.pair_id || terminal}
                      onClick={() => act(a)}>
                {busy === a.pair_id ? "…" : terminal ? "revoked" : a.authorized ? "Revoke" : "Approve"}
              </button>
            </div>
            {open === a.pair_id && (
              <div className="agent__detail">
                pair <code>{a.pair_id}</code> · state {a.state} · authorized {String(a.authorized)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
