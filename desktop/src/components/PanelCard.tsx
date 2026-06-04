import type { Panel } from "../types";

const BADGE: Record<string, string> = { ready: "🟢", pending: "⚪", blocked: "🔴" };

export function PanelCard({ panel }: { panel: Panel }) {
  return (
    <div className={`panel panel--${panel.status}`}>
      <div className="panel__head">
        <span className="panel__badge">{BADGE[panel.status] ?? ""}</span>
        <span className="panel__title">{panel.title}</span>
        <span className="panel__kind">{panel.kind}</span>
      </div>
      {panel.items.length > 0 ? (
        <ul className="panel__items">
          {panel.items.map((it, i) => (
            <li key={i}>{it}</li>
          ))}
        </ul>
      ) : (
        <p className="panel__empty">workspace will gather this</p>
      )}
    </div>
  );
}
