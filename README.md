# ax-os-test-skip — courier branch (transient)

Not part of Axiom. Temporary courier: e2e tests skip (not fail) against a
STALE Axiom build. Delete after transfer.

NEW:
- `tests/axiom_probe.py` — launches Axiom once and checks tools/list;
  reports (ready, reason). A build missing axiom_workspace is treated like
  an absent one (skip with a clear "update your Axiom install" message).

UPDATES (overwrite existing test files):
- `tests/test_bridge.py`, `tests/test_demo2.py`, `tests/test_demo_e2e.py`,
  `tests/test_demo3_e2e.py` — gate on axiom_probe.axiom_ready() instead of
  mere presence, so a stale Axiom skips rather than erroring.

Pairs with Axiom PR #66 (VERSION 1.9.0). No Axiom source vendored.
