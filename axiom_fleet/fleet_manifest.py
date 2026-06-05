"""Fleet manifest — describes a set of SRD-quantized specialist models.

Each specialist in the fleet is a ≤0.5B model packed into a signed .axm
container. The manifest records which role each specialist fills, what
modality it handles, and its AXM fingerprint so routing decisions are
cryptographically auditable.

File format: JSON (see examples/fleets/medical_fleet.json).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional


# ── Specialist config ─────────────────────────────────────────────────────────


@dataclass
class SpecialistConfig:
    """One model in the fleet."""
    role:           str            # unique name: "research", "imaging", "triage"
    modality:       str            # "text" | "vision" | "multimodal"
    domain:         str            # "medical" | "legal" | "finance" | "general" | …
    base_model:     str            # HF model ID or local path used to pack the AXM
    axm_path:       str            # path to .axm file (relative to manifest dir or absolute)
    gguf_path:      Optional[str]  # path to .gguf (text models only; None for vision)
    intent_classes: List[str]      # which intents this specialist handles
    domains:        List[str]      # domain tags (superset of `domain` for multi-domain specialists)
    bpw:            float          # bits-per-weight after SRD pack
    params_m:       int            # parameter count in millions (≤ 500 for ≤0.5B constraint)
    fingerprint:    Optional[str]  # 8-char AXM fingerprint, filled after packing
    description:    str  = ""


# ── Routing policy ────────────────────────────────────────────────────────────


@dataclass
class RoutingPolicy:
    fallback_role:         str  = "research"    # role used when nothing else matches
    image_attachment_role: str  = "imaging"     # role used when an image is attached
    harm_block:            bool = True          # block HARM/DECEIVE before routing
    uncertain_fallback:    bool = True          # route UNCERTAIN to fallback role


# ── Fleet manifest ────────────────────────────────────────────────────────────


@dataclass
class FleetManifest:
    fleet_id:     str
    version:      str
    description:  str
    specialists:  List[SpecialistConfig]
    routing:      RoutingPolicy = field(default_factory=RoutingPolicy)

    def get_specialist(self, role: str) -> Optional[SpecialistConfig]:
        for s in self.specialists:
            if s.role == role:
                return s
        return None

    def roles(self) -> List[str]:
        return [s.role for s in self.specialists]

    def by_modality(self, modality: str) -> List[SpecialistConfig]:
        return [s for s in self.specialists if s.modality == modality]

    def by_domain(self, domain: str) -> List[SpecialistConfig]:
        return [s for s in self.specialists if domain in s.domains]

    def total_params_m(self) -> int:
        return sum(s.params_m for s in self.specialists)

    def estimated_disk_gb(self) -> float:
        # bpw × params × 1e6 / 8 / 1e9 summed across specialists
        return sum(s.bpw * s.params_m * 1e6 / 8 / 1e9 for s in self.specialists)


# ── Serialisation ─────────────────────────────────────────────────────────────


def _spec_to_dict(s: SpecialistConfig) -> dict:
    return asdict(s)


def _spec_from_dict(d: dict) -> SpecialistConfig:
    return SpecialistConfig(
        role=d["role"],
        modality=d["modality"],
        domain=d["domain"],
        base_model=d["base_model"],
        axm_path=d["axm_path"],
        gguf_path=d.get("gguf_path"),
        intent_classes=d.get("intent_classes", ["INFORM", "CLARIFY"]),
        domains=d.get("domains", [d["domain"]]),
        bpw=float(d.get("bpw", 4.5)),
        params_m=int(d.get("params_m", 0)),
        fingerprint=d.get("fingerprint"),
        description=d.get("description", ""),
    )


def _policy_from_dict(d: dict) -> RoutingPolicy:
    return RoutingPolicy(
        fallback_role=d.get("fallback_role", "research"),
        image_attachment_role=d.get("image_attachment_role", "imaging"),
        harm_block=d.get("harm_block", True),
        uncertain_fallback=d.get("uncertain_fallback", True),
    )


def load_manifest(path: str | Path) -> FleetManifest:
    """Load a fleet manifest from a JSON file."""
    data = json.loads(Path(path).read_text())
    return FleetManifest(
        fleet_id=data.get("fleet_id", str(uuid.uuid4())[:8]),
        version=data.get("version", "1.0"),
        description=data.get("description", ""),
        specialists=[_spec_from_dict(s) for s in data.get("specialists", [])],
        routing=_policy_from_dict(data.get("routing", {})),
    )


def save_manifest(manifest: FleetManifest, path: str | Path) -> None:
    """Write a fleet manifest to a JSON file."""
    data = {
        "fleet_id":    manifest.fleet_id,
        "version":     manifest.version,
        "description": manifest.description,
        "specialists": [_spec_to_dict(s) for s in manifest.specialists],
        "routing": {
            "fallback_role":         manifest.routing.fallback_role,
            "image_attachment_role": manifest.routing.image_attachment_role,
            "harm_block":            manifest.routing.harm_block,
            "uncertain_fallback":    manifest.routing.uncertain_fallback,
        },
    }
    Path(path).write_text(json.dumps(data, indent=2))


def validate_manifest(manifest: FleetManifest) -> List[str]:
    """Return a list of validation error strings. Empty = valid."""
    errors: List[str] = []
    roles = set()
    for s in manifest.specialists:
        if s.role in roles:
            errors.append(f"Duplicate role: {s.role!r}")
        roles.add(s.role)
        if s.modality not in ("text", "vision", "multimodal"):
            errors.append(f"[{s.role}] Unknown modality: {s.modality!r}")
        if s.params_m > 500:
            errors.append(f"[{s.role}] params_m={s.params_m} exceeds ≤0.5B constraint")
        if s.modality == "text" and not s.gguf_path:
            errors.append(f"[{s.role}] text specialist needs gguf_path for llama.cpp inference")
    if manifest.routing.fallback_role not in roles:
        errors.append(f"fallback_role {manifest.routing.fallback_role!r} not in specialist roles")
    if manifest.routing.image_attachment_role not in roles:
        errors.append(
            f"image_attachment_role {manifest.routing.image_attachment_role!r} not in specialist roles"
        )
    return errors
