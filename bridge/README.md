# bridge/ — Axiom integration seam

The single, reviewable place where AX OS talks to the Axiom trust layer.
**All** Axiom access goes through `AxiomBridge`; nothing else in this repo
imports or vendors Axiom. This is what makes the boundary in
[`../BOUNDARY.md`](../BOUNDARY.md) enforceable.

## How it works

`AxiomBridge` launches the Axiom MCP server as a subprocess and speaks
JSON-RPC 2.0 over stdio — the standard MCP transport. Axiom is consumed as
a **published package / external server**, never as copied source.

```python
from bridge import AxiomBridge

with AxiomBridge() as ax:
    ctx = ax.assemble_workspace("help me work on the launch demo", domain="general")
    if ctx["allowed"]:
        context = ctx["recalled"]      # signed local governance context, or None
    else:
        reason = ctx["blocked_reason"] # intent gate refused a harmful goal
```

By default the bridge runs `python -m axiom_mcp_server` (the installed
Axiom package). For local development against a checkout:

```python
AxiomBridge(command=["python", "axiom_mcp_server.py"],
            cwd="/path/to/axiom",
            env={"AXIOM_MASTER_KEY": "...", "AXIOM_MEMORY_STORE": "ax_os_memory.jsonl"})
```

## Typed surface

| Method | Axiom tool | Purpose |
|--------|-----------|---------|
| `assemble_workspace(goal, domain=None)` | `axiom_workspace` | intent-gated workspace assembly (safety check → local recall) |
| `remember(text, domain=…, constraints=…, resolution=…, history=…)` | `axiom_memory` | store a signed memory packet |
| `recall(query, domain=None)` | `axiom_memory` | recall the closest authentic packet |
| `guard_check(text)` | `axiom_guard_check` | two-layer constitutional safety check |
| `list_tools()` / `call_tool(name, args)` | — | generic access to any Axiom tool |

Every Axiom result carries an `hmac_signature` (the trust envelope); the
bridge relays it unmodified for parties that hold the verifying key.

## Tests

`tests/test_bridge.py` round-trips against a **real** Axiom server. They
auto-skip unless one is reachable — set `AXIOM_REPO=/path/to/axiom` or
`pip install` the Axiom package, then `pytest tests/test_bridge.py`.
