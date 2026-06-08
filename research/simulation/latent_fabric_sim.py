"""Axiom Latent Agent Fabric — runnable demo.

10 dormant MiniSRDAgents. One event drives the full cycle:
  parse → score → wake → distill → merge → log → print MET chain.

Usage:
    AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
        python3 research/simulation/latent_fabric_sim.py

    --query  "custom event text"
    --k      max agents to wake (default 4)
    --quiet  suppress detailed scoring table
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── Ensure repo root on sys.path ──────────────────────────────────────────────
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from axiom_agent_fabric import (
    AgentRouter,
    FabricCoordinator,
    MiniSRDAgent,
)

# ─── Default query ────────────────────────────────────────────────────────────

_DEFAULT_QUERY = (
    "Can we build a medical research agent that checks papers "
    "and avoids legal risk around patient data?"
)

# ─── Pre-defined dormant agent registry ──────────────────────────────────────

_AGENTS: list[MiniSRDAgent] = [
    MiniSRDAgent(
        agent_id="medical_researcher",
        role="medical research and clinical evidence analysis",
        wake_conditions=["medical", "research", "clinical", "papers", "health"],
        skills=["pubmed_search", "clinical_trial_analysis", "drug_interaction"],
        tool_permissions=["pubmed", "web", "pdf_reader"],
        memory_pointer="srd://bundles/medical_researcher",
        compression_state="dormant",
        governance_limits=["HIPAA_compliant", "no_diagnosis", "→ citation_checker"],
        axm_fingerprint="a1b2c3d4",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="legal_compliance",
        role="legal compliance and regulatory risk assessment",
        wake_conditions=["legal", "compliance", "risk", "liability", "regulation"],
        skills=["contract_review", "gdpr_audit", "risk_scoring"],
        tool_permissions=["legal_db", "web"],
        memory_pointer="srd://bundles/legal_compliance",
        compression_state="dormant",
        governance_limits=["no_legal_advice", "cite_jurisdiction"],
        axm_fingerprint="b2c3d4e5",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="citation_checker",
        role="citation verification and source credibility analysis",
        wake_conditions=["citation", "source", "reference", "papers", "pubmed", "doi"],
        skills=["doi_lookup", "impact_factor_scoring", "retraction_check"],
        tool_permissions=["crossref", "pubmed", "web"],
        memory_pointer="srd://bundles/citation_checker",
        compression_state="dormant",
        governance_limits=["no_fabrication"],
        axm_fingerprint="c3d4e5f6",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="privacy_auditor",
        role="privacy and data protection governance",
        wake_conditions=["privacy", "patient", "data", "gdpr", "hipaa", "pii"],
        skills=["pii_scan", "consent_check", "data_flow_mapping"],
        tool_permissions=["policy_db"],
        memory_pointer="srd://bundles/privacy_auditor",
        compression_state="dormant",
        governance_limits=["GDPR_compliant", "HIPAA_compliant"],
        axm_fingerprint="d4e5f6a7",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="audio_analyst",
        role="audio signal analysis and ambient sound classification",
        wake_conditions=["audio", "sound", "noise", "frequency", "waveform"],
        skills=["ambient_classify", "speech_detect", "impact_profile"],
        tool_permissions=["audio_tools"],
        memory_pointer="srd://bundles/audio_analyst",
        compression_state="dormant",
        governance_limits=[],
        axm_fingerprint="e5f6a7b8",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="vision_analyst",
        role="video and visual scene analysis motion classification",
        wake_conditions=["video", "visual", "motion", "scene", "image", "camera"],
        skills=["object_detect", "motion_classify", "scene_graph"],
        tool_permissions=["vision_tools"],
        memory_pointer="srd://bundles/vision_analyst",
        compression_state="dormant",
        governance_limits=[],
        axm_fingerprint="f6a7b8c9",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="code_reviewer",
        role="code review security analysis and static analysis",
        wake_conditions=["code", "software", "security", "vulnerability", "bug"],
        skills=["sast", "dependency_audit", "license_check"],
        tool_permissions=["github", "sonar"],
        memory_pointer="srd://bundles/code_reviewer",
        compression_state="dormant",
        governance_limits=["no_execute"],
        axm_fingerprint="a7b8c9d0",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="finance_analyst",
        role="financial analysis and quantitative risk modelling",
        wake_conditions=["finance", "financial", "risk", "portfolio", "market"],
        skills=["quant_model", "risk_scoring", "regulatory_report"],
        tool_permissions=["bloomberg", "sec_edgar"],
        memory_pointer="srd://bundles/finance_analyst",
        compression_state="dormant",
        governance_limits=["no_investment_advice"],
        axm_fingerprint="b8c9d0e1",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="governance_auditor",
        role="constitutional governance audit and policy enforcement",
        wake_conditions=["governance", "policy", "audit", "compliance", "ethics"],
        skills=["policy_audit", "constitutional_check", "hmac_verify"],
        tool_permissions=["policy_db"],
        memory_pointer="srd://bundles/governance_auditor",
        compression_state="dormant",
        governance_limits=["CANNOT_MUTATE"],
        axm_fingerprint="c9d0e1f2",
        bpw=4.5,
        params_m=135,
    ).sign(),
    MiniSRDAgent(
        agent_id="game_dev",
        role="game development interactive simulation and engine scripting",
        wake_conditions=["game", "unity", "physics", "render", "sprite", "engine"],
        skills=["scene_scripting", "collision_detect", "asset_pipeline"],
        tool_permissions=["unity_api", "github"],
        memory_pointer="srd://bundles/game_dev",
        compression_state="dormant",
        governance_limits=[],
        axm_fingerprint="d0e1f2a3",
        bpw=4.5,
        params_m=135,
    ).sign(),
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

_W = 70


def _bar(char: str = "═") -> str:
    return char * _W


def _section(title: str, char: str = "─") -> None:
    print(f"\n{char * 3} {title} {char * max(0, _W - len(title) - 5)}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Axiom Latent Agent Fabric demo")
    ap.add_argument("--query", default=_DEFAULT_QUERY)
    ap.add_argument("--k",     type=int, default=4)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    query = args.query
    k     = args.k

    print(_bar("═"))
    print("  AXIOM LATENT AGENT FABRIC — DORMANT CAPSULE DEMO")
    print(_bar("═"))
    print(f'  Event: "{query[:72]}{"…" if len(query) > 72 else ""}"')

    # ── Build fabric ──────────────────────────────────────────────────────────
    fabric = FabricCoordinator(_AGENTS, k=k, min_score=0.35)
    result = fabric.run(query)

    # ── Parse-stage info ──────────────────────────────────────────────────────
    tok    = result.event_token
    intent = ""
    conf   = 0.0
    risks: list[str] = []
    if tok.text:
        intent = tok.text.payload.get("intent_class", "?")
        conf   = tok.text.payload.get("confidence", 0.0)
    if tok.governance:
        risks = tok.governance.payload.get("risk_clusters", [])
    print(f"  MET:   intent={intent}  conf={conf:.2f}  risks={risks}")

    # ── Scoring table ─────────────────────────────────────────────────────────
    _section(f"SCORING ({len(result.scores)} dormant agents)")
    if not args.quiet:
        for ws in result.scores:
            action = ws.action(0.35)
            flag   = "WAKE ✓" if action == "WAKE" else "sleep"
            boost_str = f"+{ws.intent_boost:.2f}" if ws.intent_boost else "+0.00"
            kw_str    = f"{ws.keyword_hits}/{len(ws.agent.wake_conditions)}"
            print(f"  {ws.agent.agent_id:<25} kw={kw_str:<5} "
                  f"boost={boost_str}  total={ws.total_score:.2f}  {flag}")

    # ── Wake cycle ────────────────────────────────────────────────────────────
    n_active  = len(result.woken)
    n_dormant = len(_AGENTS) - n_active
    vram_saved = sum(
        a.activation_cost
        for ws in result.scores
        if ws.agent.agent_id not in {w.agent_id for w in result.woken}
        for a in [ws.token]
    )
    _section("WAKE CYCLE")
    print(f"  Dormant: {n_dormant}  |  Active: {n_active}  |  "
          f"VRAM activation units saved: {vram_saved}")

    # ── Agent results ─────────────────────────────────────────────────────────
    _section("AGENT RESULTS")
    for r in result.results:
        nxt = f"  next→{r.next_recommended_agent}" if r.next_recommended_agent else ""
        risks_str = str(r.risk_flags[:2]) if r.risk_flags else "[]"
        print(f"  {r.agent_id:<25} conf={r.confidence:.2f}  "
              f"risks={risks_str}{nxt}")
        if not args.quiet:
            print(f"    {r.answer_summary[:72]}")

    # ── Coordinator merge ─────────────────────────────────────────────────────
    _section("COORDINATOR MERGE")
    mt  = result.merge_token
    et  = result.event_token
    clen = len(result.chain)
    mt_fp = mt.id[:8].upper()
    et_fp = et.id[:8].upper()
    print(f"  merge [ENCAP_{mt_fp}] → parent [ENCAP_{et_fp}]  "
          f"chain={clen}  sigs {'✓' if mt.verify() else '✗'}")

    # ── Routing record ────────────────────────────────────────────────────────
    _section("SIGNED ROUTING RECORD")
    rr  = result.routing_record
    sig = rr.get("signature", "")[:16]
    print(f"  event_id={rr['event_id']}  woken={rr['woken_ids']}  sig={sig}…")

    # ── Chain summary ─────────────────────────────────────────────────────────
    _section("MET CHAIN")
    for i, token in enumerate(result.chain.tokens):
        vok = "✓" if token.verify() else "✗"
        par = f"parent={token.parent_signature[:8]}…" if token.parent_signature else "root"
        print(f"  [{i}] [ENCAP_{token.id[:8].upper()}]  {par}  sig={vok}")

    print(f"\n{_bar('═')}\n")


if __name__ == "__main__":
    main()
