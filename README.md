# ax-os-demo3 — courier branch (transient)

Not part of Axiom. Temporary courier for the AX OS **Demo 3 + 4** surface
(signed agent install / review / approve / revoke). Delete after transfer.

UPDATES (overwrite existing ax-os files):
- `bridge/client.py` — adds mkt_verify/install/review/approve/revoke/authority
- `aui/render.py` — adds render_install_review + render_authority

NEW:
- `marketplace/store.py` + `marketplace/__init__.py` — AgentStore (install flow + signed audit logging)
- `aui/demo3.py` — CLI: `python -m aui.demo3 --manifest signed.json --approve --then-revoke`
- `tests/test_marketplace_store.py` — pure (fake bridge)
- `tests/test_demo3_e2e.py` — real lifecycle (auto-skips without Axiom)

Needs Axiom axiom_marketplace + axiom_ledger (#64, #65, merged). No Axiom source vendored in product code.
