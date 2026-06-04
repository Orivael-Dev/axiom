import { useEffect, useState } from "react";
import type { RefObject } from "react";

interface PathDef { d: string; key: string; }

// SVG overlay drawing teal bezier flows from the goal pill to each panel
// (and memory/context → agents), re-measured through the entrance/reflow so
// the lines track panels smoothly. Coordinates are stage-relative px = SVG
// user units (no viewBox), so they map 1:1.
export function ConnectorLayer({ stageRef, dep }: {
  stageRef: RefObject<HTMLDivElement>;
  dep: string;
}) {
  const [paths, setPaths] = useState<PathDef[]>([]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    let raf = 0;
    let stopAt = 0;

    const measure = () => {
      const sr = stage.getBoundingClientRect();
      const goalEl = stage.querySelector<HTMLElement>('[data-anchor="goal"]');
      const panels = Array.from(stage.querySelectorAll<HTMLElement>("[data-panel]"));
      if (!goalEl || panels.length === 0) { setPaths([]); return; }

      const gr = goalEl.getBoundingClientRect();
      const gx = gr.left - sr.left + gr.width / 2;
      const gy = gr.bottom - sr.top;
      const defs: PathDef[] = [];

      panels.forEach((p, i) => {
        const pr = p.getBoundingClientRect();
        const px = pr.left - sr.left + pr.width / 2;
        const py = pr.top - sr.top;
        const dy = Math.max(40, py - gy);
        defs.push({ key: "g" + i, d: `M ${gx} ${gy} C ${gx} ${gy + dy * 0.5}, ${px} ${py - dy * 0.5}, ${px} ${py}` });
      });

      const src = panels.find((p) => ["memory", "context"].includes(p.dataset.panel || ""));
      const dst = panels.find((p) => p.dataset.panel === "agents");
      if (src && dst && src !== dst) {
        const a = src.getBoundingClientRect();
        const b = dst.getBoundingClientRect();
        const ax = a.right - sr.left, ay = a.top - sr.top + a.height / 2;
        const bx = b.left - sr.left, by = b.top - sr.top + b.height / 2;
        const dx = Math.max(40, bx - ax);
        defs.push({ key: "s", d: `M ${ax} ${ay} C ${ax + dx * 0.5} ${ay}, ${bx - dx * 0.5} ${by}, ${bx} ${by}` });
      }
      setPaths(defs);
    };

    const tick = () => {
      measure();
      if (performance.now() < stopAt) raf = requestAnimationFrame(tick);
    };
    const settle = () => {
      stopAt = performance.now() + 700; // re-measure through entrance + reflow
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };

    settle();
    const ro = new ResizeObserver(measure);
    ro.observe(stage);
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [dep, stageRef]);

  return (
    <svg className="connectors" aria-hidden>
      <defs>
        <linearGradient id="cx-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" className="cx-stop0" />
          <stop offset="100%" className="cx-stop1" />
        </linearGradient>
      </defs>
      {paths.map((p) => (
        <path key={p.key} d={p.d} className="connector" />
      ))}
    </svg>
  );
}
