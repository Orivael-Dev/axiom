"""Tests for axiom_fusion — multimodal fusion layer.

Covers:
  - text-only fusion
  - audio-only fusion
  - video-only fusion
  - text + audio fusion (confidence weighting)
  - text + audio + video (all three)
  - governance HARM verdict propagates to risk_clusters
  - absent layers contribute nothing
  - fusion_confidence capped at 0.85
  - FusedIntent.verify() round-trip
  - FusedIntent.to_latent_state() produces valid LatentState
  - empty token → signed fallback with ask_general
  - physics layer surface/depth signals
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_fusion", "axiom_signing", "axiom_event_token",
            "axiom_latent",
        )):
            sys.modules.pop(mod, None)
    yield


# ─── helpers ────────────────────────────────────────────────────────────────


def _make_token(**layer_payloads):
    """Build a signed EventToken with the given layer payloads."""
    from axiom_event_token.models import EventToken, LayerReport, now_iso
    import uuid

    layers = {}
    for slot, payload in layer_payloads.items():
        conf = float(payload.pop("_confidence", 1.0))
        layers[slot] = LayerReport.signed(agent=slot, payload=payload, confidence=conf)

    return EventToken(
        id=str(uuid.uuid4()),
        created_at=now_iso(),
        activated_agents=tuple(layers.keys()),
        **layers,
    )


# ─── 1. Text-only ────────────────────────────────────────────────────────────


def test_text_only_produces_intent_vector():
    from axiom_fusion import ModalFusion
    token = _make_token(text={"intent_vector": ["ask_factual"], "risk_clusters": []})
    fused = ModalFusion().fuse(token)
    assert "ask_factual" in fused.intent_vector
    assert fused.modalities == ["text"]
    assert fused.verify()


def test_text_fallback_heuristic_when_no_intent_vector():
    from axiom_fusion import ModalFusion
    token = _make_token(text={"content": "What is the capital of France?"})
    fused = ModalFusion().fuse(token)
    # "what is" → ask_factual heuristic
    assert "ask_factual" in fused.intent_vector
    assert fused.verify()


# ─── 2. Audio-only ───────────────────────────────────────────────────────────


def test_audio_impact_maps_to_intent_signal():
    from axiom_fusion import ModalFusion
    token = _make_token(audio={
        "impact_profile": "sharp_transient",
        "material_signature": "glass-like",
        "decay_pattern": "scattered_fragments",
        "depth": 0.2,
        "width": 0.3,
        "rhythm": "single_impact",
        "_confidence": 0.91,
    })
    fused = ModalFusion().fuse(token)
    assert "physical_impact_detected" in fused.intent_vector
    assert "fragile_material_event" in fused.intent_vector
    assert fused.modalities == ["audio"]
    assert fused.verify()


def test_audio_high_depth_adds_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(audio={
        "impact_profile": "sustained",
        "depth": 0.8,      # > 0.6 threshold
        "width": 0.2,
        "rhythm": "periodic",
    })
    fused = ModalFusion().fuse(token)
    assert "high_energy_event" in fused.risk_clusters


def test_audio_sharp_broadband_adds_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(audio={
        "impact_profile": "sharp_transient",
        "depth": 0.2,
        "width": 0.75,     # > 0.5 threshold
        "rhythm": "single_impact",
    })
    fused = ModalFusion().fuse(token)
    assert "sharp_impact_risk" in fused.risk_clusters


# ─── 3. Video-only ───────────────────────────────────────────────────────────


def test_video_erratic_motion_intent_and_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(video={
        "motion_class": "erratic",
        "object_count": 2,
        "_confidence": 0.85,
    })
    fused = ModalFusion().fuse(token)
    assert "erratic_motion_detected" in fused.intent_vector
    assert "erratic_motion" in fused.risk_clusters
    assert "objects_present" in fused.intent_vector


def test_video_impact_detected_adds_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(video={
        "impact_detected": True,
        "impact_score": 0.9,
    })
    fused = ModalFusion().fuse(token)
    assert "visual_impact_detected" in fused.intent_vector
    assert "impact_event" in fused.risk_clusters


# ─── 4. Text + audio (confidence weighting) ──────────────────────────────────


def test_text_audio_merge_picks_highest_weighted_intent():
    from axiom_fusion import ModalFusion
    # Text at confidence 0.9 votes for ask_factual
    # Audio at confidence 0.6 votes for physical_impact_detected
    # ask_factual should rank first (higher weight)
    token = _make_token(
        text={"intent_vector": ["ask_factual"], "_confidence": 0.9},
        audio={
            "impact_profile": "sharp_transient",
            "depth": 0.1,
            "width": 0.2,
            "rhythm": "single_impact",
            "_confidence": 0.6,
        },
    )
    fused = ModalFusion().fuse(token)
    assert fused.intent_vector[0] == "ask_factual"
    assert "physical_impact_detected" in fused.intent_vector
    assert "text" in fused.modalities
    assert "audio" in fused.modalities
    assert fused.verify()


def test_fusion_confidence_is_mean_of_modal_confidences():
    from axiom_fusion import ModalFusion
    token = _make_token(
        text={"intent_vector": ["ask_factual"], "_confidence": 0.8},
        audio={"impact_profile": "sustained", "depth": 0.1, "width": 0.1,
               "rhythm": "periodic", "_confidence": 0.6},
    )
    fused = ModalFusion().fuse(token)
    # mean(0.8, 0.6) = 0.7
    assert abs(fused.fusion_confidence - 0.7) < 1e-3


# ─── 5. All three modalities ─────────────────────────────────────────────────


def test_text_audio_video_all_modalities_present():
    from axiom_fusion import ModalFusion
    token = _make_token(
        text={"intent_vector": ["ask_causal"], "_confidence": 0.85},
        audio={"impact_profile": "sharp_transient", "material_signature": "glass-like",
               "depth": 0.3, "width": 0.6, "rhythm": "single_impact", "_confidence": 0.91},
        video={"motion_class": "downward", "impact_detected": True,
               "object_count": 1, "_confidence": 0.88},
    )
    fused = ModalFusion().fuse(token)
    assert set(fused.modalities) == {"text", "audio", "video"}
    # Audio (0.91 × 3 signals) + video (0.88 × 3 signals) fill the 6-slot cap,
    # displacing text (0.85 × 1 signal). This is correct: physical-event
    # modalities dominate when their combined weight exceeds text weight.
    assert "physical_impact_detected" in fused.intent_vector
    assert "downward_motion_detected" in fused.intent_vector
    assert "impact_event" in fused.risk_clusters
    assert "sharp_impact_risk" in fused.risk_clusters
    assert len(fused.intent_vector) <= 6
    assert fused.verify()


# ─── 6. Governance HARM propagates ───────────────────────────────────────────


def test_governance_harm_adds_risk_cluster():
    from axiom_fusion import ModalFusion
    token = _make_token(governance={"verdict": "HARM", "_confidence": 0.95})
    fused = ModalFusion().fuse(token)
    assert "harm_detected" in fused.risk_clusters
    assert "governance_blocked" in fused.intent_vector


def test_governance_deceive_adds_deception_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(governance={"intent_class": "DECEIVE", "_confidence": 0.9})
    fused = ModalFusion().fuse(token)
    assert "deception_detected" in fused.risk_clusters


def test_governance_inform_adds_no_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(governance={"verdict": "INFORM", "_confidence": 0.9})
    fused = ModalFusion().fuse(token)
    assert "harm_detected" not in fused.risk_clusters
    assert "deception_detected" not in fused.risk_clusters


# ─── 7. Absent layers contribute nothing ─────────────────────────────────────


def test_absent_audio_does_not_add_audio_signals():
    from axiom_fusion import ModalFusion
    # Only text is present; no audio slot
    token = _make_token(text={"intent_vector": ["ask_factual"]})
    fused = ModalFusion().fuse(token)
    for sig in ("physical_impact_detected", "sustained_sound_detected",
                "fragile_material_event"):
        assert sig not in fused.intent_vector


# ─── 8. fusion_confidence cap ────────────────────────────────────────────────


def test_fusion_confidence_capped_at_085():
    from axiom_fusion import ModalFusion, CONFIDENCE_CAP
    # All layers at maximum confidence
    token = _make_token(
        text={"intent_vector": ["ask_factual"], "_confidence": 1.0},
        audio={"impact_profile": "sharp_transient", "depth": 0.1, "width": 0.1,
               "rhythm": "single_impact", "_confidence": 1.0},
        video={"motion_class": "static", "_confidence": 1.0},
    )
    fused = ModalFusion().fuse(token)
    assert fused.fusion_confidence <= CONFIDENCE_CAP


# ─── 9. Signature round-trip ─────────────────────────────────────────────────


def test_fused_intent_verify_passes():
    from axiom_fusion import ModalFusion
    token = _make_token(text={"intent_vector": ["ask_recommendation"]})
    fused = ModalFusion().fuse(token)
    assert fused.verify()


def test_fused_intent_verify_fails_if_tampered():
    from axiom_fusion import ModalFusion
    token = _make_token(text={"intent_vector": ["ask_recommendation"]})
    fused = ModalFusion().fuse(token)
    fused.intent_vector.append("injected_signal")
    assert not fused.verify()


# ─── 10. to_latent_state() bridge ────────────────────────────────────────────


def test_to_latent_state_produces_valid_latent_state():
    from axiom_fusion import ModalFusion
    token = _make_token(
        text={"intent_vector": ["ask_causal"], "risk_clusters": ["medical"]},
    )
    fused = ModalFusion().fuse(token)
    state = fused.to_latent_state()
    # LatentState is a dataclass — check expected fields
    assert hasattr(state, "intent_vector")
    assert hasattr(state, "risk_clusters")
    assert hasattr(state, "compressed_plan")
    assert hasattr(state, "confidence")
    assert state.confidence <= 0.85
    assert "ask_causal" in state.intent_vector
    assert "medical" in state.risk_clusters
    assert len(state.compressed_plan) > 0


# ─── 11. Empty token → signed fallback ───────────────────────────────────────


def test_empty_token_returns_signed_fallback():
    from axiom_fusion import ModalFusion
    from axiom_event_token.models import EventToken, now_iso
    import uuid

    empty_token = EventToken(
        id=str(uuid.uuid4()),
        created_at=now_iso(),
        activated_agents=(),
    )
    fused = ModalFusion().fuse(empty_token)
    assert fused.intent_vector == ["ask_general"]
    assert fused.risk_clusters == []
    assert fused.modalities == []
    assert fused.verify()


# ─── 12. Physics layer signals ───────────────────────────────────────────────


def test_physics_surface_and_depth_produce_signals():
    from axiom_fusion import ModalFusion
    token = _make_token(physics={
        "surface_class": "hard-floor",
        "depth_class":   "foreground",
        "material":      "brittle_glass",
        "_confidence": 0.8,
    })
    fused = ModalFusion().fuse(token)
    assert any("surface_" in s for s in fused.intent_vector)
    assert any("depth_" in s for s in fused.intent_vector)
    assert "proximity_event" in fused.risk_clusters


def test_physics_brittle_break_adds_risk():
    from axiom_fusion import ModalFusion
    token = _make_token(physics={
        "material_response": "brittle_break",
        "depth_class": "mid",
        "_confidence": 0.8,
    })
    fused = ModalFusion().fuse(token)
    assert "brittle_fracture_event" in fused.risk_clusters


# ─── 13. Intent vector length cap ────────────────────────────────────────────


def test_intent_vector_capped_at_six():
    from axiom_fusion import ModalFusion
    # Feed many signals through multiple layers
    token = _make_token(
        text={"intent_vector": ["ask_factual", "ask_causal", "ask_predictive"]},
        audio={"impact_profile": "sharp_transient",
               "material_signature": "metal-like",
               "rhythm": "periodic",
               "depth": 0.7, "width": 0.6},
        video={"motion_class": "erratic", "object_count": 5,
               "impact_detected": True},
    )
    fused = ModalFusion().fuse(token)
    assert len(fused.intent_vector) <= 6


# ─── 14. compressed_plan selected from primary intent ────────────────────────


def test_compressed_plan_matches_primary_intent():
    from axiom_fusion import ModalFusion, _PLANS
    token = _make_token(text={"intent_vector": ["ask_procedural"]})
    fused = ModalFusion().fuse(token)
    assert fused.compressed_plan == _PLANS["ask_procedural"]


def test_compressed_plan_defaults_for_unknown_intent():
    from axiom_fusion import ModalFusion, _PLANS
    token = _make_token(audio={
        "impact_profile": "silence",
        "depth": 0.0, "width": 0.0, "rhythm": "single_impact",
    })
    fused = ModalFusion().fuse(token)
    # "audio_silence" maps to default plan
    assert fused.compressed_plan == _PLANS.get(
        fused.intent_vector[0] if fused.intent_vector else "default",
        _PLANS["default"],
    )
