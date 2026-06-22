"""Tests for axiom_blueprint.py — v1.4 declarative state-machine compiler.

All tests run without GPU, model downloads, or network access.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from axiom_blueprint import (
    BLUEPRINT_VERSION,
    MAX_STATE_VISITS,
    AxiomBlueprint,
    BlueprintParser,
    BlueprintStateMachine,
    ExecutionTelemetry,
    InvariantViolation,
    RuntimeConfig,
    WeightedCategory,
    load_blueprint,
    run_blueprint,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

REFACTOR_AXIOM = Path(__file__).parent.parent / "examples" / "refactor_agent.axiom"

_MINIMAL_AXIOM = """
module Test.Minimal;

runtime Cfg {
    max_context_tokens: 4096;
    reserve_response_tokens: 512;
    execution_mode: "local_first";
    temperature_profile: dynamic(0.1, 0.5);
}

invariant NetGate {
    allow_network: false;
}

state Alpha {
    prompt: "Do analysis.";
    evaluate probabilistic_weights {
        "catA" => weight(0.70),
        "catB" => weight(0.30)
    }
    transition_threshold: 0.60;
    on_success(certainty >= transition_threshold) => state::Beta;
    on_failure(certainty < transition_threshold)  => state::Beta;
}

state Beta {
    action: "Apply result.";
}

finalize Audit {
    log_telemetry: [runtime.telemetry.token_usage];
    sign_payload: axiom.crypto.hmac.sha256(env.KEY);
    destination: "memory";
}
"""


@pytest.fixture
def parser() -> BlueprintParser:
    return BlueprintParser()


@pytest.fixture
def minimal_bp(parser: BlueprintParser) -> AxiomBlueprint:
    return parser.parse(_MINIMAL_AXIOM)


# ── CANNOT_MUTATE sentinels ───────────────────────────────────────────────────

def test_blueprint_version_cannot_mutate():
    import axiom_blueprint
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        axiom_blueprint.BLUEPRINT_VERSION = "9.9"


def test_max_state_visits_cannot_mutate():
    import axiom_blueprint
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        axiom_blueprint.MAX_STATE_VISITS = 0


def test_blueprint_version_is_1_4():
    assert BLUEPRINT_VERSION == "1.4"


def test_max_state_visits_positive():
    assert MAX_STATE_VISITS > 0


# ── Parser: module + imports ──────────────────────────────────────────────────

def test_parses_module_name(minimal_bp: AxiomBlueprint):
    assert minimal_bp.module == "Test.Minimal"


def test_refactor_example_parses(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    assert bp.module == "Orivael.RefactorEngine"
    assert "axiom.crypto.hmac" in bp.imports
    assert "axiom.validators.compiler" in bp.imports


# ── Parser: RuntimeConfig ─────────────────────────────────────────────────────

def test_parses_runtime_token_budget(minimal_bp: AxiomBlueprint):
    rc = minimal_bp.runtime_config
    assert rc.max_context_tokens == 4096
    assert rc.reserve_response_tokens == 512


def test_parses_execution_mode(minimal_bp: AxiomBlueprint):
    assert minimal_bp.runtime_config.execution_mode == "local_first"


def test_parses_temperature_profile(minimal_bp: AxiomBlueprint):
    rc = minimal_bp.runtime_config
    assert rc.temperature_min == 0.1
    assert rc.temperature_max == 0.5


def test_refactor_temperature_profile(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    assert bp.runtime_config.temperature_min == 0.1
    assert bp.runtime_config.temperature_max == 0.7


# ── Parser: InvariantSpec ─────────────────────────────────────────────────────

def test_parses_network_invariant(minimal_bp: AxiomBlueprint):
    inv = minimal_bp.invariants[0]
    assert inv.name == "NetGate"
    assert inv.allow_network is False


def test_refactor_security_boundaries(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    sec = next(inv for inv in bp.invariants if inv.name == "SecurityBoundaries")
    assert "./src/refactored/" in sec.allow_fs_write
    assert len(sec.privacy_filters) == 2
    assert any("sk-" in f for f in sec.privacy_filters)


def test_refactor_compilation_guard(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    cg = next(inv for inv in bp.invariants if inv.name == "CompilationGuard")
    assert cg.validate_via == "axiom.validators.compiler.rustc"
    assert ("edition", "2021") in cg.validate_kwargs


# ── Parser: StateSpec ─────────────────────────────────────────────────────────

def test_parses_two_states(minimal_bp: AxiomBlueprint):
    assert len(minimal_bp.states) == 2
    assert minimal_bp.states[0].name == "Alpha"
    assert minimal_bp.states[1].name == "Beta"


def test_parses_probabilistic_weights(minimal_bp: AxiomBlueprint):
    weights = minimal_bp.states[0].probabilistic_weights
    assert len(weights) == 2
    labels = {w.label for w in weights}
    assert labels == {"catA", "catB"}


def test_weights_normalised(parser: BlueprintParser):
    text = """
    module M; runtime R {}
    state S {
        evaluate probabilistic_weights {
            "x" => weight(2),
            "y" => weight(2)
        }
    }
    """
    bp = parser.parse(text)
    weights = bp.states[0].probabilistic_weights
    total = sum(w.weight for w in weights)
    assert abs(total - 1.0) < 1e-6


def test_parses_transition_threshold(minimal_bp: AxiomBlueprint):
    assert minimal_bp.states[0].transition_threshold == 0.60


def test_parses_transitions(minimal_bp: AxiomBlueprint):
    transitions = minimal_bp.states[0].transitions
    assert len(transitions) == 2
    conds = {t.condition for t in transitions}
    assert "on_success" in conds
    assert "on_failure" in conds
    targets = {t.target_state for t in transitions}
    assert "Beta" in targets


def test_refactor_retry_spec(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    exec_state = next(s for s in bp.states if s.name == "ExecuteRefactor")
    retry = exec_state.retry_spec
    assert retry is not None
    assert retry.max_attempts == 3
    assert retry.on_violation == "CompilationGuard"
    assert retry.temperature_delta < 0  # temperature decreases on violation
    adjs = {a.category: a.delta for a in retry.weight_adjustments}
    assert adjs["type_safety"] > 0
    assert adjs["memory_leak"] < 0


# ── Parser: FinalizeSpec ──────────────────────────────────────────────────────

def test_parses_finalize(minimal_bp: AxiomBlueprint):
    fin = minimal_bp.finalize
    assert fin is not None
    assert fin.name == "Audit"
    assert "runtime.telemetry.token_usage" in fin.log_telemetry
    assert "hmac" in fin.sign_payload


# ── Certainty computation ─────────────────────────────────────────────────────

def test_certainty_uniform_weights_is_zero():
    sm = BlueprintStateMachine(AxiomBlueprint(
        module="T", imports=(), runtime_config=RuntimeConfig(),
        invariants=(), states=(), finalize=None,
    ))
    uniform = (
        WeightedCategory("a", 0.5),
        WeightedCategory("b", 0.5),
    )
    assert sm._compute_certainty(uniform) < 0.01


def test_certainty_single_dominant_is_high():
    sm = BlueprintStateMachine(AxiomBlueprint(
        module="T", imports=(), runtime_config=RuntimeConfig(),
        invariants=(), states=(), finalize=None,
    ))
    skewed = (
        WeightedCategory("a", 0.95),
        WeightedCategory("b", 0.05),
    )
    assert sm._compute_certainty(skewed) > 0.7


def test_certainty_empty_weights_is_one():
    sm = BlueprintStateMachine(AxiomBlueprint(
        module="T", imports=(), runtime_config=RuntimeConfig(),
        invariants=(), states=(), finalize=None,
    ))
    assert sm._compute_certainty(()) == 1.0


# ── InvariantViolation ────────────────────────────────────────────────────────

def test_network_invariant_fires_on_network_access(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    violations = sm.enforce_all_invariants({"network_access": True})
    assert "NetGate" in violations


def test_network_invariant_passes_when_no_network(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    violations = sm.enforce_all_invariants({"network_access": False})
    assert not violations


def test_privacy_filter_fires_on_api_key(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    sm = BlueprintStateMachine(bp)
    fake_key = "sk-" + "A" * 48
    violations = sm.enforce_all_invariants({"output": f"token = {fake_key}"})
    assert "SecurityBoundaries" in violations


def test_fs_write_invariant_rejects_bad_path(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    sm = BlueprintStateMachine(bp)
    violations = sm.enforce_all_invariants({"fs_writes": ["/etc/passwd"]})
    assert "SecurityBoundaries" in violations


def test_fs_write_invariant_allows_good_path(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    sm = BlueprintStateMachine(bp)
    violations = sm.enforce_all_invariants({"fs_writes": ["./src/refactored/main.rs"]})
    # SecurityBoundaries should not fire for allowed path
    assert "SecurityBoundaries" not in violations


# ── State machine execution ───────────────────────────────────────────────────

def test_run_returns_telemetry(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    tel = sm.run()
    assert isinstance(tel, ExecutionTelemetry)


def test_run_visits_at_least_one_state(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    tel = sm.run()
    assert len(tel.states_visited) >= 1


def test_run_records_certainty_scores(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    tel = sm.run()
    assert "Alpha" in tel.certainty_scores


def test_run_token_usage_positive(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    tel = sm.run()
    assert tel.token_usage > 0


def test_run_telemetry_is_signed(minimal_bp: AxiomBlueprint):
    sm = BlueprintStateMachine(minimal_bp)
    tel = sm.run()
    assert isinstance(tel.hmac_signature, str)
    assert len(tel.hmac_signature) > 0


def test_run_refactor_agent(parser: BlueprintParser):
    bp = parser.parse_file(REFACTOR_AXIOM)
    sm = BlueprintStateMachine(bp)
    tel = sm.run(initial_state="OptimizationAnalysis")
    assert "OptimizationAnalysis" in tel.states_visited
    assert tel.hmac_signature


def test_max_state_visits_guard(parser: BlueprintParser):
    """A blueprint that loops forever must terminate within MAX_STATE_VISITS."""
    text = """
    module Loop; runtime R {}
    state A {
        evaluate probabilistic_weights { "x" => weight(0.5), "y" => weight(0.5) }
        transition_threshold: 0.99;
        on_failure(certainty < transition_threshold) => state::A;
    }
    """
    bp = parser.parse(text)
    sm = BlueprintStateMachine(bp)
    tel = sm.run()
    assert len(tel.states_visited) <= MAX_STATE_VISITS + 5  # small buffer for retry records


def test_load_blueprint_convenience():
    bp = load_blueprint(REFACTOR_AXIOM)
    assert bp.module == "Orivael.RefactorEngine"


def test_run_blueprint_convenience():
    tel = run_blueprint(REFACTOR_AXIOM)
    assert isinstance(tel, ExecutionTelemetry)
    assert tel.hmac_signature
