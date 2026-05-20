"""Tests for axiom_exoskeleton — the §9 company-exoskeleton agent.

No network. Uses a stub SLMBackend so every test is deterministic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier", "axiom_exoskeleton",
        )):
            sys.modules.pop(mod, None)
    yield


# ── stub backend ─────────────────────────────────────────────────────────


class _CannedBackend:
    """Returns a delegate-specific canned payload so tests can assert
    which delegate's system prompt actually got used."""
    name  = "stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        self.calls.append({
            "system":            system,
            "prompt":            prompt,
            "max_output_tokens": max_output_tokens,
        })
        # Encode the delegate's identifying word from its system prompt
        # so tests can confirm dispatch.
        marker = "?"
        for tok in ("investor-research", "enterprise-targeting",
                    "outreach-personalization", "demo-script",
                    "objection-handling", "competitive-analysis",
                    "grant-application", "patent-counsel-packet",
                    "customer-discovery-synthesis"):
            if tok in system:
                marker = tok
                break
        from axiom_event_token.backends import BackendResult
        return BackendResult(
            text=f"DELEGATE={marker} OK",
            input_tokens=len(prompt) // 4,
            output_tokens=12,
            latency_ms=5,
            backend=self.name,
            model=self.model,
        )


# ── pack build ───────────────────────────────────────────────────────────


def test_pack_contains_nine_delegates(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    names = [d.name for d in c.delegates]
    assert len(names) == 9
    expected = {
        "investor_research", "enterprise_targeting",
        "outreach_personalization", "demo_scripts",
        "sales_objection_handling", "competitive_analysis",
        "grant_application", "patent_counsel_packet",
        "customer_discovery",
    }
    assert set(names) == expected


def test_pack_delegates_have_budgets_and_prompts(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    for d in c.delegates:
        assert d.prompt_budget >= 400
        assert d.output_budget >= 250
        sys_file = c.path / "delegates" / d.name / "system_prompt.txt"
        assert sys_file.exists()
        body = sys_file.read_text(encoding="utf-8")
        assert len(body) > 100   # non-trivial scoped prompt


def test_pack_signatures_verify(isolated, tmp_path):
    """Every delegate signature on the packed container verifies under
    the axiom-axm-delegate-v1 namespace."""
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_axm import _delegate_key, _sign
    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    key = _delegate_key()
    for d in c.delegates:
        expected = _sign(key, d._payload())
        assert d.signature == expected, f"bad sig on {d.name}"


# ── ExoskeletonAgent API ────────────────────────────────────────────────


def test_exo_lists_use_cases(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    exo = ExoskeletonAgent(c, backend=_CannedBackend())
    names = exo.use_cases()
    assert "outreach_personalization" in names
    assert "customer_discovery" in names
    assert len(names) == 9


def test_exo_describe_returns_metadata(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    exo = ExoskeletonAgent(c, backend=_CannedBackend())
    info = exo.describe("competitive_analysis")
    assert info["name"] == "competitive_analysis"
    assert info["prompt_budget"] >= 400
    assert "INFORM" in info["intent_classes"]


def test_exo_invoke_returns_signed_event_token(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _CannedBackend()
    exo = ExoskeletonAgent(c, backend=backend)
    token = exo.invoke(
        "outreach_personalization",
        "Buyer: CISO at a 1500-person fintech. Signal: posted job for "
        "AI Governance Lead three days ago.",
    )
    assert token.verify()
    assert token.activated_agents == ("outreach_personalization",)
    assert token.text is not None
    p = token.text.payload
    assert p["delegate"] == "outreach_personalization"
    assert p["backend"]  == "stub"
    assert "DELEGATE=outreach-personalization" in p["output"]
    # The right system prompt reached the backend. The honesty
    # preamble now leads every delegate prompt, so just check the
    # delegate's identity phrase appears somewhere in the system.
    assert (
        "You are AXIOM's outreach-personalization delegate"
        in backend.calls[0]["system"]
    )


def test_exo_invoke_unknown_use_case_raises(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent, ExoskeletonError

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    exo = ExoskeletonAgent(c, backend=_CannedBackend())
    with pytest.raises(ExoskeletonError, match="unknown use case"):
        exo.invoke("not_a_real_delegate", "anything")


def test_exo_invoke_empty_input_raises(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent, ExoskeletonError

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    exo = ExoskeletonAgent(c, backend=_CannedBackend())
    with pytest.raises(ExoskeletonError, match="non-empty"):
        exo.invoke("investor_research", "   ")


def test_exo_each_delegate_dispatches_to_its_own_prompt(isolated, tmp_path):
    """Sanity: invoking each delegate uses ITS scoped system prompt."""
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _CannedBackend()
    exo = ExoskeletonAgent(c, backend=backend)
    for name in exo.use_cases():
        token = exo.invoke(name, f"test input for {name}")
        assert token.verify()
        assert token.text is not None
        assert token.text.payload["delegate"] == name


def test_exo_from_default_pack_works(isolated):
    """No on-disk pack — build one in a tempdir and run it."""
    from axiom_exoskeleton import ExoskeletonAgent
    exo = ExoskeletonAgent.from_default_pack(backend=_CannedBackend())
    assert len(exo.use_cases()) == 9
    token = exo.invoke("demo_scripts", "Feature: signed event token.")
    assert token.verify()


# ── CLI ─────────────────────────────────────────────────────────────────


def test_cli_list(isolated, tmp_path, capsys, monkeypatch):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    rc = main(["--list", "--pack", str(c.path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "investor_research" in out
    assert "customer_discovery" in out


def test_cli_invoke_with_inline_input(isolated, tmp_path, capsys, monkeypatch):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be

    c = build_exoskeleton_pack(tmp_path / "exo.axm")

    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    rc = main([
        "customer_discovery",
        "--pack", str(c.path),
        "--input", "Call transcript: buyer mentioned compliance risk.",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "customer_discovery" in out
    assert "DELEGATE=customer-discovery-synthesis" in out


def test_cli_save_token_writes_signed_json(isolated, tmp_path, capsys,
                                            monkeypatch):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be
    from axiom_event_token import EventToken

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    save_to = tmp_path / "token.json"
    rc = main([
        "investor_research",
        "--pack", str(c.path),
        "--input", "AI governance thesis.",
        "--save-token", str(save_to),
    ])
    assert rc == 0
    assert save_to.exists()
    data = json.loads(save_to.read_text(encoding="utf-8"))
    token = EventToken.from_dict(data)
    assert token.verify()
    assert token.activated_agents == ("investor_research",)


def test_cli_input_file_works(isolated, tmp_path, capsys, monkeypatch):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    in_file = tmp_path / "in.txt"
    in_file.write_text("Pretend transcript.", encoding="utf-8")
    rc = main([
        "competitive_analysis",
        "--pack", str(c.path),
        "--input-file", str(in_file),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "competitive_analysis" in out


def test_cli_unknown_use_case_exits_nonzero(isolated, tmp_path, capsys,
                                             monkeypatch):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be

    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    monkeypatch.setattr(be, "default_backend", lambda: _CannedBackend())
    rc = main([
        "no_such_delegate",
        "--pack", str(c.path),
        "--input", "x",
    ])
    assert rc != 0
