# -*- coding: utf-8 -*-
"""
AXIOM Fusion tests — ModalFusion.fuse(EventToken) -> FusedIntent.
=================================================================
Covers the spec'd behaviours: per-layer extraction + confidence-weighted vote,
top-6 intent_vector, risk-cluster union, governance HARM/DECEIVE propagation,
physical-event dominance, the 0.85 confidence cap (CANNOT_MUTATE), empty-token
fallback, and tamper detection via verify().
"""
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_fusion"

import pytest  # noqa: E402

from axiom_event_token.models import EventToken, LayerReport  # noqa: E402
from axiom_fusion import (  # noqa: E402
    ModalFusion, FusedIntent, CONF_CAP, FALLBACK_SIGNAL, TOP_SIGNALS,
)


def _layer(agent, *, signals=None, confidence=1.0, intent_class=None, risk=None):
    payload = {}
    if signals is not None:
        payload["intent_signals"] = list(signals)
    if intent_class is not None:
        payload["intent_class"] = intent_class
    if risk is not None:
        payload["risk_signals"] = list(risk)
    return LayerReport(agent=agent, payload=payload, confidence=confidence)


def _token(**layers):
    return EventToken(id="evt-test", **layers)


# ── empty token → signed fallback ───────────────────────────────────────────

def test_empty_token_falls_back_to_ask_general():
    fi = ModalFusion().fuse(_token())
    assert fi.intent_vector == (FALLBACK_SIGNAL,)
    assert fi.risk_clusters == () and fi.modalities == ()
    assert fi.verify() is True


def test_none_token_also_falls_back():
    fi = ModalFusion().fuse(None)
    assert fi.intent_vector == (FALLBACK_SIGNAL,) and fi.verify()


# ── absent layers contribute nothing ────────────────────────────────────────

def test_absent_layers_are_ignored():
    fi = ModalFusion().fuse(_token(text=_layer("text", signals=["greet"], confidence=0.7)))
    assert fi.modalities == ("text",)
    assert fi.intent_vector == ("greet",)


# ── confidence-weighted vote + top-6 ────────────────────────────────────────

def test_intent_vector_is_top_signals_by_weight():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["a", "b", "c", "d", "e", "f", "g"], confidence=0.8),
    ))
    assert len(fi.intent_vector) == TOP_SIGNALS  # capped at 6


def test_higher_confidence_modality_outranks_lower_on_shared_signal():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["chat", "x"], confidence=0.3),
        tempo=_layer("tempo", signals=["chat", "y"], confidence=0.9),
    ))
    # "chat" accumulates from both → ranks first
    assert fi.intent_vector[0] == "chat"


# ── physical-event dominance (audio + video over text) ──────────────────────

def test_physical_modalities_dominate_text_when_strong():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["smalltalk"], confidence=0.8),
        audio=_layer("audio", signals=["impact", "loud"], confidence=0.8),
        video=_layer("video", signals=["impact", "motion"], confidence=0.8),
    ))
    # audio+video each fire 2 strong signals → "impact" (shared, boosted) leads,
    # and text's lone signal is outranked.
    assert fi.intent_vector[0] == "impact"
    assert fi.intent_vector.index("impact") < fi.intent_vector.index("smalltalk")


def test_weak_physical_does_not_get_boost():
    # single-signal / low-confidence physical layers do not dominate
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["plan", "plan2"], confidence=0.85),
        audio=_layer("audio", signals=["hum"], confidence=0.3),
    ))
    assert fi.intent_vector[0] == "plan"


# ── risk clusters: union + governance propagation ───────────────────────────

def test_governance_harm_propagates_to_risk_clusters():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["request"], confidence=0.7),
        governance=_layer("governance", intent_class="HARM", confidence=0.8),
    ))
    assert "HARM" in fi.risk_clusters


def test_risk_clusters_are_union_across_modalities():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["t"], intent_class="DECEIVE", confidence=0.6),
        audio=_layer("audio", signals=["a"], risk=["loud_anomaly"], confidence=0.6),
    ))
    assert set(fi.risk_clusters) == {"DECEIVE", "loud_anomaly"}


def test_no_risk_when_none_raised():
    fi = ModalFusion().fuse(_token(text=_layer("text", signals=["hi"], confidence=0.5)))
    assert fi.risk_clusters == ()


# ── confidence cap (CANNOT_MUTATE) ──────────────────────────────────────────

def test_fusion_confidence_capped_at_085():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["a"], confidence=1.0),
        audio=_layer("audio", signals=["b"], confidence=1.0),
    ))
    assert fi.fusion_confidence == CONF_CAP  # mean 1.0 → capped to 0.85


def test_fusion_confidence_is_mean_when_below_cap():
    fi = ModalFusion().fuse(_token(
        text=_layer("text", signals=["a"], confidence=0.4),
        audio=_layer("audio", signals=["b"], confidence=0.6),
    ))
    assert fi.fusion_confidence == pytest.approx(0.5)


def test_cannot_mutate_constants():
    import axiom_fusion as m
    with pytest.raises(AttributeError):
        m.CONF_CAP = 0.99


# ── signing + tamper detection ──────────────────────────────────────────────

def test_signed_result_verifies():
    fi = ModalFusion().fuse(_token(text=_layer("text", signals=["x"], confidence=0.7)))
    assert len(fi.signature) == 64 and fi.verify() is True


def test_verify_detects_tampering():
    fi = ModalFusion().fuse(_token(text=_layer("text", signals=["x"], confidence=0.7)))
    tampered = replace(fi, risk_clusters=("HARM",))      # inject a risk after signing
    assert tampered.verify() is False
    tampered2 = replace(fi, fusion_confidence=0.99)
    assert tampered2.verify() is False


def test_to_dict_roundtrip_shape():
    fi = ModalFusion().fuse(_token(text=_layer("text", signals=["x"], confidence=0.7)))
    d = fi.to_dict()
    assert set(d) == {"intent_vector", "risk_clusters", "fusion_confidence",
                      "modalities", "timestamp", "signature"}
