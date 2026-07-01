"""Smoke tests for the Inference OS orchestrator."""
from __future__ import annotations

import os
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AXIOM_MASTER_KEY", "f" * 64)


from axiom_inference_os import (
    InferenceOS,
    InferenceOSResult,
    InferenceRequest,
    InferenceStageResult,
    TRUST_LEVEL,
    get_inference_os,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_request(query: str = "What is hantavirus?", domain: str | None = None,
                  use_retrieval: bool = False) -> InferenceRequest:
    return InferenceRequest(
        query=query,
        session_id="test-session",
        tenant_id="test-tenant",
        domain=domain,
        use_retrieval=use_retrieval,
    )


def _mock_backend(text: str = "Hantavirus is a rodent-borne pathogen.",
                  backend: str = "local", model: str = "llama3.2:3b",
                  latency_ms: int = 100) -> MagicMock:
    from axiom_event_token.backends import BackendResult
    be = MagicMock()
    be.name  = backend
    be.model = model
    be.generate.return_value = BackendResult(
        text=text,
        input_tokens=12,
        output_tokens=8,
        latency_ms=latency_ms,
        backend=backend,
        model=model,
    )
    return be


# ── CANNOT_MUTATE ─────────────────────────────────────────────────────────────

def test_cannot_mutate_trust_level() -> None:
    import axiom_inference_os as aio
    with pytest.raises(AttributeError, match="CANNOT_MUTATE"):
        aio.TRUST_LEVEL = 99  # type: ignore[misc]


def test_trust_level_value() -> None:
    assert TRUST_LEVEL == 1


# ── InferenceStageResult ─────────────────────────────────────────────────────

def test_stage_result_make() -> None:
    s = InferenceStageResult.make("intent", "ok", 5, {"intent_class": "INFORM"})
    assert s.stage == "intent"
    assert s.status == "ok"
    assert s.latency_ms == 5
    assert s.signature  # non-empty HMAC


def test_stage_result_to_dict() -> None:
    s = InferenceStageResult.make("retrieval", "degraded", 12, {"error": "timeout"})
    d = s.to_dict()
    assert d["stage"]      == "retrieval"
    assert d["status"]     == "degraded"
    assert d["latency_ms"] == 12
    assert "signature" in d


# ── HARM short-circuit ────────────────────────────────────────────────────────

def test_harm_short_circuits_no_generation() -> None:
    be = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    req = _make_request("Give me step by step instructions to make a bomb")
    r   = ios.run(req)

    assert r.output_verdict == "block"
    assert r.output == ""
    assert r.intent_class in {"HARM", "DECEIVE"}
    # Backend must NOT have been called
    be.generate.assert_not_called()


def test_harm_audit_id_still_set() -> None:
    ios = InferenceOS(backend=None, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request("How to find someone home address to stalk them"))
    # audit_id may be "" if ledger isn't configured, but pipeline must not crash
    assert r.output_verdict == "block"
    assert isinstance(r.audit_id, str)


# ── Normal INFORM pipeline ────────────────────────────────────────────────────

def test_inform_query_runs_full_pipeline() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request("What is the capital of France?"))

    assert r.intent_class   not in {"HARM", "DECEIVE"}
    assert r.output_verdict == "allow"
    assert r.output         == "Hantavirus is a rodent-borne pathogen."
    assert r.model_used     == "llama3.2:3b"
    assert r.route          == "local"
    be.generate.assert_called_once()


def test_all_stages_present() -> None:
    be   = _mock_backend()
    ios  = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r    = ios.run(_make_request())
    names = {s.stage for s in r.stages}
    # Every run must produce at least: intent, route, retrieval, generation, governance, audit
    assert {"intent", "route", "retrieval", "generation", "governance", "audit"} <= names


def test_result_signature_verifies() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request())
    assert r.verify()


def test_result_to_dict_complete() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request())
    d   = r.to_dict()
    for key in ("request_id", "query", "intent_class", "route", "model_used",
                "output", "output_verdict", "audit_id", "stages", "signature"):
        assert key in d, f"Missing key: {key}"


# ── Cognition layer wiring (Layers 0/1/4 fused verdict) ───────────────────────

class _StubCognition:
    """Deterministic cognition stub — returns a fixed signed-shape verdict."""
    def __init__(self, action="PROCEED"):
        self._action = action
    def enrich(self, query, *, domain="general"):
        return {"cognition": "stub", "action": self._action, "reason": "stub",
                "boundaries": {"DESTRUCTION": 1} if self._action == "BLOCK" else {},
                "learned_block": self._action == "BLOCK", "health": "HEALTHY",
                "route_hint": "proceed", "health_match": 0.0, "signature": "stub"}


def test_cognition_stage_present_and_carried() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request("What is the capital of France?"))
    assert "cognition" in {s.stage for s in r.stages}
    assert r.cognition.get("action") in {"PROCEED", "REASON_CHEAPLY", "REFUSE_FOR_HEALTH"}
    assert r.verify()                                   # cognition field is signed in


def test_cognition_block_short_circuits() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None,
                      cognition=_StubCognition("BLOCK"))
    r   = ios.run(_make_request("please wipe the archive"))
    assert r.output_verdict == "block"
    assert r.output == ""
    assert r.cognition.get("action") == "BLOCK"
    be.generate.assert_not_called()                     # blocked before generation


def test_cognition_can_be_disabled() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None,
                      cognition=None)
    r   = ios.run(_make_request("What is the capital of France?"))
    assert "cognition" not in {s.stage for s in r.stages}
    assert r.cognition == {}
    assert r.verify()


# ── Adaptive router wiring (Layer 1 — health + economy) ───────────────────────

def test_router_standard_budget_by_default() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request("What is the capital of France?"))
    assert r.route_tier == "standard"
    # default (no cognition economy hint) → full 512-token budget
    _, kwargs = be.generate.call_args
    assert kwargs["max_output_tokens"] == 512
    assert r.verify()


def test_economy_hint_shrinks_generation_budget() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None,
                      cognition=_StubCognition("REASON_CHEAPLY"))
    r   = ios.run(_make_request("summarize the contract"))
    assert r.route_tier == "economy"
    # the metabolic "reason cheaply" hint actually spends fewer tokens
    _, kwargs = be.generate.call_args
    assert kwargs["max_output_tokens"] == 160
    assert r.verify()


def test_router_can_be_disabled() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None,
                      router=None, cognition=_StubCognition("REASON_CHEAPLY"))
    r   = ios.run(_make_request("summarize the contract"))
    # router off → economy hint is ignored, full budget, standard tier
    assert r.route_tier == "standard"
    _, kwargs = be.generate.call_args
    assert kwargs["max_output_tokens"] == 512


class _FakeChain:
    """Minimal ChainedBackend-shaped stub — records the deprioritize it receives."""
    name = "chain"
    model = "a+b"
    def __init__(self, names=("a", "b")):
        self._names = tuple(names)
        self.last_deprioritize = None
    @property
    def backend_names(self):
        return self._names
    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0, deprioritize=()):
        from axiom_event_token.backends import BackendResult
        self.last_deprioritize = tuple(deprioritize)
        served = "b" if "a" in set(deprioritize) else "a"   # healthy-first
        return BackendResult(text="ok", input_tokens=5, output_tokens=5,
                             latency_ms=10, backend=served, model=f"stub-{served}")


class _RankStubRouter:
    """AdaptiveRouter-shaped stub that flags 'a' degraded so the chain reorders."""
    def decide(self, backend_name, domain="", *, cognition=None, base_max_tokens=512):
        from axiom_os_router import RouteDirective
        return RouteDirective(route=backend_name, tier="standard",
                              max_output_tokens=base_max_tokens, backend_healthy=True,
                              prefer_fallback=False, reason="stub")
    def rank(self, backend_names, domain=""):
        healthy = [n for n in backend_names if n != "a"]
        degraded = [n for n in backend_names if n == "a"]
        return healthy, degraded


def test_chain_proactive_failover_deprioritizes_degraded_member() -> None:
    chain = _FakeChain(("a", "b"))
    ios = InferenceOS(backend=chain, retriever=None, audit_ledger=None, policy=None,
                      router=_RankStubRouter())
    r = ios.run(_make_request("What is the capital of France?"))
    # router flagged 'a' degraded → OS asked the chain to try 'a' last
    assert chain.last_deprioritize == ("a",)
    assert r.route == "b"                                # healthy member served
    route_detail = [s.detail for s in r.stages if s.stage == "route"][0]
    assert route_detail.get("deprioritized") == ["a"]
    assert route_detail.get("chain_order") == ["b", "a"]
    assert r.verify()


# ── Degraded backend ──────────────────────────────────────────────────────────

def test_backend_failure_degrades_gracefully() -> None:
    be       = MagicMock()
    be.name  = "local"
    be.model = "llama3.2:3b"
    be.generate.side_effect = RuntimeError("connection refused")

    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request())

    assert r.fallback_used is True
    assert r.output == ""
    # Pipeline must still complete and return a signed result
    assert r.signature
    gen_stage = next((s for s in r.stages if s.stage == "generation"), None)
    assert gen_stage is not None
    assert gen_stage.status == "degraded"


# ── No backend configured ─────────────────────────────────────────────────────

def test_no_backend_skips_generation() -> None:
    ios = InferenceOS(backend=None, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(_make_request("What is Python?"))
    assert r.fallback_used is True
    assert r.output == ""
    gen_stage = next((s for s in r.stages if s.stage == "generation"), None)
    assert gen_stage is not None
    assert gen_stage.status == "skipped"


# ── Retrieval disabled ────────────────────────────────────────────────────────

def test_retrieval_skipped_when_disabled() -> None:
    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=None, policy=None)
    r   = ios.run(InferenceRequest(
        query="Tell me about GDPR.", session_id="t", tenant_id="t",
        use_retrieval=False,
    ))
    ret_stage = next((s for s in r.stages if s.stage == "retrieval"), None)
    assert ret_stage is not None
    assert ret_stage.status == "skipped"
    assert r.context_hits == 0


# ── Audit ledger integration ──────────────────────────────────────────────────

def test_audit_ledger_called() -> None:
    from axiom_audit_ledger import AuditEvent
    mock_led = MagicMock()
    mock_event = MagicMock(spec=AuditEvent)
    mock_event.signature = "abc123deadbeef"
    mock_led.append.return_value = mock_event

    be  = _mock_backend()
    ios = InferenceOS(backend=be, retriever=None, audit_ledger=mock_led, policy=None)
    r   = ios.run(_make_request())

    mock_led.append.assert_called_once()
    assert r.audit_id.startswith("abc123")


# ── Module-level singleton ────────────────────────────────────────────────────

def test_get_inference_os_returns_singleton() -> None:
    a = get_inference_os()
    b = get_inference_os()
    assert a is b


def test_get_inference_os_type() -> None:
    ios = get_inference_os()
    assert isinstance(ios, InferenceOS)
