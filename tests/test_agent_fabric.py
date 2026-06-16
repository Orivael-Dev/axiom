"""Unit tests for axiom_agent_fabric — capsule, result, router, coordinator."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_agent_fabric", "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier", "axiom_fusion", "axiom_exoskeleton_ledger",
        )):
            sys.modules.pop(mod, None)
    yield


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_agent(
    agent_id: str = "test_agent",
    role: str = "test role for unit testing",
    wake_conditions: list | None = None,
    compression_state: str = "dormant",
    governance_limits: list | None = None,
):
    from axiom_agent_fabric.capsule import MiniSRDAgent
    agent = MiniSRDAgent(
        agent_id=agent_id,
        role=role,
        wake_conditions=wake_conditions or ["test", "unit", "example"],
        skills=["test_skill"],
        tool_permissions=["web"],
        memory_pointer=f"srd://bundles/{agent_id}",
        compression_state=compression_state,
        governance_limits=governance_limits or [],
        axm_fingerprint="abcd1234",
        bpw=4.5,
        params_m=135,
    )
    return agent.sign()


# ─── 1. MiniSRDAgent sign and verify ─────────────────────────────────────────


def test_mini_srd_agent_sign_and_verify(isolated):
    agent = _make_agent()
    assert agent.signature != ""
    assert agent.verify()


# ─── 2. Tampered capsule breaks verify ───────────────────────────────────────


def test_mini_srd_agent_tamper_breaks_verify(isolated):
    from axiom_agent_fabric.capsule import MiniSRDAgent
    agent = _make_agent()
    # Tamper with tool_permissions after signing
    tampered = MiniSRDAgent(
        agent_id=agent.agent_id,
        role=agent.role,
        wake_conditions=agent.wake_conditions,
        skills=agent.skills,
        tool_permissions=["web", "root_shell"],  # injected
        memory_pointer=agent.memory_pointer,
        compression_state=agent.compression_state,
        governance_limits=agent.governance_limits,
        axm_fingerprint=agent.axm_fingerprint,
        bpw=agent.bpw,
        params_m=agent.params_m,
        signature=agent.signature,  # original sig — should fail
    )
    assert not tampered.verify()


# ─── 3. VRAMAgentToken strips heavy fields ───────────────────────────────────


def test_vram_token_strips_heavy_fields(isolated):
    agent = _make_agent()
    token = agent.to_vram_token()
    assert token.agent_id == agent.agent_id
    assert token.wake_conditions == agent.wake_conditions
    assert len(token.role_embedding) == 8
    assert token.verify()
    # Heavy fields not present on the token
    assert not hasattr(token, "skills")
    assert not hasattr(token, "governance_limits")
    assert not hasattr(token, "bpw")


# ─── 4. AgentRouter wakes top-k by keyword score ─────────────────────────────


def test_router_wakes_top_k_by_keyword_score(isolated):
    from axiom_agent_fabric.router import AgentRouter

    a1 = _make_agent("high_match", wake_conditions=["medical", "research", "paper"])
    a2 = _make_agent("low_match",  wake_conditions=["audio", "sound", "waveform"])
    a3 = _make_agent("mid_match",  wake_conditions=["research", "data", "analysis"])
    router = AgentRouter([a1, a2, a3], k=2, min_score=0.1)

    scores = router.score("medical research on new papers")
    woken  = router.wake(scores)

    woken_ids = {w.agent_id for w in woken}
    # high_match (3/3 keywords) and mid_match (1/3) should beat low_match (0/3)
    assert "high_match" in woken_ids
    assert len(woken) <= 2


# ─── 5. AgentRouter skips archived agents ────────────────────────────────────


def test_router_skips_archived_agents(isolated):
    from axiom_agent_fabric.router import AgentRouter

    active  = _make_agent("active_one",   wake_conditions=["hello", "world"])
    archived = _make_agent("archived_one", wake_conditions=["hello", "world"],
                            compression_state="archived")
    router = AgentRouter([active, archived], k=4, min_score=0.0)

    scores = router.score("hello world")
    woken  = router.wake(scores)

    woken_ids = {w.agent_id for w in woken}
    assert "archived_one" not in woken_ids
    assert "active_one" in woken_ids


# ─── 6. Intent boost elevates matching agent ──────────────────────────────────


def test_router_intent_boost_elevates_matching_agent(isolated):
    from axiom_agent_fabric.router import AgentRouter

    # "inform" should match agent role containing "inform"
    agent_with_boost    = _make_agent("inform_agent", role="information delivery and inform")
    agent_without_boost = _make_agent("other_agent",  role="data storage pipeline")

    router = AgentRouter([agent_with_boost, agent_without_boost], k=4, min_score=0.0)
    scores = router.score("hello there", intent_vector=["INFORM_CLARIFY"])

    score_map = {s.agent.agent_id: s for s in scores}
    # Both have 0 keyword hits, but inform_agent gets the intent boost
    assert score_map["inform_agent"].intent_boost == 0.30
    assert score_map["other_agent"].intent_boost  == 0.0
    assert score_map["inform_agent"].total_score > score_map["other_agent"].total_score


# ─── 7. min_score threshold filters low-relevance agents ──────────────────────


def test_router_min_score_threshold_filters_low_relevance(isolated):
    from axiom_agent_fabric.router import AgentRouter

    # Wake condition has zero overlap with event text → score = 0.0
    no_match = _make_agent("no_match", wake_conditions=["xyzzy", "quux", "plugh"])
    router   = AgentRouter([no_match], k=4, min_score=0.35)

    scores = router.score("hello world how are you")
    woken  = router.wake(scores)

    assert len(woken) == 0  # score=0.0 < min_score=0.35


# ─── 8. AgentResult.from_layer_report ────────────────────────────────────────


def test_agent_result_from_layer_report(isolated):
    from axiom_agent_fabric.result import AgentResult
    from axiom_event_token.models import LayerReport

    report = LayerReport.signed(
        agent="test",
        payload={
            "phrase":       "example text",
            "intent_class": "HARM",
            "confidence":   0.9,
            "signals":      ["violence", "threat"],
            "verdict":      "HARM",
        },
        confidence=0.9,
    )
    result = AgentResult.from_layer_report("test_agent", report)
    assert result.agent_id == "test_agent"
    assert result.confidence == 0.9
    assert "HARM" in result.risk_flags
    assert result.verify()


# ─── 9. FabricCoordinator full cycle ─────────────────────────────────────────


def test_fabric_coordinator_full_cycle(isolated, tmp_path):
    from axiom_agent_fabric import FabricCoordinator, MiniSRDAgent

    agents = [
        _make_agent("medical_researcher", role="medical research",
                    wake_conditions=["medical", "research", "health"]),
        _make_agent("legal_compliance",   role="legal compliance",
                    wake_conditions=["legal", "risk", "compliance"]),
        _make_agent("game_dev",           role="game development",
                    wake_conditions=["game", "unity", "engine"]),
    ]
    fabric = FabricCoordinator(agents, k=2, min_score=0.1,
                               ledger_path=tmp_path / "test_ledger.jsonl")
    result = fabric.run("medical research for health compliance")

    assert result.event_token is not None
    assert result.event_token.verify()
    assert result.merge_token is not None
    assert result.merge_token.verify()
    assert len(result.scores) == 3  # all 3 non-archived agents scored
    assert len(result.woken) >= 1   # at least one woke
    assert len(result.results) == len(result.woken)
    assert result.routing_record["signature"] != ""


# ─── 10. Chain parent links are consistent ────────────────────────────────────


def test_fabric_coordinator_chain_parent_links(isolated, tmp_path):
    from axiom_agent_fabric import FabricCoordinator

    agents = [_make_agent("alpha", wake_conditions=["alpha", "beta", "gamma"])]
    fabric = FabricCoordinator(agents, k=1, min_score=0.0,
                               ledger_path=tmp_path / "chain_ledger.jsonl")
    result = fabric.run("alpha beta gamma test")

    chain_tokens = result.chain.tokens
    assert len(chain_tokens) >= 2

    # merge_token.parent_signature must equal event_token.signature
    event_sig = result.event_token.signature
    merge_parent = result.merge_token.parent_signature
    assert merge_parent == event_sig


# ─── 11. Ledger is written ────────────────────────────────────────────────────


def test_fabric_coordinator_ledger_written(isolated, tmp_path):
    from axiom_agent_fabric import FabricCoordinator
    from axiom_exoskeleton_ledger import read_ledger

    agents = [_make_agent("ledger_agent", wake_conditions=["test", "ledger"])]
    ledger_path = tmp_path / "ledger_test.jsonl"
    fabric = FabricCoordinator(agents, k=1, min_score=0.0,
                               ledger_path=ledger_path)
    fabric.run("test ledger write check")

    entries = read_ledger(ledger_path)
    assert len(entries) >= 2  # event entry + merge entry
    # Every entry must have a signature
    for e in entries:
        assert e.signature != ""
