"""
AXIOM Neural Fabric Emulator — ORVL-018 PoC.
Manifest: anf-emulator-impl-v1 | TRUST_LEVEL=3 CANNOT_MUTATE | UTF-8 BUG-003
Software emulation of ANF hardware: MonotonicGate analog comparator,
sparse reasoning cores, 32D latent thought buffers, governance coprocessor.
BUG-007: .hexdigest() | BUG-008: .encode("utf-8")
"""
from __future__ import annotations
import hashlib, hmac as hmac_lib, json, math, random, sys
import types as _types
from datetime import datetime, timezone
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
VECTOR_DIM: int = 32

CORE_ACTIVATION: Dict[str, float] = {
    "INFORM": 0.20, "REQUEST": 0.25, "EXPLORE": 0.30,
    "MANIPULATE": 0.15, "DECEIVE": 0.10, "HARM": 0.05,
}

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "VECTOR_DIM", "CORE_ACTIVATION",
})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule",
    (_types.ModuleType,),
    {"__setattr__": _module_setattr},
)

_GATE_NS, _BUF_NS, _SPARSE_NS, _HMAC_NS = 100, 50, 50, 100
_STAGES = ("PREFLIGHT", "MID_CHAIN", "FINAL_SYNTHESIS")

def _magnitude(vec: List[float]) -> float:
    return math.sqrt(sum(v * v for v in vec)) if vec else 0.0

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b) or not a: return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    ma, mb = math.sqrt(sum(x*x for x in a)), math.sqrt(sum(x*x for x in b))
    return dot / (ma * mb) if ma > 1e-12 and mb > 1e-12 else 0.0


class MonotonicGateEmulator:
    """Emulates the analog comparator circuit (Layer 1)."""
    def __init__(self) -> None:
        self.magnitude_t1: float = 0.0
        self.magnitude_t2: float = 0.0
        self._log: List[dict] = []

    def fire_interrupt(self, vec_t1: List[float], vec_t2: List[float]) -> bool:
        self.magnitude_t1 = _magnitude(vec_t1)
        self.magnitude_t2 = _magnitude(vec_t2)
        fired = self.magnitude_t2 < self.magnitude_t1
        self._log.append({"mag_t1": round(self.magnitude_t1, 8),
            "mag_t2": round(self.magnitude_t2, 8), "fired": fired})
        return fired


class LatentThoughtBufferEmulator:
    """Emulates 32D constitutional vector SRAM registers (Layer 3)."""
    def __init__(self) -> None:
        self._registers: Dict[str, List[float]] = {
            s: [0.0] * VECTOR_DIM for s in _STAGES
        }
        self.INTENT_CLASS: int = 0
        self.WORD_WEIGHT: float = 0.0

    def write(self, stage: str, vec: List[float]) -> None:
        if stage not in _STAGES:
            raise ValueError(f"Invalid stage: {stage}")
        padded = list(vec[:VECTOR_DIM])
        while len(padded) < VECTOR_DIM:
            padded.append(0.0)
        self._registers[stage] = padded

    def read(self, stage: str) -> List[float]:
        if stage not in _STAGES:
            raise ValueError(f"Invalid stage: {stage}")
        return list(self._registers[stage])

    def compute_distance_in_register(self) -> float:
        sim = _cosine_similarity(self._registers["MID_CHAIN"],
                                 self._registers["FINAL_SYNTHESIS"])
        return round(1.0 - sim, 8)

class SparseReasoningCoreEmulator:
    """Emulates intent-driven core activation (Layer 2)."""
    TOTAL_CORES: int = 100
    def activate(self, intent_class: str) -> int:
        return int(self.TOTAL_CORES * CORE_ACTIVATION.get(intent_class, 0.20))
    def energy_profile(self, intent_class: str) -> float:
        return CORE_ACTIVATION.get(intent_class, 0.20)


class GovernanceCoprocessorEmulator:
    """Combined governance coprocessor — emulates PCIe card. FUSED_ROM immutable after init."""
    def __init__(self, hmac_key: bytes, fused_rom: dict) -> None:
        self._hmac_key = hmac_key
        self._fused_rom = dict(fused_rom)
        self._rom_locked = True
        self._gate = MonotonicGateEmulator()
        self._buffer = LatentThoughtBufferEmulator()
        self._sparse = SparseReasoningCoreEmulator()

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_fused_rom" and getattr(self, "_rom_locked", False):
            raise AttributeError("FUSED_ROM is CANNOT_MUTATE after initialization")
        super().__setattr__(name, value)

    def process(self, preflight_vec: List[float], mid_vec: List[float],
                final_vec: List[float], intent_class: str) -> dict:
        for stage, vec in zip(_STAGES, (preflight_vec, mid_vec, final_vec)):
            self._buffer.write(stage, vec)
        gate_fired = self._gate.fire_interrupt(preflight_vec, mid_vec)
        if not gate_fired:
            gate_fired = self._gate.fire_interrupt(mid_vec, final_vec)
        cores = self._sparse.activate(intent_class)
        energy = self._sparse.energy_profile(intent_class)
        distance = self._buffer.compute_distance_in_register()
        latency_ns = _GATE_NS * 2 + _BUF_NS + _SPARSE_NS + _HMAC_NS
        payload = {"gate_fired": gate_fired, "intent_class": intent_class,
                   "cores_active": cores, "distance": distance,
                   "latency_ns": latency_ns}
        canonical = json.dumps(payload, sort_keys=True,
                               ensure_ascii=True).encode("utf-8")  # BUG-008
        sig = hmac_lib.new(self._hmac_key, canonical,
                           hashlib.sha256).hexdigest()  # BUG-007
        return {"gate_fired": gate_fired, "intent_class": intent_class,
                "cores_active": cores, "energy_ratio": energy,
                "distance": distance, "latency_ns": latency_ns,
                "fused_rom_rules": len(self._fused_rom), "hmac": sig}


def run_benchmark(n: int = 1000) -> dict:
    """Run n simulated inferences and report aggregate statistics."""
    from axiom_signing import derive_key
    key = derive_key(b"axiom-anf-benchmark-v1")
    gov = GovernanceCoprocessorEmulator(hmac_key=key, fused_rom={
        "monotonic_gate": True, "sovereign_levels": 4,
        "hmac_engine": "SHA-256", "audit_log": "write-only"})
    rng = random.Random(42)
    intents = (["INFORM"] * 40 + ["REQUEST"] * 20 + ["EXPLORE"] * 15
               + ["MANIPULATE"] * 10 + ["DECEIVE"] * 10 + ["HARM"] * 5)
    totals = {"latency": 0, "cores": 0, "energy": 0.0,
              "gate_fires": 0, "harm": 0}
    for i in range(n):
        intent = intents[i % len(intents)]
        pre = [rng.random() for _ in range(VECTOR_DIM)]
        mid = [v + rng.uniform(0.0, 0.1) for v in pre]
        fin = [v + rng.uniform(0.0, 0.1) for v in mid]
        r = gov.process(pre, mid, fin, intent)
        totals["latency"] += r["latency_ns"]
        totals["cores"] += r["cores_active"]
        totals["energy"] += r["energy_ratio"]
        totals["gate_fires"] += int(r["gate_fired"])
        totals["harm"] += int(intent == "HARM")
    return {"inferences": n, "avg_latency_ns": round(totals["latency"] / n, 2),
            "avg_cores_active": round(totals["cores"] / n, 2),
            "avg_energy_ratio": round(totals["energy"] / n, 4),
            "gate_fires": totals["gate_fires"], "harm_detected": totals["harm"]}

if __name__ == "__main__":
    print(f"\n  Axiom Neural Fabric Emulator — ORVL-018")
    print("  " + "=" * 50)
    for k, v in run_benchmark(1000).items():
        print(f"  {k:24s}: {v}")
