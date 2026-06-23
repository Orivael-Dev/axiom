"""
AXIOM Memory Engine v1.0 — ORVL-015
====================================
Constitutional memory compression and LSH retrieval.
Lossless for governance, lossy for language.

Usage:
  from axiom_memory_engine import ConstitutionalMemoryEngine, LSHIndex
  engine = ConstitutionalMemoryEngine("memory.jsonl", LSHIndex())
  packet = engine.remember(text, vec, domain, constraints, resolution, history)
  result = engine.recall(query_vec)

github.com/Orivael-Dev/axiom | Patent Pending ORVL-001-PROV
"""
import sys, os, json, hmac, hashlib, math, random
from dataclasses import dataclass, replace, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding="utf-8")  # BUG-003
from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-memory-engine-v1")

# ── CANNOT_MUTATE ────────────────────────────────────────────────
_COMPRESSION_TARGET = 0.05
_SIMILARITY_THRESHOLD = 0.75
_VECTOR_DIMENSIONS = 32
COMPRESSION_TARGET = _COMPRESSION_TARGET
SIMILARITY_THRESHOLD = _SIMILARITY_THRESHOLD
VECTOR_DIMENSIONS = _VECTOR_DIMENSIONS

_orig = sys.modules[__name__]
_PROTECTED = {"COMPRESSION_TARGET", "SIMILARITY_THRESHOLD", "VECTOR_DIMENSIONS"}

class _ProtectedModule(type(_orig)):
    def __setattr__(self, name, value):
        if name in _PROTECTED:
            raise AttributeError(f"{name} is CANNOT_MUTATE")
        super().__setattr__(name, value)

_orig.__class__ = _ProtectedModule

# ── ConstitutionalPacket ─────────────────────────────────────────
@dataclass(frozen=True)
class ConstitutionalPacket:
    domain_cluster: str
    active_constraints: tuple  # frozen for hashability
    boundary_proximity: float
    resolution: str
    compressed_vec: tuple      # frozen for hashability
    sovereign_history: tuple   # frozen for hashability
    token_count_original: int
    token_count_packet: int
    compression_ratio: float
    timestamp: str
    hmac_signature: str

def _sign_packet(p) -> str:
    data = {
        "domain_cluster": p.domain_cluster,
        "active_constraints": list(p.active_constraints),
        "boundary_proximity": p.boundary_proximity,
        "resolution": p.resolution,
        "compressed_vec": list(p.compressed_vec),
        "sovereign_history": list(p.sovereign_history),
        "token_count_original": p.token_count_original,
        "token_count_packet": p.token_count_packet,
        "compression_ratio": p.compression_ratio,
        "timestamp": p.timestamp,
    }
    payload = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()

def _verify_packet(p) -> bool:
    """Constant-time verify of a packet's HMAC signature.

    Uses ``hmac.compare_digest`` so a partial-match attacker cannot
    learn signature bytes by timing successive equality checks.
    """
    stored = getattr(p, "hmac_signature", "")
    expected = _sign_packet(p)
    if not isinstance(stored, str) or len(stored) != len(expected):
        return False
    return hmac.compare_digest(stored, expected)

def _quantize_vec(vec, dim=_VECTOR_DIMENSIONS):
    if len(vec) > dim:
        vec = vec[:dim]
    elif len(vec) < dim:
        vec = list(vec) + [0.0] * (dim - len(vec))
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return tuple(round(x / mag, 6) for x in vec)

# ── LSHIndex ─────────────────────────────────────────────────────
class LSHIndex:
    """Multi-table LSH index for sub-linear constitutional packet retrieval.

    Uses L independent hash tables each with b-bit bucket keys.  A query
    does one direct O(1) lookup per table → O(L) total, where L is a small
    constant.  Candidates from all tables are deduplicated then ranked by
    cosine similarity.

    Compared to the single-table + hamming-scan design, this gives:
      - True O(L) retrieval (L=8) vs O(n) hamming bucket scan
      - Higher recall: L independent "votes" to find a neighbour
      - Bounded candidate set: L × BUCKET_CAP << n
    """
    NUM_TABLES  = 8    # L independent tables
    PLANES_PER  = 8    # b bits per table → 2^8 = 256 buckets per table
    BUCKET_CAP  = 256  # max packets per bucket (FIFO eviction on overflow)

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed
        # L tables, each a dict: bucket_key → [packet, ...]
        self._tables: list[dict] = [{} for _ in range(self.NUM_TABLES)]
        rng = random.Random(seed)
        # L × b independent random hyperplanes, each of length VECTOR_DIMENSIONS
        self._planes: list[list[list[float]]] = [
            [
                [rng.gauss(0, 1) for _ in range(_VECTOR_DIMENSIONS)]
                for _ in range(self.PLANES_PER)
            ]
            for _ in range(self.NUM_TABLES)
        ]

    def _hash_table(self, vec: tuple, t: int) -> int:
        """Hash *vec* into table *t* → integer key in [0, 2^PLANES_PER)."""
        bits = 0
        for i, plane in enumerate(self._planes[t]):
            if sum(v * p for v, p in zip(vec, plane)) >= 0:
                bits |= (1 << i)
        return bits

    def index(self, packet) -> list[int]:
        """Insert *packet* into all L tables. Returns the list of bucket keys."""
        vec = packet.compressed_vec
        keys = []
        for t in range(self.NUM_TABLES):
            key = self._hash_table(vec, t)
            bucket = self._tables[t].setdefault(key, [])
            if len(bucket) < self.BUCKET_CAP:
                bucket.append(packet)
            keys.append(key)
        return keys

    @staticmethod
    def _cosine(a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        ma = math.sqrt(sum(x * x for x in a)) or 1.0
        mb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (ma * mb)

    def retrieve(self, query_vec, k: int = 5) -> list[tuple[float, object]]:
        """O(L) multi-table direct lookup — no full-index scan.

        For each of the L tables, compute the bucket key and collect its
        packets.  Deduplicate by object identity, score by cosine, return
        the top-k sorted descending.
        """
        qvec = _quantize_vec(query_vec)
        seen: set[int] = set()
        candidates = []
        for t in range(self.NUM_TABLES):
            key = self._hash_table(qvec, t)
            for p in self._tables[t].get(key, []):
                pid = id(p)
                if pid not in seen:
                    seen.add(pid)
                    candidates.append(p)
        scored = [(self._cosine(qvec, p.compressed_vec), p) for p in candidates]
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def stats(self) -> dict:
        """Return occupancy stats across all tables (useful for observability)."""
        total = sum(len(b) for t in self._tables for b in t.values())
        buckets = sum(len(t) for t in self._tables)
        return {
            "tables": self.NUM_TABLES,
            "planes_per_table": self.PLANES_PER,
            "total_entries": total,
            "populated_buckets": buckets,
        }

# ── MemoryDecay ──────────────────────────────────────────────────
class MemoryDecay:
    DECAY_FLOOR = 0.03
    DAMPEN_FACTOR = 0.5
    DOMAIN_RATES = {
        "medical": 0.97,
        "financial": 0.95,
        "os_security": 0.90,
        "general": 0.85,
    }

    def apply(self, packet, days_since_retrieved):
        rate = self.DOMAIN_RATES.get(packet.domain_cluster, 0.85)
        decayed = packet.boundary_proximity * (rate ** (days_since_retrieved * self.DAMPEN_FACTOR))
        return max(decayed, self.DECAY_FLOOR)

    def is_boundary_risk(self, packet, days):
        return self.apply(packet, days) <= self.DECAY_FLOOR

# ── FounderAgent (TRUST_LEVEL 4) ────────────────────────────────
class FounderAgent:
    TRUST_LEVEL = 4

    def compress(self, conversation_text, final_synthesis_vec, domain,
                 active_constraints, resolution, sovereign_history):
        tok_orig = len(conversation_text.split())
        vec = _quantize_vec(final_synthesis_vec)
        # Packet preserves governance, discards text (lossy for language)
        governance_text = json.dumps({
            "domain": domain, "constraints": active_constraints,
            "resolution": resolution, "history": sovereign_history,
        })
        tok_packet = len(governance_text.split())
        ratio = tok_packet / tok_orig if tok_orig > 0 else 1.0
        ts = datetime.now(timezone.utc).isoformat() + "Z"
        # Build unsigned packet first
        unsigned = ConstitutionalPacket(
            domain_cluster=domain,
            active_constraints=tuple(active_constraints),
            boundary_proximity=round(random.uniform(0.1, 1.0), 4),
            resolution=resolution,
            compressed_vec=vec,
            sovereign_history=tuple(sovereign_history),
            token_count_original=tok_orig,
            token_count_packet=tok_packet,
            compression_ratio=round(ratio, 6),
            timestamp=ts,
            hmac_signature="",
        )
        sig = _sign_packet(unsigned)
        return replace(unsigned, hmac_signature=sig)

    def store(self, packet, store_path, lsh_index):
        entry = {
            "domain_cluster": packet.domain_cluster,
            "active_constraints": list(packet.active_constraints),
            "boundary_proximity": packet.boundary_proximity,
            "resolution": packet.resolution,
            "compressed_vec": list(packet.compressed_vec),
            "sovereign_history": list(packet.sovereign_history),
            "token_count_original": packet.token_count_original,
            "token_count_packet": packet.token_count_packet,
            "compression_ratio": packet.compression_ratio,
            "timestamp": packet.timestamp,
            "hmac_signature": packet.hmac_signature,
        }
        with open(store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
        lsh_index.index(packet)
        return f"ME-{packet.domain_cluster[:3].upper()}-{packet.timestamp[:10]}"

# ── ConstitutionalMemoryEngine ───────────────────────────────────
class ConstitutionalMemoryEngine:
    def __init__(self, store_path, lsh_index):
        self._path = store_path
        self._lsh = lsh_index
        self._agent = FounderAgent()
        self._decay = MemoryDecay()

    def remember(self, conversation_text, final_synthesis_vec, domain,
                 active_constraints, resolution, sovereign_history):
        packet = self._agent.compress(
            conversation_text, final_synthesis_vec, domain,
            active_constraints, resolution, sovereign_history)
        self._agent.store(packet, self._path, self._lsh)
        return packet

    def recall(self, query_vec, domain=None):
        results = self._lsh.retrieve(query_vec)
        if domain:
            results = [(s, p) for s, p in results if p.domain_cluster == domain]
        for score, packet in results:
            if score >= _SIMILARITY_THRESHOLD and _verify_packet(packet):
                return packet
        return None

    @staticmethod
    def token_savings(packet):
        return packet.compression_ratio

# ── Text embedding (shared by remember + recall) ─────────────────
def _tokenize(text):
    out, cur = [], []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur)); cur = []
    if cur:
        out.append("".join(cur))
    return out

def embed_text(text, dim=_VECTOR_DIMENSIONS):
    """Map text to a stable ``dim``-length vector via signed feature hashing.

    Deterministic and dependency-free: identical text always yields the
    same vector, and texts sharing tokens land near each other under
    cosine similarity. Callers holding a real semantic embedding can pass
    their own vector instead — this is the zero-dependency default so the
    memory engine is usable from text alone. ``remember`` and ``recall``
    MUST use the same embedding for cosine recall to work. The engine
    quantizes and L2-normalizes downstream, so the raw accumulator is
    returned here.
    """
    vec = [0.0] * dim
    for tok in _tokenize(text):
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        vec[h[0] % dim] += 1.0 if (h[1] & 1) else -1.0
    return vec

# ── Store replay (rebuild the in-memory LSH index) ───────────────
def packet_from_dict(d):
    """Reconstruct a frozen ConstitutionalPacket from a stored JSONL row."""
    return ConstitutionalPacket(
        domain_cluster=d["domain_cluster"],
        active_constraints=tuple(d.get("active_constraints", [])),
        boundary_proximity=d["boundary_proximity"],
        resolution=d.get("resolution", ""),
        compressed_vec=tuple(d.get("compressed_vec", [])),
        sovereign_history=tuple(d.get("sovereign_history", [])),
        token_count_original=d.get("token_count_original", 0),
        token_count_packet=d.get("token_count_packet", 0),
        compression_ratio=d.get("compression_ratio", 1.0),
        timestamp=d.get("timestamp", ""),
        hmac_signature=d.get("hmac_signature", ""),
    )

def _iter_store_packets(store_path):
    """Yield ``(packet, authentic)`` for each parseable row in the store.

    Unparseable rows are skipped entirely; ``authentic`` is the result of
    the packet's HMAC verification. Shared by ``load_store`` and
    ``count_verified`` so both agree on exactly which rows count.
    """
    p = Path(store_path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                packet = packet_from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            yield packet, _verify_packet(packet)

def load_store(store_path, lsh_index):
    """Rebuild the in-memory LSH index from a persisted store.

    The LSH index lives only in memory; just the packets are persisted to
    JSONL. A fresh process (e.g. an MCP-server restart) therefore recalls
    nothing until the store is replayed. This indexes only HMAC-authentic
    rows — tampered or corrupt rows are skipped so recall never serves
    unsigned memory. Returns the number of packets indexed.
    """
    loaded = 0
    for packet, authentic in _iter_store_packets(store_path):
        if authentic:
            lsh_index.index(packet)
            loaded += 1
    return loaded

def count_verified(store_path):
    """Count only HMAC-authentic packets in the store.

    Matches exactly what ``load_store`` would index (and therefore what
    ``recall`` can serve), so callers don't over-report tampered/corrupt
    rows the engine has deliberately rejected.
    """
    return sum(1 for _, authentic in _iter_store_packets(store_path) if authentic)

if __name__ == "__main__":
    print("AXIOM Memory Engine v1.0 — ORVL-015")
    print("Import and use programmatically.")
    print("  from axiom_memory_engine import ConstitutionalMemoryEngine, LSHIndex")
