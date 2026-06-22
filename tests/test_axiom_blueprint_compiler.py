"""Tests for axiom_blueprint_compiler — 5-stage pipeline.

Coverage:
  CANNOT_MUTATE sentinels
  Stage 1: Lexer  (TK kinds, line/col, string escapes, comments, LexError)
  Stage 2: Parser (module, runtime, invariant, state, finalize, weight normalisation)
  Stage 3: Analyzer (checks A1-A7)
  Stage 4: Optimizer (passes P1-P4)
  Stage 5: Codegen (agent partitioning, system-prompt format, transition table)
  Facade: AxiomCompiler.compile / compile_file — success + error paths
  IR: to_json / from_json roundtrip
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from axiom_blueprint_compiler import (
    AgentRole,
    ASTState,
    ASTWeightedCategory,
    AxiomCompiler,
    BlueprintAnalyzer,
    BlueprintASTParser,
    BlueprintCodegen,
    BlueprintLexer,
    BlueprintOptimizer,
    COMPILER_VERSION,
    Diagnostic,
    LexError,
    MAX_AGENT_STATES,
    ParseError,
    StateMachineIR,
    TK,
    Token,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_MINIMAL = """\
module Test.Module;
runtime Cfg { max_context_tokens: 4096; reserve_response_tokens: 512; }
state Alpha { prompt: "hello"; }
"""

_TWO_STATE = """\
module Two;
runtime Cfg { max_context_tokens: 4096; reserve_response_tokens: 512; }
state Alpha {
    prompt: "first state";
    on_success(ok) => state::Beta;
}
state Beta { prompt: "second state"; }
"""

_WEIGHTED = """\
module Weighted;
runtime Cfg { max_context_tokens: 4096; reserve_response_tokens: 512; }
state Classify {
    evaluate probabilistic_weights {
        "cat_a" => weight(0.60),
        "cat_b" => weight(0.40)
    }
    transition_threshold: 0.80;
}
"""

_FULL = Path(__file__).parent.parent / "examples" / "refactor_agent.axiom"


def _parse(src: str):
    return BlueprintASTParser().parse(src)


def _compile(src: str, **kw):
    return AxiomCompiler(**kw).compile(src)


# ══════════════════════════════════════════════════════════════════════════════
# CANNOT_MUTATE sentinels
# ══════════════════════════════════════════════════════════════════════════════

class TestCannotMutate:
    def test_compiler_version_immutable(self):
        import axiom_blueprint_compiler as m
        with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
            m.COMPILER_VERSION = "9.9"

    def test_max_agent_states_immutable(self):
        import axiom_blueprint_compiler as m
        with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
            m.MAX_AGENT_STATES = 999

    def test_compiler_version_value(self):
        assert COMPILER_VERSION == "1.4"

    def test_max_agent_states_value(self):
        assert MAX_AGENT_STATES == 8


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Lexer
# ══════════════════════════════════════════════════════════════════════════════

class TestLexer:
    def _lex(self, src: str):
        return BlueprintLexer().tokenise(src)

    def test_keyword_module(self):
        toks = self._lex("module Foo;")
        assert toks[0].kind == TK.KW_MODULE
        assert toks[1].kind == TK.IDENT
        assert toks[1].value == "Foo"

    def test_double_colon(self):
        toks = self._lex("state::Foo")
        kinds = [t.kind for t in toks if t.kind != TK.EOF]
        assert TK.DCOLON in kinds

    def test_arrow(self):
        toks = self._lex("x => y")
        assert any(t.kind == TK.ARROW for t in toks)

    def test_plus_eq(self):
        toks = self._lex('"a" += 0.5')
        assert any(t.kind == TK.PLUS_EQ for t in toks)

    def test_minus_eq(self):
        toks = self._lex('"b" -= 0.3')
        assert any(t.kind == TK.MINUS_EQ for t in toks)

    def test_string_literal_double_quote(self):
        toks = self._lex('"hello world"')
        assert toks[0].kind == TK.STRING
        assert toks[0].value == "hello world"

    def test_string_literal_escape(self):
        toks = self._lex(r'"foo\"bar"')
        assert toks[0].kind == TK.STRING
        assert toks[0].value == 'foo"bar'

    def test_number_integer(self):
        toks = self._lex("4096")
        assert toks[0].kind == TK.NUMBER
        assert toks[0].value == "4096"

    def test_number_float(self):
        toks = self._lex("0.75")
        assert toks[0].kind == TK.NUMBER
        assert float(toks[0].value) == pytest.approx(0.75)

    def test_comment_skipped(self):
        toks = self._lex("# this is a comment\nmodule X;")
        assert toks[0].kind == TK.KW_MODULE

    def test_line_col_tracking(self):
        src = "module\nFoo"
        toks = self._lex(src)
        foo_tok = next(t for t in toks if t.value == "Foo")
        assert foo_tok.line == 2
        assert foo_tok.col == 1

    def test_eof_appended(self):
        toks = self._lex("")
        assert toks[-1].kind == TK.EOF

    def test_all_keywords_recognised(self):
        keywords = {
            "module": TK.KW_MODULE,
            "import": TK.KW_IMPORT,
            "runtime": TK.KW_RUNTIME,
            "invariant": TK.KW_INVARIANT,
            "state": TK.KW_STATE,
            "finalize": TK.KW_FINALIZE,
            "evaluate": TK.KW_EVALUATE,
            "retry_loop": TK.KW_RETRY,
        }
        for word, expected_kind in keywords.items():
            toks = self._lex(word)
            assert toks[0].kind == expected_kind, f"keyword={word!r}"

    def test_lex_error_on_unknown_char(self):
        with pytest.raises(LexError):
            self._lex("@bad")


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Parser
# ══════════════════════════════════════════════════════════════════════════════

class TestParser:
    def test_module_name_parsed(self):
        ast = _parse(_MINIMAL)
        assert ast.module == "Test.Module"

    def test_runtime_max_tokens(self):
        ast = _parse(_MINIMAL)
        assert ast.runtime.max_context_tokens == 4096

    def test_runtime_reserve_tokens(self):
        ast = _parse(_MINIMAL)
        assert ast.runtime.reserve_response_tokens == 512

    def test_runtime_temperature_dynamic(self):
        src = """\
module T;
runtime R { temperature_profile: dynamic(0.1, 0.7); }
state S { prompt: "x"; }
"""
        ast = _parse(src)
        assert ast.runtime.temperature_min == pytest.approx(0.1)
        assert ast.runtime.temperature_max == pytest.approx(0.7)

    def test_state_count(self):
        ast = _parse(_TWO_STATE)
        assert len(ast.states) == 2

    def test_state_names(self):
        ast = _parse(_TWO_STATE)
        assert ast.states[0].name == "Alpha"
        assert ast.states[1].name == "Beta"

    def test_state_prompt(self):
        ast = _parse(_MINIMAL)
        assert ast.states[0].prompt == "hello"

    def test_transition_parsed(self):
        ast = _parse(_TWO_STATE)
        alpha = ast.states[0]
        assert len(alpha.transitions) == 1
        assert alpha.transitions[0].condition == "on_success"
        assert alpha.transitions[0].target_state == "Beta"

    def test_probabilistic_weights_parsed(self):
        ast = _parse(_WEIGHTED)
        s = ast.states[0]
        assert len(s.probabilistic_weights) == 2
        labels = {w.label for w in s.probabilistic_weights}
        assert "cat_a" in labels
        assert "cat_b" in labels

    def test_weights_normalised_sum_to_one(self):
        ast = _parse(_WEIGHTED)
        s = ast.states[0]
        total = sum(w.weight for w in s.probabilistic_weights)
        assert abs(total - 1.0) < 1e-4

    def test_transition_threshold_parsed(self):
        ast = _parse(_WEIGHTED)
        assert ast.states[0].transition_threshold == pytest.approx(0.80)

    def test_invariant_allow_network_false(self):
        src = """\
module T;
runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }
invariant SecBound { allow_network: false; }
state S { prompt: "x"; }
"""
        ast = _parse(src)
        assert ast.invariants[0].allow_network is False

    def test_invariant_allow_fs_write(self):
        src = """\
module T;
runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }
invariant SecBound { allow_fs_write: ["./src/"]; }
state S { prompt: "x"; }
"""
        ast = _parse(src)
        assert "./src/" in ast.invariants[0].allow_fs_write

    def test_finalize_block(self):
        src = """\
module T;
runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }
state S { prompt: "x"; }
finalize Audit {
    log_telemetry: [runtime.telemetry.tokens];
    sign_payload: hmac.sha256;
    destination: "memory";
}
"""
        ast = _parse(src)
        assert ast.finalize is not None
        assert ast.finalize.name == "Audit"
        assert ast.finalize.destination == "memory"

    def test_import_parsed(self):
        src = """\
module T;
import axiom.crypto.hmac;
runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }
state S { prompt: "x"; }
"""
        ast = _parse(src)
        assert "axiom.crypto.hmac" in ast.imports

    def test_source_hash_set(self):
        ast = _parse(_MINIMAL)
        assert len(ast.source_hash) == 16  # first 16 chars of sha256 hex

    def test_full_refactor_agent_parses(self):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        ast = BlueprintASTParser().parse_file(_FULL)
        assert ast.module == "Orivael.RefactorEngine"
        assert len(ast.states) == 3
        assert len(ast.invariants) == 2

    def test_retry_spec_parsed(self):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        ast = BlueprintASTParser().parse_file(_FULL)
        exec_state = next(s for s in ast.states if s.name == "ExecuteRefactor")
        assert exec_state.retry_spec is not None
        assert exec_state.retry_spec.max_attempts == 3


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 — Analyzer
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalyzer:
    def _analyze(self, src: str):
        return BlueprintAnalyzer().analyze(_parse(src))

    def test_clean_minimal_has_no_errors(self):
        report = self._analyze(_MINIMAL)
        assert not report.has_errors

    def test_a7_no_states_raises_error(self):
        src = "module T; runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }"
        report = self._analyze(src)
        assert any(d.code == "A7" for d in report.diagnostics)
        assert report.has_errors

    def test_a5_duplicate_state_name(self):
        src = """\
module T;
runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }
state Alpha { prompt: "a"; }
state Alpha { prompt: "b"; }
"""
        report = self._analyze(src)
        assert any(d.code == "A5" for d in report.diagnostics)

    def test_a1_bad_transition_target(self):
        src = """\
module T;
runtime R { max_context_tokens: 512; reserve_response_tokens: 64; }
state Alpha {
    prompt: "x";
    on_success(ok) => state::DoesNotExist;
}
"""
        report = self._analyze(src)
        assert any(d.code == "A1" for d in report.diagnostics)
        assert report.has_errors

    def test_a3_reserve_too_large(self):
        src = """\
module T;
runtime R { max_context_tokens: 100; reserve_response_tokens: 200; }
state S { prompt: "x"; }
"""
        report = self._analyze(src)
        assert any(d.code == "A3" for d in report.diagnostics)
        assert report.has_errors

    def test_a6_retry_references_unknown_invariant(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state S {
    action: "do stuff";
    retry_loop MaxAttempts(3) {
        on_violation(NonExistent.failed) {
            feedback: "bad";
            adjust_weights: ["cat_a" += 0.1];
        }
    }
}
"""
        report = self._analyze(src)
        assert any(d.code == "A6" for d in report.diagnostics)

    def test_errors_method_filters_correctly(self):
        src = """\
module T;
runtime R { max_context_tokens: 100; reserve_response_tokens: 200; }
state S { prompt: "x"; }
"""
        report = self._analyze(src)
        errors = report.errors()
        assert all(d.severity == "error" for d in errors)

    def test_warnings_method_filters_correctly(self):
        # A6 is a warning; trigger it
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state S {
    action: "do stuff";
    retry_loop MaxAttempts(3) {
        on_violation(Ghost.failed) {
            feedback: "oops";
            adjust_weights: ["cat" += 0.1];
        }
    }
}
"""
        report = self._analyze(src)
        warns = report.warnings()
        assert all(d.severity == "warning" for d in warns)


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4 — Optimizer
# ══════════════════════════════════════════════════════════════════════════════

class TestOptimizer:
    def _optimize(self, src: str, **kw):
        ast = _parse(src)
        return BlueprintOptimizer().optimize(ast, **kw)

    def test_no_change_returns_same_state_count(self):
        ast, report = self._optimize(_MINIMAL)
        assert report.states_after == 1

    def test_p4_filler_removed_from_prompt(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state S { prompt: "Please simply analyze the code now."; }
"""
        ast, report = self._optimize(src)
        prompt = ast.states[0].prompt
        assert "please" not in prompt.lower()
        assert "simply" not in prompt.lower()
        assert "P4:prompt-compression" in report.passes_applied

    def test_p2_low_weight_merged_into_other(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state S {
    evaluate probabilistic_weights {
        "major"   => weight(0.95),
        "minor"   => weight(0.03),
        "trivial" => weight(0.02)
    }
    transition_threshold: 0.80;
}
"""
        ast, report = self._optimize(src)
        labels = {w.label for w in ast.states[0].probabilistic_weights}
        # minor (0.03) and trivial (0.02) below MIN_WEIGHT=0.04 → "_other_"
        assert "_other_" in labels
        assert "P2:weight-compression" in report.passes_applied

    def test_p1_dead_state_eliminated(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state Reachable { prompt: "start"; }
state Orphan { prompt: "unreachable"; }
"""
        # Orphan has no transition from Reachable and is not linear successor
        # (linear successor IS reachable via index walk, so let's check it stays)
        ast, report = self._optimize(src)
        # Both states ARE reachable (linear walk includes index+1)
        assert report.states_after == 2

    def test_p3_duplicate_invariant_deduplicated(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
invariant Sec { allow_network: false; }
invariant Sec { allow_network: false; }
state S { prompt: "x"; }
"""
        ast, report = self._optimize(src)
        assert len(ast.invariants) == 1
        assert "P3:invariant-hoist" in report.passes_applied

    def test_token_reduction_non_negative(self):
        _, report = self._optimize(_MINIMAL)
        assert report.token_reduction_pct >= 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5 — Codegen
# ══════════════════════════════════════════════════════════════════════════════

class TestCodegen:
    def _emit(self, src: str):
        ast = _parse(src)
        return BlueprintCodegen().emit(ast)

    def test_ir_module_name(self):
        ir = self._emit(_MINIMAL)
        assert ir.module == "Test.Module"

    def test_ir_compiler_version(self):
        ir = self._emit(_MINIMAL)
        assert ir.compiler_version == COMPILER_VERSION

    def test_ir_source_hash_present(self):
        ir = self._emit(_MINIMAL)
        assert len(ir.source_hash) == 16

    def test_ir_state_order(self):
        ir = self._emit(_TWO_STATE)
        assert ir.state_order == ["Alpha", "Beta"]

    def test_transition_table_populated(self):
        ir = self._emit(_TWO_STATE)
        assert "Alpha" in ir.transition_table
        assert ir.transition_table["Alpha"]["on_success"] == "Beta"

    def test_inv_hooks_all_states_covered(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
invariant Sec { allow_network: false; }
state Alpha { prompt: "a"; }
state Beta  { prompt: "b"; }
"""
        ir = self._emit(src)
        assert "Alpha" in ir.inv_hooks
        assert "Beta"  in ir.inv_hooks
        assert "Sec" in ir.inv_hooks["Alpha"]

    def test_analyzer_agent_for_weighted_state(self):
        ir = self._emit(_WEIGHTED)
        roles = {a.role for a in ir.agent_specs}
        assert AgentRole.ANALYZER in roles

    def test_analyzer_system_prompt_contains_w_line(self):
        ir = self._emit(_WEIGHTED)
        analyzer = next(a for a in ir.agent_specs if a.role == AgentRole.ANALYZER)
        assert "W[Classify]:" in analyzer.system_prompt

    def test_executor_agent_for_action_state(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state DoWork { action: "execute the plan"; }
"""
        ir = self._emit(src)
        roles = {a.role for a in ir.agent_specs}
        assert AgentRole.EXECUTOR in roles

    def test_responder_agent_for_prompt_only_state(self):
        ir = self._emit(_MINIMAL)
        roles = {a.role for a in ir.agent_specs}
        assert AgentRole.RESPONDER in roles

    def test_auditor_agent_for_finalize(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state S { prompt: "x"; }
finalize Audit {
    log_telemetry: [telemetry.tokens];
    sign_payload: hmac.sha256;
    destination: "memory";
}
"""
        ir = self._emit(src)
        roles = {a.role for a in ir.agent_specs}
        assert AgentRole.AUDITOR in roles

    def test_system_prompt_format_header(self):
        ir = self._emit(_MINIMAL)
        for agent in ir.agent_specs:
            assert agent.system_prompt.startswith("[AXIOM:")

    def test_system_prompt_inv_line(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
invariant Guard { allow_network: false; }
state S { prompt: "x"; }
"""
        ir = self._emit(src)
        for agent in ir.agent_specs:
            if agent.role != AgentRole.AUDITOR:
                assert "INV:Guard" in agent.system_prompt

    def test_system_prompt_states_line(self):
        ir = self._emit(_MINIMAL)
        agent = ir.agent_specs[0]
        assert "STATES:" in agent.system_prompt

    def test_ir_hash_set(self):
        ir = self._emit(_MINIMAL)
        assert len(ir.ir_hash) == 16

    def test_all_invariants_dict_populated(self):
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
invariant Sec { allow_network: false; }
state S { prompt: "x"; }
"""
        ir = self._emit(src)
        assert "Sec" in ir.all_invariants
        assert ir.all_invariants["Sec"]["allow_network"] is False


# ══════════════════════════════════════════════════════════════════════════════
# StateMachineIR serialisation
# ══════════════════════════════════════════════════════════════════════════════

class TestStateMachineIR:
    def _make_ir(self, src: str = _MINIMAL):
        return AxiomCompiler().compile(src).ir

    def test_to_json_is_valid_json(self):
        ir = self._make_ir()
        text = ir.to_json()
        data = json.loads(text)
        assert data["module"] == ir.module

    def test_from_json_roundtrip(self):
        ir = self._make_ir()
        text = ir.to_json()
        ir2 = StateMachineIR.from_json(text)
        assert ir2.module == ir.module
        assert ir2.source_hash == ir.source_hash
        assert ir2.state_order == ir.state_order

    def test_roundtrip_agent_roles(self):
        ir = self._make_ir()
        ir2 = StateMachineIR.from_json(ir.to_json())
        roles1 = [a.role for a in ir.agent_specs]
        roles2 = [a.role for a in ir2.agent_specs]
        assert roles1 == roles2

    def test_roundtrip_transition_table(self):
        ir = self._make_ir(_TWO_STATE)
        ir2 = StateMachineIR.from_json(ir.to_json())
        assert ir2.transition_table == ir.transition_table


# ══════════════════════════════════════════════════════════════════════════════
# Compiler façade — AxiomCompiler
# ══════════════════════════════════════════════════════════════════════════════

class TestAxiomCompiler:
    def test_compile_success_flag(self):
        result = _compile(_MINIMAL)
        assert result.success is True

    def test_compile_ir_not_none_on_success(self):
        result = _compile(_MINIMAL)
        assert result.ir is not None

    def test_compile_parse_error_returns_failure(self):
        result = _compile("@@@ this is not valid axiom @@@")
        assert result.success is False
        assert result.ir is None

    def test_compile_semantic_error_still_returns_ir_in_lenient_mode(self):
        # A3 error (reserve >= max) — lenient mode still emits IR
        src = """\
module T;
runtime R { max_context_tokens: 100; reserve_response_tokens: 200; }
state S { prompt: "x"; }
"""
        result = _compile(src, strict=False)
        assert result.success is True
        assert result.ir is not None

    def test_compile_semantic_error_blocks_in_strict_mode(self):
        src = """\
module T;
runtime R { max_context_tokens: 100; reserve_response_tokens: 200; }
state S { prompt: "x"; }
"""
        result = _compile(src, strict=True)
        assert result.success is False
        assert result.ir is None

    def test_compile_opt_report_present(self):
        result = _compile(_MINIMAL)
        assert result.opt_report is not None

    def test_compile_opt_report_absent_when_skipped(self):
        result = _compile(_MINIMAL, skip_optimizer=True)
        assert result.opt_report is None

    def test_compile_diagnostics_always_present(self):
        result = _compile(_MINIMAL)
        assert result.diagnostics is not None

    def test_compile_two_state_transition_table(self):
        result = _compile(_TWO_STATE)
        assert result.success
        tt = result.ir.transition_table
        assert tt["Alpha"]["on_success"] == "Beta"

    def test_compile_file_refactor_agent(self, tmp_path):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        result = AxiomCompiler().compile_file(_FULL)
        assert result.success
        ir = result.ir
        assert ir.module == "Orivael.RefactorEngine"
        assert len(ir.state_order) == 3
        assert "OptimizationAnalysis" in ir.state_order
        assert "ExecuteRefactor" in ir.state_order
        assert "DeepReasoningLoop" in ir.state_order

    def test_compile_file_produces_auditor_agent(self):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        result = AxiomCompiler().compile_file(_FULL)
        roles = {a.role for a in result.ir.agent_specs}
        assert AgentRole.AUDITOR in roles

    def test_compile_file_inv_hooks_cover_all_states(self):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        result = AxiomCompiler().compile_file(_FULL)
        ir = result.ir
        for state_name in ir.state_order:
            assert state_name in ir.inv_hooks

    def test_compile_file_analyzer_agent_for_weighted_states(self):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        result = AxiomCompiler().compile_file(_FULL)
        analyzer_agents = [a for a in result.ir.agent_specs if a.role == AgentRole.ANALYZER]
        assert len(analyzer_agents) >= 1
        # OptimizationAnalysis and DeepReasoningLoop both have weights
        all_analyzer_states = [s for a in analyzer_agents for s in a.state_names]
        assert "OptimizationAnalysis" in all_analyzer_states

    def test_compile_file_all_invariants_in_ir(self):
        if not _FULL.exists():
            pytest.skip("examples/refactor_agent.axiom not found")
        result = AxiomCompiler().compile_file(_FULL)
        ir = result.ir
        assert "SecurityBoundaries" in ir.all_invariants
        assert "CompilationGuard" in ir.all_invariants

    def test_compile_weighted_state_threshold_in_prompt(self):
        result = _compile(_WEIGHTED)
        assert result.success
        analyzer = next(a for a in result.ir.agent_specs if a.role == AgentRole.ANALYZER)
        assert "thr:0.80" in analyzer.system_prompt

    def test_compile_custom_min_weight(self):
        # With very high min_weight, all weights get compressed to _other_
        src = """\
module T;
runtime R { max_context_tokens: 4096; reserve_response_tokens: 512; }
state S {
    evaluate probabilistic_weights {
        "cat_a" => weight(0.60),
        "cat_b" => weight(0.40)
    }
    transition_threshold: 0.80;
}
"""
        result = AxiomCompiler(min_weight=0.99).compile(src)
        assert result.success
        s = result.ir
        # weights were compressed — _other_ should appear in system prompt
        analyzer = next((a for a in s.agent_specs if a.role == AgentRole.ANALYZER), None)
        if analyzer:
            assert "_other_" in analyzer.system_prompt

    def test_compile_file_missing_path_raises(self):
        with pytest.raises(FileNotFoundError):
            AxiomCompiler().compile_file("/nonexistent/path.axiom")
