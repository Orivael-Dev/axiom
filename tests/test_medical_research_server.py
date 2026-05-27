"""Tests for the medical-research HTTP endpoints in axiom_research_server."""
from __future__ import annotations

import json
import sys

import pytest

# Skip the entire module if fastapi+httpx aren't importable in this
# environment — the rest of the research server tests will skip the
# same way.
fastapi = pytest.importorskip("fastapi")
httpx   = pytest.importorskip("httpx")
from fastapi.testclient import TestClient   # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    monkeypatch.delenv("AXIOM_MEDICAL_LEDGER", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_medical_", "axiom_research_server",
            "axiom_exoskeleton",
        )):
            sys.modules.pop(mod, None)
    yield


class _StubBackend:
    name = "stub"
    model = "stub-model"
    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        marker = "?"
        for tok in ("medical_source", "medical_claim", "medical_data",
                    "medical_bio", "medical_physics",
                    "medical_governance"):
            if tok in system or tok.replace("_", "-") in system:
                marker = tok
                break
        body = json.dumps({"delegate": marker})
        return BackendResult(
            text=body, input_tokens=10, output_tokens=4,
            latency_ms=1, backend=self.name, model=self.model,
        )


def _client(monkeypatch) -> TestClient:
    import axiom_event_token.backends as be
    monkeypatch.setattr(be, "default_backend", lambda: _StubBackend())
    from axiom_research_server import app
    return TestClient(app)


def test_profiles_endpoint_lists_five(isolated, monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/api/medical/profiles")
    assert r.status_code == 200
    d = r.json()
    names = set(d["profiles"].keys())
    assert names == {
        "summarize", "mechanism", "compare",
        "patient_apply", "hypothesize",
    }
    assert "source" in d["profiles"]["summarize"]


def test_research_endpoint_returns_expected_shape(isolated, monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/medical/research", json={
        "question": "What does Cochrane say about GLP-1 + CRP?",
        "profile":  "summarize",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    for key in (
        "question", "profile", "container_id",
        "event_tokens", "coordinator_tokens",
        "descriptor", "manifest_root",
        "requires_human_review", "tier_distribution",
    ):
        assert key in d, f"missing key: {key}"
    assert d["descriptor"].startswith("[EVENT_TOKEN")
    assert d["event_tokens"]
    assert d["coordinator_tokens"]
    assert d["manifest_root"].startswith("sha256:")


def test_research_endpoint_rejects_unknown_profile(isolated, monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/medical/research", json={
        "question": "q",
        "profile":  "not_a_real_profile",
    })
    assert r.status_code == 400
    assert "unknown profile" in r.text


def test_research_endpoint_rejects_empty_question(isolated, monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/medical/research", json={
        "question": "",
        "profile":  "summarize",
    })
    assert r.status_code == 422       # pydantic min_length=1 rejection


def test_ledger_endpoint_returns_signed_entries(
    isolated, monkeypatch, tmp_path,
):
    monkeypatch.setenv("AXIOM_MEDICAL_LEDGER",
                       str(tmp_path / "medled.jsonl"))
    client = _client(monkeypatch)
    # Trigger one research call → ledger gets one entry.
    r = client.post("/api/medical/research", json={
        "question": "ledger test",
        "profile":  "summarize",
    })
    assert r.status_code == 200, r.text
    r2 = client.get("/api/medical/ledger?limit=5")
    assert r2.status_code == 200
    d = r2.json()
    assert d["count"] >= 1
    assert all(e.get("signature") for e in d["entries"])
