"""AX OS → Axiom integration seam.

The single, reviewable place where AX OS talks to the Axiom trust layer.
All Axiom access goes through ``AxiomBridge`` (MCP stdio client) — no
Axiom source is vendored into this repo. See ``BOUNDARY.md``.
"""
from bridge.client import AxiomBridge, AxiomError

__all__ = ["AxiomBridge", "AxiomError"]
