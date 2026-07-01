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
