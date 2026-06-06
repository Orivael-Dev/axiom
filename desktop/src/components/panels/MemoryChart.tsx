import type { Panel } from "../../types";

// Small status donut (mockup's memory ring) + the recalled items.
// The ring is a status glyph, not fabricated data: full when context is
// present (ready), faint when pending.
export function MemoryChart({ panel }: { panel: Panel }) {
  const filled = panel.status === "ready";
  const R = 16, C = 2 * Math.PI * R;
  const frac = filled ? 1 : 0.15;
  return (
    <div className="memchart">
      <svg width="48" height="48" viewBox="0 0 48 48" aria-hidden>
        <circle cx="24" cy="24" r={R} className="ring ring--track" />
        <circle
          cx="24" cy="24" r={R}
          className="ring ring--value"
          strokeDasharray={`${C * frac} ${C}`}
          transform="rotate(-90 24 24)"
        />
      </svg>
      <ul className="panel__items">
        {panel.items.length ? panel.items.map((it, i) => <li key={i}>{it}</li>)
          : <li className="muted">no prior local context yet</li>}
      </ul>
    </div>
  );
}
