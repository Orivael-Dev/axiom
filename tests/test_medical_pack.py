"""Tests for examples/medical_pack — the 6 layer-scoped delegates."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    # NB: do NOT pop axiom_axm here — its module-level imports
    # publish class objects (AXMError etc.) that downstream tests
    # capture by identity. Reloading creates fresh class objects
    # and breaks `pytest.raises(AXMError)` in those tests.
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_medical_", "examples.",
        )):
            sys.modules.pop(mod, None)
    yield


def test_pack_contains_six_delegates(isolated, tmp_path):
    from examples.medical_pack import build_medical_pack
    c = build_medical_pack(tmp_path / "med.axm")
    names = [d.name for d in c.delegates]
    assert len(names) == 6
    assert set(names) == {
        "medical_source", "medical_claim", "medical_data",
        "medical_bio", "medical_physics", "medical_governance",
    }


def test_each_delegate_has_system_prompt(isolated, tmp_path):
    from examples.medical_pack import build_medical_pack
    c = build_medical_pack(tmp_path / "med.axm")
    for d in c.delegates:
        sys_file = c.path / "delegates" / d.name / "system_prompt.txt"
        assert sys_file.exists(), f"missing system_prompt for {d.name}"
        body = sys_file.read_text(encoding="utf-8")
        assert len(body) > 200, f"system_prompt for {d.name} too short"
        assert "JSON" in body, \
            f"{d.name} prompt does not require JSON output"


def test_delegate_budgets_reasonable(isolated, tmp_path):
    from examples.medical_pack import build_medical_pack
    c = build_medical_pack(tmp_path / "med.axm")
    for d in c.delegates:
        assert d.prompt_budget >= 400
        assert d.output_budget >= 250


def test_pack_signatures_verify(isolated, tmp_path):
    from examples.medical_pack import build_medical_pack
    from axiom_axm import _delegate_key, _sign
    c = build_medical_pack(tmp_path / "med.axm")
    key = _delegate_key()
    for d in c.delegates:
        expected = _sign(key, d._payload())
        assert d.signature == expected, f"bad sig on {d.name}"


def test_governance_delegate_has_refuse_intent(isolated, tmp_path):
    from examples.medical_pack import build_medical_pack
    c = build_medical_pack(tmp_path / "med.axm")
    gov = next(d for d in c.delegates if d.name == "medical_governance")
    assert "REFUSE" in gov.intent_classes


def test_pack_spec_constant_matches_built_pack(isolated):
    from examples.medical_pack import MEDICAL_DELEGATES, MEDICAL_PACK_SPEC
    assert MEDICAL_PACK_SPEC["core_logic"] == "medical-research-v1"
    assert len(MEDICAL_PACK_SPEC["delegates"]) == len(MEDICAL_DELEGATES)


def test_each_delegate_prompts_for_its_layer_schema(isolated):
    from examples.medical_pack import MEDICAL_DELEGATES
    # Spot-check each delegate's prompt mentions a distinguishing
    # field from its layer's schema.
    by_name = {d["name"]: d for d in MEDICAL_DELEGATES}
    assert "doi" in by_name["medical_source"]["system_prompt"].lower()
    assert "claim" in by_name["medical_claim"]["system_prompt"].lower()
    assert "p_value" in by_name["medical_data"]["system_prompt"]
    assert "pathway" in by_name["medical_bio"]["system_prompt"].lower()
    assert "plausible" in by_name["medical_physics"]["system_prompt"].lower()
    assert "phi" in by_name["medical_governance"]["system_prompt"].lower()
