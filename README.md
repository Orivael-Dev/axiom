# ax-os-fill-panels — courier branch (transient)

Not part of Axiom. Temporary courier: fill WorkspacePlan panels with real
local + bridge data. Delete after transfer.

DEPENDS ON the AUI surface (ax-os-aui). Apply after it (and ax-os-planner).

UPDATES:
- `aui/server.py` — /assemble now fills panels (reads AX_OS_REPO, default cwd)

NEW:
- `aui/panels.py` — providers fill files / tools / branch / tests / docs / notes /
  agents (authorized agents derived from the signed audit ledger). Kinds with no
  data source (tracks / plugins / session / documents / reminders / guidelines)
  stay pending — honest, not faked. All Axiom access via the bridge.
- `tests/test_panels.py` — provider tests (pure, throwaway git repo)

Point the workspace at a repo with AX_OS_REPO. No Axiom source vendored.
