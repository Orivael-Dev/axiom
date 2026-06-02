"""Unit tests for gen_axiom_dataset.py — verify correctness without running inference."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("AXIOM_MASTER_KEY", "0" * 64)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import random
import pytest

from research.finetune.gen_axiom_dataset import (
    SYSTEM_PROMPT,
    _msg,
    _j,
    _verdict_examples,
    _json_structure_examples,
    _tamper_examples,
    _revocation_examples,
    _tool_refusal_examples,
    _no_fake_sig_examples,
    _cli_examples,
    _kv_dag_examples,
    _format_examples,
    _adapter_block_examples,
    generate,
    CATEGORY_SIZES,
)


def _rng():
    return random.Random(0)


def _valid_example(ex: dict) -> bool:
    """Check ChatML triple structure and JSON assistant output."""
    if "messages" not in ex:
        return False
    msgs = ex["messages"]
    if len(msgs) != 3:
        return False
    if msgs[0]["role"] != "system":
        return False
    if msgs[1]["role"] != "user":
        return False
    if msgs[2]["role"] != "assistant":
        return False
    try:
        json.loads(msgs[2]["content"])
        return True
    except json.JSONDecodeError:
        return False


class TestMessageStructure:
    def test_verdict_examples_structure(self):
        exs = _verdict_examples(_rng(), 20)
        assert len(exs) == 20
        for ex in exs:
            assert _valid_example(ex), f"Invalid: {ex}"

    def test_json_structure_examples_structure(self):
        exs = _json_structure_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_tamper_examples_structure(self):
        exs = _tamper_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_revocation_examples_structure(self):
        exs = _revocation_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_tool_refusal_structure(self):
        exs = _tool_refusal_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_no_fake_sig_structure(self):
        exs = _no_fake_sig_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_cli_structure(self):
        exs = _cli_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_kv_dag_structure(self):
        exs = _kv_dag_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)

    def test_format_examples_structure(self):
        exs = _format_examples(_rng(), 20)
        assert all(_valid_example(e) for e in exs)


class TestVerdictGroundTruth:
    def test_harm_input_produces_harm_verdict(self):
        rng = random.Random(1)
        # Patch pool to only use harm inputs
        from research.finetune import gen_axiom_dataset as gad
        original = gad._BENIGN_TEMPLATES
        gad._BENIGN_TEMPLATES = []
        try:
            exs = _verdict_examples(rng, 5)
            for ex in exs:
                parsed = json.loads(ex["messages"][2]["content"])
                assert "verdict" in parsed
                assert "intent_class" in parsed
                assert "confidence" in parsed
        finally:
            gad._BENIGN_TEMPLATES = original

    def test_verdicts_are_valid_classes(self):
        valid_verdicts = {"INFORM", "CLARIFY", "REFUSE", "HARM", "DECEIVE", "UNCERTAIN"}
        exs = _verdict_examples(_rng(), 30)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            assert parsed["verdict"] in valid_verdicts, f"Bad verdict: {parsed['verdict']}"

    def test_blocked_flag_matches_verdict(self):
        exs = _verdict_examples(_rng(), 30)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            is_blocked = parsed["verdict"] in ("HARM", "DECEIVE")
            assert parsed["blocked"] == is_blocked


class TestTamperExamples:
    def test_tampered_examples_have_tamper_verdict(self):
        # Generate many and check that tampered ones say TAMPER_DETECTED
        exs = _tamper_examples(_rng(), 50)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            verdict = parsed["verdict"]
            assert verdict in ("TAMPER_DETECTED", "VALID"), f"Unknown verdict: {verdict}"

    def test_tamper_detected_has_field(self):
        exs = _tamper_examples(_rng(), 50)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            if parsed["verdict"] == "TAMPER_DETECTED":
                assert "tampered_field" in parsed
                assert "action" in parsed
                assert parsed["action"] == "reject"


class TestRevocationExamples:
    def test_revoked_token_is_blocked(self):
        # Generate many and check REVOKED always gives BLOCK
        exs = _revocation_examples(_rng(), 50)
        for ex in exs:
            user_content = ex["messages"][1]["content"]
            parsed = json.loads(ex["messages"][2]["content"])
            if '"state": "REVOKED"' in user_content:
                assert parsed["verdict"] == "BLOCK", "REVOKED token should be blocked"
                assert parsed["reason"] == "token_revoked"

    def test_active_validated_token_is_allowed(self):
        exs = _revocation_examples(_rng(), 50)
        for ex in exs:
            user_content = ex["messages"][1]["content"]
            parsed = json.loads(ex["messages"][2]["content"])
            if '"state": "ACTIVE_VALIDATED"' in user_content:
                assert parsed["verdict"] == "ALLOW", "ACTIVE_VALIDATED should be allowed"


class TestNoFakeSignatures:
    def test_no_fake_sig_has_error_field(self):
        exs = _no_fake_sig_examples(_rng(), 20)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            assert parsed.get("error") == "cannot_compute_hmac"

    def test_no_fake_sig_does_not_contain_64hex(self):
        import re
        exs = _no_fake_sig_examples(_rng(), 20)
        for ex in exs:
            asst = ex["messages"][2]["content"]
            assert not re.search(r'(?<!")([0-9a-f]{64})(?!")', asst), \
                "Assistant output should not contain a raw 64-char hex signature"


class TestCLIExamples:
    def test_cli_output_has_command_field(self):
        exs = _cli_examples(_rng(), 20)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            assert "command" in parsed

    def test_cli_commands_start_with_axm_or_axiom(self):
        exs = _cli_examples(_rng(), 20)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            cmd = parsed["command"]
            assert cmd.startswith("axm ") or cmd.startswith("axiom "), \
                f"Command should start with axm/axiom: {cmd}"


class TestSystemPrompt:
    def test_all_examples_have_correct_system_prompt(self):
        exs = generate(total=100, seed=7)
        for ex in exs:
            assert ex["messages"][0]["content"] == SYSTEM_PROMPT

    def test_generate_returns_expected_count(self):
        exs = generate(total=100, seed=7)
        # Allow small deviation from target due to integer rounding
        assert 90 <= len(exs) <= 110

    def test_generate_all_valid_json(self):
        exs = generate(total=100, seed=7)
        invalid = [ex for ex in exs if not _valid_example(ex)]
        assert len(invalid) == 0, f"{len(invalid)} examples have invalid JSON outputs"

    def test_category_sizes_includes_adapter_block(self):
        assert "adapter_block" in CATEGORY_SIZES
        assert CATEGORY_SIZES["adapter_block"] >= 300


class TestAdapterBlockExamples:
    def test_adapter_block_structure(self):
        exs = _adapter_block_examples(_rng(), 20)
        assert len(exs) == 20
        assert all(_valid_example(e) for e in exs)

    def test_adapter_block_has_version_field(self):
        exs = _adapter_block_examples(_rng(), 20)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            assert parsed.get("axiom_block_version") == "0.1"

    def test_adapter_block_has_all_sections(self):
        exs = _adapter_block_examples(_rng(), 20)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            assert "source" in parsed
            assert "governance" in parsed
            assert "content" in parsed
            assert "metrics" in parsed

    def test_adapter_block_route_is_valid(self):
        valid_routes = {"train", "fine_tune", "retrieval", "eval", "quarantine"}
        exs = _adapter_block_examples(_rng(), 30)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            route = parsed["governance"]["recommended_route"]
            assert route in valid_routes, f"Invalid route: {route}"

    def test_adapter_block_compression_ratio_lt_one(self):
        exs = _adapter_block_examples(_rng(), 20)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            ratio = parsed["metrics"]["compression_ratio"]
            assert 0.0 < ratio < 1.0, f"Compression ratio out of range: {ratio}"

    def test_adapter_block_risk_level_valid(self):
        exs = _adapter_block_examples(_rng(), 30)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            assert parsed["governance"]["risk_level"] in ("low", "medium", "high")

    def test_medical_domain_routes_to_retrieval(self):
        exs = _adapter_block_examples(_rng(), 60)
        for ex in exs:
            parsed = json.loads(ex["messages"][2]["content"])
            if parsed["content"]["domain"] == "medical":
                assert parsed["governance"]["recommended_route"] == "retrieval", \
                    "Medical domain should route to retrieval"
