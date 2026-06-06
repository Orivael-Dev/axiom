import type { Panel } from "../../types";

const ICON: Record<string, string> = {
  files: "📄", tools: "🛠", branch: "🌿", tests: "✓", docs: "📚",
  notes: "📝", session: "🎚", tracks: "🎵", plugins: "🔌",
  documents: "🗂", reminders: "⏰", guidelines: "📋", safety: "⛔",
};

export function ListPanel({ panel }: { panel: Panel }) {
  if (!panel.items.length) {
    return <p className="panel__empty">workspace will gather this</p>;
  }
  const icon = ICON[panel.kind] ?? "•";
  return (
    <ul className="panel__items panel__items--icon">
      {panel.items.map((it, i) => (
        <li key={i}><span className="li-icon">{icon}</span>{it}</li>
      ))}
    </ul>
  );
}
