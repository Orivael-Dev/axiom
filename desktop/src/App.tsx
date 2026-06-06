import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { api, BASE } from "./api";
import type { WorkspacePlan, AuditTrail } from "./types";
import { PanelCard } from "./components/PanelCard";
import { GoalBar } from "./components/GoalBar";
import { ConnectorLayer } from "./components/ConnectorLayer";
import { StatusStrip } from "./components/StatusStrip";
import { SearchPanel } from "./components/SearchPanel";
import { CompanionPanel } from "./components/CompanionPanel";
import { gridVariants } from "./motion";
import type { Theme } from "./theme";
import { themeForScene, loadThemeOverride, saveThemeOverride, applyTheme } from "./theme";

export default function App() {
  const [goal, setGoal] = useState("work on the launch demo branch");
  const [domain, setDomain] = useState("(auto)");
  const [plan, setPlan] = useState<WorkspacePlan | null>(null);
  const [trail, setTrail] = useState<AuditTrail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [override, setOverride] = useState<Theme | null>(loadThemeOverride());

  const stageRef = useRef<HTMLDivElement>(null);

  // Effective theme: manual override wins, else derived from the plan's scene.
  const theme: Theme = override ?? themeForScene(plan?.scene) ?? "studio";
  useEffect(() => { applyTheme(theme); }, [theme]);

  // Re-measure connectors whenever the assembled set changes.
  const planSig = useMemo(
    () => (plan ? `${plan.scene}|${plan.panels.map((p) => p.kind).join(",")}` : "none"),
    [plan]
  );

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
          `Start it with:  python -m aui.server  (${e instanceof Error ? e.message : e})`
      );
      setPlan(null);
    } finally {
      setBusy(false);
    }
  }

  function toggleTheme() {
    chooseTheme(theme === "studio" ? "calm" : "studio");
  }

  function chooseTheme(next: Theme) {
    setOverride(next);
    saveThemeOverride(next);
  }

  return (
    <div className="app">
      <div className="stage" ref={stageRef}>
        <ConnectorLayer stageRef={stageRef} dep={planSig} />

        <StatusStrip theme={theme} onTheme={chooseTheme} />

        <GoalBar
          goal={goal} domain={domain} scene={plan?.scene} planner={plan?.planner}
          busy={busy} theme={theme}
          onGoal={setGoal} onDomain={setDomain} onOpen={openWorkspace} onToggleTheme={toggleTheme}
        />

        {error && <div className="banner banner--error">{error}</div>}
        {plan && !plan.allowed && (
          <div className="banner banner--blocked">
            Goal refused by the intent gate — no workspace assembled.
          </div>
        )}

        <AnimatePresence mode="popLayout">
          {plan && (
            <motion.div
              className="grid"
              key={planSig}
              variants={gridVariants}
              initial="hidden"
              animate="visible"
            >
              {plan.panels.map((p, i) => (
                <PanelCard key={`${p.kind}-${i}`} panel={p} />
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        <SearchPanel />
        <CompanionPanel />
      </div>

      {plan && trail && (
        <details className="audit" open>
          <summary>
            Signed audit trail · {trail.count} events ·{" "}
            {trail.all_verified ? "✓ all verified" : "⚠ tamper detected"}
          </summary>
          <ul>
            {[...trail.events].reverse().map((e, i) => (
              <li key={i}><code>{e.event_type}</code> → {e.outcome || "-"}</li>
            ))}
          </ul>
        </details>
      )}

      {plan && <footer className="sig">signed {plan.signature.slice(0, 24)}…</footer>}
    </div>
  );
}
