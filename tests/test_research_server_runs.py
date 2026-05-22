"""Tests for GET /api/runs — the unified resume-picker endpoint.

Uses FastAPI's TestClient with a stub backend so no LLM call hits the network.
The ledgers are written against sandboxed temp HOME directories so they don't
touch any real ~/.axiom path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_RESEARCH_TOKEN", raising=False)
    monkeypatch.delenv("AXIOM_RESEARCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    monkeypatch.delenv("AXIOM_MEDICAL_LEDGER", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier", "axiom_exoskeleton",
            "axiom_research_server", "axiom_exoskeleton_ledger",
            "axiom_medical_ledger",
        )):
            sys.modules.pop(mod, None)
    yield tmp_path


class _CannedBackend:
    name  = "stub"
    model = "stub-model"
    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        return BackendResult(text="OK", input_tokens=10, output_tokens=4,
                             latency_ms=21, backend=self.name, model=self.model)


def _client(monkeypatch):
    import axiom_event_token.backends as be
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    import axiom_research_server as rs
    rs._state = rs._ServerState()
    return TestClient(rs.app), rs


def _write_exo_entry(tmp_path, question="What is the main pain?"):
    """Write one signed exoskeleton ledger entry using the real writer."""
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent
    from axiom_exoskeleton_ledger import LedgerWriter, default_ledger_path

    c = build_exoskeleton_pack(tmp_path / f"exo_{len(list(tmp_path.glob('exo_*.axm')))}.axm")
    ledger_path = default_ledger_path()
    ledger = LedgerWriter(ledger_path)
    exo = ExoskeletonAgent(c, backend=_CannedBackend(), ledger=ledger)
    exo.invoke("customer_discovery", question)


def _write_med_entry(question="GLP-1 mechanism for inflammation",
                     profile="mechanism"):
    """Write one signed medical ledger entry directly (avoids the full agent stack)."""
    from axiom_signing import derive_key
    from axiom_medical_ledger import default_ledger_path

    ledger_path = default_ledger_path()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    key = derive_key(b"axiom-medical-ledger-v1")
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z"),
        "research_question": question,
        "profile":            profile,
        "container_id":       "axm-med-test",
        "coordinator_token_id": "medcoord_test1234",
        "event_token_ids":    [],
        "active_layers":      ["source", "text"],
        "primary_layer":      "text",
        "cross_layer_consistency": 0.85,
        "tier_distribution":  {"1": 1, "2": 0, "3": 0, "4": 0, "5": 0},
        "requires_human_review": False,
        "manifest_root":      "",
        "verified":           True,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True).encode("utf-8")
    sig = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    entry = dict(payload, signature=sig)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


# ── Tests ──────────────────────────────────────────────────────────────────


def test_runs_empty_when_both_ledgers_empty(server_env, monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.get("/api/runs?limit=5")
    assert r.status_code == 200
    d = r.json()
    assert "runs" in d
    assert d["runs"] == []


def test_runs_exoskeleton_only(server_env, monkeypatch):
    _write_exo_entry(server_env)
    client, _ = _client(monkeypatch)
    r = client.get("/api/runs?limit=5")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert any(x["kind"] == "exoskeleton" for x in runs)


def test_runs_medical_only(server_env, monkeypatch):
    _write_med_entry()
    client, _ = _client(monkeypatch)
    r = client.get("/api/runs?limit=5")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert any(x["kind"] == "medical" for x in runs)


def test_runs_merged_and_sorted_desc(server_env, monkeypatch):
    _write_exo_entry(server_env)
    _write_med_entry()
    client, _ = _client(monkeypatch)
    r = client.get("/api/runs?limit=10")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) >= 2
    kinds = {x["kind"] for x in runs}
    assert "exoskeleton" in kinds
    assert "medical" in kinds
    timestamps = [x["timestamp_utc"] for x in runs]
    assert timestamps == sorted(timestamps, reverse=True)


def test_runs_limit_caps_results(server_env, monkeypatch):
    for i in range(4):
        _write_exo_entry(server_env, question=f"Question number {i}")
    client, _ = _client(monkeypatch)
    r = client.get("/api/runs?limit=2")
    assert r.status_code == 200
    assert len(r.json()["runs"]) <= 2


def test_runs_verified_field_is_bool(server_env, monkeypatch):
    _write_exo_entry(server_env)
    _write_med_entry()
    client, _ = _client(monkeypatch)
    r = client.get("/api/runs?limit=10")
    assert r.status_code == 200
    for run in r.json()["runs"]:
        assert isinstance(run["verified"], bool), (
            f"verified should be bool, got {type(run['verified'])} for {run}"
        )
