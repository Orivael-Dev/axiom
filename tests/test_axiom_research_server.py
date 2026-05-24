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
    # Test suite must NEVER hit external research APIs. Local-only.
    monkeypatch.setenv("AXIOM_EXTERNAL_RETRIEVAL", "0")
    monkeypatch.delenv("AXIOM_RESEARCH_TOKEN", raising=False)
    monkeypatch.delenv("AXIOM_RESEARCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    # Drop cached modules so default-backend / ledger pick up the env above.
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            # `axiom_intent_classifier` AND `axiom_intent_gate` must
            # be popped together — IntentGate.__init__ does an
            # isinstance(classifier, IntentClassifier) check using
            # whichever class object it captured at import time, so
            # reloading one without the other leaves a stale
            # reference and causes spurious TypeError failures in
            # later tests (e.g. test_axiom_server_integration).
            "axiom_intent_classifier", "axiom_intent_gate",
            "axiom_exoskeleton", "axiom_research_server",
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

    # Sales-context diagnostics surface on /api/health so operators
    # can see whether customer_discovery & friends will get context
    # injected, without having to run the request and inspect output.
    sc = d.get("sales_context")
    assert sc is not None
    assert "root" in sc
    assert "records" in sc
    assert "status" in sc
    assert sc["status"] in ("loaded", "empty")
    for k in ("companies", "buyers", "objections", "competitors"):
        assert k in sc["records"]

    # Trigger lazy build via use-cases.
    r = client.get("/api/use-cases")
    assert r.status_code == 200
    assert "customer_discovery" in r.json()["real_delegates"]

    r = client.get("/api/health")
    assert r.json()["state_built"] is True


def test_health_sales_context_reports_env_override(
    server_env, monkeypatch, tmp_path,
):
    """When AXIOM_SALES_CONTEXT_ROOT is set, the diagnostics block
    must report it as env_override=True and read records from the
    pointed-to directory — proves the operator can override the
    resolved path without code changes."""
    sales_dir = tmp_path / "sales"
    sales_dir.mkdir()
    (sales_dir / "companies.jsonl").write_text(
        '{"name":"Acme","industry":"saas","size":"smb"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AXIOM_SALES_CONTEXT_ROOT", str(sales_dir))
    client, _ = _client(monkeypatch)
    sc = client.get("/api/health").json()["sales_context"]
    assert sc["env_override"] is True
    assert sc["root"] == str(sales_dir)
    assert sc["root_exists"] is True
    assert sc["records"]["companies"] == 1
    assert sc["status"] == "loaded"


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
    # Sources + branches arrive as lists.
    assert len(d["sources"])  >= 1
    assert len(d["branches"]) == 4
    # Meta confirms what's real vs stubbed.
    assert d["_meta"]["synthesis_is_real"] is True
    # Retriever is wired live — real for a query that hits any repo doc,
    # stub only when nothing matches. Branches stay stubbed for "general".
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


# ─── Real retriever wiring ──────────────────────────────────────────────


def test_research_uses_real_retriever(server_env, monkeypatch):
    """Real retriever should return non-stub sources from the repo's docs."""
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query":    "event token coordinator signing layer",
        "domain":   "general",
        "workflow": "general_research",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["_meta"]["sources_are_stubbed"] is False
    assert d["_meta"]["retriever_indexed_files"] > 0
    # No source should have "STUB" in its kind once the retriever fired.
    assert not any("STUB" in s["kind"] for s in d["sources"])
    # Top source should reference something in docs / README.
    assert d["sources"]
    assert d["sources"][0]["score"] == 1.0


def test_research_sources_include_provider_and_tier(server_env, monkeypatch,
                                                     tmp_path):
    """When a MultiProviderRetriever is wired, provider + evidence_tier
    fields propagate through the SSE/JSON shape to the UI consumer."""
    client, rs = _client(monkeypatch)
    rs._state.ensure()
    from axiom_research_retriever import RetrievedSource
    from axiom_research_providers.multi import MultiProviderRetriever

    class _FakeExternal:
        name    = "pubmed"
        domains = ("*",)
        def retrieve(self, q, *, k=5, domain=None):
            return [RetrievedSource(
                title="Mock PubMed paper",
                uri="https://pubmed.ncbi.nlm.nih.gov/99/",
                kind="pubmed · article",
                score=1.0,
                snippet="Fake abstract.",
                provider="pubmed",
                evidence_tier=1,
            )]
        def stats(self):
            return {"name": self.name, "domains": list(self.domains)}

    rs._state.retriever = MultiProviderRetriever([_FakeExternal()])

    r = client.post("/api/research", json={
        "query":    "anything",
        "domain":   "general",
        "workflow": "general_research",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["sources"], "should have at least one source"
    src = d["sources"][0]
    assert src["provider"] == "pubmed"
    assert src["evidence_tier"] == 1
    assert src["uri"].startswith("https://pubmed.ncbi.nlm.nih.gov/")


def test_research_retriever_no_hit_falls_back_to_stub(server_env, monkeypatch,
                                                       tmp_path, monkeypatch_retriever=None):
    """Nonsense query against an empty retriever index → no-hit stub."""
    client, rs = _client(monkeypatch)
    # Force the server to use a retriever pointed at an empty tmpdir so
    # nothing matches regardless of what nonsense we pass in.
    rs._state.ensure()
    from axiom_research_retriever import LocalRetriever
    empty = tmp_path / "empty-corpus"
    empty.mkdir()
    rs._state.retriever = LocalRetriever(roots=[empty])

    r = client.post("/api/research", json={
        "query":    "any query at all",
        "domain":   "general",
        "workflow": "general_research",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["_meta"]["sources_are_stubbed"] is True
    assert any("no-hit" in s["kind"].lower() or "stub" in s["kind"].lower()
               for s in d["sources"])


# ─── Real QRF wiring ────────────────────────────────────────────────────


def test_research_real_qrf_for_supported_domain(server_env, monkeypatch):
    """A supported domain (finance) fires real QRF, not the stub."""
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query":    "How does AXIOM reduce LLM risk for banks?",
        "domain":   "finance",        # → "financial" in QRF
        "workflow": "competitive_analysis",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["_meta"]["branches_are_stubbed"] is False
    # Real QRF returns 6 branches for the financial domain.
    assert len(d["branches"]) == 6
    # The receipt should carry a non-empty QRF signature prefix.
    assert d["receipt"]["qrf_signature"]
    # Every branch should have a probability + status.
    for b in d["branches"]:
        assert "probability" in b
        assert b["status"] in ("passed", "rival", "killed")


def test_research_general_domain_keeps_qrf_stub(server_env, monkeypatch):
    """`general` domain is unsupported by QRF → stub branches remain."""
    client, _ = _client(monkeypatch)
    r = client.post("/api/research", json={
        "query":    "anything",
        "domain":   "general",
        "workflow": "general_research",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["_meta"]["branches_are_stubbed"] is True
    # Stub QRF receipt has empty qrf_signature.
    assert d["receipt"]["qrf_signature"] == ""


# ─── SSE streaming ──────────────────────────────────────────────────────


def test_research_stream_emits_stages_then_result(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    with client.stream("POST", "/api/research/stream", json={
        "query":    "Compare AXIOM against Guardrails AI",
        "domain":   "general",
        "workflow": "competitive_analysis",
    }) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.read().decode("utf-8")

    # Should contain at least one of each named event.
    assert "event: stage" in body
    assert "event: partial" in body
    assert "event: result" in body
    assert "event: done" in body

    # Pull out the result event's data line.
    blocks = [blk for blk in body.split("\n\n")
              if "event: result" in blk]
    assert blocks, "no result event found in stream"
    data_line = [l for l in blocks[0].splitlines()
                 if l.startswith("data:")][0]
    payload = json.loads(data_line[5:].strip())
    assert payload["receipt"]["verified"] is True
    assert payload["_meta"]["synthesis_is_real"] is True


def test_research_stream_unknown_workflow_returns_400(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.post("/api/research/stream", json={
        "query": "x", "domain": "general", "workflow": "no_such_workflow",
    })
    assert r.status_code == 400


# ─── Ledger viewer route ────────────────────────────────────────────────


def test_ledger_viewer_html_served(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.get("/ledger")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "AXIOM Exoskeleton Ledger" in r.text
    # And the viewer must reference the JSON API it consumes.
    assert "/api/ledger" in r.text
