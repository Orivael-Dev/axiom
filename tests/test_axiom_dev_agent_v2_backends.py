# -*- coding: utf-8 -*-
"""
AxiomDevAgentV2 LLM-backend + propose() tests
==============================================
2 BLOCKED + 3 PASSED + 2 INVARIANTS

Pins the constitutional discipline of the propose loop: the LLM is
just another diff source. Same four gates apply regardless of who
wrote the diff.

BUG-003: UTF-8 output encoding
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_backends"

from axiom_dev_agent_v2 import AxiomDevAgentV2
from axiom_dev_agent_v2_backends import (
    LLMBackend, LLMResponse, SimulatorBackend, AnthropicBackend,
    OpenAIBackend, select_backend, SYSTEM_PROMPT,
    _parse_diff_and_citations,
)


# A scriptable backend for testing retry behavior. Returns the
# diffs in `diffs` sequentially across calls; records every retry_hint
# the agent fed back.
class ScriptedBackend(LLMBackend):
    name = "scripted"

    def __init__(self, diffs: List[str],
                 cited_patterns: tuple = ("traj-axiom-agent-bug_fix",)):
        self._diffs = list(diffs)
        self._cited = cited_patterns
        self.call_count = 0
        self.received_hints: List[Optional[str]] = []

    def available(self) -> bool:
        return True

    def generate_diff(self, *, description, task_class, context="",
                      retry_hint=None) -> LLMResponse:
        self.received_hints.append(retry_hint)
        diff = self._diffs[min(self.call_count, len(self._diffs) - 1)]
        self.call_count += 1
        return LLMResponse(backend_name=self.name, diff=diff,
                            cited_patterns=self._cited,
                            model="scripted-v1")


# ===========================================================================
# SECTION 1 — BLOCKED (the gates DO apply to LLM output)
# ===========================================================================

class TestPropseBlocked:

    def test_blocked_llm_eval_diff_is_refused(self):
        """An LLM that outputs eval() must be refused at Layer 0 — no
        privileged path for the agent's own output. Even on retry,
        if the LLM keeps outputting eval(), the final verdict stays
        REFLEX_REFUSED."""
        agent = AxiomDevAgentV2(persistence_path=None)
        bad_diff = "+ result = eval(payload)\n"
        backend = ScriptedBackend(diffs=[bad_diff, bad_diff, bad_diff])
        outcome = agent.propose(
            description="add config eval", task_class="FEATURE",
            backend=backend, max_retries=2,
        )
        assert outcome.final_verdict == "REFLEX_REFUSED"
        # Three attempts (initial + 2 retries) made.
        assert backend.call_count == 3
        # Second + third calls got the refusal reason as a hint.
        assert backend.received_hints[0] is None
        assert backend.received_hints[1] is not None
        assert "eval" in backend.received_hints[1].lower()

    def test_blocked_master_key_in_llm_output_refused(self):
        """A 64-hex string that the LLM might emit (memorisation
        leak) must also be refused — Layer 0 doesn't trust the
        source, it inspects the content."""
        agent = AxiomDevAgentV2(persistence_path=None)
        hex_blob = "0123456789abcdef" * 4
        bad_diff = f"+ key = '{hex_blob}'\n"
        backend = ScriptedBackend(diffs=[bad_diff])
        outcome = agent.propose(
            description="paste config", task_class="FEATURE",
            backend=backend, max_retries=0,
        )
        assert outcome.final_verdict == "REFLEX_REFUSED"
        assert any("master key" in r.lower()
                   for r in outcome.reflex.reasons)


# ===========================================================================
# SECTION 2 — PASSED (the loop converges on good output)
# ===========================================================================

class TestPropsePassed:

    def test_passed_simulator_backend_always_available(self):
        """The simulator is the floor — always usable, no API keys,
        no network. Without it tests + CI couldn't run."""
        sb = SimulatorBackend()
        assert sb.available() is True
        resp = sb.generate_diff(description="fix bug",
                                 task_class="BUG_FIX")
        assert "regex" in resp.diff.lower() or "foo" in resp.diff
        assert resp.backend_name == "simulator"

    def test_passed_retry_converges_after_initial_refusal(self):
        """LLM emits a refused diff first, then a clean one. The
        agent must NOT silently accept the refusal — it must
        retry, get the clean diff, and emit a non-REFLEX outcome."""
        agent = AxiomDevAgentV2(persistence_path=None)
        agent.reviewer.set_all(1.0)   # pre-trust so reviewer doesn't VETO
        bad = "+ x = eval(s)\n"
        good = (
            '--- a/x.py\n+++ b/x.py\n@@ -1,0 +1,1 @@\n'
            '+from ast import literal_eval as _safe_eval\n'
        )
        backend = ScriptedBackend(diffs=[bad, good])
        outcome = agent.propose(
            description="parse value safely", task_class="FEATURE",
            backend=backend, max_retries=2,
        )
        # First call was REFLEX_REFUSED; second succeeded → final is
        # not the refusal.
        assert outcome.final_verdict != "REFLEX_REFUSED"
        assert backend.call_count == 2
        # Retry hint mentioned the eval refusal.
        assert backend.received_hints[1] is not None
        assert "eval" in backend.received_hints[1].lower()

    def test_passed_select_backend_prefers_simulator_without_keys(self,
                                                                    monkeypatch):
        """With no API keys present, the factory must fall through to
        the simulator. Tests + CI rely on this."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        b = select_backend(prefer="auto")
        assert b.name == "simulator"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestPropseInvariants:

    def test_invariant_system_prompt_pins_constitutional_rules(self):
        """The SYSTEM_PROMPT is the contract between the agent and
        any external LLM. The constitutional rules MUST appear in it
        verbatim — otherwise the LLM can drift on what it considers
        a valid diff. Pinning the strings catches a silent edit."""
        for rule in ("eval()", "exec()", "subprocess", "500 lines",
                     "traj-axiom-agent"):
            assert rule in SYSTEM_PROMPT, (
                f"SYSTEM_PROMPT no longer contains {rule!r}"
            )

    def test_invariant_propose_signature_separation(self):
        """A diff the LLM produced and the agent merged carries an
        examiner certificate signed under the EXAMINER's key, not
        the agent's overall identity. The LLM cannot forge a passing
        certificate because the backend never touches the signing
        infrastructure."""
        agent = AxiomDevAgentV2(persistence_path=None)
        agent.reviewer.set_all(1.0)
        backend = SimulatorBackend()
        outcome = agent.propose(description="add docstring",
                                  task_class="DOCUMENTATION",
                                  backend=backend, max_retries=0)
        assert outcome.final_verdict == "MERGED"
        assert outcome.ci is not None
        # Verify under the examiner key — must succeed.
        assert agent.examiner.verify_certificate(outcome.ci) is True
        # And the certificate signature is a real 64-char HMAC.
        assert len(outcome.ci.signature) == 64
