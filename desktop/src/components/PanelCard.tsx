import { motion } from "motion/react";
import type { Panel } from "../types";
import { panelVariants, layoutTransition } from "../motion";
import { MemoryChart } from "./panels/MemoryChart";
import { AgentCard } from "./panels/AgentCard";
import { ListPanel } from "./panels/ListPanel";

const BADGE: Record<string, string> = { ready: "●", pending: "○", blocked: "■" };

function Body({ panel }: { panel: Panel }) {
  if (panel.kind === "memory" || panel.kind === "context") return <MemoryChart panel={panel} />;
  if (panel.kind === "agents") return <AgentCard panel={panel} />;
  return <ListPanel panel={panel} />;
}

export function PanelCard({ panel }: { panel: Panel }) {
  return (
    <motion.div
      className={`panel panel--${panel.status}`}
      data-panel={panel.kind}
      variants={panelVariants}
      layout
      transition={layoutTransition}
      whileHover={{ y: -3 }}
    >
      <div className="panel__head">
        <span className={`panel__badge badge--${panel.status}`}>{BADGE[panel.status] ?? "•"}</span>
        <span className="panel__title">{panel.title}</span>
        <span className="panel__kind">{panel.kind}</span>
      </div>
      <Body panel={panel} />
    </motion.div>
  );
}
