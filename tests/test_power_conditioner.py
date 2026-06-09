"""Tests for axiom_agent_fabric/power_conditioner.py."""
import os
import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "a" * 64)

from axiom_agent_fabric.power_conditioner import (
    PowerConditionerAgent,
    PowerProfile,
    PowerSensor,
    PowerState,
    InputConditioner,
    InferenceConfig,
    PROFILE_CONFIGS,
    RAM_BUDGET_MB,
    select_profile,
)


# ── PowerState signing ────────────────────────────────────────────────────────

def test_power_state_sign_produces_signature():
    state = PowerState(battery_pct=80, is_charging=True, is_backup=False,
                       thermal_ok=True, source="mock").sign()
    assert len(state.signature) == 64


def test_power_state_tamper_detected():
    state = PowerState(battery_pct=80, is_charging=True, is_backup=False,
                       thermal_ok=True, source="mock").sign()
    sig = state.signature
    state.battery_pct = 5
    assert state.signature == sig  # signature not recomputed automatically
    # Manually verify: signing with changed values yields different sig
    tampered = PowerState(battery_pct=5, is_charging=True, is_backup=False,
                          thermal_ok=True, source="mock").sign()
    assert tampered.signature != sig


# ── Profile selection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("pct,charging,backup,expected", [
    (100, True,  False, PowerProfile.FULL),
    ( 85, False, False, PowerProfile.FULL),
    ( 70, False, False, PowerProfile.FULL),
    ( 65, False, False, PowerProfile.STANDARD),
    ( 40, False, False, PowerProfile.CONSERVE),
    ( 39, False, False, PowerProfile.CONSERVE),
    ( 20, False, False, PowerProfile.CRITICAL),
    ( 19, False, False, PowerProfile.CRITICAL),
    (  5, False, False, PowerProfile.CRITICAL),
    ( 72, False, True,  PowerProfile.EMERGENCY),  # UPS even with good battery
    (  5, False, True,  PowerProfile.EMERGENCY),  # UPS + critical battery
    ( 50, True,  True,  PowerProfile.EMERGENCY),  # charging but on backup
])
def test_select_profile(pct, charging, backup, expected):
    state = PowerState(battery_pct=pct, is_charging=charging,
                       is_backup=backup, thermal_ok=True, source="mock")
    assert select_profile(state) == expected


def test_charging_overrides_low_battery():
    """Even at 10 %, charging keeps profile at FULL."""
    state = PowerState(battery_pct=10, is_charging=True, is_backup=False,
                       thermal_ok=True, source="mock")
    assert select_profile(state) == PowerProfile.FULL


# ── InferenceConfig ───────────────────────────────────────────────────────────

def test_inference_config_sign_and_verify():
    cfg = PROFILE_CONFIGS[PowerProfile.CONSERVE]
    import copy
    c = copy.copy(cfg)
    c.sign()
    assert len(c.signature) == 64
    assert c.verify()


def test_all_profiles_have_configs():
    for profile in PowerProfile:
        assert profile in PROFILE_CONFIGS
        assert profile in RAM_BUDGET_MB


def test_alpha_decreases_with_power_pressure():
    alphas = [PROFILE_CONFIGS[p].alpha for p in [
        PowerProfile.FULL, PowerProfile.STANDARD, PowerProfile.CONSERVE,
        PowerProfile.CRITICAL, PowerProfile.EMERGENCY,
    ]]
    assert alphas == sorted(alphas, reverse=True)


def test_context_window_decreases_with_power_pressure():
    ctxs = [PROFILE_CONFIGS[p].context_window for p in [
        PowerProfile.FULL, PowerProfile.STANDARD, PowerProfile.CONSERVE,
        PowerProfile.CRITICAL, PowerProfile.EMERGENCY,
    ]]
    assert ctxs == sorted(ctxs, reverse=True)


def test_emergency_has_model_fallback():
    cfg = PROFILE_CONFIGS[PowerProfile.EMERGENCY]
    assert cfg.model_fallback != ""
    assert "135" in cfg.model_fallback or "SmolLM" in cfg.model_fallback


def test_full_and_standard_no_compression():
    assert PROFILE_CONFIGS[PowerProfile.FULL].compress_input is False
    assert PROFILE_CONFIGS[PowerProfile.STANDARD].compress_input is False


# ── InputConditioner ──────────────────────────────────────────────────────────

def test_conditioner_passthrough_within_budget():
    cond = InputConditioner()
    text = "Short text. Only a few words."
    result, orig, compressed = cond.compress(text, query="text", budget=50)
    assert result == text
    assert orig == compressed


def test_conditioner_reduces_long_text():
    cond = InputConditioner()
    # Build a text with clear query-relevant and irrelevant sentences
    relevant   = "The Byzantine dome was constructed using pendentives and squinches."
    irrelevant = "The weather was fine that day and people went for walks."
    text = " ".join([irrelevant] * 10 + [relevant] * 2 + [irrelevant] * 10)
    result, orig, compressed = cond.compress(text, query="Byzantine dome construction", budget=60)
    assert compressed <= 60
    assert orig > 60
    # The relevant sentence should survive — it scores highest on query overlap
    assert "Byzantine" in result or "dome" in result


def test_conditioner_always_keeps_first_and_last():
    cond = InputConditioner()
    sentences = [
        "First sentence here is very important and unique.",
        "Middle filler sentence about nothing relevant at all.",
        "Another filler sentence with no keywords whatsoever.",
        "More filler content that doesn't match any query terms.",
        "Last sentence here is also unique and important to keep.",
    ]
    text = " ".join(sentences)
    result, orig, compressed = cond.compress(text, query="unrelated query xyz", budget=20)
    assert "First sentence" in result
    assert "Last sentence" in result


def test_conditioner_short_text_truncates():
    """Text with ≤2 sentences gets truncated, not scored."""
    cond = InputConditioner()
    text = "One long sentence " + "word " * 100
    result, orig, compressed = cond.compress(text, query="word", budget=10)
    assert len(result.split()) <= 10


# ── PowerSensor mock ──────────────────────────────────────────────────────────

def test_power_sensor_mock_fallback():
    """On a machine with no /sys power_supply, mock_full is returned."""
    import unittest.mock as mock
    sensor = PowerSensor()

    # Patch all platform readers to return None
    with mock.patch.object(sensor, "_linux_sys", return_value=None), \
         mock.patch.object(sensor, "_termux",    return_value=None), \
         mock.patch.object(sensor, "_jetson",    return_value=None):
        state = sensor.read()

    assert state.battery_pct == 100
    assert state.is_charging is True
    assert state.source == "mock_full"


# ── Full agent cycle ──────────────────────────────────────────────────────────

def test_agent_full_cycle_normal_battery():
    agent  = PowerConditionerAgent()
    state  = PowerState(battery_pct=85, is_charging=False, is_backup=False,
                        thermal_ok=True, source="mock")
    result = agent.condition("Hello world.", query="hello", state=state)
    assert result.profile == PowerProfile.FULL
    assert result.config.alpha == 1.0
    assert result.compressed is False


def test_agent_full_cycle_emergency():
    agent  = PowerConditionerAgent()
    state  = PowerState(battery_pct=60, is_charging=False, is_backup=True,
                        thermal_ok=True, source="mock")
    long_text = "Some important content. " * 200  # well over any budget
    result = agent.condition(long_text, query="content", state=state)
    assert result.profile == PowerProfile.EMERGENCY
    assert result.config.alpha == 0.0
    assert result.compressed is True
    assert result.conditioned_tokens <= result.config.compress_budget * 1.1  # 10% tolerance
    assert result.config.model_fallback != ""


def test_agent_conserve_compresses_long_input():
    agent  = PowerConditionerAgent()
    state  = PowerState(battery_pct=30, is_charging=False, is_backup=False,
                        thermal_ok=True, source="mock")
    long_text = "Battery technology advances rapidly. " * 300  # 1500 tokens > 1024 budget
    result = agent.condition(long_text, query="battery technology", state=state)
    assert result.profile == PowerProfile.CONSERVE
    assert result.compressed is True
    assert result.conditioned_tokens < result.original_tokens


def test_agent_no_compression_below_budget():
    agent  = PowerConditionerAgent()
    state  = PowerState(battery_pct=30, is_charging=False, is_backup=False,
                        thermal_ok=True, source="mock")
    short_text = "Short query."
    result = agent.condition(short_text, query="query", state=state)
    assert result.profile == PowerProfile.CONSERVE
    assert result.compressed is False
    assert result.conditioned_text == short_text


def test_ram_budget_api():
    agent = PowerConditionerAgent()
    assert agent.ram_budget_mb(PowerProfile.FULL, "qwen3_1b7") == 1409
    assert agent.ram_budget_mb(PowerProfile.EMERGENCY, "tinyllama_1b") == 25
    assert agent.ram_budget_mb(PowerProfile.FULL, "unknown_model") == -1
