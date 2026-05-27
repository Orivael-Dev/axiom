"""AXM Medical Research Container — the sealed session vault.

Wraps the existing `axiom_axm.AXMContainer` with the medical-specific
spec dict described in PDF section 2: research_question,
governance_profile, allowed_sources, disallowed_outputs,
claim_graph, signed_manifest, human_review_threshold,
event_token_refs[].

Constitutional rule (PDF section 7): a defined set of fields
CANNOT_MUTATE once the container is sealed — they are signed into
the AXM header + proof ledger, and any subsequent attempt to
re-pack the container with a different value for those fields
must be refused (see `verify_cannot_mutate`).

The medical spec rides inside `spec["core"]` so the existing
AXMContainer machinery serializes + tamper-detects it for free
(the proof ledger at `axiom_axm.py:614-624` hashes `core/core.json`).

Public API:
    MedicalContainerSpec — validated dataclass for the spec dict
    build_medical_container(spec, output_path) -> AXMContainer
    load_medical_container(path)              -> (AXMContainer, dict)
    verify_cannot_mutate(before, after)       -> list[str]
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from axiom_medical_safety import (
    EVIDENCE_TIER_REGISTRY,
    verify_cannot_mutate as _verify_cannot_mutate,
)


# ── Constants ────────────────────────────────────────────────────────


CONTAINER_TYPE = "AXM_MEDICAL_RESEARCH"
CORE_LOGIC = "medical-research-v1"

MEDICAL_GOVERNANCE_PROFILE = "healthcare.axiom.v1"

ALLOWED_GOVERNANCE_PROFILES: frozenset[str] = frozenset({
    MEDICAL_GOVERNANCE_PROFILE,
})

ALLOWED_SOURCE_REGISTRIES: frozenset[str] = frozenset({
    "pubmed", "clinicaltrials", "fda", "nih", "who",
    "cdc", "ema", "cochrane", "major_journals",
})

MEDICAL_DEFAULT_DISALLOWED_OUTPUTS: tuple[str, ...] = (
    "diagnosis",
    "personal treatment plan",
    "unsupported clinical claim",
    "dosing recommendation",
)

HUMAN_REVIEW_THRESHOLDS: frozenset[str] = frozenset({
    "never",
    "high_risk_only",
    "patient_specific_or_high_risk",
    "all",
})

# Fields whose value is signed into the container at pack time and
# must NEVER be silently changed mid-session. Any mismatch between a
# container's on-disk core.json and the in-memory spec under these
# names triggers a refusal to operate.
CANNOT_MUTATE_FIELDS: frozenset[str] = frozenset({
    "research_question",
    "governance_profile",
    "allowed_sources",
    "evidence_tier_rules_hash",
    "hipaa_phi_policy",
    "clinical_advice_boundary",
    "citation_requirement",
    "human_review_threshold",
})


# ── Exceptions ───────────────────────────────────────────────────────


class MedicalContainerError(ValueError):
    """Spec validation failure or CANNOT_MUTATE violation."""


# ── Spec dataclass ──────────────────────────────────────────────────


@dataclass(frozen=True)
class MedicalContainerSpec:
    """Validated medical-research container spec.

    The dataclass enforces PDF section 2's schema. Use `.to_core()`
    to render the dict that gets written into `spec["core"]` on the
    AXM container.
    """
    container_id:            str
    research_question:       str
    governance_profile:      str           = MEDICAL_GOVERNANCE_PROFILE
    allowed_sources:         tuple[str, ...] = tuple(
        sorted(ALLOWED_SOURCE_REGISTRIES)
    )
    disallowed_outputs:      tuple[str, ...] = MEDICAL_DEFAULT_DISALLOWED_OUTPUTS
    human_review_threshold:  str           = "patient_specific_or_high_risk"
    hipaa_phi_policy:        str           = "safe_harbor_45_cfr_164.514"
    clinical_advice_boundary: str          = "research_only"
    citation_requirement:    bool          = True
    domain:                  str           = "medical_research"

    # Mutable session state (NOT in CANNOT_MUTATE):
    event_token_refs:        tuple[str, ...] = field(default_factory=tuple)
    claim_graph:             dict           = field(default_factory=dict)

    def __post_init__(self) -> None:
        # frozen dataclasses use object.__setattr__ for normalisation
        if not isinstance(self.research_question, str) or \
                not self.research_question.strip():
            raise MedicalContainerError(
                "research_question must be a non-empty string"
            )
        if self.governance_profile not in ALLOWED_GOVERNANCE_PROFILES:
            raise MedicalContainerError(
                f"governance_profile {self.governance_profile!r} must be "
                f"one of {sorted(ALLOWED_GOVERNANCE_PROFILES)}"
            )
        unknown = set(self.allowed_sources) - ALLOWED_SOURCE_REGISTRIES
        if unknown:
            raise MedicalContainerError(
                f"allowed_sources contains unknown registries: "
                f"{sorted(unknown)}. Permitted: "
                f"{sorted(ALLOWED_SOURCE_REGISTRIES)}"
            )
        if self.human_review_threshold not in HUMAN_REVIEW_THRESHOLDS:
            raise MedicalContainerError(
                f"human_review_threshold {self.human_review_threshold!r} "
                f"must be one of {sorted(HUMAN_REVIEW_THRESHOLDS)}"
            )

    def to_core(self) -> dict:
        """Serialize to the dict that lives in core/core.json.

        Includes a deterministic `evidence_tier_rules_hash` so any
        change to the safety tables `axiom_medical_safety.py` produces
        a different hash — and a CANNOT_MUTATE diff will catch it.
        """
        d = asdict(self)
        d["container_type"] = CONTAINER_TYPE
        d["core_logic"]     = CORE_LOGIC
        d["evidence_tier_rules_hash"] = _tier_rules_hash()
        return d


def _tier_rules_hash() -> str:
    """Stable hash over the evidence-tier registry tables.

    Bound into the container's core.json so that a Tier-5 pattern
    being silently downgraded (e.g. removing the anti_vaccine
    triggers) produces a different hash, which the CANNOT_MUTATE
    diff will flag.
    """
    import hashlib
    from axiom_medical_safety import (
        EVIDENCE_TIER_REGISTRY, TIER_5_PATTERNS,
        FDA_BLACK_BOX_PAIRS, EMERGENCY_SIGNALS,
    )
    payload = {
        "registry": dict(sorted(EVIDENCE_TIER_REGISTRY.items())),
        "tier5":    [(c, list(t)) for c, t in TIER_5_PATTERNS],
        "blackbox": [(sorted(p), w) for p, w in FDA_BLACK_BOX_PAIRS],
        "emergency": list(EMERGENCY_SIGNALS),
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(canon).hexdigest()


# ── Public API ──────────────────────────────────────────────────────


def build_medical_container(
    spec: MedicalContainerSpec,
    output_path: str | Path,
    *,
    delegates: Optional[list[Mapping[str, Any]]] = None,
    archive: bool = False,
):
    """Pack a medical AXM container at `output_path` and return the
    loaded AXMContainer.

    `delegates` defaults to the standard 6-delegate medical pack
    from `examples.medical_pack.MEDICAL_DELEGATES`. Pass a custom
    list to swap or restrict the delegate set per session.
    """
    from axiom_axm import AXMContainer

    if delegates is None:
        from examples.medical_pack import MEDICAL_DELEGATES
        delegates = list(MEDICAL_DELEGATES)

    pack_spec = {
        "core_logic": CORE_LOGIC,
        "core":       spec.to_core(),
        "delegates":  delegates,
    }
    return AXMContainer.pack(pack_spec, str(output_path), archive=archive)


def load_medical_container(path: str | Path) -> tuple[Any, dict]:
    """Load a medical AXM container + its core.json medical spec dict.

    Raises MedicalContainerError if `core.json` is missing the
    medical fields (i.e. the container is not a medical container).
    """
    from axiom_axm import AXMContainer
    container = AXMContainer.from_path(str(path))
    core_path = Path(container.path) / "core" / "core.json"
    if not core_path.exists():
        raise MedicalContainerError(
            f"container at {path} has no core/core.json"
        )
    core = json.loads(core_path.read_text(encoding="utf-8"))
    if core.get("container_type") != CONTAINER_TYPE:
        raise MedicalContainerError(
            f"container_type is {core.get('container_type')!r}; expected "
            f"{CONTAINER_TYPE!r}"
        )
    return container, core


def verify_cannot_mutate(before: dict, after: dict) -> list[str]:
    """Return the list of CANNOT_MUTATE_FIELDS whose value differs
    between `before` and `after`. Empty list ⇒ safe to proceed."""
    return _verify_cannot_mutate(
        before, after, fields=CANNOT_MUTATE_FIELDS,
    )
