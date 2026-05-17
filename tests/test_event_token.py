"""Tests for the 3D / multimodal event token validation prototype.

The 8 tests from the saved plan. These prove the CONTAINER abstraction
holds — not that the underlying agents are accurate (Audio + Video
Phase A tests cover those when those engines ship).
"""
from __future__ import annotations

import json
import sys

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing", "axiom_intent_classifier",
        )):
            sys.modules.pop(mod, None)
    yield


# ─── 1. Shape — full token matches the concept-note layer schema ────────


def test_shape_full_token_matches_concept_note_fields(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    token = coord.compose(
        text="The glass cup fell and shattered.",
        audio={"impact_profile": "sharp_transient", "material_signature": "glass",
               "decay_pattern": "scattered_fragments", "confidence": 0.91},
        video={"object_motion": "downward", "impact_point": "floor",
               "fracture_pattern": "radial_scatter", "confidence": 0.88},
        physics={"material": "brittle_glass", "surface": "hard_surface",
                 "motion": "downward"},
        token_id="event_cup_shatter_001",
    )

    d = token.to_dict()
    # All five layer slots present + non-null
    for layer_name in ("text", "audio", "video", "physics", "governance"):
        assert d[layer_name] is not None, f"{layer_name} layer missing"

    # Text payload mirrors IntentClassifier output
    assert d["text"]["payload"]["intent_class"] in {
        "INFORM", "CLARIFY", "REFUSE", "HARM", "DECEIVE", "UNCERTAIN",
    }
    # Audio payload preserves the worked-example fields verbatim
    assert d["audio"]["payload"]["impact_profile"] == "sharp_transient"
    assert d["audio"]["payload"]["material_signature"] == "glass"
    # Video payload preserves fracture pattern
    assert d["video"]["payload"]["fracture_pattern"] == "radial_scatter"
    # Physics rule fired for brittle_glass + hard_surface + downward
    assert d["physics"]["payload"]["material_response"] == "brittle_break"
    assert d["physics"]["payload"]["plausible"] is True
    # Governance evidence trace covers the four other layers in order
    assert d["governance"]["payload"]["evidence_trace"] == [
        "text", "audio", "video", "physics",
    ]


# ─── 2. Selective activation — caller picks which agents fire ──────────


def test_selective_activation_only_fires_requested_agents(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    token = coord.compose(
        text="Buy gift cards now",
        activate=("text", "governance"),
    )

    d = token.to_dict()
    assert d["text"] is not None, "text was requested + should be present"
    assert d["governance"] is not None, "governance was requested + should be present"
    assert d["audio"] is None, "audio was NOT requested + should be null"
    assert d["video"] is None, "video was NOT requested + should be null"
    assert d["physics"] is None, "physics was NOT requested + should be null"
    assert d["activated_agents"] == ["text", "governance"]


# ─── 3. Three-tier signing — per-layer + coordinator + outer ───────────


def test_three_tier_signing_each_tier_verifies_independently(isolated):
    from axiom_event_token import Coordinator
    from axiom_event_token.models import (
        COORD_KEY_NS, TOKEN_KEY_NS, _canonical_coordinator,
        _canonical_token, _sign,
    )

    coord = Coordinator()
    token = coord.compose(text="hi there", activate=("text", "governance"))

    # Per-layer sig verifies under LAYER_KEY_NS
    assert token.text.verify()
    assert token.governance.verify()

    # Coordinator sig verifies under COORD_KEY_NS
    expected_coord = _sign(_canonical_coordinator(token), COORD_KEY_NS)
    assert token.coordinator_sig == expected_coord

    # Outer token sig verifies under TOKEN_KEY_NS
    expected_outer = _sign(_canonical_token(token), TOKEN_KEY_NS)
    assert token.signature == expected_outer

    # Full verify() short-circuits to True for an untampered token
    assert token.verify() is True


def test_tampering_a_single_layer_breaks_only_its_signature(isolated):
    """If audio's payload is silently swapped, audio.verify() returns False
    but text.verify() still passes. The outer token's verify() catches
    the tamper via the coordinator sig (which covers all layer sigs).
    """
    from axiom_event_token import Coordinator
    from axiom_event_token.models import EventToken, LayerReport

    coord = Coordinator()
    token = coord.compose(
        text="hi",
        audio={"impact_profile": "sharp_transient", "confidence": 0.9},
        activate=("text", "audio"),
    )

    # Swap the audio layer's payload but keep the old signature
    tampered_audio = LayerReport(
        agent=token.audio.agent,
        payload={"impact_profile": "TAMPERED", "confidence": 0.9},
        confidence=token.audio.confidence,
        signature=token.audio.signature,  # stale signature
    )
    tampered_token = EventToken(
        id=token.id, format_version=token.format_version,
        created_at=token.created_at,
        activated_agents=token.activated_agents,
        text=token.text, audio=tampered_audio,
        video=token.video, physics=token.physics, governance=token.governance,
        coordinator_sig=token.coordinator_sig,
        signature=token.signature,
    )

    # The audio layer alone fails verify
    assert tampered_audio.verify() is False
    # The text layer is unaffected
    assert tampered_token.text.verify() is True
    # The outer full verify() catches it (composition integrity)
    assert tampered_token.verify() is False


# ─── 4. Text agent wires through real IntentClassifier ─────────────────


def test_text_agent_uses_real_intent_classifier(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    token = coord.compose(
        text="Buy Google Play gift cards immediately to clear your debt",
        activate=("text",),
    )

    # HARM verdict from the real classifier flows through
    assert token.text.payload["intent_class"] == "HARM"
    # Signals (e.g. "harm:1") show up
    assert any(sig.startswith("harm:") for sig in token.text.payload["signals"])


# ─── 5. Physics-plausibility lookup table ───────────────────────────────


def test_physics_lookup_returns_plausible_for_brittle_glass(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    token = coord.compose(
        physics={"material": "brittle_glass", "surface": "hard_surface",
                 "motion": "downward"},
        activate=("physics", "governance"),
    )
    assert token.physics.payload["plausible"] is True
    assert token.physics.payload["material_response"] == "brittle_break"


def test_physics_unknown_combination_returns_not_plausible(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    token = coord.compose(
        physics={"material": "antimatter", "surface": "unicorn",
                 "motion": "warp"},
        activate=("physics", "governance"),
    )
    assert token.physics.payload["plausible"] is False
    assert "no rule matched" in token.physics.payload["note"]


# ─── 6. Governance evidence trace ───────────────────────────────────────


def test_governance_records_evidence_trace_of_other_layers(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    token = coord.compose(
        text="hi",
        audio={"impact_profile": "soft", "confidence": 0.6},
        video={"object_motion": "static", "confidence": 0.4},
        activate=("text", "audio", "video", "governance"),
    )

    payload = token.governance.payload
    assert payload["evidence_trace"] == ["text", "audio", "video"]
    assert payload["audit_mode"] == "enabled"
    assert payload["layer_activation"] == "task_specific"
    # Per-layer confidence is captured
    assert payload["per_layer_confidence"]["audio"] == 0.6
    assert payload["per_layer_confidence"]["video"] == 0.4
    # Aggregate is the mean
    assert payload["aggregate_confidence"] > 0.0


# ─── 7. Coordinator selective-activation API supports any subset ──────


def test_coordinator_supports_arbitrary_agent_subsets(isolated):
    """The API surface accepts any subset of the 5 agents in any order
    and still produces a verifiable token. Proves the design is right
    for the future ThreadPoolExecutor swap.
    """
    from axiom_event_token import Coordinator

    coord = Coordinator()
    cases = [
        ("text",),
        ("audio",),
        ("text", "audio"),
        ("video", "governance"),
        ("text", "physics", "governance"),
    ]
    for subset in cases:
        token = coord.compose(
            text="hi" if "text" in subset else None,
            audio={"impact_profile": "soft"} if "audio" in subset else None,
            video={"object_motion": "static"} if "video" in subset else None,
            physics={"material": "rubber_ball", "surface": "hard_surface",
                     "motion": "downward"} if "physics" in subset else None,
            activate=subset,
        )
        assert token.verify(), f"subset {subset} produced invalid token"
        assert tuple(token.activated_agents) == subset


def test_coordinator_rejects_unknown_agent(isolated):
    from axiom_event_token import Coordinator

    coord = Coordinator()
    with pytest.raises(ValueError, match="Unknown agents"):
        coord.compose(text="hi", activate=("text", "not_a_real_agent"))


# ─── 8. Roundtrip — serialize, deserialize, re-verify ──────────────────


def test_roundtrip_json_serialize_deserialize_signatures_pass(isolated):
    from axiom_event_token import Coordinator
    from axiom_event_token.models import EventToken

    coord = Coordinator()
    original = coord.compose(
        text="The glass cup fell and shattered.",
        audio={"impact_profile": "sharp_transient", "confidence": 0.91},
        physics={"material": "brittle_glass", "surface": "hard_surface",
                 "motion": "downward"},
        activate=("text", "audio", "physics", "governance"),
        token_id="event_roundtrip_01",
    )

    payload = original.to_json()
    restored = EventToken.from_dict(json.loads(payload))

    assert restored.id == original.id
    assert restored.format_version == original.format_version
    assert restored.signature == original.signature
    assert restored.verify() is True

    # Re-serialize and verify the JSON is byte-stable
    assert restored.to_json() == payload
