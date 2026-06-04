import type { Panel } from "../../types";

// `agents` panel: each item is a currently-authorized agent (from the signed
// ledger). Shown as a verified card with review/approve affordances.
// Buttons are visual in the shell; the lifecycle runs via /marketplace/*.
export function AgentCard({ panel }: { panel: Panel }) {
  if (!panel.items.length) {
    return <p className="panel__empty">no authorized agents</p>;
  }
  return (
    <div className="agents">
      {panel.items.map((name, i) => (
        <div className="agent" key={i}>
          <span className="agent__dot" />
          <span className="agent__name">{name}</span>
          <span className="agent__tag">verified</span>
          <div className="agent__actions">
            <button className="btn btn--ghost" title="manage via the AX Store">Review</button>
            <button className="btn btn--accent" title="manage via the AX Store">Approve</button>
          </div>
        </div>
      ))}
    </div>
  );
}
