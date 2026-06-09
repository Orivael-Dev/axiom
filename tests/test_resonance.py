"""Unit tests for axiom_resonance — token, encoder, router, detector, layer."""
from __future__ import annotations

import math
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_resonance", "axiom_agent_fabric", "axiom_event_token",
            "axiom_signing", "axiom_intent_classifier", "axiom_fusion",
            "axiom_exoskeleton_ledger", "axiom_latent_v2",
        )):
            sys.modules.pop(mod, None)
    yield


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_agent(
    agent_id: str = "test_agent",
    role: str = "general purpose assistant",
    wake_conditions: list | None = None,
    compression_state: str = "dormant",
):
    from axiom_agent_fabric.capsule import MiniSRDAgent
    a = MiniSRDAgent(
        agent_id          = agent_id,
        role              = role,
        wake_conditions   = wake_conditions or ["test", "example"],
        skills            = ["skill_a"],
        tool_permissions  = ["web"],
        memory_pointer    = f"srd://bundles/{agent_id}",
        compression_state = compression_state,
        governance_limits = [],
        axm_fingerprint   = "abcd1234",
        bpw               = 4.5,
        params_m          = 135,
    )
    return a.sign()


def _make_layer_report(
    agent:      str = "text",
    intent_cls: str = "INFORM",
    confidence: float = 0.72,
    verdict:    str = "INFORM",
    risk_cls:   list | None = None,
):
    from axiom_event_token.models import LayerReport
    payload = {
        "intent_class": intent_cls,
        "confidence":   confidence,
        "verdict":      verdict,
    }
    if risk_cls:
        payload["risk_clusters"] = risk_cls
    return LayerReport.signed(agent=agent, payload=payload, confidence=confidence)


def _make_event_token(
    intent_cls:  str = "INFORM",
    confidence:  float = 0.72,
    verdict:     str = "INFORM",
    risk_cls:    list | None = None,
):
    """Build a minimal signed EventToken via Coordinator.compose()."""
    from axiom_event_token.coordinator import Coordinator
    coord = Coordinator()
    token = coord.compose(text=f"test event intent={intent_cls} conf={confidence}")
    return token


def _make_signal(
    token_id:   str = "test_signal",
    domain:     str = "general",
    amplitude:  float = 0.7,
    phase:      float = 0.0,
    confidence: float = 0.72,
):
    from axiom_resonance.token import ResonanceSignal, domain_to_frequency
    from datetime import datetime, timezone
    sig = ResonanceSignal(
        token_id   = token_id,
        domain     = domain,
        frequency  = round(domain_to_frequency(domain), 6),
        amplitude  = amplitude,
        phase      = phase,
        decay      = 1.0,
        confidence = confidence,
        risk_flags = [],
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
    return sig.sign()


def _make_ret(signal=None, event_token=None):
    """Build a minimal signed ResonantEventToken."""
    from axiom_resonance.token import ResonantEventToken
    if event_token is None:
        event_token = _make_event_token()
    if signal is None:
        signal = _make_signal()
    ret = ResonantEventToken(
        event_token = event_token,
        resonance   = signal,
    )
    return ret.sign()


# ─── 1. domain_to_frequency determinism ───────────────────────────────────────


def test_domain_to_frequency_deterministic():
    from axiom_resonance.token import domain_to_frequency
    f1 = domain_to_frequency("medical")
    f2 = domain_to_frequency("medical")
    assert f1 == f2
    assert 0.0 <= f1 <= 1.0


# ─── 2. different domains yield different frequencies ─────────────────────────


def test_domain_to_frequency_different_domains_differ():
    from axiom_resonance.token import domain_to_frequency
    domains = ["legal", "medical", "security", "finance", "code",
               "physics", "governance", "memory", "general"]
    freqs = [domain_to_frequency(d) for d in domains]
    # All 9 must be distinct
    assert len(set(freqs)) == len(freqs), "duplicate frequencies detected"


# ─── 3. ResonanceSignal sign + verify ────────────────────────────────────────


def test_resonance_signal_sign_and_verify():
    sig = _make_signal()
    assert sig.signature != ""
    assert sig.verify()


# ─── 4. Tampered signal fails verify ─────────────────────────────────────────


def test_resonance_signal_tamper_breaks_verify():
    from axiom_resonance.token import ResonanceSignal
    original = _make_signal(amplitude=0.7)
    tampered = ResonanceSignal(
        token_id   = original.token_id,
        domain     = original.domain,
        frequency  = original.frequency,
        amplitude  = 0.99,          # tampered
        phase      = original.phase,
        decay      = original.decay,
        confidence = original.confidence,
        risk_flags = original.risk_flags,
        timestamp  = original.timestamp,
        signature  = original.signature,
    )
    assert not tampered.verify()


# ─── 5. Phase maps correctly from payload ────────────────────────────────────


def test_resonance_encoder_phase_from_payload():
    from axiom_resonance.encoder import _phase_from_payload, DOMAIN_KEYWORDS
    from axiom_resonance.token import PHASE_STABLE, PHASE_UNCERTAIN, PHASE_OPPOSING

    # HARM verdict → opposing phase
    p_harm = {"verdict": "HARM", "intent_class": "HARM", "confidence": 0.8}
    assert _phase_from_payload(p_harm) == PHASE_OPPOSING

    # DECEIVE intent → opposing
    p_dec = {"verdict": "DECEIVE", "intent_class": "DECEIVE", "confidence": 0.7}
    assert _phase_from_payload(p_dec) == PHASE_OPPOSING

    # Low confidence → uncertain
    p_low = {"verdict": "INFORM", "intent_class": "INFORM", "confidence": 0.3}
    assert _phase_from_payload(p_low) == PHASE_UNCERTAIN

    # Normal INFORM → stable
    p_ok = {"verdict": "INFORM", "intent_class": "INFORM", "confidence": 0.8}
    assert _phase_from_payload(p_ok) == PHASE_STABLE


# ─── 6. Amplitude uses risk amplification ─────────────────────────────────────


def test_resonance_encoder_amplitude_risk_amplification():
    from axiom_resonance.encoder import ResonanceEncoder
    from axiom_event_token.models import LayerReport

    enc = ResonanceEncoder()
    payload_with_risk = {
        "intent_class":  "INFORM",
        "confidence":    0.8,
        "verdict":       "INFORM",
    }
    lr_risk = LayerReport.signed(agent="text", payload=payload_with_risk, confidence=0.8)
    sig_risk = enc.encode_layer(lr_risk, gov_payload={"risk_clusters": ["data_leak"]})
    assert sig_risk.amplitude == round(min(0.8 * 1.15, 1.0), 4)

    lr_clean = LayerReport.signed(agent="text", payload=payload_with_risk, confidence=0.8)
    sig_clean = enc.encode_layer(lr_clean, gov_payload={})
    assert sig_clean.amplitude == round(0.8, 4)


# ─── 7. Decay is 1.0 at creation time ────────────────────────────────────────


def test_resonance_encoder_decay_at_creation():
    from axiom_resonance.encoder import ResonanceEncoder
    from axiom_event_token.models import LayerReport

    enc = ResonanceEncoder()
    lr  = LayerReport.signed(
        agent="text",
        payload={"intent_class": "INFORM", "confidence": 0.72},
        confidence=0.72,
    )
    # created_at=None → age_s=0 → decay=exp(0)=1.0
    sig = enc.encode_layer(lr)
    assert sig.decay == 1.0


# ─── 8. Router prefers frequency band match ───────────────────────────────────


def test_resonance_router_prefers_frequency_band_match():
    from axiom_resonance.router import ResonanceRouter

    medical_agent = _make_agent("medical_agent", role="medical research and health analysis")
    code_agent    = _make_agent("code_agent",    role="code review and software debugging")

    router = ResonanceRouter([medical_agent, code_agent], min_score=0.0)

    # Medical-domain signal
    medical_sig = _make_signal(domain="medical")
    scores = router.score(medical_sig, event_text="patient clinical research")

    by_id = {s.agent.agent_id: s for s in scores}
    assert by_id["medical_agent"].resonance_sim > by_id["code_agent"].resonance_sim


# ─── 9. Router min_score filters out low-relevance agents ─────────────────────


def test_resonance_router_min_score_filters_low_relevance():
    from axiom_resonance.router import ResonanceRouter
    from axiom_resonance.token import PHASE_OPPOSING

    agent = _make_agent("medical_agent", role="medical research and health analysis")
    router = ResonanceRouter([agent], min_score=0.8)

    # Opposing-phase signal → phase_align=0.0
    opposing_sig = _make_signal(domain="finance", phase=PHASE_OPPOSING)
    scores = router.score(opposing_sig, event_text="unrelated stock market data")
    woken  = router.wake(scores)
    assert len(woken) == 0


# ─── 10. Detector: single signal → no alert ──────────────────────────────────


def test_detector_no_alert_on_single_signal():
    from axiom_resonance.detector import ResonanceDetector

    det = ResonanceDetector()
    ret = _make_ret()
    alert = det.detect(ret)
    # First call always initialises baseline → NONE
    assert alert.alert_type == "NONE"
    assert alert.signature != ""


# ─── 11. Detector: phase conflict fires above threshold ───────────────────────


def test_detector_phase_conflict_fires_above_threshold():
    from axiom_resonance.detector import ResonanceDetector, PHASE_CONFLICT_THRESHOLD
    from axiom_resonance.token import PHASE_STABLE, PHASE_OPPOSING

    det = ResonanceDetector()

    # Initialise baseline with stable signal
    ret_stable = _make_ret(_make_signal(phase=PHASE_STABLE, amplitude=0.6))
    det.detect(ret_stable)
    det.update_baseline(ret_stable)

    # Send opposing-phase signal with large amplitude spike
    ret_opposing = _make_ret(_make_signal(phase=PHASE_OPPOSING, amplitude=0.95))
    alert = det.detect(ret_opposing)

    assert alert.alert_type == "PHASE_CONFLICT"
    assert alert.severity in ("HIGH", "CRITICAL")
    assert alert.phase_conflict_score > PHASE_CONFLICT_THRESHOLD
    assert alert.signature != ""
    assert alert.verify()


# ─── 12. Detector: amplitude spike fires above EMA ───────────────────────────


def test_detector_amplitude_spike_fires_above_ema():
    from axiom_resonance.detector import ResonanceDetector, AMPLITUDE_SPIKE_THRESHOLD

    det = ResonanceDetector()

    # Initialise with low amplitude baseline
    ret_low = _make_ret(_make_signal(amplitude=0.2, phase=0.0))
    det.detect(ret_low)
    det.update_baseline(ret_low)

    # Send high-amplitude signal (same phase → no phase conflict)
    ret_high = _make_ret(_make_signal(amplitude=0.9, phase=0.0))
    alert = det.detect(ret_high)

    spike = abs(0.9 - 0.2)
    if spike > AMPLITUDE_SPIKE_THRESHOLD:
        assert alert.alert_type in ("AMPLITUDE_SPIKE", "PHASE_CONFLICT")
    else:
        assert alert.alert_type == "NONE"


# ─── 13. ResonanceLayer full cycle returns correct result ─────────────────────


def test_resonance_layer_full_cycle_returns_result():
    from axiom_resonance.layer import ResonanceLayer, ResonanceLayerResult

    agents = [
        _make_agent("medical_agent", role="medical research and clinical evidence"),
        _make_agent("legal_agent",   role="legal compliance and regulatory risk"),
        _make_agent("code_agent",    role="code review and software debugging"),
    ]
    layer  = ResonanceLayer(agents=agents)
    result = layer.run("Research medical publications on patient privacy regulations.")

    assert isinstance(result, ResonanceLayerResult)
    # Event token should be signed
    assert result.fabric_result.event_token.verify()
    # Resonance signals should be signed
    assert all(ret.resonance.verify() for ret in result.signals)
    assert all(ret.verify() for ret in result.signals)
    # Alerts should all be signed
    assert all(a.signature != "" for a in result.alerts)
    # Router scored all 3 agents
    assert len(result.route_scores) == 3
    # Audit record should have a signature
    assert result.audit_record.get("signature", "") != ""
