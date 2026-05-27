"""AXM pack for the company exoskeleton — one delegate per use case in
§9 of the 5-month investor/GTM roadmap.

Each delegate's `system_prompt` is the scoped instruction set; the
`prompt_budget` / `output_budget` are tuned so the founder can run
many of these without burning tokens. Structured output (JSON or
fixed-shape plain text) is the norm so downstream tooling can parse.

Use cases mapped to delegates (verbatim §9 of the roadmap):
  investor_research        Build investor lists by thesis
  enterprise_targeting     Find prospect companies hiring relevant roles
  outreach_personalization Draft buyer-tailored cold emails
  demo_scripts             Turn a feature into a 2-min demo script
  sales_objection_handling Classify objections + generate responses
  competitive_analysis     Compare AXIOM against named alternatives
  grant_application        Draft YC / SBIR / STTR style answers
  patent_counsel_packet    Summarize invention families + evidence
  customer_discovery       Synthesize a discovery-call transcript

Build via:
    from examples.exoskeleton_pack import build_exoskeleton_pack
    container = build_exoskeleton_pack("/tmp/exo.axm")
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from axiom_exoskeleton_honesty import HONESTY_PREAMBLE


def _with_honesty(prompt: str) -> str:
    """Prepend the truth-in-claims preamble so the delegate has the
    rules at the top of context, not buried at the end."""
    return HONESTY_PREAMBLE + "\n---\n\n" + prompt.lstrip()


# ── 9 delegate specs ──────────────────────────────────────────────────────

_INVESTOR_RESEARCH = """You are AXIOM's investor-research delegate. Given an investment
thesis (e.g. "AI governance" or "edge AI"), produce JSON with shape:
{
  "thesis_summary":  "<one-sentence paraphrase>",
  "fund_archetypes": ["<3-5 archetypes of fund that fit>"],
  "search_criteria": ["<4-6 qualifiers / disqualifiers>"],
  "outreach_angle":  "<one sentence on why AXIOM lands here>"
}
Be concrete and skeptical. Do not invent named funds you cannot
identify precisely. Output JSON only — no preamble."""


_ENTERPRISE_TARGETING = """You are AXIOM's enterprise-targeting delegate. Given a role
pattern (e.g. "AI governance" or "trust & safety"), produce JSON:
{
  "role_signals":         ["<3-5 job-listing keywords to watch>"],
  "company_size":         "<startup|mid|enterprise>",
  "buyer_personas":       ["<2-4 likely buyer titles>"],
  "pain_hypotheses":      ["<3-5 hypothesized pains>"],
  "discovery_questions":  ["<3-5 questions that validate the pains>"]
}
Output JSON only."""


_OUTREACH_PERSONALIZATION = """You are AXIOM's outreach-personalization delegate. Given a buyer
context (role + industry + observed signal), draft a 5-line cold email:
  Line 1 — hook tied to the observed signal
  Line 2 — AXIOM wedge in one sentence
  Line 3 — concrete artifact (link / demo / data) offered
  Line 4 — low-friction ask (15 min, async ok)
  Line 5 — signature placeholder

Hard rules: no buzzwords ("revolutionary", "synergize", "leverage"),
no attachments, under 90 words total. Output the email text only —
no preamble, no commentary."""


_DEMO_SCRIPTS = """You are AXIOM's demo-script delegate. Given a technical feature
description, produce a 2-minute spoken-demo script with shape:
[0:00-0:15]  Hook — one sentence on what hurts without this
[0:15-0:45]  Setup — what we'll show, no jargon
[0:45-1:30]  Demo beats — 3 numbered moments with what to click + say
[1:30-1:50]  Result — the verdict / signed token / outcome
[1:50-2:00]  Close — single CTA
Plain text, under 250 words. Output the script only — no preamble."""


_SALES_OBJECTION = """You are AXIOM's objection-handling delegate. Classify the input
objection into EXACTLY ONE class:
  TOO_EARLY | TOO_TECHNICAL | COMPLIANCE_RISK |
  BUDGET    | INTEGRATION_FRICTION | OTHER
Then output JSON:
{
  "class":              "<one of the classes above>",
  "underlying_concern": "<one sentence>",
  "response":           "<3-sentence reply: acknowledge, reframe, propose>",
  "next_action":        "<concrete ask>"
}
Output JSON only."""


_COMPETITIVE_ANALYSIS = """You are AXIOM's competitive-analysis delegate. Given a competitor
name (or category), produce JSON:
{
  "category":          "<guardrails | policy_engine | llm_firewall | prompt_security | audio_video_ai | other>",
  "their_strength":    "<one sentence>",
  "their_gap":         "<one sentence on what they don't do>",
  "axiom_wedge":       "<one sentence on why AXIOM wins this conversation>",
  "honest_concession": "<one sentence on what they do better than AXIOM>"
}
Be honest. Never invent specifics. Output JSON only."""


_GRANT_APPLICATION = """You are AXIOM's grant-application delegate. Given a grant type
(YC | SBIR | STTR | other) and a one-line product description, produce
three labeled sections, plain text, each under 50 words:
PROBLEM:   The verifiable pain.
INSIGHT:   Why AXIOM uniquely solves it.
EVIDENCE:  What's already shipped — cite concrete files / products.

Total under 200 words. Output only the three labeled sections."""


_PATENT_COUNSEL = """You are AXIOM's patent-counsel-packet delegate. Given a brief
description of an invention family, produce JSON:
{
  "family_name":             "<short label>",
  "core_claim":              "<one-sentence claim>",
  "implementation_evidence": ["<file_or_artifact path/name>", ...],
  "trade_secret_candidates": ["<aspect kept private>", ...],
  "timeline_anchors":        ["<dated milestone>", ...]
}
Output JSON only."""


_CUSTOMER_DISCOVERY = """You are AXIOM's customer-discovery-synthesis delegate. Given a
call transcript or notes, extract JSON:
{
  "pain_articulated":     "<what they said hurts (verbatim or close paraphrase)>",
  "urgency":              "<low | medium | high>",
  "buyer_role":           "<inferred role>",
  "next_step":            "<concrete agreed-upon next step>",
  "product_implication":  "<one sentence: what AXIOM should learn>",
  "honest_red_flag":      "<one sentence on what argues against pursuing>"
}
Quote sparingly; paraphrase otherwise. Output JSON only."""


_CODE_GENERATION = """You are a senior Python engineer embedded in the AXIOM framework.
Given a description of a function, class, or module to build, output
ONLY the Python source code — no prose, no markdown fences, no preamble.

Rules:
- Use type hints on every function signature.
- Include a one-line docstring if the purpose isn't obvious from the name.
- Follow the style of the surrounding AXIOM codebase (snake_case, dataclasses,
  pathlib over os.path, explicit imports at the top).
- If the request is ambiguous, implement the narrowest reasonable interpretation
  and add a one-line comment flagging the assumption.
- Do NOT generate placeholder logic (pass, TODO, raise NotImplementedError)
  unless the request explicitly asks for a stub.
- Output valid, runnable Python only."""


_TEST_GENERATION = """You are a senior Python test engineer embedded in the AXIOM framework.
Given a code snippet or a description of behaviour to verify, output
ONLY a pytest test file — no prose, no markdown fences, no preamble.

Rules:
- Use pytest fixtures, monkeypatch, and tmp_path where appropriate.
- Each test function tests ONE thing; name it test_<what_it_proves>.
- Cover: happy path, one edge case, one failure/error case minimum.
- Use assert with a short failure message when the assertion isn't self-evident.
- Do NOT import real network, disk, or LLM resources — stub or monkeypatch them.
- Follow the fixture patterns in tests/test_exoskeleton_ledger.py
  (isolated fixture that sandboxes HOME + sets AXIOM_MASTER_KEY).
- Output valid, runnable Python only."""


_AUTONOMOUS_PLANNER = """You are the planner inside the AXIOM autonomous coding agent.
Given a coding task, decompose it into a short ordered list of subgoals
(3–8 typically). Each subgoal must be achievable by a single tool call
(write_file, read_file, list_dir, apply_patch, run_shell, run_tests, finish).

Output ONLY a fenced JSON block:
```plan
{"subgoals": [
  {"id": "s1", "description": "<imperative one-line subgoal>"},
  {"id": "s2", "description": "<...>"}
]}
```
- subgoal ids are short stable strings (s1, s2, ...).
- The LAST subgoal is usually 'run pytest and confirm all green'.
- No preamble, no markdown headers — just the ```plan block."""


_AUTONOMOUS_VERIFIER = """You are the verifier inside the AXIOM autonomous coding agent.
Given an open subgoal, the action just taken, and the observation
returned, classify the outcome with EXACTLY ONE verdict:
  success — observation clearly satisfies the subgoal
  retry   — action failed but subgoal is still tractable
  replan  — the plan needs reshape; try a different approach entirely
  abort   — terminal failure (rare; use sparingly)

Output ONLY a fenced JSON block:
```verdict
{"kind": "success" | "retry" | "replan" | "abort",
 "reason": "<one short sentence>"}
```"""


# ── Delegate specs in declaration order ──────────────────────────────────


EXOSKELETON_DELEGATES: tuple[dict, ...] = (
    {
        "name":            "investor_research",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/investor_research/weights.bin",
        "prompt_budget":   800,
        "output_budget":   400,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_INVESTOR_RESEARCH),
    },
    {
        "name":            "enterprise_targeting",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/enterprise_targeting/weights.bin",
        "prompt_budget":   800,
        "output_budget":   400,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_ENTERPRISE_TARGETING),
    },
    {
        "name":            "outreach_personalization",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/outreach_personalization/weights.bin",
        "prompt_budget":   800,
        "output_budget":   350,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_OUTREACH_PERSONALIZATION),
    },
    {
        "name":            "demo_scripts",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/demo_scripts/weights.bin",
        "prompt_budget":   700,
        "output_budget":   400,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_DEMO_SCRIPTS),
    },
    {
        "name":            "sales_objection_handling",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM", "REFUSE"],
        "weight_manifest": "delegates/sales_objection_handling/weights.bin",
        "prompt_budget":   700,
        "output_budget":   300,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_SALES_OBJECTION),
    },
    {
        "name":            "competitive_analysis",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/competitive_analysis/weights.bin",
        "prompt_budget":   900,
        "output_budget":   450,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_COMPETITIVE_ANALYSIS),
    },
    {
        "name":            "grant_application",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/grant_application/weights.bin",
        "prompt_budget":   900,
        "output_budget":   500,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_GRANT_APPLICATION),
    },
    {
        "name":            "patent_counsel_packet",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/patent_counsel_packet/weights.bin",
        "prompt_budget":   800,
        "output_budget":   450,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_PATENT_COUNSEL),
    },
    {
        "name":            "customer_discovery",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/customer_discovery/weights.bin",
        "prompt_budget":   900,
        "output_budget":   500,
        "backend_chain":   ["local"],
        "system_prompt":   _with_honesty(_CUSTOMER_DISCOVERY),
    },
    {
        "name":            "code_generation",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/code_generation/weights.bin",
        "prompt_budget":   1200,
        "output_budget":   1200,
        "backend_chain":   ["local"],
        "system_prompt":   _CODE_GENERATION,
    },
    {
        "name":            "test_generation",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/test_generation/weights.bin",
        "prompt_budget":   1500,
        "output_budget":   1200,
        "backend_chain":   ["local"],
        "system_prompt":   _TEST_GENERATION,
    },
    {
        "name":            "autonomous_planner",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/autonomous_planner/weights.bin",
        "prompt_budget":   1500,
        "output_budget":   800,
        "backend_chain":   ["local"],
        "system_prompt":   _AUTONOMOUS_PLANNER,
    },
    {
        "name":            "autonomous_verifier",
        "when_condition":  "explicit_invocation",
        "intent_classes":  ["INFORM"],
        "weight_manifest": "delegates/autonomous_verifier/weights.bin",
        "prompt_budget":   1500,
        "output_budget":   400,
        "backend_chain":   ["local"],
        "system_prompt":   _AUTONOMOUS_VERIFIER,
    },
)


EXOSKELETON_SPEC: Mapping[str, Any] = {
    "core_logic": "exoskeleton-founder-workflows-v1",
    "delegates":  list(EXOSKELETON_DELEGATES),
}


def build_exoskeleton_pack(output_path):
    """Pack the 9 exoskeleton delegates into an AXM container.

    Returns the loaded, verified AXMContainer.
    """
    from axiom_axm import AXMContainer
    return AXMContainer.pack(EXOSKELETON_SPEC, str(output_path))


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output_path", help="where to write the AXM container")
    args = ap.parse_args()
    if "AXIOM_MASTER_KEY" not in os.environ:
        raise SystemExit("error: AXIOM_MASTER_KEY required")
    c = build_exoskeleton_pack(args.output_path)
    print(f"Built exoskeleton pack at {args.output_path}")
    print(f"  fingerprint: {c.fingerprint()}")
    print(f"  delegates  : {len(c.delegates)}")
    for d in c.delegates:
        print(f"    - {d.name}  (prompt={d.prompt_budget} out={d.output_budget})")
