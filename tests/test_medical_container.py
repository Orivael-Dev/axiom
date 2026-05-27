"""Tests for axiom_medical_container — AXM Medical Research Container."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_medical_", "axiom_exoskeleton",
        )):
            sys.modules.pop(mod, None)
    yield


def test_default_spec_validates(isolated):
    from axiom_medical_container import MedicalContainerSpec
    spec = MedicalContainerSpec(
        container_id="axm-med-test-001",
        research_question="What is the GLP-1 inflammation mechanism?",
    )
    assert spec.governance_profile == "healthcare.axiom.v1"
    assert "pubmed" in spec.allowed_sources
    assert spec.citation_requirement is True


def test_spec_rejects_empty_research_question(isolated):
    from axiom_medical_container import (
        MedicalContainerSpec, MedicalContainerError,
    )
    with pytest.raises(MedicalContainerError, match="research_question"):
        MedicalContainerSpec(
            container_id="x", research_question="   ",
        )


def test_spec_rejects_unknown_governance_profile(isolated):
    from axiom_medical_container import (
        MedicalContainerSpec, MedicalContainerError,
    )
    with pytest.raises(MedicalContainerError, match="governance_profile"):
        MedicalContainerSpec(
            container_id="x", research_question="q",
            governance_profile="custom.v1",
        )


def test_spec_rejects_unknown_allowed_source(isolated):
    from axiom_medical_container import (
        MedicalContainerSpec, MedicalContainerError,
    )
    with pytest.raises(MedicalContainerError, match="allowed_sources"):
        MedicalContainerSpec(
            container_id="x", research_question="q",
            allowed_sources=("pubmed", "not_a_real_registry"),
        )


def test_spec_rejects_unknown_human_review_threshold(isolated):
    from axiom_medical_container import (
        MedicalContainerSpec, MedicalContainerError,
    )
    with pytest.raises(MedicalContainerError, match="human_review_threshold"):
        MedicalContainerSpec(
            container_id="x", research_question="q",
            human_review_threshold="whenever",
        )


def test_to_core_includes_evidence_tier_rules_hash(isolated):
    from axiom_medical_container import MedicalContainerSpec
    spec = MedicalContainerSpec(
        container_id="x", research_question="q",
    )
    core = spec.to_core()
    assert core["container_type"] == "AXM_MEDICAL_RESEARCH"
    assert core["core_logic"] == "medical-research-v1"
    assert core["evidence_tier_rules_hash"].startswith("sha256:")
    assert len(core["evidence_tier_rules_hash"]) == len("sha256:") + 64


def test_build_medical_container_packs_six_delegates(isolated, tmp_path):
    from axiom_medical_container import (
        MedicalContainerSpec, build_medical_container,
    )
    spec = MedicalContainerSpec(
        container_id="axm-med-build-001",
        research_question="GLP-1 inflammation",
    )
    c = build_medical_container(spec, tmp_path / "med.axm")
    names = {d.name for d in c.delegates}
    assert names == {
        "medical_source", "medical_claim", "medical_data",
        "medical_bio", "medical_physics", "medical_governance",
    }


def test_load_medical_container_returns_core_dict(isolated, tmp_path):
    from axiom_medical_container import (
        MedicalContainerSpec, build_medical_container,
        load_medical_container,
    )
    spec = MedicalContainerSpec(
        container_id="axm-med-load-001",
        research_question="aspirin secondary prevention dose",
    )
    build_medical_container(spec, tmp_path / "med.axm")
    container, core = load_medical_container(tmp_path / "med.axm")
    assert core["container_type"] == "AXM_MEDICAL_RESEARCH"
    assert core["research_question"] == "aspirin secondary prevention dose"
    assert core["container_id"] == "axm-med-load-001"
    assert container.delegates  # delegates verified at load time


def test_load_rejects_non_medical_container(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_medical_container import (
        load_medical_container, MedicalContainerError,
    )
    build_exoskeleton_pack(tmp_path / "exo.axm")
    with pytest.raises(MedicalContainerError, match="container_type"):
        load_medical_container(tmp_path / "exo.axm")


def test_verify_cannot_mutate_flags_research_question_change(isolated):
    from axiom_medical_container import verify_cannot_mutate
    before = {
        "research_question": "Q1",
        "governance_profile": "healthcare.axiom.v1",
        "allowed_sources": ["pubmed"],
        "human_review_threshold": "patient_specific_or_high_risk",
    }
    after = dict(before)
    after["research_question"] = "Q2"
    diffs = verify_cannot_mutate(before, after)
    assert "research_question" in diffs
