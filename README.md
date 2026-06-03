# ax-os-agent-actions — courier branch (transient)

Not part of Axiom. Temporary courier: gated agent actions (governed AI
labor). Delete after transfer.

DEPENDS ON the full AUI + marketplace stack (demo3 + aui + planner + fill).

NEW:
- `marketplace/runner.py` — AgentRunner.run_action(pair_id, action): authority
  gate (bonded) -> safety gate (Axiom guard) -> perform + signed audit; a
  revoked/pending agent is blocked, a harmful action is blocked even when
  authorized. All Axiom access via the bridge.
- `tests/test_agent_runner.py` — pure gating tests (fake bridge)

UPDATES (overwrite existing ax-os files):
- `marketplace/__init__.py` — export AgentRunner, ActionResult
- `aui/server.py` — adds POST /marketplace/run
- `tests/test_server.py` — guard_check on the fake bridge + /marketplace/run test

No Axiom source vendored.
