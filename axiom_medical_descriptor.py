"""Bracketed Token Descriptor — compact text rendering of per-layer
medical EventTokens for consumption by plain LLMs (Qwen / NIM /
OpenAI-compatible).

PDF section 5 format:
    [EVENT_TOKEN id=evt_glp1_001 type=medical_research confidence=0.86]
    SOURCE: tier=1; source_type=RCT; doi=...; source_hash=sha256...
    CLAIM: GLP-1 drug is associated with reduced inflammatory marker Y.
    DATA: n=420; effect_size=0.31; p=0.02; ci=...
    BIO: pathway=GLP-1 receptor signaling; biomarkers=Y,Z
    PHYSICS: plausible=true; checks=fluid_pressure,diffusion
    GOV: research_only=true; no_diagnosis=true; citation_required=true
    LINKS: supports=evt_000221; contradicts=evt_000317
    SIGNATURE: coordinator=sha256...; outer=sha256...
    [/EVENT_TOKEN]

The descriptor is a LOSSY projection — the real EventToken is still
the source of truth. Round-tripping retrieves the labeled fields but
not the underlying signatures-as-bytes / nested LayerReport detail.

Public API:
    render(event_tokens, coord=None)            -> str
    parse(text)                                  -> list[ParsedFragment]
    wrap_for_llm_prompt(descriptors, *, user_question, ...) -> str
    DEFAULT_MEDICAL_SYSTEM: str
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ── PDF section 5 prompt wrapper (verbatim) ──────────────────────────


DEFAULT_MEDICAL_SYSTEM = """You are a medical research synthesis agent. Treat bracketed EVENT_TOKEN blocks as structured evidence. Do not diagnose or prescribe. Use evidence tiers, contradictions, uncertainty, and citations. Do not invent missing data."""


_DEFAULT_USER_INSTRUCTION = """Using the following EVENT_TOKEN blocks, produce a research-only synthesis. Include: strongest claims, contradictions, mechanism map, limitations, uncertainty, and signed manifest IDs."""


# Order matters — render emits labels in this sequence.
_LABEL_ORDER: tuple[str, ...] = (
    "SOURCE", "CLAIM", "DATA", "BIO", "PHYSICS",
    "GOV", "LINKS", "SIGNATURE",
)


# ── Render side ─────────────────────────────────────────────────────


def render(
    event_tokens: Iterable,
    coord: Optional[Any] = None,
) -> str:
    """Emit one bracketed block per EventToken; join with blank lines.

    `coord` (a `MedicalCoordinatorToken`) is optional — when provided
    its `layer_links` populate the `LINKS:` line on each block (every
    event in a coordinator gets the same LINKS rendering since the
    coordinator is the binding scope).
    """
    blocks: list[str] = []
    coord_link_line = _coord_links_line(coord) if coord is not None else None
    coord_summary = coord.summary if coord is not None else None
    for tok in event_tokens:
        blocks.append(_render_one(
            tok,
            coord_link_line=coord_link_line,
            coord_summary=coord_summary,
        ))
    return "\n\n".join(blocks)


def _render_one(
    tok,
    *,
    coord_link_line: Optional[str] = None,
    coord_summary:   Optional[str] = None,
) -> str:
    payload = _primary_payload(tok)
    confidence = _primary_confidence(tok)
    header = (
        f"[EVENT_TOKEN id={tok.id} type=medical_research "
        f"confidence={confidence:.2f}]"
    )
    lines = [header]
    if coord_summary:
        # The PDF didn't formalize a SUMMARY: line but a one-liner
        # gives the LLM the gist before the labeled fields.
        lines.append(f"SUMMARY: {_one_line(coord_summary)}")

    for label in _LABEL_ORDER:
        if label == "LINKS":
            line = coord_link_line or _links_line_from_payload(payload)
        elif label == "SIGNATURE":
            line = _signature_line(tok)
        else:
            line = _label_line(label, payload)
        if line:
            lines.append(line)
    lines.append("[/EVENT_TOKEN]")
    return "\n".join(lines)


def _primary_payload(tok) -> dict:
    """Return the structured payload from the layer the medical
    delegate populated — usually `text`, but governance / physics
    delegates land in their own slots."""
    for slot in ("text", "governance", "physics", "qrf"):
        layer = getattr(tok, slot, None)
        if layer is not None and isinstance(layer.payload, dict):
            return layer.payload
    return {}


def _primary_confidence(tok) -> float:
    for slot in ("text", "governance", "physics", "qrf"):
        layer = getattr(tok, slot, None)
        if layer is not None:
            return float(getattr(layer, "confidence", 0.0))
    return 0.0


def _label_line(label: str, payload: dict) -> str:
    """Render one labeled line: 'LABEL: k1=v1; k2=v2'.

    The payload may carry the medical delegate's structured output
    keyed under `source_layer`, `text_layer`, etc. (PDF naming) or
    flat keys (e.g. `doi`, `claim`). We accept either shape.
    """
    lab_lower = label.lower()
    nested_keys = {
        "SOURCE":  ("source_layer", "source"),
        "CLAIM":   ("text_layer", "claim"),
        "DATA":    ("data_layer", "data"),
        "BIO":     ("bio_layer", "bio"),
        "PHYSICS": ("physics_layer", "physics"),
        "GOV":     ("governance_layer", "governance"),
    }.get(label)
    if not nested_keys:
        return ""

    # Try nested first; otherwise flat keys at the payload root.
    src: Optional[dict] = None
    for nk in nested_keys:
        v = payload.get(nk)
        if isinstance(v, dict):
            src = v
            break

    if src is None:
        # Flat payload (the medical delegate emitted a single layer's
        # JSON). Heuristic: if the payload has any of THIS layer's
        # canonical keys, render the whole payload.
        canonical_keys = _CANONICAL_KEYS.get(label, ())
        if any(k in payload for k in canonical_keys):
            src = {k: payload[k] for k in canonical_keys if k in payload}
        # If we found nothing for this label, skip the line entirely.
        if not src:
            return ""

    parts = []
    for k, v in src.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        rendered_v = _flatten_value(v)
        parts.append(f"{k}={rendered_v}")
    if not parts:
        return ""
    return f"{label}: " + "; ".join(parts)


_CANONICAL_KEYS: dict[str, tuple[str, ...]] = {
    "SOURCE":  ("source_type", "doi", "pmid", "publication_venue",
                "year", "evidence_tier", "tier_justification",
                "source_hash", "retrieved_at"),
    "CLAIM":   ("claim", "methods_summary", "limitations",
                "population", "intervention", "comparator",
                "outcome", "confidence_words"),
    "DATA":    ("sample_size", "effect_size", "effect_size_metric",
                "p_value", "confidence_interval", "adverse_events",
                "dropout_rate", "follow_up_duration"),
    "BIO":     ("condition", "intervention", "mechanism", "pathway",
                "biomarkers", "mechanism_status"),
    "PHYSICS": ("world_model_check", "plausible", "constraints",
                "failure_reason"),
    "GOV":     ("phi_present", "phi_categories",
                "clinical_advice_block", "block_reason",
                "tier_5_match", "emergency", "citation_required",
                "uncertainty_required", "requires_human_review"),
}


def _flatten_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return ",".join(_flatten_value(x) for x in v)
    if isinstance(v, dict):
        return ",".join(f"{k}={_flatten_value(val)}"
                        for k, val in v.items())
    if v is None:
        return "null"
    s = str(v)
    # Replace ';' so it can't break the field separator.
    return s.replace(";", ",").strip()


def _coord_links_line(coord) -> Optional[str]:
    if coord is None or not coord.layer_links:
        return None
    parts = [f"{layer}={tid}"
             for layer, tid in sorted(coord.layer_links.items())]
    if coord.contradictions:
        contradictions_str = ",".join(coord.contradictions)
        parts.append(f"contradicts={contradictions_str}")
    return "LINKS: " + "; ".join(parts)


def _links_line_from_payload(payload: dict) -> str:
    links = payload.get("links") or payload.get("layer_links")
    if not isinstance(links, dict) or not links:
        return ""
    parts = [f"{k}={v}" for k, v in sorted(links.items())]
    return "LINKS: " + "; ".join(parts)


def _signature_line(tok) -> str:
    coord_sig = getattr(tok, "coordinator_sig", "") or ""
    outer_sig = getattr(tok, "signature", "") or ""
    if not (coord_sig or outer_sig):
        return ""
    parts = []
    if coord_sig:
        parts.append(f"coordinator=sha256:{coord_sig[:16]}…")
    if outer_sig:
        parts.append(f"outer=sha256:{outer_sig[:16]}…")
    return "SIGNATURE: " + "; ".join(parts)


def _one_line(s: Any) -> str:
    return " ".join(str(s).split())


# ── Parse side ──────────────────────────────────────────────────────


@dataclass
class ParsedEventTokenFragment:
    """Lossy parse result. The descriptor is a projection; verification
    is NOT possible from this object — for that, look up the real
    EventToken by `id`."""
    id:         str
    type:       str
    confidence: float
    summary:    Optional[str]      = None
    fields:     dict[str, dict]    = field(default_factory=dict)
    links:      dict[str, str]     = field(default_factory=dict)
    signatures: dict[str, str]     = field(default_factory=dict)


_HEAD_RE = re.compile(
    r"^\[EVENT_TOKEN\s+id=(\S+)\s+type=(\S+)\s+confidence=([\d.]+)\s*\]\s*$",
    re.MULTILINE,
)
_FOOT_RE = re.compile(r"^\[/EVENT_TOKEN\]\s*$", re.MULTILINE)
_LABEL_RE = re.compile(r"^([A-Z]+):\s*(.+)$")


def parse(text: str) -> list[ParsedEventTokenFragment]:
    """Parse a block of bracketed descriptors into fragment objects.

    Lossy: signatures-as-bytes are NOT reconstructed (the descriptor
    only carries truncated previews). Use the real EventToken via the
    medical ledger for cryptographic verification."""
    out: list[ParsedEventTokenFragment] = []
    pos = 0
    while True:
        m = _HEAD_RE.search(text, pos)
        if not m:
            break
        foot = _FOOT_RE.search(text, m.end())
        if not foot:
            break
        body = text[m.end():foot.start()].strip("\n")
        frag = ParsedEventTokenFragment(
            id=m.group(1),
            type=m.group(2),
            confidence=float(m.group(3)),
        )
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            lm = _LABEL_RE.match(line)
            if not lm:
                continue
            label, rest = lm.group(1), lm.group(2)
            if label == "SUMMARY":
                frag.summary = rest.strip()
            elif label == "LINKS":
                frag.links = _parse_kv(rest)
            elif label == "SIGNATURE":
                frag.signatures = _parse_kv(rest)
            else:
                frag.fields[label.lower()] = _parse_kv(rest)
        out.append(frag)
        pos = foot.end()
    return out


def _parse_kv(s: str) -> dict[str, str]:
    """'a=1; b=2,3; c=true' → {'a': '1', 'b': '2,3', 'c': 'true'}."""
    out: dict[str, str] = {}
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        out[k.strip()] = v.strip()
    return out


# ── Prompt wrapper ──────────────────────────────────────────────────


def wrap_for_llm_prompt(
    descriptors: Iterable[str],
    *,
    user_question: str,
    system: str = DEFAULT_MEDICAL_SYSTEM,
    extra_rules: tuple[str, ...] = (),
    user_instruction: str = _DEFAULT_USER_INSTRUCTION,
) -> str:
    """Assemble SYSTEM + USER per PDF page 6.

    Output shape:
        SYSTEM:
        <system text>
        <extra_rules as bullet lines, if any>

        USER:
        <user_instruction>

        QUESTION: <user_question>

        <descriptors joined by blank line>
    """
    descs = list(descriptors)
    sys_block_parts = [system.strip()]
    if extra_rules:
        sys_block_parts.append(
            "\nAdditional rules:\n" +
            "\n".join(f"- {r}" for r in extra_rules)
        )
    sys_block = "\n".join(sys_block_parts)

    body = "\n\n".join(d.strip() for d in descs if d and d.strip())

    return (
        f"SYSTEM:\n{sys_block}\n\n"
        f"USER:\n{user_instruction.strip()}\n\n"
        f"QUESTION: {user_question.strip()}\n\n"
        f"{body}"
    )
