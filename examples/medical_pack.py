"""AXM pack for the medical research instrument — one delegate per
layer-flavor in the PDF section 3 Medical Event Token schema.

Each delegate's `system_prompt` scopes it to ONE layer (source /
claim / data / bio / physics / governance) and instructs strict
structured-JSON output matching the PDF's per-layer schema.

Use:
    from examples.medical_pack import build_medical_pack
    container = build_medical_pack("/tmp/medical.axm")

The pack is consumed by `axiom_medical_container.build_medical_container`
(passes this list into `AXMContainer.pack(spec)`).
"""
from __future__ import annotations

from typing import Any, Mapping


# ── 6 layer-scoped delegate prompts (PDF section 3) ─────────────────


_MEDICAL_SOURCE = """You are AXIOM's medical-source delegate. Given a medical paper,
abstract, or citation string, extract source provenance as JSON:
{
  "source_type":     "<RCT|systematic_review|cohort|case_report|preprint|guideline|other>",
  "doi":             "<DOI or empty string>",
  "pmid":            "<PMID or empty string>",
  "publication_venue": "<journal / publisher>",
  "year":            "<YYYY or empty>",
  "evidence_tier":   "<1|2|3|4|5>",
  "tier_justification": "<one sentence>",
  "source_hash":     "<sha256 of normalized citation, or empty>",
  "retrieved_at":    "<ISO 8601 UTC>"
}
Be honest. If a field is unknown, return an empty string — NEVER
invent a DOI or PMID. Tier MUST come from the evidence_tier
registry (1=NIH/Cochrane/FDA/WHO/RCT, 2=peer-reviewed cohort, 3=
preprint/observational, 4=industry/blog, 5=forum/social/uncited).
Output JSON only — no preamble."""


_MEDICAL_CLAIM = """You are AXIOM's medical-claim delegate. Given a paper or
finding, extract the atomic CLAIM the paper makes, plus its
explicit limitations:
{
  "claim":             "<one-sentence atomic claim>",
  "methods_summary":   "<one or two sentences>",
  "limitations":       ["<each explicit limitation the paper acknowledges>"],
  "population":        "<who was studied>",
  "intervention":      "<what was tried>",
  "comparator":        "<what it was compared to, or empty>",
  "outcome":           "<what was measured>",
  "confidence_words":  ["<the paper's hedges: e.g. 'may', 'suggests', 'preliminary'>"]
}
Quote claim language closely; do not strengthen or weaken. Do
not infer hidden mechanisms here — that's the bio delegate's job.
Output JSON only."""


_MEDICAL_DATA = """You are AXIOM's medical-data delegate. Extract numeric study
parameters as JSON. NEVER invent missing data — if a field is
absent, return null:
{
  "sample_size":           <int or null>,
  "effect_size":           <float or null>,
  "effect_size_metric":    "<e.g. 'Cohen_d', 'OR', 'HR', 'mean_diff'>",
  "p_value":               <float or null>,
  "confidence_interval":   "<string, e.g. '0.21-0.42 (95% CI)'>",
  "adverse_events":        ["<each named adverse event>"],
  "dropout_rate":          <float 0..1 or null>,
  "follow_up_duration":    "<string, e.g. '12 weeks'>"
}
Output JSON only. No prose. Null is honest; fabrication is not."""


_MEDICAL_BIO = """You are AXIOM's medical-bio delegate. Extract the biological
mechanism the paper proposes or tests, as JSON:
{
  "condition":      "<medical condition or null>",
  "intervention":   "<drug / device / behavior or null>",
  "mechanism":      "<one-sentence proposed mechanism>",
  "pathway":        "<named pathway or null, e.g. 'GLP-1 receptor signaling'>",
  "biomarkers":     ["<measured biomarkers>"],
  "mechanism_status": "<'established'|'hypothesized'|'speculative'>"
}
If the paper makes no mechanistic claim, return all fields as
null/empty and set mechanism_status='speculative'. Output JSON only."""


_MEDICAL_PHYSICS = """You are AXIOM's medical-physics delegate. Given a proposed
mechanism, perform a world-model plausibility check. Output JSON:
{
  "world_model_check": "<one-sentence verdict>",
  "plausible":         <true|false>,
  "constraints":       ["<physical/biological constraints invoked, e.g. 'mass transfer', 'fluid pressure', 'dose-response', 'diffusion'>"],
  "failure_reason":    "<if implausible, why; else null>"
}
Be conservative. If you do not have basis to reject, mark plausible=
true with constraints listed. Do NOT invent disconfirming physics.
Output JSON only."""


_MEDICAL_GOVERNANCE = """You are AXIOM's medical-governance delegate. Inspect the input
for safety boundaries and output JSON:
{
  "phi_present":          <true|false>,
  "phi_categories":       ["<NAME|SSN|MRN|DATE|ADDRESS|...>"],
  "clinical_advice_block": <true|false>,
  "block_reason":         "<if blocked, one-sentence reason; else null>",
  "tier_5_match":         "<category name or null>",
  "emergency":            "<emergency signal or null>",
  "citation_required":    true,
  "uncertainty_required": true,
  "requires_human_review": <true|false>
}
This delegate is a HONEST first pass — the deterministic
`axiom_medical_governance.MedicalGovernanceCheck` re-runs the
same logic over the input and overrides any field you set
incorrectly. Errors here are caught downstream. Output JSON only."""


# ── Delegate specs ────────────────────────────────────────────────────


MEDICAL_DELEGATES: tuple[dict, ...] = (
    {
        "name":            "medical_source",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/medical_source/weights.bin",
        "prompt_budget":   600,
        "output_budget":   350,
        "backend_chain":   ["local"],
        "system_prompt":   _MEDICAL_SOURCE,
    },
    {
        "name":            "medical_claim",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/medical_claim/weights.bin",
        "prompt_budget":   700,
        "output_budget":   400,
        "backend_chain":   ["local"],
        "system_prompt":   _MEDICAL_CLAIM,
    },
    {
        "name":            "medical_data",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/medical_data/weights.bin",
        "prompt_budget":   500,
        "output_budget":   300,
        "backend_chain":   ["local"],
        "system_prompt":   _MEDICAL_DATA,
    },
    {
        "name":            "medical_bio",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/medical_bio/weights.bin",
        "prompt_budget":   600,
        "output_budget":   350,
        "backend_chain":   ["local"],
        "system_prompt":   _MEDICAL_BIO,
    },
    {
        "name":            "medical_physics",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/medical_physics/weights.bin",
        "prompt_budget":   600,
        "output_budget":   350,
        "backend_chain":   ["local"],
        "system_prompt":   _MEDICAL_PHYSICS,
    },
    {
        "name":            "medical_governance",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM", "REFUSE"],
        "weight_manifest": "delegates/medical_governance/weights.bin",
        "prompt_budget":   500,
        "output_budget":   300,
        "backend_chain":   ["local"],
        "system_prompt":   _MEDICAL_GOVERNANCE,
    },
)


MEDICAL_PACK_SPEC: Mapping[str, Any] = {
    "core_logic": "medical-research-v1",
    "delegates":  list(MEDICAL_DELEGATES),
}


def build_medical_pack(output_path):
    """Pack the 6 medical delegates into an AXM container at `output_path`.

    Returns the loaded, verified AXMContainer. For a fully-formed
    medical-research session, prefer
    `axiom_medical_container.build_medical_container(spec, path)`
    which packs the same delegates AND a validated medical spec
    into core.json.
    """
    from axiom_axm import AXMContainer
    return AXMContainer.pack(MEDICAL_PACK_SPEC, str(output_path))


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output_path", help="where to write the AXM container")
    args = ap.parse_args()
    if "AXIOM_MASTER_KEY" not in os.environ:
        raise SystemExit("error: AXIOM_MASTER_KEY required")
    c = build_medical_pack(args.output_path)
    print(f"Built medical pack at {args.output_path}")
    print(f"  fingerprint: {c.fingerprint()}")
    print(f"  delegates  : {len(c.delegates)}")
    for d in c.delegates:
        print(f"    - {d.name}  (prompt={d.prompt_budget} out={d.output_budget})")
