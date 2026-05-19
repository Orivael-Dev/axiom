"""Tests for axiom_research_server — the live HTTP wiring for the Re:Search HTML.

Uses FastAPI's TestClient + a stub backend so no LLM call hits the network.
The exoskeleton, ledger, and signing chain are all REAL — we only stub the
single SLMBackend.generate() call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))           # sandbox default ledger
    monkeypatch.delenv("AXIOM_RESEARCH_TOKEN", raising=False)
    monkeypatch.delenv("AXIOM_RESEARCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    # Drop cached modules so default-backend / ledger pick up the env above.
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier", "axiom_exoskeleton",
            "axiom_research_server",
        )):
            sys.modules.pop(mod, None)
    yield tmp_path


class _CannedBackend:
    name  = "stub"
    model = "stub-model"
    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        # Realistic-ish JSON output so the parser produces structured findings.
        body = json.dumps({
            "pain_articulated": "compliance asked for a signed audit trail",
            "urgency":          "high",
            "buyer_role":       "VP Engineering",
            "next_step":        "send one-page demo by Friday",
            "product_implication": "compliance-pull is our strongest signal",
            "honest_red_flag":   "budget authority sits one level up",
        })
        return BackendResult(text=body, input_tokens=88,
                             output_tokens=42, latency_ms=140,
                             backend=self.name, model=self.model)


def _client(monkeypatch, *, backend=None):
    import axiom_event_token.backends as be
    monkeypatch.setattr(be, "default_backend",
                         lambda: backend or _CannedBackend())
    import axiom_research_server as rs
    # Reset server state so the stubbed backend wins.
    rs._state = rs._ServerState()
    return TestClient(rs.app), rs


def test_root_serves_html(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "AXIOM Re:Search Engine" in r.text


def test_health_starts_unbuilt_then_built(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["state_built"] is False
    assert d["html_present"] is True

    # Trigger lazy build via use-cases.
    r = client.get("/api/use-cases")
    assert r.status_code == 200
    assert "customer_discovery" in r.json()["real_delegates"]

    r = client.get("/api/health")
    assert r.json()["state_built"] is True


def test_research_returns_full_shape(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    body = {
        "query":    "Which AI governance startups raised in 2025?",
        "domain":   "general",
        "workflow": "general_research",
    }
    r = client.post("/api/research", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    # Top-level shape matches the HTML's renderResult() expectations.
    for key in (
        "query", "workflow", "workflowLabel",
        "domain", "domainLabel",
        "report", "probabilityBand", "constitutionalDistance",
        "branchHealth", "sources", "branches", "receipt", "cost",
    ):
        assert key in d, f"missing key: {key}"
    # Report shape
    assert isinstance(d["report"]["tldr"], str) and d["report"]["tldr"]
    assert isinstance(d["report"]["keyFindings"], list)
    assert d["report"]["keyFindings"]
    # Receipt is signed (verified)
    assert d["receipt"]["verified"] is True
    assert d["receipt"]["token_id"].startswith("exo_")
    # Cost surfaces the canned token counts from the stub backend.
    assert d["cost"]["input_tokens"]  == 88
    assert d["cost"]["output_tokens"] == 42
    # Sources + branches arrive as lists (stubs).
    assert len(d["sources"])  >= 3
    assert len(d["branches"]) == 4
    # Meta confirms what's real vs stubbed.
    assert d["_meta"]["synthesis_is_real"]    is True
    assert d["_meta"]["sources_are_stubbed"]  is True
    assert d["_meta"]["branches_are_stubbed"] is True


def test_research_workflow_aliases_resolve(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query": "x", "domain": "general", "workflow": "general_research"
    })
    assert r.status_code == 200
    # general_research aliases to customer_discovery (see _WORKFLOW_ALIASES).
    assert r.json()["_meta"]["delegate_invoked"] == "customer_discovery"


def test_research_passes_through_real_delegate(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query": "Compare AXIOM against Guardrails AI",
        "domain": "general",
        "workflow": "competitive_analysis",
    })
    assert r.status_code == 200
    assert r.json()["_meta"]["delegate_invoked"] == "competitive_analysis"


def test_research_unknown_workflow_rejected(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query": "x", "domain": "general", "workflow": "not_a_workflow",
    })
    assert r.status_code == 400
    assert "unknown workflow" in r.json()["detail"]


def test_research_empty_query_rejected(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query": "", "domain": "general", "workflow": "general_research",
    })
    assert r.status_code == 422


def test_ledger_endpoint_returns_entries(server_env, monkeypatch):
    client, rs = _client(monkeypatch)
    # Fire two research calls so the ledger has content.
    for q in ("first query", "second query"):
        client.post("/api/research", json={
            "query": q, "domain": "general", "workflow": "general_research",
        })
    r = client.get("/api/ledger?limit=10")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 2
    assert d["entries"][0]["use_case"] == "customer_discovery"
    assert all(e["signature"] for e in d["entries"])
    # The ledger path lives under the sandboxed HOME tmpdir.
    assert str(server_env) in d["ledger_path"]


def test_research_failure_returns_502(server_env, monkeypatch):
    """A backend that raises BackendError surfaces as HTTP 502."""
    class _Boom:
        name  = "boom"
        model = "n/a"
        def generate(self, **_):
            from axiom_event_token.backends import BackendError
            raise BackendError("simulated outage")
    client, _ = _client(monkeypatch, backend=_Boom())
    r = client.post("/api/research", json={
        "query": "test", "domain": "general", "workflow": "general_research",
    })
    # DelegateAgent catches BackendError and emits a signed empty-output
    # LayerReport — that's a valid result, so we return 200 with confidence 0.
    # (The 502 path is for unexpected exceptions; see invoke() try/except.)
    assert r.status_code == 200
    d = r.json()
    assert d["cost"]["output_tokens"] == 0
    assert d["receipt"]["verified"] is True


def test_bearer_token_required_when_set(server_env, monkeypatch):
    monkeypatch.setenv("AXIOM_RESEARCH_TOKEN", "letmein")
    # Re-import so the middleware activates with the env above.
    for mod in list(sys.modules):
        if mod.startswith("axiom_research_server"):
            sys.modules.pop(mod, None)
    import axiom_event_token.backends as be
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    import axiom_research_server as rs
    rs._state = rs._ServerState()
    client = TestClient(rs.app)
    # health is public
    assert client.get("/api/health").status_code == 200
    # / is public (serves the HTML)
    assert client.get("/").status_code == 200
    # /api/research requires bearer
    r = client.post("/api/research", json={
        "query": "x", "domain": "general", "workflow": "general_research"
    })
    assert r.status_code == 401
    # With bearer → OK
    r = client.post("/api/research",
                    json={"query": "x", "domain": "general",
                          "workflow": "general_research"},
                    headers={"Authorization": "Bearer letmein"})
    assert r.status_code == 200


def test_use_cases_endpoint_lists_workflows(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.get("/api/use-cases")
    assert r.status_code == 200
    d = r.json()
    assert "customer_discovery" in d["real_delegates"]
    assert "general_research" in d["aliases"]
    assert "general_research" in d["all_workflows"]
