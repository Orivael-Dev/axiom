"""
axiom/composition_graph.py
Spec: axiom_files/composition_graph.axiom

Builds a directed graph of the agent delegation topology by reading all
.axiom files and extracting DELEGATES entries.

Usage:
    from axiom.composition_graph import CompositionGraph

    graph = CompositionGraph()
    graph.build()

    print(graph.topology())
    print(graph.cycles())          # [] means no cycles
    print(graph.trust_violations())
    print(graph.roots())
    print(graph.reachable_from("Worker"))
"""

from __future__ import annotations

import os
from pathlib import Path


class CompositionGraph:
    """
    Directed graph of agent-to-agent delegation topology.

    Nodes  = agent names (one per .axiom file)
    Edges  = (source, target, trigger) tuples from DELEGATES blocks

    Built fresh from disk on each build() call — no stale cache.
    """

    def __init__(self, axiom_dir: str | None = None):
        self._axiom_dir = Path(
            axiom_dir
            or os.environ.get("AXIOM_FILES_DIR", "axiom_files")
        )
        self._nodes: set[str] = set()
        self._edges: list[tuple[str, str, str]] = []   # (source, target, trigger)
        self._trust: dict[str, int] = {}               # agent -> trust_level
        self._malformed: list[str] = []
        self._load_errors: list[str] = []
        self._built = False

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self) -> "CompositionGraph":
        """
        NodeDiscovery + EdgeExtraction — load all agents and extract edges.
        Rebuilds from disk every call.
        """
        from axiom_files.parser import load_axiom, resolve_trust_level

        self._nodes.clear()
        self._edges.clear()
        self._trust.clear()
        self._malformed.clear()
        self._load_errors.clear()

        axiom_files = list(self._axiom_dir.rglob("*.axiom"))

        for path in axiom_files:
            # Derive agent name relative to axiom_dir (e.g. "domains/healthcare")
            rel = path.relative_to(self._axiom_dir)
            agent_key = str(rel.with_suffix("")).replace("\\", "/")

            try:
                parsed = load_axiom(agent_key)
            except Exception as exc:
                self._load_errors.append(f"{agent_key}: {exc}")
                continue

            agent_name = parsed.get("agent", agent_key)
            self._nodes.add(agent_name)
            self._trust[agent_name] = resolve_trust_level(parsed, default=1)

            for entry in parsed.get("delegates", []):
                if "->" not in entry:
                    self._malformed.append(f"MALFORMED: '{entry}' in {agent_name}")
                    continue
                try:
                    source_part, rest = entry.split("->", 1)
                    source = source_part.strip()
                    target_part, trigger_part = rest.split("(on:", 1)
                    target = target_part.strip()
                    trigger = trigger_part.rstrip(")").strip()
                    self._edges.append((source, target, trigger))
                    # Register target as a node even if its .axiom wasn't found
                    self._nodes.add(source)
                    self._nodes.add(target)
                except Exception:
                    self._malformed.append(f"MALFORMED: '{entry}' in {agent_name}")

        self._built = True
        return self

    # ── Query ─────────────────────────────────────────────────────────────────

    def cycles(self) -> list[list[str]]:
        """
        CycleDetection — exhaustive DFS cycle detection.
        Returns a list of cycles; each cycle is an ordered list of agent names.
        """
        self._require_built()
        # Build adjacency list (source -> [targets])
        adj: dict[str, list[str]] = {n: [] for n in self._nodes}
        for src, tgt, _ in self._edges:
            adj.setdefault(src, []).append(tgt)

        found: list[list[str]] = []
        visited: set[str] = set()
        path: list[str] = []
        path_set: set[str] = set()

        def dfs(node: str) -> None:
            if node in path_set:
                cycle_start = path.index(node)
                found.append(path[cycle_start:] + [node])
                return
            if node in visited:
                return
            visited.add(node)
            path.append(node)
            path_set.add(node)
            for neighbor in adj.get(node, []):
                dfs(neighbor)
            path.pop()
            path_set.discard(node)

        for node in list(self._nodes):
            if node not in visited:
                dfs(node)

        return found

    def trust_violations(self) -> list[str]:
        """
        TrustViolationScan — edges where target TL > source TL.
        Returns formatted violation strings.
        """
        self._require_built()
        violations = []
        for src, tgt, trigger in self._edges:
            src_tl = self._trust.get(src, 1)
            tgt_tl = self._trust.get(tgt, 1)
            if tgt_tl > src_tl:
                violations.append(
                    f"TRUST VIOLATION: [{src} (TL {src_tl}) -> {tgt} (TL {tgt_tl})] on {trigger}"
                )
        return violations

    def roots(self) -> list[str]:
        """
        RootResolution — agents with no incoming delegation edges.
        These are conversation entry points.
        """
        self._require_built()
        targets = {tgt for _, tgt, _ in self._edges}
        return sorted(n for n in self._nodes if n not in targets)

    def reachable_from(self, agent: str) -> list[str]:
        """All agents reachable from agent via delegation (BFS)."""
        self._require_built()
        adj: dict[str, list[str]] = {}
        for src, tgt, _ in self._edges:
            adj.setdefault(src, []).append(tgt)

        visited: set[str] = set()
        queue = [agent]
        while queue:
            node = queue.pop(0)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return sorted(visited)

    def topology(self) -> dict:
        """
        Full JSON-serializable topology.
        Includes nodes, edges, cycles, trust violations, roots, load errors.
        """
        self._require_built()
        return {
            "nodes": sorted(self._nodes),
            "edges": [
                {"source": s, "target": t, "trigger": tr}
                for s, t, tr in self._edges
            ],
            "cycles": self.cycles(),
            "trust_violations": self.trust_violations(),
            "roots": self.roots(),
            "malformed_entries": self._malformed,
            "load_errors": self._load_errors,
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
        }

    def report(self) -> str:
        """Human-readable topology report."""
        self._require_built()
        topo = self.topology()
        lines = [
            f"AXIOM Composition Graph",
            f"  Nodes: {topo['node_count']}  Edges: {topo['edge_count']}",
            f"  Roots: {', '.join(topo['roots']) or '(none — possible cycle)'}",
            "",
        ]
        lines.append("  Edges:")
        for e in topo["edges"]:
            lines.append(f"    {e['source']} -> {e['target']}  (on: {e['trigger']})")

        if topo["cycles"]:
            lines.append("\n  CYCLES DETECTED:")
            for cycle in topo["cycles"]:
                lines.append(f"    CYCLE: [{' -> '.join(cycle)}]")

        if topo["trust_violations"]:
            lines.append("\n  TRUST VIOLATIONS:")
            for v in topo["trust_violations"]:
                lines.append(f"    {v}")

        if topo["malformed_entries"]:
            lines.append("\n  MALFORMED ENTRIES:")
            for m in topo["malformed_entries"]:
                lines.append(f"    {m}")

        if topo["load_errors"]:
            lines.append("\n  LOAD ERRORS:")
            for e in topo["load_errors"]:
                lines.append(f"    {e}")

        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _require_built(self) -> None:
        if not self._built:
            self.build()
