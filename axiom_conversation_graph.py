"""
AXIOM ConversationGraph — ORVL-007 CCG Components 1 & 2
========================================================
Node, edge, and seed management for the Constitutional Conversation Graph.

Component 1: Nodes and edges — append-only graph of completed conversations
with cosine similarity edge detection and HMAC-SHA256 signing.

Component 2: Coordinate propagation — seed_from() and find_best_seed()
enable new conversations to inherit prior final_synthesis vectors as
dampened preflight seeds (DAMPEN_FACTOR = 0.5).

Storage: append-only axiom_conversation_graph.jsonl

Usage:
  from axiom_conversation_graph import ConversationGraph
  g = ConversationGraph()
  g.add_node(conversation_record)
  g.add_edge(from_id, to_id, "vector_proximity")
  related = g.find_related([0.99, 0.77], threshold=0.70)
  seed = g.seed_from("conv-001")
  best = g.find_best_seed([0.99, 0.77], risk_clusters=["medical"])

github.com/Orivael-Dev/axiom
"""

import hashlib
import hmac
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding="utf-8")

from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-conversation-graph-v1")

# ── CANNOT_MUTATE constants ────────────────────────────────────────────────
EDGE_REASONS: frozenset = frozenset({"vector_proximity", "shared_risk_cluster", "user_linked"})
DEFAULT_THRESHOLD: float = 0.70
DAMPEN_FACTOR: float = 0.5   # CANNOT_MUTATE — seed vectors scaled to 50% magnitude
_REQUIRED_NODE_FIELDS = ("conversation_id", "prompt_hash", "final_synthesis")
GRAPH_FILE = Path("axiom_conversation_graph.jsonl")


# ── Exceptions ─────────────────────────────────────────────────────────────

class GraphNodeError(ValueError):
    """Raised when a node record is invalid or missing required fields."""


class GraphEdgeError(ValueError):
    """Raised when an edge cannot be created — missing node or invalid reason."""


# ── Cosine similarity for float vectors ────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two float vectors. CANNOT_MUTATE formula."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return round(dot / (mag_a * mag_b), 8)


# ── Signing ────────────────────────────────────────────────────────────────

def _sign_entry(entry: dict) -> str:
    """HMAC-SHA256 sign a graph entry. BUG-007: explicit .hexdigest()."""
    payload = json.dumps(
        {k: v for k, v in entry.items() if k != "signature"},
        sort_keys=True, default=str,
    ).encode("utf-8")  # BUG-008: explicit encoding
    sig = hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{sig}"


# ── ConversationGraph ──────────────────────────────────────────────────────

class ConversationGraph:
    """ORVL-007 Constitutional Conversation Graph — Component 1.

    Append-only graph of conversation nodes and constitutional edges.
    All entries HMAC-SHA256 signed with AXIOM_MASTER_KEY derivation.
    CANNOT_MUTATE: node_signatures, edge_signatures, cosine_threshold_default, edge_reason_set.
    """

    def __init__(self, store_path: Optional[str] = None):
        self._path = Path(store_path) if store_path else GRAPH_FILE
        self._nodes: dict = {}   # conversation_id -> node dict
        self._edges: list = []   # list of edge dicts
        self._load()

    def _load(self):
        """Load existing nodes and edges from JSONL store."""
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "node":
                self._nodes[entry["conversation_id"]] = entry
            elif entry.get("type") == "edge":
                self._edges.append(entry)

    def _append(self, entry: dict):
        """Append a signed entry to the JSONL store."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    # ── Node operations ────────────────────────────────────────────────────

    def add_node(self, record: dict) -> str:
        """Add a conversation node to the graph.

        Args:
            record: dict with at minimum conversation_id, prompt_hash,
                    final_synthesis. Additional fields (constitutional_distance,
                    intent_type, verdict, foresight_score, risk_clusters,
                    manifest_id) are preserved as-is.

        Returns:
            conversation_id of the stored node.

        Raises:
            GraphNodeError: if required fields are missing.
        """
        for field in _REQUIRED_NODE_FIELDS:
            if field not in record:
                raise GraphNodeError(
                    f"Missing required field: {field}"
                )

        node = {
            "type": "node",
            "conversation_id": record["conversation_id"],
            "prompt_hash": record["prompt_hash"],
            "final_synthesis": record["final_synthesis"],
            "constitutional_distance": record.get("constitutional_distance", 0.0),
            "intent_type": record.get("intent_type", ""),
            "verdict": record.get("verdict", ""),
            "foresight_score": record.get("foresight_score", 0.0),
            "risk_clusters": record.get("risk_clusters", []),
            "manifest_id": record.get("manifest_id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        node["signature"] = _sign_entry(node)
        self._nodes[node["conversation_id"]] = node
        self._append(node)
        return node["conversation_id"]

    def get_node(self, conversation_id: str) -> Optional[dict]:
        """Retrieve a node by conversation_id. Returns None if not found."""
        return self._nodes.get(conversation_id)

    def list_nodes(self) -> List[dict]:
        """Return all stored nodes."""
        return list(self._nodes.values())

    # ── Edge operations ────────────────────────────────────────────────────

    def add_edge(self, from_id: str, to_id: str, reason: str) -> str:
        """Create a directed constitutional edge between two nodes.

        Args:
            from_id: conversation_id of the source node.
            to_id:   conversation_id of the target node.
            reason:  one of vector_proximity, shared_risk_cluster, user_linked.

        Returns:
            edge_id (uuid string).

        Raises:
            GraphEdgeError: if either node does not exist or reason is invalid.
        """
        if from_id not in self._nodes:
            raise GraphEdgeError(
                f"Source node not found: {from_id}"
            )
        if to_id not in self._nodes:
            raise GraphEdgeError(
                f"Target node not found: {to_id}"
            )
        if reason not in EDGE_REASONS:
            raise GraphEdgeError(
                f"Invalid edge reason: {reason}. Must be one of {sorted(EDGE_REASONS)}"
            )

        from_node = self._nodes[from_id]
        to_node = self._nodes[to_id]

        similarity = _cosine_similarity(
            from_node["final_synthesis"],
            to_node["final_synthesis"],
        )
        cd_delta = (
            to_node.get("constitutional_distance", 0.0)
            - from_node.get("constitutional_distance", 0.0)
        )

        edge_id = f"E-{uuid.uuid4().hex[:12]}"
        edge = {
            "type": "edge",
            "edge_id": edge_id,
            "from_id": from_id,
            "to_id": to_id,
            "similarity": similarity,
            "reason": reason,
            "cd_delta": round(cd_delta, 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        edge["signature"] = _sign_entry(edge)
        self._edges.append(edge)
        self._append(edge)
        return edge_id

    # ── Query operations ───────────────────────────────────────────────────

    def find_related(
        self,
        vector: List[float],
        threshold: float = DEFAULT_THRESHOLD,
    ) -> List[dict]:
        """Find nodes whose final_synthesis is cosine-similar to the query vector.

        Args:
            vector:    query vector (same dimensionality as final_synthesis).
            threshold: minimum cosine similarity (default 0.70, CANNOT_MUTATE).

        Returns:
            List of node dicts sorted by similarity descending.
        """
        results = []
        for node in self._nodes.values():
            sim = _cosine_similarity(vector, node["final_synthesis"])
            if sim >= threshold:
                results.append((sim, node))
        results.sort(key=lambda x: -x[0])
        return [node for _, node in results]

    # ── Seed operations (Component 2 — coordinate propagation) ──────────

    def _verify_node_signature(self, node: dict) -> bool:
        """Verify HMAC-SHA256 signature on a stored node entry."""
        stored_sig = node.get("signature", "")
        expected = _sign_entry(node)
        return stored_sig == expected

    def seed_from(self, conversation_id: str) -> dict:
        """Retrieve a verified node for use as a preflight seed.

        Args:
            conversation_id: id of the source conversation to seed from.

        Returns:
            Full node dict including final_synthesis vector and signature.

        Raises:
            GraphNodeError: if the conversation_id does not exist or
                            if the node signature verification fails.
        """
        node = self._nodes.get(conversation_id)
        if node is None:
            raise GraphNodeError(
                f"Seed node not found: {conversation_id}"
            )
        if not self._verify_node_signature(node):
            raise GraphNodeError(
                f"Seed node signature verification failed: {conversation_id}"
            )
        return node

    def find_best_seed(
        self,
        current_vector: List[float],
        risk_clusters: Optional[List[str]] = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Optional[dict]:
        """Find the single best seed node for coordinate propagation.

        Computes cosine similarity of current_vector against all stored
        node final_synthesis vectors. Nodes sharing risk_clusters with
        the query get a +0.05 boost per shared cluster.

        Args:
            current_vector: the new conversation's intent vector.
            risk_clusters:  risk clusters of the new conversation.
            threshold:      minimum effective similarity (default 0.70).

        Returns:
            Best matching node dict, or None if no node qualifies.
        """
        risk_clusters = risk_clusters or []
        best_score = -1.0
        best_node = None

        for node in self._nodes.values():
            sim = _cosine_similarity(current_vector, node["final_synthesis"])
            # Boost for shared risk clusters
            node_clusters = node.get("risk_clusters", [])
            shared = len(set(risk_clusters) & set(node_clusters))
            effective_sim = sim + (shared * 0.05)

            if effective_sim >= threshold and effective_sim > best_score:
                best_score = effective_sim
                best_node = node

        return best_node

    def list_edges(self) -> List[dict]:
        """Return all stored edges."""
        return list(self._edges)
