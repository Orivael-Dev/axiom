"""End-to-end tests for axiom_medical_agent.MedicalResearchAgent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_MEDICAL_LEDGER", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_medical_", "axiom_redact",
            "examples.",
        )):
            sys.modules.pop(mod, None)
    yield


class _RecordingBackend:
    """Backend that returns a delegate-tagged JSON payload so tests
    can confirm which delegate fired."""
    name  = "stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_output_tokens,
                 timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        self.calls.append({"system": system, "prompt": prompt})
        # Encode the delegate from the system prompt.
        marker = "?"
        for tok in ("medical_source", "medical_claim", "medical_data",
                    "medical_bio", "medical_physics",
                    "medical_governance"):
            if tok.replace("_", "-") in system or tok in system:
                marker = tok
                break
        body = {"delegate": marker, "output_marker": marker}
        return BackendResult(
            text=json.dumps(body),
            input_tokens=len(prompt) // 4,
            output_tokens=10,
            latency_ms=3,
            backend=self.name,
            model=self.model,
        )


def test_list_profiles_returns_five(isolated):
    from axiom_medical_agent import MedicalResearchAgent
    agent = MedicalResearchAgent.from_default_pack(
        backend=_RecordingBackend(),
    )
    profiles = agent.list_profiles()
    assert set(profiles) == {
        "summarize", "mechanism", "compare",
        "patient_apply", "hypothesize",
    }


def test_summarize_profile_fires_source_claim_data_governance(isolated):
    from axiom_medical_agent import MedicalResearchAgent
    backend = _RecordingBackend()
    agent = MedicalResearchAgent.from_default_pack(backend=backend)
    result = agent.research(
        research_question="What does Cochrane say about GLP-1 + CRP?",
        sources=[{"name": "src", "text": "abstract here"}],
        profile="summarize",
    )
    fired_systems = "\n".join(c["system"] for c in backend.calls)
    assert "medical-source" in fired_systems
    assert "medical-claim" in fired_systems
    assert "medical-data" in fired_systems
    assert "medical-governance" in fired_systems
    # bio + physics NOT in summarize profile.
    assert "medical-bio" not in fired_systems
    assert "medical-physics" not in fired_systems


def test_mechanism_profile_fires_bio_physics_data_governance(isolated):
    from axiom_medical_agent import MedicalResearchAgent
    backend = _RecordingBackend()
    agent = MedicalResearchAgent.from_default_pack(backend=backend)
    agent.research(
        research_question="How does GLP-1 modulate inflammation?",
        sources=[{"name": "src", "text": "abstract"}],
        profile="mechanism",
    )
    sys_blob = "\n".join(c["system"] for c in backend.calls)
    assert "medical-bio" in sys_blob
    assert "medical-physics" in sys_blob
    assert "medical-data" in sys_blob
    assert "medical-governance" in sys_blob


def test_result_carries_signed_coordinator_and_descriptor(isolated):
    from axiom_medical_agent import MedicalResearchAgent
    agent = MedicalResearchAgent.from_default_pack(
        backend=_RecordingBackend(),
    )
    result = agent.research(
        research_question="GLP-1 mechanism",
        sources=[{"name": "demo", "text": "abstract"}],
        profile="summarize",
    )
    assert result.coordinator_tokens
    coord = result.coordinator_tokens[0]
    assert coord.verify()
    assert result.descriptor.startswith("[EVENT_TOKEN")
    assert result.manifest_root.startswith("sha256:")


def test_research_with_tier_5_question_sets_human_review(isolated):
    from axiom_medical_agent import MedicalResearchAgent
    agent = MedicalResearchAgent.from_default_pack(
        backend=_RecordingBackend(),
    )
    result = agent.research(
        research_question=(
            "Should I stop my insulin and use cinnamon instead?"
        ),
        sources=[{"name": "demo", "text": "x"}],
        profile="patient_apply",
    )
    assert result.requires_human_review is True


def test_unknown_profile_raises(isolated):
    from axiom_medical_agent import (
        MedicalResearchAgent, MedicalAgentError,
    )
    agent = MedicalResearchAgent.from_default_pack(
        backend=_RecordingBackend(),
    )
    with pytest.raises(MedicalAgentError, match="unknown profile"):
        agent.research("q", profile="not_a_profile")


def test_empty_question_raises(isolated):
    from axiom_medical_agent import (
        MedicalResearchAgent, MedicalAgentError,
    )
    agent = MedicalResearchAgent.from_default_pack(
        backend=_RecordingBackend(),
    )
    with pytest.raises(MedicalAgentError, match="non-empty"):
        agent.research("   ", profile="summarize")


def test_ledger_appends_signed_entry(isolated, tmp_path):
    from axiom_medical_agent import MedicalResearchAgent
    from axiom_medical_ledger import LedgerWriter, read_ledger
    ledger_path = tmp_path / "medled.jsonl"
    agent = MedicalResearchAgent.from_default_pack(
        backend=_RecordingBackend(),
        ledger=LedgerWriter(ledger_path),
    )
    agent.research(
        research_question="GLP-1 + inflammation review",
        sources=[{"name": "S1", "text": "abstract"}],
        profile="summarize",
    )
    entries = read_ledger(ledger_path)
    assert len(entries) == 1
    assert entries[0].verify()
    assert entries[0].profile == "summarize"
    assert entries[0].active_layers   # at least one layer recorded


def test_cli_list_profiles(isolated, capsys):
    from axiom_medical_agent import main
    rc = main(["--list-profiles"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "summarize" in out
    assert "mechanism" in out


def test_cli_runs_research_with_inline_question(
    isolated, monkeypatch, tmp_path, capsys,
):
    from axiom_medical_agent import main
    import axiom_event_token.backends as be
    monkeypatch.setattr(be, "default_backend", lambda: _RecordingBackend())
    save_dir = tmp_path / "tokens"
    rc = main([
        "--question", "GLP-1 and inflammation",
        "--profile", "summarize",
        "--save-tokens", str(save_dir),
        "--no-ledger",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[EVENT_TOKEN id=" in out
    saved = list(save_dir.glob("*.json"))
    assert saved
    assert any(p.name == "_coordinator.json" for p in saved)
