# ax-os-desktop-look — courier branch (transient)

Not part of Axiom. Temporary courier: the AX OS desktop shell with the
mockup look + eased motion. **Supersedes `ax-os-desktop`** — ships the full
`desktop/` tree, so land just this one. Delete after transfer.

Adds to the React/Tauri shell:
- `src/theme.ts` + two CSS token sets (`styles.css`) — **Studio** (dark
  cyan/violet, dev/pro) and **Calm** (warm, everyday); switch by scene
  (dev→Studio, else Calm) + a manual toggle (persisted).
- `src/components/ConnectorLayer.tsx` — teal bezier flows from the goal pill
  to each panel (+ memory→agents), gently animated, re-measured on
  reflow/resize (SVG overlay).
- `src/components/GoalBar.tsx` — the "[Goal] …" pill + domain + theme toggle.
- `src/components/PanelCard.tsx` + `panels/{MemoryChart,AgentCard,ListPanel}` —
  floating glowing cards with kind-specific bodies (donut, verified-agent
  cards, icon lists).
- `src/motion.ts` + Framer Motion (`motion` dep) — eased staggered entrances,
  `AnimatePresence`, `layout`; `prefers-reduced-motion` honored.

Run: `python -m aui.server` (terminal 1) + `cd desktop && npm install &&
npm run dev` (browser :1420) or `npm run tauri dev`. No Axiom imports.
