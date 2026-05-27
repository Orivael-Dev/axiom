# Project notes for Claude Code sessions

## Post-beta monetization plan

After the public beta period ends, **some skill packs and some MCP patent
tools will move behind a paywall.** Treat this as a future product
constraint when:

- Adding new packs under `packs/` — flag whether the pack is intended to
  be free-tier or paid-tier at design time (decision belongs in the
  manifest, not bolted on later).
- Adding new MCP tools in `axiom_mcp_server.py` / `axiom_packs/` — same
  question. The patent-emulator tools (ORVL-001 / 013 / 016 / 017 / 019
  / 022 / 023) are the primary paywall candidates; the core five
  (`axiom_guard_check`, `axiom_lint`, `axiom_trace`, `axiom_qrf`,
  `axiom_status`) are expected to stay free.
- Touching the firewall billing / tier surfaces
  (`axiom_firewall/billing.py`, `auth.TIER_*`, `templates/landing.html`'s
  pricing block) — make sure the language and gating doesn't assume the
  current "everything is free during beta" state.
- Wiring new pack-install or MCP-tool-invocation paths — leave room for
  a tier check at the entry point, even if it's a no-op during beta
  (`AXIOM_FIREWALL_BETA_MODE=1`).

The free / paid split itself isn't finalized — don't hardcode pack names
or tool names into a paywall list yet. The right shape is probably a
`tier` field on `SkillPackManifest` and an analogous attribute on
registered MCP tools, defaulting to `"free"`. When the user is ready to
flip the switch, the gate is one place to edit.

Beta mode is controlled by `AXIOM_FIREWALL_BETA_MODE=1` (default on);
the /billing page already swaps Stripe checkout for Contact-Sales while
beta is active.
