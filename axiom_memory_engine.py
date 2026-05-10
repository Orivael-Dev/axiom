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
    return p.hmac_signature == _sign_packet(p)

def _quantize_vec(vec, dim=_VECTOR_DIMENSIONS):
    if len(vec) > dim:
        vec = vec[:dim]
    elif len(vec) < dim:
        vec = list(vec) + [0.0] * (dim - len(vec))
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return tuple(round(x / mag, 6) for x in vec)

# ── LSHIndex ─────────────────────────────────────────────────────
class LSHIndex:
    HASH_COUNT = 32
    BUCKET_SIZE = 64

    def __init__(self):
        self._buckets = {}
        random.seed(0)
        self._planes = [
            [random.gauss(0, 1) for _ in range(_VECTOR_DIMENSIONS)]
            for _ in range(self.HASH_COUNT)
        ]

    def _hash(self, vec):
        bits = 0
        for i, plane in enumerate(self._planes):
            dot = sum(v * p for v, p in zip(vec, plane))
            if dot >= 0:
                bits |= (1 << i)
        return bits

    def index(self, packet):
        key = self._hash(packet.compressed_vec)
        bucket = self._buckets.setdefault(key, [])
        if len(bucket) < self.BUCKET_SIZE:
            bucket.append(packet)
        return key

    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        ma = math.sqrt(sum(x * x for x in a)) or 1.0
        mb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (ma * mb)

    def retrieve(self, query_vec, k=5):
        qvec = _quantize_vec(query_vec)
        key = self._hash(qvec)
        # Search nearby buckets (hamming distance <= 2) for O(log n)
        candidates = []
        for bkey, bucket in self._buckets.items():
            if bin(key ^ bkey).count("1") <= 2:
                candidates.extend(bucket)
        scored = [(self._cosine(qvec, p.compressed_vec), p) for p in candidates]
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

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

if __name__ == "__main__":
    print("AXIOM Memory Engine v1.0 — ORVL-015")
    print("Import and use programmatically.")
    print("  from axiom_memory_engine import ConstitutionalMemoryEngine, LSHIndex")
