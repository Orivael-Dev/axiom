"""
AXIOM eXchange Model (.AXM) — ORVL-023 software emulator.

A successor-to-GGUF container that treats a model as a living execution
graph rather than a frozen weight file. The container is a directory tree
with six modules:

  header.json                 Semantic state header (signed)
  core/core.json              Core Logic Module manifest
  delegates/<name>/skill.json Per-skill manifest (each signed independently)
  trajectories/*.jsonl        Pre-compiled reasoning trajectories
  vertices.json               Vector-Vertex DB (class → geometry primitive)
  proofs/ledger.jsonl         HMAC-signed proof entries (one per sub-module)

Trust model: HYBRID (user-selected from the AXM brief §5).
  • Container header signed under derive_key("axiom-axm-container-v1")
  • Each skill delegate signed under derive_key("axiom-axm-delegate-v1")
  • Proof ledger signed under  derive_key("axiom-axm-proof-v1")
  No encryption — open container, signed sub-modules, sandboxed activation.

This is an architectural emulator. It does NOT load real weights, run
inference, or perform bit-serialized arithmetic — those are hardware
concerns and out of scope. It exercises the AXM container shape against
three sibling patents: ORVL-004 (MKB BlockRegistry), ORVL-018 (ANF
governance coprocessor), and ORVL-019 (Sovereign Phone Neural Compute
Block).

Trust  : TRUST_LEVEL = 1   (Master — the container is the runtime authority)
Encoding: UTF-8   BUG-003 compliant
HMAC   : SHA-256 over canonical JSON, finalised with .hexdigest()
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import os
import shutil
import sys
import time
import types as _types
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 1
FORMAT_VERSION: str = "0.1-concept"
HARDWARE_MAPS: Tuple[str, ...] = (
    "compile_on_load", "cpu", "gpu", "npu", "fpga",
)

_FROZEN = frozenset({"TRUST_LEVEL", "FORMAT_VERSION", "HARDWARE_MAPS"})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})


# ── Exceptions ────────────────────────────────────────────────────────────
class AXMError(Exception):
    """Base for .AXM container errors."""


class AXMSignatureMismatch(AXMError):
    """An HMAC signature did not verify."""


class AXMNotVerified(AXMError):
    """route() was called before verify_proofs() returned True."""


# ── Signing helpers ───────────────────────────────────────────────────────
def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac_lib.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def _verify(key: bytes, payload: Mapping[str, Any], signature: str) -> bool:
    expected = _sign(key, payload)
    return hmac_lib.compare_digest(expected, signature)


def _sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file. Matches bundle_v1_8.sha256_file()."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _container_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-axm-container-v1")


def _delegate_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-axm-delegate-v1")


def _proof_key() -> bytes:
    from axiom_signing import derive_key
    return derive_key(b"axiom-axm-proof-v1")


# ── Frozen dataclasses (every record HMAC-signed) ─────────────────────────
@dataclass(frozen=True)
class AXMHeader:
    """Semantic State Header — replaces GGUF's flat key-value metadata."""
    format_version: str
    core_logic:     str
    quant_map:      str
    delegates:      Tuple[str, ...]
    safety_proofs:  bool
    hardware_map:   str
    signature:      str = ""

    def _payload(self) -> dict:
        return {
            "format_version": self.format_version,
            "core_logic":     self.core_logic,
            "quant_map":      self.quant_map,
            "delegates":      list(self.delegates),
            "safety_proofs":  self.safety_proofs,
            "hardware_map":   self.hardware_map,
        }


@dataclass(frozen=True)
class SkillDelegate:
    name:            str
    when_condition:  str
    intent_classes:  Tuple[str, ...]
    weight_manifest: str
    signature:       str = ""

    def _payload(self) -> dict:
        return {
            "name":            self.name,
            "when_condition":  self.when_condition,
            "intent_classes":  list(self.intent_classes),
            "weight_manifest": self.weight_manifest,
        }


@dataclass(frozen=True)
class TrajectoryBlock:
    id:              str
    task_pattern:    str
    action_sequence: Tuple[str, ...]
    signature:       str = ""


@dataclass(frozen=True)
class VectorVertexEntry:
    semantic_class: str
    vertex_cluster: str
    signature:      str = ""


@dataclass(frozen=True)
class ProofLedgerEntry:
    subject:   str
    claim:     str
    file_sha:  str
    signature: str = ""

    def _payload(self) -> dict:
        return {"subject": self.subject, "claim": self.claim,
                "file_sha": self.file_sha}


@dataclass(frozen=True)
class AXMRouteResult:
    task:            str
    intent_class:    str
    confidence:      float
    loaded_skills:   Tuple[str, ...]
    skipped_skills:  Tuple[str, ...]
    anf_distance:    float
    anf_cores_active: int
    timestamp:       str
    signature:       str = ""


# ── Container ─────────────────────────────────────────────────────────────
class AXMContainer:
    """Directory-tree container for an AXM model.

    Loading the container verifies the header signature and reads every
    sub-module. Skill delegates are NOT registered with MKB until route()
    decides they match the current task — the "active loading principle"
    from the AXM brief §2.
    """

    # ── construction ──────────────────────────────────────────────────
    def __init__(self, path: Path, header: AXMHeader,
                 delegates: List[SkillDelegate],
                 trajectories: List[TrajectoryBlock],
                 vertices: List[VectorVertexEntry],
                 proofs: List[ProofLedgerEntry]):
        self._path        = path
        self._header      = header
        self._delegates   = delegates
        self._trajectories = trajectories
        self._vertices    = vertices
        self._proofs      = proofs
        self._verified    = False
        self._loaded:     dict = {}      # name -> SkillDelegate
        self._mkb_registry = None        # built lazily on first route()

    # ── public read-only properties ───────────────────────────────────
    @property
    def path(self) -> Path:               return self._path
    @property
    def header(self) -> AXMHeader:        return self._header
    @property
    def delegates(self) -> Tuple[SkillDelegate, ...]:
        return tuple(self._delegates)
    @property
    def trajectories(self) -> Tuple[TrajectoryBlock, ...]:
        return tuple(self._trajectories)
    @property
    def vertices(self) -> Tuple[VectorVertexEntry, ...]:
        return tuple(self._vertices)
    @property
    def proofs(self) -> Tuple[ProofLedgerEntry, ...]:
        return tuple(self._proofs)
    @property
    def loaded_skills(self) -> Tuple[str, ...]:
        return tuple(sorted(self._loaded.keys()))
    @property
    def verified(self) -> bool:           return self._verified

    def fingerprint(self) -> str:
        """First 8 hex of HMAC(container_key, header signature). Safe
        to display — does not leak the key."""
        return hmac_lib.new(_container_key(),
                            self._header.signature.encode("utf-8"),
                            hashlib.sha256).hexdigest()[:8]

    def __repr__(self) -> str:
        return (f"<AXMContainer fp={self.fingerprint()} "
                f"delegates={len(self._delegates)} "
                f"proofs={len(self._proofs)} verified={self._verified}>")

    __str__ = __repr__

    # ── factory: load from disk ───────────────────────────────────────
    @classmethod
    def from_path(cls, path: str) -> "AXMContainer":
        root = Path(path)
        if not root.is_dir():
            raise AXMError(f"not a directory: {path}")
        header = cls._load_header(root)
        delegates    = cls._load_delegates(root)
        trajectories = cls._load_trajectories(root)
        vertices     = cls._load_vertices(root)
        proofs       = cls._load_proofs(root)
        return cls(root, header, delegates, trajectories, vertices, proofs)

    @staticmethod
    def _load_header(root: Path) -> AXMHeader:
        p = root / "header.json"
        if not p.exists():
            raise AXMError("header.json missing")
        data = json.loads(p.read_text(encoding="utf-8"))
        header = AXMHeader(
            format_version=data["format_version"],
            core_logic=data["core_logic"],
            quant_map=data["quant_map"],
            delegates=tuple(data.get("delegates") or ()),
            safety_proofs=bool(data.get("safety_proofs", True)),
            hardware_map=data.get("hardware_map", "compile_on_load"),
            signature=data.get("signature", ""),
        )
        if not _verify(_container_key(), header._payload(), header.signature):
            raise AXMSignatureMismatch("header.json signature failed to verify")
        return header

    @staticmethod
    def _load_delegates(root: Path) -> List[SkillDelegate]:
        out: List[SkillDelegate] = []
        deldir = root / "delegates"
        if not deldir.exists():
            return out
        for sub in sorted(deldir.iterdir()):
            if not sub.is_dir():
                continue
            manifest = sub / "skill.json"
            if not manifest.exists():
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
            sd = SkillDelegate(
                name=data["name"],
                when_condition=data["when_condition"],
                intent_classes=tuple(data.get("intent_classes") or ()),
                weight_manifest=data.get("weight_manifest", ""),
                signature=data.get("signature", ""),
            )
            out.append(sd)
        return out

    @staticmethod
    def _load_trajectories(root: Path) -> List[TrajectoryBlock]:
        out: List[TrajectoryBlock] = []
        traj = root / "trajectories"
        if not traj.exists():
            return out
        for p in sorted(traj.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                out.append(TrajectoryBlock(
                    id=data["id"],
                    task_pattern=data["task_pattern"],
                    action_sequence=tuple(data.get("action_sequence") or ()),
                    signature=data.get("signature", ""),
                ))
        return out

    @staticmethod
    def _load_vertices(root: Path) -> List[VectorVertexEntry]:
        p = root / "vertices.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return [VectorVertexEntry(
            semantic_class=e["semantic_class"],
            vertex_cluster=e["vertex_cluster"],
            signature=e.get("signature", ""),
        ) for e in data]

    @staticmethod
    def _load_proofs(root: Path) -> List[ProofLedgerEntry]:
        p = root / "proofs" / "ledger.jsonl"
        if not p.exists():
            return []
        out: List[ProofLedgerEntry] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            out.append(ProofLedgerEntry(
                subject=data["subject"],
                claim=data["claim"],
                file_sha=data["file_sha"],
                signature=data.get("signature", ""),
            ))
        return out

    # ── factory: pack from a Python spec ──────────────────────────────
    @classmethod
    def pack(cls, spec: Mapping[str, Any], output_path: str) -> "AXMContainer":
        """Build a fresh container under `output_path` from a dict spec.

        Spec shape (all keys optional except `core_logic`):
            {
                "format_version": "0.1-concept",
                "core_logic": "axiom_core_3b",
                "quant_map": "elastic_per_layer",
                "hardware_map": "compile_on_load",
                "core": {<core.json contents>},
                "delegates": [
                    {"name": ..., "when_condition": ...,
                     "intent_classes": [...], "weight_manifest": ...},
                    ...
                ],
                "trajectories": [
                    {"id": ..., "task_pattern": ...,
                     "action_sequence": [...]}, ...
                ],
                "vertices": [
                    {"semantic_class": ..., "vertex_cluster": ...}, ...
                ],
            }
        """
        root = Path(output_path)
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)

        # Core
        core_dir = root / "core"
        core_dir.mkdir()
        core_data = spec.get("core") or {
            "name":   spec.get("core_logic", "axiom_core_3b"),
            "params": "3B (stub)",
            "quant_map": spec.get("quant_map", "elastic_per_layer"),
        }
        (core_dir / "core.json").write_text(
            json.dumps(core_data, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

        # Delegates
        dkey = _delegate_key()
        delegate_names: List[str] = []
        for dspec in spec.get("delegates", []) or []:
            name = dspec["name"]
            sd = SkillDelegate(
                name=name,
                when_condition=dspec["when_condition"],
                intent_classes=tuple(dspec.get("intent_classes") or ()),
                weight_manifest=dspec.get("weight_manifest",
                                          f"delegates/{name}/weights.bin"),
            )
            payload = sd._payload()
            signed = SkillDelegate(**{**asdict(sd),
                                       "signature": _sign(dkey, payload)})
            ddir = root / "delegates" / name
            ddir.mkdir(parents=True)
            (ddir / "skill.json").write_text(
                json.dumps({**payload, "signature": signed.signature},
                            indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
            delegate_names.append(name)

        # Trajectories
        tdir = root / "trajectories"
        tdir.mkdir()
        traj_lines: List[TrajectoryBlock] = []
        if spec.get("trajectories"):
            with open(tdir / "history.jsonl", "w", encoding="utf-8") as fh:
                for t in spec["trajectories"]:
                    tb = TrajectoryBlock(
                        id=t["id"],
                        task_pattern=t["task_pattern"],
                        action_sequence=tuple(t.get("action_sequence") or ()),
                    )
                    payload = {"id": tb.id, "task_pattern": tb.task_pattern,
                               "action_sequence": list(tb.action_sequence)}
                    sig = _sign(_proof_key(), payload)
                    fh.write(json.dumps({**payload, "signature": sig},
                                          ensure_ascii=True) + "\n")
                    traj_lines.append(TrajectoryBlock(**{**asdict(tb),
                                                          "signature": sig}))

        # Vertices
        vert_list: List[VectorVertexEntry] = []
        if spec.get("vertices"):
            entries = []
            for v in spec["vertices"]:
                vv = VectorVertexEntry(
                    semantic_class=v["semantic_class"],
                    vertex_cluster=v["vertex_cluster"],
                )
                payload = {"semantic_class": vv.semantic_class,
                           "vertex_cluster": vv.vertex_cluster}
                sig = _sign(_proof_key(), payload)
                entries.append({**payload, "signature": sig})
                vert_list.append(VectorVertexEntry(**{**asdict(vv),
                                                       "signature": sig}))
            (root / "vertices.json").write_text(
                json.dumps(entries, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )

        # Header — must come AFTER delegates so the delegate list is real.
        header = AXMHeader(
            format_version=spec.get("format_version", FORMAT_VERSION),
            core_logic=spec.get("core_logic", "axiom_core_3b"),
            quant_map=spec.get("quant_map", "elastic_per_layer"),
            delegates=tuple(delegate_names),
            safety_proofs=bool(spec.get("safety_proofs", True)),
            hardware_map=spec.get("hardware_map", "compile_on_load"),
        )
        if header.hardware_map not in HARDWARE_MAPS:
            raise AXMError(f"hardware_map must be one of {HARDWARE_MAPS}")
        hsig = _sign(_container_key(), header._payload())
        header = AXMHeader(**{**asdict(header), "signature": hsig})
        (root / "header.json").write_text(
            json.dumps({**header._payload(), "signature": header.signature},
                        indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

        # Proof ledger — one entry per sub-module, hashing its on-disk file.
        proofs_dir = root / "proofs"
        proofs_dir.mkdir()
        proof_entries: List[ProofLedgerEntry] = []
        targets = [("header", root / "header.json"),
                   ("core",   root / "core" / "core.json")]
        for name in delegate_names:
            targets.append((f"delegate:{name}",
                            root / "delegates" / name / "skill.json"))
        if (root / "vertices.json").exists():
            targets.append(("vertices", root / "vertices.json"))
        for tname, tpath in targets:
            file_sha = _sha256_file(tpath)
            payload = {"subject": tname,
                       "claim":   "module integrity at pack time",
                       "file_sha": file_sha}
            sig = _sign(_proof_key(), payload)
            proof_entries.append(ProofLedgerEntry(**{**payload, "signature": sig}))
        with open(proofs_dir / "ledger.jsonl", "w", encoding="utf-8") as fh:
            for pe in proof_entries:
                fh.write(json.dumps({**pe._payload(),
                                       "signature": pe.signature},
                                      ensure_ascii=True) + "\n")

        return cls(root, header,
                   cls._load_delegates(root),
                   traj_lines, vert_list, proof_entries)

    # ── inspect / verify / route ──────────────────────────────────────
    def inspect(self) -> dict:
        return {
            "path":             str(self._path),
            "fingerprint":      self.fingerprint(),
            "header": {
                "format_version": self._header.format_version,
                "core_logic":     self._header.core_logic,
                "quant_map":      self._header.quant_map,
                "hardware_map":   self._header.hardware_map,
                "delegates":      list(self._header.delegates),
            },
            "delegate_count":   len(self._delegates),
            "trajectory_count": len(self._trajectories),
            "vertex_count":     len(self._vertices),
            "proof_count":      len(self._proofs),
            "verified":         self._verified,
            "loaded_skills":    list(self.loaded_skills),
        }

    def verify_proofs(self, anf_emulator=None) -> bool:
        """Verify every signed sub-module + every proof-ledger entry +
        drive the ANF coprocessor once per proof (ORVL-018 wiring).

        Returns True iff every check passes. Sets self._verified."""
        # Header is already verified at load time. Re-check delegates and
        # vertices and trajectories — any tampered byte fails here.
        dkey = _delegate_key()
        for d in self._delegates:
            if not _verify(dkey, d._payload(), d.signature):
                return False
        pkey = _proof_key()
        for t in self._trajectories:
            payload = {"id": t.id, "task_pattern": t.task_pattern,
                       "action_sequence": list(t.action_sequence)}
            if not _verify(pkey, payload, t.signature):
                return False
        for v in self._vertices:
            payload = {"semantic_class": v.semantic_class,
                       "vertex_cluster": v.vertex_cluster}
            if not _verify(pkey, payload, v.signature):
                return False
        for p in self._proofs:
            if not _verify(pkey, p._payload(), p.signature):
                return False

        # Drive ANF for each proof — the brief §3 row "Verification: formal
        # logic proofs and runtime safety claims embedded in the format"
        # maps onto the ANF Governance Coprocessor's process() pipeline.
        if anf_emulator is None:
            from axiom_anf_emulator import GovernanceCoprocessorEmulator
            from axiom_signing import derive_key
            anf_emulator = GovernanceCoprocessorEmulator(
                hmac_key=derive_key(b"axiom-axm-anf-v1"),
                fused_rom={"axm_proof_check": True},
            )
        # Map header.hardware_map onto an ANF intent class.
        hw_to_intent = {
            "compile_on_load": "INFORM", "cpu": "REQUEST", "gpu": "EXPLORE",
            "npu": "INFORM", "fpga": "EXPLORE",
        }
        anf_class = hw_to_intent.get(self._header.hardware_map, "INFORM")
        for _ in self._proofs:
            VEC = 32
            anf_emulator.process(
                [0.3] * VEC, [0.6] * VEC, [0.9] * VEC, anf_class,
            )
        self._verified = True
        return True

    def _matches(self, skill: SkillDelegate, intent_class: str) -> bool:
        if skill.when_condition.strip().lower() == "always":
            return True
        return intent_class in skill.intent_classes

    def _ensure_mkb(self):
        if self._mkb_registry is not None:
            return self._mkb_registry
        from axiom_mkb import BlockRegistry
        from axiom_signing import derive_key
        # Per-container registry path — keeps test runs isolated.
        registry_path = str(self._path / "_mkb_registry.jsonl")
        self._mkb_registry = BlockRegistry(
            hmac_key=derive_key(b"axiom-axm-mkb-v1"),
            registry_path=registry_path,
        )
        return self._mkb_registry

    def _register_skill_with_mkb(self, skill: SkillDelegate) -> None:
        from axiom_mkb import KnowledgeBlock
        registry = self._ensure_mkb()
        if registry.find(skill.name) is not None:
            return  # idempotent: don't double-register
        # manifest_id is the SHA-256 of the on-disk skill.json
        skill_path = self._path / "delegates" / skill.name / "skill.json"
        manifest_id = _sha256_file(skill_path) if skill_path.exists() else "0" * 64
        kb = KnowledgeBlock(
            name=skill.name,
            version="0.1",
            block_type="AXM_SKILL",
            constraints=[f"WHEN {skill.when_condition}"],
            dependencies=[],
            manifest_id=manifest_id,
            hmac_signature=skill.signature,
        )
        registry.register(kb)

    def route(self, task: str, classifier=None,
              session_id: Optional[str] = None) -> AXMRouteResult:
        """Classify a task, decide which delegates match, lazy-load them
        into the MKB BlockRegistry, return a signed route result.

        Enforces the constitutional rule: verify_proofs() must have
        returned True before any skill activates."""
        if not self._verified:
            raise AXMNotVerified(
                "call verify_proofs() before route() — "
                "the AXM spec rejects activation without proof verification"
            )

        # Pick a classifier if the caller didn't supply one.
        if classifier is None:
            from axiom_intent_classifier import IntentClassifier
            from axiom_signing import derive_key
            classifier = IntentClassifier(derive_key(b"axiom-axm-classifier-v1"))
        result = classifier.classify(task)

        loaded:   List[str] = []
        skipped:  List[str] = []
        for d in self._delegates:
            if self._matches(d, result.intent_class):
                if d.name not in self._loaded:
                    self._loaded[d.name] = d
                    self._register_skill_with_mkb(d)
                loaded.append(d.name)
            else:
                skipped.append(d.name)

        # Drive the ANF emulator for the route itself — every route is one
        # constitutional decision through the fabric. Reuse one emulator
        # for the lifetime of this container.
        from axiom_anf_emulator import GovernanceCoprocessorEmulator
        from axiom_signing import derive_key
        anf = GovernanceCoprocessorEmulator(
            hmac_key=derive_key(b"axiom-axm-anf-v1"),
            fused_rom={"axm_route": True},
        )
        VEC = 32
        c = float(result.confidence)
        anf_result = anf.process(
            [0.3 * c] * VEC, [0.6 * c] * VEC, [0.9 * c] * VEC,
            "INFORM" if result.intent_class in ("INFORM", "CLARIFY")
            else "MANIPULATE" if result.intent_class == "DECEIVE"
            else "HARM" if result.intent_class == "HARM"
            else "EXPLORE",
        )

        from datetime import datetime, timezone
        payload = {
            "task":             task[:120],
            "intent_class":     result.intent_class,
            "confidence":       round(c, 4),
            "loaded_skills":    sorted(loaded),
            "skipped_skills":   sorted(skipped),
            "anf_distance":     anf_result["distance"],
            "anf_cores_active": anf_result["cores_active"],
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }
        sig = _sign(_container_key(), payload)
        return AXMRouteResult(
            task=payload["task"], intent_class=payload["intent_class"],
            confidence=payload["confidence"],
            loaded_skills=tuple(payload["loaded_skills"]),
            skipped_skills=tuple(payload["skipped_skills"]),
            anf_distance=payload["anf_distance"],
            anf_cores_active=payload["anf_cores_active"],
            timestamp=payload["timestamp"],
            signature=sig,
        )

    def unload_skills(self) -> int:
        """Free all currently loaded skill delegates. Returns count freed.
        Power-saving hook (per AXM brief §2 'active loading principle')."""
        n = len(self._loaded)
        self._loaded.clear()
        return n


# ── CLI entry-point ───────────────────────────────────────────────────────
def _main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="axiom_axm",
        description="AXIOM eXchange Model (.AXM) — ORVL-023 container emulator",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    p_inspect = sub.add_parser("inspect", help="print header + module counts")
    p_inspect.add_argument("container")
    p_verify = sub.add_parser("verify", help="verify every signature + drive ANF")
    p_verify.add_argument("container")
    p_route = sub.add_parser("route", help="classify a task and lazy-load delegates")
    p_route.add_argument("container")
    p_route.add_argument("task")
    p_pack = sub.add_parser("pack", help="write the starter container")
    p_pack.add_argument("output")
    args = parser.parse_args(argv)

    if args.action == "inspect":
        c = AXMContainer.from_path(args.container)
        print(json.dumps(c.inspect(), indent=2, ensure_ascii=True))
        return 0
    if args.action == "verify":
        c = AXMContainer.from_path(args.container)
        ok = c.verify_proofs()
        print(json.dumps({"verified": ok, "proofs_checked": len(c.proofs),
                            "fingerprint": c.fingerprint()},
                          indent=2, ensure_ascii=True))
        return 0 if ok else 1
    if args.action == "route":
        c = AXMContainer.from_path(args.container)
        c.verify_proofs()
        r = c.route(args.task)
        print(json.dumps({
            "task":            r.task, "intent_class": r.intent_class,
            "confidence":      r.confidence,
            "loaded_skills":   list(r.loaded_skills),
            "skipped_skills":  list(r.skipped_skills),
            "anf_distance":    r.anf_distance,
            "anf_cores_active": r.anf_cores_active,
            "signature":       r.signature,
        }, indent=2, ensure_ascii=True))
        return 0
    if args.action == "pack":
        from examples.axm_pack_starter import STARTER_SPEC  # type: ignore
        c = AXMContainer.pack(STARTER_SPEC, args.output)
        print(f"packed {args.output} — {len(c.delegates)} delegates, "
                f"{len(c.proofs)} proofs, fingerprint {c.fingerprint()}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
