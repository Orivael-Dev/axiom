// Theme system — Studio (dark, dev/pro) and Calm (warm, everyday).
// Tokens live in styles.css under :root[data-theme="..."]; this picks which.
export type Theme = "studio" | "calm";

const STORAGE_KEY = "ax-os-theme";

// Scenes the planner emits (aui/plan.py infer_scene). Dev-ish → Studio.
const STUDIO_SCENES = new Set(["dev", "os_security"]);

export function themeForScene(scene: string | undefined): Theme {
  return scene && STUDIO_SCENES.has(scene) ? "studio" : "calm";
}

export function loadThemeOverride(): Theme | null {
  const v = typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
  return v === "studio" || v === "calm" ? v : null;
}

export function saveThemeOverride(theme: Theme | null): void {
  if (typeof localStorage === "undefined") return;
  if (theme) localStorage.setItem(STORAGE_KEY, theme);
  else localStorage.removeItem(STORAGE_KEY);
}

export function applyTheme(theme: Theme): void {
  if (typeof document !== "undefined") document.documentElement.dataset.theme = theme;
}
