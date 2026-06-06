import { motion } from "motion/react";
import { fadeSlide } from "../motion";
import type { Theme } from "../theme";

const DOMAINS = ["(auto)", "general", "dev", "financial", "music", "medical"];

export function GoalBar(props: {
  goal: string;
  domain: string;
  scene?: string;
  planner?: "local" | "cloud";
  busy: boolean;
  theme: Theme;
  onGoal: (v: string) => void;
  onDomain: (v: string) => void;
  onOpen: () => void;
  onToggleTheme: () => void;
}) {
  const { goal, domain, scene, planner, busy, theme } = props;
  return (
    <motion.div className="goalbar" variants={fadeSlide} initial="hidden" animate="visible">
      <div className="goalbar__pill" data-anchor="goal">
        <span className="goalbar__label">Goal</span>
        <input
          className="goalbar__input"
          value={goal}
          onChange={(e) => props.onGoal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && props.onOpen()}
          placeholder="What are you working on?"
        />
      </div>
      <select className="goalbar__domain" value={domain} onChange={(e) => props.onDomain(e.target.value)}>
        {DOMAINS.map((d) => <option key={d}>{d}</option>)}
      </select>
      <button className="btn btn--accent" onClick={props.onOpen} disabled={busy}>
        {busy ? "Assembling…" : "Open workspace"}
      </button>
      <button className="btn btn--ghost goalbar__theme" onClick={props.onToggleTheme}
              title="Toggle Studio / Calm">
        {theme === "studio" ? "🌙 Studio" : "☀ Calm"}
      </button>
      {scene && <span className="goalbar__scene">scene: <strong>{scene}</strong></span>}
      {planner && (
        <span className={`goalbar__planner goalbar__planner--${planner}`}
              title={planner === "cloud"
                ? "Workspace laid out by a cloud model (Claude)"
                : "Workspace laid out on-device"}
              aria-label={`planner: ${planner}`}>
          {planner === "cloud" ? "☁" : "🖥"}
        </span>
      )}
    </motion.div>
  );
}
