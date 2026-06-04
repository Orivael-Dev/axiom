"""
AXIOM MKB — Modular Constitutional Knowledge Blocks (ORVL-004).
Manifest  : mkb-impl-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Isolation : ISOLATION = True  CANNOT_MUTATE
Encoding  : UTF-8             BUG-003 compliant

Parses .axiom specs into typed KnowledgeBlocks, registers them in an
append-only HMAC-signed registry, composes pairs with CBV non-overlap
validation, and provides lookup by name, version, or block type.

BUG mitigations in this file:
  BUG-003 : sys.stdout reconfigured to utf-8; all open() calls use encoding="utf-8"
  BUG-007 : HMAC always finalised with .hexdigest() — never held as partial object
  BUG-008 : all payload strings encoded via .encode("utf-8") before HMAC/hashing
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import logging
import os
import re
import sys
import types as _types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ── BUG-003: UTF-8 stdout/stderr ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 3
ISOLATION: bool = True
REGISTRY_VERSION: int = 1
BLOCK_TYPES: frozenset = frozenset({
    "GUARD", "AGENT", "SPEC", "REWARD", "SOVEREIGN", "VALIDATOR",
})

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "ISOLATION", "REGISTRY_VERSION", "BLOCK_TYPES",
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

LOG = logging.getLogger("axiom.mkb")

# ── Block-type inference heuristics ───────────────────────────────────────
# Map PURPOSE keywords to BLOCK_TYPES.  First match wins.
_TYPE_HEURISTICS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(guard|security|pii|injection|destructive)\b", re.I), "GUARD"),
    (re.compile(r"\b(reward|reinforcement|crl|rl signal)\b", re.I), "REWARD"),
    (re.compile(r"\b(sovereign|escalat|oversight)\b", re.I), "SOVEREIGN"),
    (re.compile(r"\b(valid|boundar|cbv|certif|verify)\b", re.I), "VALIDATOR"),
    (re.compile(r"\b(spec|language|format|schema|protocol)\b", re.I), "SPEC"),
    # Default fallback — anything with an AGENT header is an AGENT
]

# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class CertificationResult:
    """Result from certifying a KnowledgeBlock."""
    passed: bool
    block_name: str
    details: list = field(default_factory=list)


@dataclass
class KnowledgeBlock:
    """A typed unit of constitutional knowledge parsed from a .axiom spec."""
    name: str
    version: str
    block_type: str
    constraints: list
    dependencies: list
    manifest_id: str          # SHA-256 hex of source file bytes
    hmac_signature: str       # HMAC-SHA256 hex over canonical fields
    _source_path: str = ""    # internal: path used for certify

    def certify(self) -> CertificationResult:
        """Validate internal consistency of this block."""
        details = []
        passed = True

        if self.block_type not in BLOCK_TYPES:
            details.append(f"Invalid block_type: {self.block_type}")
            passed = False

        if not self.name:
            details.append("Missing name")
            passed = False

        if not self.manifest_id or len(self.manifest_id) != 64:
            details.append("Invalid manifest_id")
            passed = False

        if not self.hmac_signature or len(self.hmac_signature) != 64:
            details.append("Invalid hmac_signature")
            passed = False

        # Verify manifest_id still matches source file if path is available
        if self._source_path and os.path.isfile(self._source_path):
            current_hash = hashlib.sha256(
                open(self._source_path, "rb").read()
            ).hexdigest()
            if current_hash != self.manifest_id:
                details.append("manifest_id does not match current file content")
                passed = False

        if not details:
            details.append("All checks passed")

        return CertificationResult(
            passed=passed,
            block_name=self.name,
            details=details,
        )

    def to_registry_entry(self) -> dict:
        """Produce a dict suitable for JSONL registry storage."""
        return {
            "entry_id": _entry_id(self.name, self.version),
            "name": self.name,
            "version": self.version,
            "block_type": self.block_type,
            "manifest_id": self.manifest_id,
            "constraint_count": len(self.constraints),
            "hmac_signature": self.hmac_signature,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }


@dataclass
class ComposedBlock:
    """Two KnowledgeBlocks merged into a single governance unit."""
    parent_a: str             # name of first parent
    parent_b: str             # name of second parent
    constraints: list         # merged constraint list
    hmac_signature: str       # HMAC-SHA256 over composed canonical fields
    name: str = ""            # auto-generated: "parent_a+parent_b"
    version: str = "0.1"
    block_type: str = "SPEC"  # composed blocks default to SPEC


# ── Helper functions ─────────────────────────────────────────────────────

def _entry_id(name: str, version: str) -> str:
    """SHA-256 of 'name:version' as the canonical entry identifier."""
    return hashlib.sha256(f"{name}:{version}".encode("utf-8")).hexdigest()  # BUG-008


def _sign_block(name: str, version: str, block_type: str,
                manifest_id: str, constraint_count: int,
                hmac_key: bytes) -> str:
    """Compute HMAC-SHA256 signature over canonical block fields."""
    canonical = json.dumps({
        "name": name,
        "version": version,
        "block_type": block_type,
        "manifest_id": manifest_id,
        "constraint_count": constraint_count,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
    return hmac_lib.new(hmac_key, canonical, hashlib.sha256).hexdigest()  # BUG-007


def _sign_composed(parent_a: str, parent_b: str, constraint_count: int,
                   hmac_key: bytes) -> str:
    """HMAC-SHA256 over composed block canonical fields."""
    canonical = json.dumps({
        "parent_a": parent_a,
        "parent_b": parent_b,
        "constraint_count": constraint_count,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
    return hmac_lib.new(hmac_key, canonical, hashlib.sha256).hexdigest()  # BUG-007


def _infer_block_type(purpose: str, trust_level: int) -> str:
    """Infer BLOCK_TYPE from PURPOSE keywords and TRUST_LEVEL."""
    for pattern, btype in _TYPE_HEURISTICS:
        if pattern.search(purpose):
            return btype
    # TL4 sovereign, TL1 usually guard-adjacent specs
    if trust_level >= 4:
        return "SOVEREIGN"
    return "AGENT"


# ── Parser ───────────────────────────────────────────────────────────────

def load_from_axiom(filepath: str, hmac_key: bytes) -> KnowledgeBlock:
    """Parse a .axiom spec file into a KnowledgeBlock.

    Extracts AGENT name, VERSION, TRUST_LEVEL, CONSTRAINT lines,
    and computes manifest_id + HMAC signature.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Axiom spec not found: {filepath}")

    raw = open(filepath, "rb").read()
    manifest_id = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")  # BUG-008

    name = ""
    version = ""
    trust_level = 0
    purpose = ""
    constraints: list[str] = []
    dependencies: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("AGENT "):
            name = stripped.split(None, 1)[1]
        elif stripped.startswith("VERSION "):
            version = stripped.split(None, 1)[1]
        elif stripped.startswith("TRUST_LEVEL "):
            try:
                trust_level = int(stripped.split()[-1])
            except ValueError:
                pass
        elif stripped.startswith("PURPOSE "):
            purpose = stripped.split(None, 1)[1]
        elif stripped.startswith("CONSTRAINT "):
            constraints.append(stripped.split(None, 1)[1])
        elif stripped.startswith("REQUIRES "):
            # Potential dependency reference
            deps_text = stripped.split(None, 1)[1]
            for token in re.findall(r"axiom_\w+", deps_text):
                if token not in dependencies:
                    dependencies.append(token)

    block_type = _infer_block_type(purpose, trust_level)

    signature = _sign_block(name, version, block_type, manifest_id,
                            len(constraints), hmac_key)

    return KnowledgeBlock(
        name=name,
        version=version,
        block_type=block_type,
        constraints=constraints,
        dependencies=dependencies,
        manifest_id=manifest_id,
        hmac_signature=signature,
        _source_path=filepath,
    )


# ── Registry ─────────────────────────────────────────────────────────────

class BlockRegistry:
    """Append-only HMAC-signed registry of KnowledgeBlocks.

    TRUST_LEVEL = 3 (CANNOT_MUTATE)
    ISOLATION = True (CANNOT_MUTATE)

    Optional gate_fn signature:
        gate_fn(agent_id: str, action: str, data_class: str) -> bool

    When supplied, register() calls gate_fn(agent_id, "write", block_type)
    and find() calls gate_fn(agent_id, "read", block_type) before returning.
    Pass agent_id via the optional parameter on those methods.
    """

    def __init__(self, hmac_key: bytes,
                 registry_path: str = "axiom_mkb_registry.jsonl",
                 gate_fn: Optional[Any] = None):
        self._hmac_key = hmac_key
        self._gate_fn = gate_fn
        self._registry_path = registry_path
        self._blocks: dict[str, KnowledgeBlock] = {}  # entry_id -> block
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing registry entries from disk."""
        if not os.path.isfile(self._registry_path):
            return
        try:
            with open(self._registry_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    eid = entry.get("entry_id", "")
                    if eid:
                        # Reconstruct minimal KnowledgeBlock from registry entry
                        block = KnowledgeBlock(
                            name=entry["name"],
                            version=entry["version"],
                            block_type=entry["block_type"],
                            constraints=["(from registry)"] * entry.get("constraint_count", 0),
                            dependencies=[],
                            manifest_id=entry["manifest_id"],
                            hmac_signature=entry["hmac_signature"],
                        )
                        self._blocks[eid] = block
        except (json.JSONDecodeError, KeyError) as exc:
            LOG.warning("Failed to load registry entry: %s", exc)

    def register(self, block: KnowledgeBlock, agent_id: str = "") -> str:
        """Append block to registry. Returns entry_id.

        If a gate_fn is configured and agent_id is provided, the gate
        is checked before writing.  Raises PermissionError on denial.
        """
        if self._gate_fn and agent_id:
            if not self._gate_fn(agent_id, "write", block.block_type):
                raise PermissionError(
                    f"Data gate denied: agent={agent_id!r} "
                    f"action=write data_class={block.block_type!r}"
                )
        eid = _entry_id(block.name, block.version)
        if eid in self._blocks:
            raise ValueError(
                f"Block already registered: {block.name}:{block.version} "
                f"(entry_id={eid[:16]}...)"
            )

        entry = block.to_registry_entry()

        # Sign the registry entry itself
        reg_canonical = json.dumps({
            "entry_id": entry["entry_id"],
            "name": entry["name"],
            "version": entry["version"],
            "block_type": entry["block_type"],
            "manifest_id": entry["manifest_id"],
        }, sort_keys=True, ensure_ascii=True).encode("utf-8")  # BUG-008
        entry["registry_signature"] = hmac_lib.new(
            self._hmac_key, reg_canonical, hashlib.sha256
        ).hexdigest()  # BUG-007

        with open(self._registry_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")

        self._blocks[eid] = block
        return eid

    def find(self, name: str, version: str | None = None,
             agent_id: str = "") -> Optional[KnowledgeBlock]:
        """Find a registered block by name, optionally filtered by version.

        If a gate_fn is configured and agent_id is provided, blocks that
        fail the gate are hidden (returns None rather than raising).
        """
        for block in self._blocks.values():
            if block.name == name:
                if version is None or block.version == version:
                    if self._gate_fn and agent_id:
                        if not self._gate_fn(agent_id, "read", block.block_type):
                            return None
                    return block
        return None

    def list_blocks(self, block_type: str) -> list[KnowledgeBlock]:
        """List all registered blocks of a given type."""
        return [b for b in self._blocks.values() if b.block_type == block_type]

    def quarantine(self, block_id: str) -> None:
        """Mark a block as quarantined. Raises KeyError if not found."""
        # Match by entry_id or by block name
        for eid, block in self._blocks.items():
            if eid == block_id or block.name == block_id:
                block._quarantined = True
                return
        raise KeyError(f"Block not found: {block_id}")

    def find_composed(self, block_id: str) -> list[str]:
        """Find all composed blocks that reference block_id as a parent."""
        affected = []
        for eid, block in self._blocks.items():
            if block_id in block.dependencies:
                affected.append(block.name)
        return affected

    def rebuild_without(self, composed_name: str, removed_id: str) -> None:
        """Rebuild a composed block without the removed dependency."""
        for eid, block in self._blocks.items():
            if block.name == composed_name:
                block.dependencies = [
                    d for d in block.dependencies if d != removed_id
                ]
                block.constraints = [
                    c for c in block.constraints if removed_id not in str(c)
                ]
                return

    def compose(self, block_a: KnowledgeBlock,
                block_b: KnowledgeBlock) -> ComposedBlock:
        """Compose two blocks into a ComposedBlock with CBV non-overlap check.

        Raises ValueError if CBV non-overlap check fails on merged constraints.
        """
        from axiom_cbv import CBVEngine

        merged = block_a.constraints + block_b.constraints

        # Run CBV non-overlap on the merged set
        cbv = CBVEngine(hmac_key=self._hmac_key, n_samples=200)
        result = cbv.check_non_overlap(merged, n_samples=200)

        if not result.passed:
            raise ValueError(
                f"CBV non-overlap CERT_FAIL on composition of "
                f"{block_a.name} + {block_b.name}: "
                f"{len(result.violations)} violation(s)"
            )

        composed_name = f"{block_a.name}+{block_b.name}"
        signature = _sign_composed(block_a.name, block_b.name,
                                   len(merged), self._hmac_key)

        composed = ComposedBlock(
            parent_a=block_a.name,
            parent_b=block_b.name,
            constraints=merged,
            hmac_signature=signature,
            name=composed_name,
        )

        # Register the composed block as a new entry
        composed_kb = KnowledgeBlock(
            name=composed_name,
            version="0.1",
            block_type="SPEC",
            constraints=merged,
            dependencies=[block_a.name, block_b.name],
            manifest_id=hashlib.sha256(
                json.dumps(merged, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            hmac_signature=signature,
        )
        self.register(composed_kb)

        return composed


# ── Rival approach ───────────────────────────────────────────────────────
#
# RIVAL: Flat constraint registry (no typed blocks)
#
# Instead of parsing .axiom specs into typed KnowledgeBlocks, a flat
# approach would store each CONSTRAINT line as an independent entry in
# a single registry.  Composition would be simple list concatenation
# with no structural typing.
#
# WHY WE REJECTED IT:
#   1. No block_type means no filtering — you cannot ask "list all GUARD
#      blocks" because constraints have no parent grouping.
#   2. Manifest integrity is lost — without a per-file SHA-256, you cannot
#      detect tampering of the source spec.
#   3. Certification is impossible — certify() needs the full block context
#      (name, version, type) to validate internal consistency.
#   4. Composition is unsafe — without typed blocks, CBV non-overlap check
#      has no semantic boundary to test; you'd need to test every constraint
#      pair globally, O(n^2) with n = total constraints across all specs.
#
# The typed KnowledgeBlock approach groups constraints by their source spec,
# preserves provenance via manifest_id, and enables O(1) lookup by name/type.
# ─────────────────────────────────────────────────────────────────────────


# ── CLI demo ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from axiom_signing import derive_key

    parser = argparse.ArgumentParser(description="AXIOM MKB — Knowledge Block Manager")
    parser.add_argument("--load", type=str, help="Path to .axiom spec file to load")
    parser.add_argument("--certify", action="store_true", help="Certify loaded block")
    parser.add_argument("--list-type", type=str, help="List blocks of a given type")
    parser.add_argument("--registry", type=str, default="axiom_mkb_registry.jsonl")
    args = parser.parse_args()

    key = derive_key(b"axiom-mkb-v1")

    if args.load:
        block = load_from_axiom(args.load, hmac_key=key)
        print(f"  Loaded: {block.name} v{block.version}")
        print(f"  Type:   {block.block_type}")
        print(f"  Constraints: {len(block.constraints)}")
        print(f"  Manifest: {block.manifest_id[:16]}...")
        print(f"  HMAC:     {block.hmac_signature[:16]}...")

        if args.certify:
            result = block.certify()
            status = "PASS" if result.passed else "FAIL"
            print(f"  Certify: {status}")
            for d in result.details:
                print(f"    - {d}")

    if args.list_type:
        registry = BlockRegistry(hmac_key=key, registry_path=args.registry)
        blocks = registry.list_blocks(args.list_type)
        print(f"\n  Blocks of type {args.list_type}: {len(blocks)}")
        for b in blocks:
            print(f"    {b.name} v{b.version}")
