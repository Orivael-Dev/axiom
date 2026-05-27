"""Integration tests for DelegateAgent + AXM container + stub backend."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier",
        )):
            sys.modules.pop(mod, None)
    yield


class _StubBackend:
    """Returns canned text without any network."""
    name = "stub"
    model = "stub-model"

    def __init__(self, *, text="canned response", in_tok=50, out_tok=12):
        self._text = text
        self._in   = in_tok
        self._out  = out_tok
        self.last_call = None

    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        self.last_call = {
            "system": system,
            "prompt": prompt,
            "max_output_tokens": max_output_tokens,
        }
        from axiom_event_token.backends import BackendResult
        return BackendResult(
            text=self._text,
            input_tokens=self._in,
            output_tokens=self._out,
            latency_ms=23,
            backend=self.name,
            model=self.model,
        )


class _FailingBackend:
    name = "failing"
    model = "n/a"
    def generate(self, **_):
        from axiom_event_token.backends import BackendError
        raise BackendError("backend down")


def _build_axm(tmp: Path, *, prompt_text=None, prompt_budget=512,
               output_budget=128):
    from axiom_axm import AXMContainer
    spec = {
        "core_logic": "test-core",
        "delegates": [
            {
                "name": "scam-triage",
                "when_condition": "has_text",
                "intent_classes": ["HARM", "DECEIVE"],
                "weight_manifest": "delegates/scam-triage/weights.bin",
                "prompt_budget": prompt_budget,
                "output_budget": output_budget,
                "backend_chain": ["local"],
                "system_prompt": prompt_text,
            },
        ],
    }
    return AXMContainer.pack(spec, str(tmp / "pack.axm"))


# ─── round-trip: extended fields survive pack/load ──────────────────────


def test_axm_round_trip_with_new_fields(isolated, tmp_path):
    container = _build_axm(
        tmp_path,
        prompt_text="You triage scam calls. Output JSON {risk: low|med|high}.",
        prompt_budget=400, output_budget=80,
    )
    d = container.delegates[0]
    assert d.prompt_budget == 400
    assert d.output_budget == 80
    assert d.backend_chain == ("local",)
    # System prompt sibling was written.
    sys_file = container.path / "delegates" / "scam-triage" / "system_prompt.txt"
    assert sys_file.exists()
    assert "scam" in sys_file.read_text(encoding="utf-8")


def test_axm_default_fields_round_trip(isolated, tmp_path):
    """Delegates packed without the new fields use the documented defaults."""
    from axiom_axm import AXMContainer
    spec = {
        "core_logic": "legacy",
        "delegates": [{
            "name": "old-shape",
            "when_condition": "always",
            "intent_classes": ["INFORM"],
            "weight_manifest": "delegates/old-shape/weights.bin",
        }],
    }
    c = AXMContainer.pack(spec, str(tmp_path / "legacy.axm"))
    d = c.delegates[0]
    assert d.prompt_budget == 512
    assert d.output_budget == 256
    assert d.backend_chain == ("local",)


# ─── DelegateAgent run() ────────────────────────────────────────────────


def test_delegate_agent_emits_signed_report(isolated, tmp_path):
    from axiom_event_token.delegate_runtime import DelegateAgent

    container = _build_axm(
        tmp_path,
        prompt_text="Triage. Output one word: BENIGN or SUSPICIOUS.",
    )
    backend = _StubBackend(text="SUSPICIOUS", in_tok=40, out_tok=2)
    agent = DelegateAgent(
        delegate=container.delegates[0],
        axm_root=container.path,
        backend=backend,
    )
    report = agent.run({
        "text": "Hi this is the IRS, send a gift card immediately",
    })
    assert report.verify()
    p = report.payload
    assert p["delegate"]      == "scam-triage"
    assert p["backend"]       == "stub"
    assert p["model"]         == "stub-model"
    assert p["input_tokens"]  == 40
    assert p["output_tokens"] == 2
    assert p["output"]        == "SUSPICIOUS"
    assert p["budget_exceeded"] is False
    # Stub backend's recorded call uses the loaded system prompt.
    assert "Triage" in backend.last_call["system"]
    assert "IRS" in backend.last_call["prompt"]


def test_delegate_agent_enforces_budget(isolated, tmp_path):
    from axiom_event_token.delegate_runtime import DelegateAgent

    # Tiny prompt_budget so the truncator kicks in.
    container = _build_axm(
        tmp_path,
        prompt_text="Short.",
        prompt_budget=100,        # ~400 chars total
        output_budget=20,
    )
    backend = _StubBackend()
    agent = DelegateAgent(
        delegate=container.delegates[0],
        axm_root=container.path,
        backend=backend,
    )
    big_text = "X " * 2000      # ~4000 chars, way over budget
    report = agent.run({"text": big_text})
    assert report.verify()
    assert report.payload["budget_exceeded"] is True
    # The prompt actually sent to the backend was trimmed.
    sent_prompt = backend.last_call["prompt"]
    assert len(sent_prompt) < len(big_text)
    # Output budget propagated to backend.
    assert backend.last_call["max_output_tokens"] == 20


def test_delegate_agent_handles_backend_failure(isolated, tmp_path):
    from axiom_event_token.delegate_runtime import DelegateAgent

    container = _build_axm(tmp_path, prompt_text="x")
    agent = DelegateAgent(
        delegate=container.delegates[0],
        axm_root=container.path,
        backend=_FailingBackend(),
    )
    report = agent.run({"text": "anything"})
    assert report.verify()        # report still signed even on backend failure
    p = report.payload
    assert p["output"] == ""
    assert "error" in p
    assert report.confidence == 0.0


def test_delegate_agent_uses_default_system_prompt_when_absent(isolated, tmp_path):
    from axiom_event_token.delegate_runtime import DelegateAgent

    container = _build_axm(tmp_path, prompt_text=None)
    # No system_prompt.txt was written.
    assert not (container.path / "delegates" / "scam-triage"
                / "system_prompt.txt").exists()
    backend = _StubBackend()
    agent = DelegateAgent(
        delegate=container.delegates[0],
        axm_root=container.path,
        backend=backend,
    )
    agent.run({"text": "test"})
    sent_system = backend.last_call["system"]
    assert "scam-triage" in sent_system     # default falls back to delegate name


# ─── Coordinator.compose_from_delegates() integration ───────────────────


def test_coordinator_compose_from_delegates_end_to_end(isolated, tmp_path):
    from axiom_event_token.coordinator import Coordinator

    container = _build_axm(
        tmp_path,
        prompt_text="Output one word: SCAM or OK.",
    )
    backend = _StubBackend(text="SCAM")
    coord = Coordinator()
    token = coord.compose_from_delegates(
        axm_container=container,
        text="Hi this is the IRS, you owe back taxes, pay now via gift card",
        backend=backend,
    )
    assert token.verify()
    assert "scam-triage" in token.activated_agents
    # Delegate output landed in the `text` slot (matches modality of input).
    assert token.text is not None
    assert token.text.payload["output"] == "SCAM"


def test_coordinator_no_router_match_returns_empty_token(isolated, tmp_path):
    from axiom_event_token.coordinator import Coordinator

    container = _build_axm(tmp_path, prompt_text="triage")
    coord = Coordinator()
    token = coord.compose_from_delegates(
        axm_container=container,
        text="please tell me a happy bedtime story about clouds",
        backend=_StubBackend(),
    )
    # No delegate matched; token is still signed + verifiable but carries
    # no layer reports.
    assert token.verify()
    assert token.activated_agents == ()
    assert token.text is None
