# -*- coding: utf-8 -*-
"""
AXIOM Language Strict Mode — validator integration tests
========================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

Wires axiom_files/core/strict_mode.axiom into actual enforcement.
Strict mode is opt-in; lenient is the default to preserve backward
compatibility with the 282 prior tests.

BUG-003: UTF-8 output encoding
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_strict_mode"

from axiom_files.validator import validate_parsed, validate_file


def _base_parsed(**overrides):
    """Minimal-valid parsed dict matching tests/test_validator_v1_8.py."""
    base = {
        "agent": "TestAgent",
        "version": "1.0",
        "purpose": "test strict mode",
        "goal": "test strict mode",
        "trust_level": "1",
        "cannot_mutate": ["agent", "goal"],
        "constraints": [],
        "rules": [],
        "process": [],
        "check": [],
        "failure": [],
        "success": {},
        "security": [],
        "when": [],
        "delegates": [],
        "history": {},
        "concepts": [],
        "sandbox_agent": "",
        "receives": {},
        "emits": {},
        "mutates": [],
        "output": [],
        "tools": [],
    }
    base.update(overrides)
    return base


def _strict_errors(result):
    return [i for i in result["issues"]
            if i["level"] == "error" and i["phase"] == "strict"]


# ===========================================================================
# SECTION 1 — BLOCKED (strict-only rejections)
# ===========================================================================

class TestStrictBlocked:

    def test_blocked_arrow_function_in_rules(self):
        parsed = _base_parsed(rules=["Apply (x) => transform(x) for each input"])
        lenient = validate_parsed(parsed)
        strict = validate_parsed(parsed, strict=True)
        # Lenient: no error from existing patterns
        assert lenient["status"] != "invalid"
        # Strict: arrow function caught
        assert strict["status"] == "invalid"
        errs = _strict_errors(strict)
        assert any("arrow function" in e["message"] for e in errs)

    def test_blocked_var_declaration_anywhere(self):
        parsed = _base_parsed(process=["let counter = 0 for tracking"])
        lenient = validate_parsed(parsed)
        strict = validate_parsed(parsed, strict=True)
        assert lenient["status"] != "invalid"
        assert strict["status"] == "invalid"
        errs = _strict_errors(strict)
        assert any("variable declaration" in e["message"] for e in errs)

    def test_blocked_oo_modifier(self):
        # `private static String` is a Java-style declaration sequence; lenient
        # purity doesn't catch it but strict does.
        parsed = _base_parsed(rules=["Use private static String token for the session"])
        lenient = validate_parsed(parsed)
        strict = validate_parsed(parsed, strict=True)
        assert lenient["status"] != "invalid"
        assert strict["status"] == "invalid"
        errs = _strict_errors(strict)
        assert any("object-oriented declaration" in e["message"] for e in errs)


# ===========================================================================
# SECTION 2 — PASSED (false-positive avoidance + real specs)
# ===========================================================================

class TestStrictPassed:

    def test_passed_english_if_in_failure(self):
        """strict_mode.axiom:31 — English descriptions of concepts must be allowed."""
        parsed = _base_parsed(failure=[
            "If context is missing, request more information",
            "If confidence is below threshold, flag for review",
        ])
        for mode in (None, True):
            r = validate_parsed(parsed, strict=mode)
            errs = _strict_errors(r)
            assert errs == [], (
                f"strict={mode} flagged English prose as code: {errs}"
            )

    def test_passed_baseline_worker_axiom_in_strict(self):
        """Worker.axiom is the canonical core agent; strict mode must not regress it."""
        r = validate_file("worker", strict=True)
        assert r["status"] != "invalid", (
            f"worker.axiom regressed under strict mode: "
            f"{[i for i in r['issues'] if i['level']=='error']}"
        )

    def test_passed_strict_mode_axiom_validates_itself(self):
        """The strict_mode constitutional spec must pass its own validator."""
        r = validate_file("strict_mode", strict=True)
        assert r["status"] != "invalid", (
            f"strict_mode.axiom failed self-validation: "
            f"{[i for i in r['issues'] if i['level']=='error']}"
        )

    def test_passed_env_var_toggle(self, monkeypatch):
        """AXIOM_STRICT_MODE=1 enables strict mode without an explicit kwarg."""
        parsed = _base_parsed(rules=["Apply (x) => transform(x)"])
        # Without env var or kwarg → lenient
        assert validate_parsed(parsed)["strict_mode"] is False
        monkeypatch.setenv("AXIOM_STRICT_MODE", "1")
        r = validate_parsed(parsed)
        assert r["strict_mode"] is True
        assert r["status"] == "invalid"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestStrictInvariants:

    def test_invariant_lenient_is_default(self):
        """No kwarg, no env, no header → strict_mode False (backward compat)."""
        parsed = _base_parsed(rules=["Apply (x) => transform(x)"])
        r = validate_parsed(parsed)
        assert r["strict_mode"] is False
        # Lenient must NOT add strict-phase errors
        assert _strict_errors(r) == []

    def test_invariant_result_carries_mode(self):
        """The resolved mode is always reported back to the caller."""
        parsed = _base_parsed()
        assert validate_parsed(parsed, strict=False)["strict_mode"] is False
        assert validate_parsed(parsed, strict=True)["strict_mode"] is True
        # Parsed-dict opt-in
        parsed_opt = _base_parsed()
        parsed_opt["_strict_mode"] = True
        assert validate_parsed(parsed_opt)["strict_mode"] is True
