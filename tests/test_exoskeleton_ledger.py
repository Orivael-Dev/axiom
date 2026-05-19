"""Tests for axiom_exoskeleton_ledger — persistent audit ledger."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    # Sandbox HOME so default_ledger_path() does not touch the real one.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier", "axiom_exoskeleton",
        )):
            sys.modules.pop(mod, None)
    yield


class _CannedBackend:
    name  = "stub"
    model = "stub-model"
    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        return BackendResult(text="OK", input_tokens=33, output_tokens=4,
                             latency_ms=21, backend=self.name,
                             model=self.model)


def test_default_path_under_sandboxed_home(isolated, tmp_path):
    from axiom_exoskeleton_ledger import default_ledger_path
    assert default_ledger_path() == tmp_path / ".axiom" / "exoskeleton-ledger.jsonl"


def test_env_override_wins(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_EXOSKELETON_LEDGER", str(tmp_path / "custom.jsonl"))
    from axiom_exoskeleton_ledger import default_ledger_path
    assert default_ledger_path() == tmp_path / "custom.jsonl"


def test_writer_creates_parent_dir(isolated, tmp_path):
    from axiom_exoskeleton_ledger import LedgerWriter
    p = tmp_path / "nested" / "more" / "ledger.jsonl"
    LedgerWriter(p)
    assert p.parent.is_dir()


def test_append_then_read_roundtrip(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent
    from axiom_exoskeleton_ledger import LedgerWriter, read_ledger

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = LedgerWriter(ledger_path)
    exo = ExoskeletonAgent(c, backend=_CannedBackend(), ledger=ledger)
    exo.invoke("investor_research", "AI governance thesis")
    exo.invoke("customer_discovery", "Pretend call transcript content")
    entries = read_ledger(ledger_path)
    assert len(entries) == 2
    assert entries[0].use_case == "investor_research"
    assert entries[1].use_case == "customer_discovery"
    assert all(e.verify() for e in entries)
    assert entries[0].input_tokens == 33
    assert entries[0].verified is True


def test_tampered_entry_fails_verification(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent
    from axiom_exoskeleton_ledger import LedgerWriter, read_ledger

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    ledger_path = tmp_path / "ledger.jsonl"
    exo = ExoskeletonAgent(c, backend=_CannedBackend(),
                            ledger=LedgerWriter(ledger_path))
    exo.invoke("demo_scripts", "Feature description.")
    # Rewrite the file with a tampered input_tokens field.
    raw = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    raw["input_tokens"] = 9999
    ledger_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    entries = read_ledger(ledger_path)
    assert len(entries) == 1
    assert entries[0].verify() is False     # tampering detected


def test_query_filters(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent
    from axiom_exoskeleton_ledger import LedgerWriter, query_ledger

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    ledger_path = tmp_path / "ledger.jsonl"
    exo = ExoskeletonAgent(c, backend=_CannedBackend(),
                            ledger=LedgerWriter(ledger_path))
    exo.invoke("investor_research", "thesis A")
    exo.invoke("customer_discovery", "call A")
    exo.invoke("investor_research", "thesis B")

    only_investor = query_ledger(path=ledger_path,
                                  use_case="investor_research")
    assert len(only_investor) == 2
    assert all(e.use_case == "investor_research" for e in only_investor)

    last_one = query_ledger(path=ledger_path, limit=1)
    assert len(last_one) == 1
    assert last_one[0].use_case == "investor_research"
    assert last_one[0].input_excerpt == "thesis B"


def test_read_missing_ledger_returns_empty(isolated, tmp_path):
    from axiom_exoskeleton_ledger import read_ledger
    assert read_ledger(tmp_path / "does-not-exist.jsonl") == []


def test_cli_writes_to_ledger(isolated, tmp_path, monkeypatch, capsys):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be
    from axiom_exoskeleton_ledger import read_ledger

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    ledger_path = tmp_path / "cli-ledger.jsonl"
    rc = main([
        "competitive_analysis",
        "--pack",   str(c.path),
        "--input",  "Compare against LLM firewalls.",
        "--ledger", str(ledger_path),
    ])
    assert rc == 0
    entries = read_ledger(ledger_path)
    assert len(entries) == 1
    assert entries[0].use_case == "competitive_analysis"
    assert entries[0].verify() is True


def test_cli_no_ledger_flag(isolated, tmp_path, monkeypatch):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    # Default ledger should NOT be created with --no-ledger.
    default_path = tmp_path / ".axiom" / "exoskeleton-ledger.jsonl"
    rc = main([
        "investor_research",
        "--pack",   str(c.path),
        "--input",  "thesis",
        "--no-ledger",
    ])
    assert rc == 0
    assert not default_path.exists()
