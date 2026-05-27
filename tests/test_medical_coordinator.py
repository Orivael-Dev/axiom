"""Tests for axiom_medical_coordinator.MedicalCoordinatorToken."""
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
            "axiom_medical_",
        )):
            sys.modules.pop(mod, None)
    yield


def _make_layer(name: str = "demo", confidence: float = 0.8):
    from axiom_event_token.models import LayerReport
    return LayerReport.signed(
        agent=name,
        payload={"delegate": name, "value": "x"},
        confidence=confidence,
    )


def _make_token(token_id: str, *, confidence: float = 0.8):
    from axiom_event_token.models import EventToken
    layer = _make_layer(confidence=confidence)
    return EventToken(id=token_id, text=layer)


def test_bind_returns_signed_token(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa")
    t2 = _make_token("evt_bbb")
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"source": "evt_aaa", "text": "evt_bbb"},
        summary="test binding",
    )
    assert coord.fusion_signature
    assert coord.verify()
    assert coord.active_layers == ("source", "text")
    assert coord.primary_layer == "text"


def test_bind_computes_mean_confidence(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa", confidence=0.6)
    t2 = _make_token("evt_bbb", confidence=0.9)
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"source": "evt_aaa", "text": "evt_bbb"},
        summary="test",
    )
    assert abs(coord.cross_layer_consistency - 0.75) < 1e-6


def test_bind_explicit_consistency_wins(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa", confidence=0.1)
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1],
        layer_assignments={"text": "evt_aaa"},
        summary="x",
        cross_layer_consistency=0.99,
    )
    assert coord.cross_layer_consistency == 0.99


def test_bind_rejects_unknown_layer_name(isolated):
    from axiom_medical_coordinator import (
        MedicalCoordinatorToken, MedicalCoordError,
    )
    t1 = _make_token("evt_aaa")
    with pytest.raises(MedicalCoordError, match="unknown layer"):
        MedicalCoordinatorToken.bind(
            event_tokens=[t1],
            layer_assignments={"not_a_real_layer": "evt_aaa"},
            summary="x",
            primary_layer="not_a_real_layer",
        )


def test_bind_rejects_missing_event_token(isolated):
    from axiom_medical_coordinator import (
        MedicalCoordinatorToken, MedicalCoordError,
    )
    t1 = _make_token("evt_aaa")
    with pytest.raises(MedicalCoordError, match="event_token_ids not in"):
        MedicalCoordinatorToken.bind(
            event_tokens=[t1],
            layer_assignments={"text": "evt_does_not_exist"},
            summary="x",
        )


def test_bind_rejects_unassigned_primary_layer(isolated):
    from axiom_medical_coordinator import (
        MedicalCoordinatorToken, MedicalCoordError,
    )
    t1 = _make_token("evt_aaa")
    with pytest.raises(MedicalCoordError, match="primary_layer"):
        MedicalCoordinatorToken.bind(
            event_tokens=[t1],
            layer_assignments={"source": "evt_aaa"},
            summary="x",
            primary_layer="text",   # not in layer_assignments
        )


def test_verify_lookup_mode_catches_unknown_id(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa")
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1],
        layer_assignments={"text": "evt_aaa"},
        summary="x",
    )
    # Empty lookup → linked id not found.
    assert coord.verify(event_token_lookup={}) is False


def test_verify_lookup_mode_passes_with_full_table(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa")
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1],
        layer_assignments={"text": "evt_aaa"},
        summary="x",
    )
    # Sign t1's outer signature first (the LayerReport itself is
    # signed; whole-EventToken verify also checks the outer sig).
    # We rebuild via Coordinator to get a fully-signed token.
    from axiom_event_token.coordinator import _token_kwargs
    from axiom_event_token.models import (
        EventToken, _canonical_token, _sign, TOKEN_KEY_NS,
        _canonical_coordinator, COORD_KEY_NS,
    )
    coord_sig = _sign(_canonical_coordinator(t1), COORD_KEY_NS)
    t1_with_coord = EventToken(**{**_token_kwargs(t1),
                                  "coordinator_sig": coord_sig})
    outer_sig = _sign(_canonical_token(t1_with_coord), TOKEN_KEY_NS)
    t1_signed = EventToken(**{**_token_kwargs(t1_with_coord),
                              "signature": outer_sig})

    coord2 = MedicalCoordinatorToken.bind(
        event_tokens=[t1_signed],
        layer_assignments={"text": "evt_aaa"},
        summary="x",
    )
    assert coord2.verify(event_token_lookup={"evt_aaa": t1_signed}) is True


def test_tampered_summary_fails_verify(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa")
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1],
        layer_assignments={"text": "evt_aaa"},
        summary="original",
    )
    d = coord.to_dict()
    d["summary"] = "tampered"
    bad = MedicalCoordinatorToken.from_dict(d)
    assert bad.verify() is False


def test_to_dict_from_dict_round_trip(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa")
    t2 = _make_token("evt_bbb")
    coord = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"source": "evt_aaa", "text": "evt_bbb"},
        summary="round trip",
        contradictions=("animal vs human disagreement",),
        requires_human_review=True,
    )
    coord2 = MedicalCoordinatorToken.from_dict(coord.to_dict())
    assert coord2 == coord
    assert coord2.verify()


def test_layer_link_dict_order_does_not_affect_signature(isolated):
    from axiom_medical_coordinator import MedicalCoordinatorToken
    t1 = _make_token("evt_aaa")
    t2 = _make_token("evt_bbb")
    coord1 = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"source": "evt_aaa", "text": "evt_bbb"},
        summary="x",
    )
    coord2 = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"text": "evt_bbb", "source": "evt_aaa"},
        summary="x",
        event_id=coord1.event_id,
    )
    # created_at may differ — copy it across before comparing sigs.
    d2 = coord2.to_dict(); d2["created_at"] = coord1.created_at
    coord2_normalised = MedicalCoordinatorToken.bind(
        event_tokens=[t1, t2],
        layer_assignments={"text": "evt_bbb", "source": "evt_aaa"},
        summary="x",
        event_id=coord1.event_id,
    )
    # Direct equality won't match (timestamps differ) but the
    # canonical payload structure should be identical.
    assert coord1._payload_for_sig() == {
        **coord2_normalised._payload_for_sig(),
        "created_at": coord1.created_at,
    }
