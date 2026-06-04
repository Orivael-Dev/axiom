# ax-os-store-interactive — courier branch (transient)

Not part of Axiom. Wires the AX Store panel to the live marketplace —
install → review → approve → revoke from the AUI. Delete after transfer.

UPDATES (overwrite existing ax-os files):
- `aui/server.py` — adds GET /marketplace/agents: reconstructs installed
  agents (pair_id + name) from the signed audit ledger and reports each
  one's live bonded-authority state via the marketplace.
- `tests/test_server.py` — richer fake-bridge events + a /marketplace/agents test.
- `desktop/src/types.ts` — Agent type.
- `desktop/src/api.ts` — agents() / approve() / revoke().
- `desktop/src/components/panels/AgentCard.tsx` — self-fetching interactive
  store: lists agents with authority state, drives Approve/Revoke and a
  Review detail toggle, refreshes after each action (revoked = terminal).
- `desktop/src/components/PanelCard.tsx` — `agents` panel renders the
  interactive card.
- `desktop/src/styles.css` — agent state dot + detail styles.

Verify: `pytest tests/test_server.py -q` (needs fastapi+httpx). Live:
install a signed agent, then Approve/Revoke from the AX Store panel. No
Axiom imports in product/frontend code.
