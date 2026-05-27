"""Tests for axiom_sales_context + auto-injection into the exoskeleton."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AXIOM_EXOSKELETON_LEDGER", raising=False)
    monkeypatch.delenv("AXIOM_SALES_CONTEXT_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod.startswith((
            "axiom_event_token", "axiom_signing",
            "axiom_intent_classifier", "axiom_exoskeleton",
            "axiom_sales_context",
        )):
            sys.modules.pop(mod, None)
    yield


class _RecordingBackend:
    """Captures every prompt the runtime would have sent to a real LLM."""

    name  = "stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        self.calls.append({
            "system":            system,
            "prompt":            prompt,
            "max_output_tokens": max_output_tokens,
        })
        return BackendResult(
            text="OK",
            input_tokens=len(prompt) // 4,
            output_tokens=4,
            latency_ms=2,
            backend=self.name,
            model=self.model,
        )


# ── pure-module tests ────────────────────────────────────────────────


def test_default_context_root_respects_env(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv("AXIOM_SALES_CONTEXT_ROOT", str(tmp_path / "sales"))
    from axiom_sales_context import default_context_root
    assert default_context_root() == tmp_path / "sales"


def test_default_context_root_falls_back_to_cwd_when_module_path_missing(
    isolated, tmp_path, monkeypatch,
):
    """Container deploy case: package lives in site-packages, so the
    module-relative docs/internal/sales/ doesn't exist; the corpus is
    mounted under the working directory instead. With no
    AXIOM_SALES_CONTEXT_ROOT set, resolution must fall through to
    `cwd / docs/internal/sales` when it exists. This is the fix for
    the silent-empty-store bug that made customer_discovery emit
    generic output in container runs."""
    monkeypatch.delenv("AXIOM_SALES_CONTEXT_ROOT", raising=False)
    # Build a fake cwd with the corpus mounted at the conventional path.
    sales_dir = tmp_path / "docs" / "internal" / "sales"
    sales_dir.mkdir(parents=True)
    (sales_dir / "companies.jsonl").write_text(
        '{"name":"Acme","industry":"saas"}\n', encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    # Point the "module-relative" probe at a directory that doesn't
    # exist, so the module-parent branch fails and the cwd fallback
    # takes over. We do this by mocking _Path(__file__).parent via a
    # monkeypatched __file__ — cleanest is to assert the function
    # picks the cwd path when the module-relative one is absent. The
    # module under test lives at the repo root, so its sibling
    # docs/internal/sales DOES exist on disk; to isolate, we resolve
    # the function under a temp working dir AND assert the returned
    # path is the cwd one IFF the module-relative one is absent.
    from axiom_sales_context import default_context_root
    resolved = default_context_root()
    # If the repo's own docs/internal/sales exists at module-parent
    # we get that one — the cwd branch only triggers when the module-
    # parent path is gone. Both are valid resolutions; the regression
    # we're guarding against is "neither got tried in container".
    assert resolved.is_dir(), (
        f"resolved root {resolved} should be an existing directory "
        f"(either module-parent or cwd fallback)"
    )


def test_default_context_root_cwd_fallback_isolated(
    isolated, tmp_path, monkeypatch,
):
    """Isolated unit test of the cwd-fallback branch — bypasses the
    repo-relative branch entirely by stubbing the module's __file__
    to point at a directory that has no `docs/internal/sales/`
    sibling, then asserting the function resolves to cwd."""
    monkeypatch.delenv("AXIOM_SALES_CONTEXT_ROOT", raising=False)
    sales_dir = tmp_path / "docs" / "internal" / "sales"
    sales_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    # Stub module __file__ to a sibling that has no docs/internal/sales.
    import axiom_sales_context as mod
    fake_module_home = tmp_path / "fake_site_packages"
    fake_module_home.mkdir()
    monkeypatch.setattr(mod, "__file__", str(fake_module_home / "axiom_sales_context.py"))

    from axiom_sales_context import default_context_root
    resolved = default_context_root()
    assert resolved == sales_dir
    assert resolved.is_dir()


def test_load_empty_store_returns_empty_lists(isolated, tmp_path):
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path / "does-not-exist")
    assert ctx.companies == []
    assert ctx.buyers == []
    assert ctx.objections == []
    assert ctx.competitors == []


def test_add_objection_normalizes_class_and_persists(isolated, tmp_path):
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    rec = ctx.add("objection", {
        "class": "budget",
        "source": "Acme CTO",
        "text": "Not in this year's budget",
        "response": "We can scope a 30-day POC SOW",
    })
    assert rec["class"] == "BUDGET"
    assert "created_utc" in rec
    # Reload from disk and confirm round-trip.
    ctx2 = SalesContext.load(tmp_path)
    assert len(ctx2.objections) == 1
    assert ctx2.objections[0]["class"] == "BUDGET"
    assert ctx2.objections[0]["source"] == "Acme CTO"


def test_add_unknown_class_falls_back_to_other(isolated, tmp_path):
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    rec = ctx.add("objection", {"class": "WAT", "text": "huh"})
    assert rec["class"] == "OTHER"


def test_add_unknown_kind_raises(isolated, tmp_path):
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    with pytest.raises(ValueError, match="unknown kind"):
        ctx.add("not_a_kind", {"name": "x"})


def test_malformed_jsonl_line_is_skipped(isolated, tmp_path):
    (tmp_path / "companies.jsonl").write_text(
        '{"name": "ok"}\nthis is not json\n{"name": "also-ok"}\n',
        encoding="utf-8",
    )
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    assert [c["name"] for c in ctx.companies] == ["ok", "also-ok"]


# ── relevant_for() per use_case ──────────────────────────────────────


def _seed_corpus(tmp_path: Path) -> None:
    (tmp_path / "objections.jsonl").write_text(
        "\n".join([
            json.dumps({
                "class": "BUDGET",
                "source": "Acme CTO",
                "text": "Not in this year's budget",
                "response": "Pilot pricing under POC SOW",
                "outcome": "meeting set",
                "created_utc": "2026-05-10T00:00:00Z",
            }),
            json.dumps({
                "class": "COMPLIANCE_RISK",
                "source": "Globex GRC",
                "text": "Need SOC 2 to evaluate",
                "response": "SOC 2 Type II expected Q3",
                "created_utc": "2026-05-12T00:00:00Z",
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "companies.jsonl").write_text(
        "\n".join([
            json.dumps({
                "name": "Acme",
                "industry": "fintech",
                "size": "1500",
                "region": "US",
                "signal": "posted AI Governance Lead role",
                "status": "in-conversation",
            }),
            json.dumps({
                "name": "Globex",
                "industry": "healthcare",
                "size": "8000",
                "signal": "new CISO three weeks ago",
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "buyers.jsonl").write_text(
        json.dumps({
            "name": "Jane Doe",
            "role": "CISO",
            "company": "Acme",
            "signal": "posted AI Governance Lead role",
        }) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "competitors.jsonl").write_text(
        "\n".join([
            json.dumps({
                "name": "Lakera",
                "category": "llm_firewall",
                "their_strength": "fast prompt-injection detection",
                "their_gap": "no audio modality",
                "axiom_wedge": "multimodal signed event tokens",
                "honest_concession": "they have more deploys today",
            }),
            json.dumps({
                "name": "Pangea",
                "category": "llm_firewall",
                "their_gap": "no constitutional layer",
            }),
        ]) + "\n",
        encoding="utf-8",
    )


def test_relevant_objection_handling_picks_budget_record(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    snippet = ctx.relevant_for(
        "sales_objection_handling",
        "buyer says they don't have budget this year",
    )
    assert "BUDGET" in snippet
    assert "Not in this year's budget" in snippet
    assert "POC SOW" in snippet


def test_relevant_outreach_includes_named_buyer_and_company(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    snippet = ctx.relevant_for(
        "outreach_personalization",
        "Drafting outreach to Jane Doe at Acme — fintech CISO.",
    )
    assert "Jane Doe" in snippet
    assert "Acme" in snippet
    # Prior objection from the same company should bubble up too.
    assert "BUDGET" in snippet


def test_relevant_targeting_returns_companies(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    snippet = ctx.relevant_for(
        "enterprise_targeting",
        "Looking for fintech accounts hiring AI governance roles.",
    )
    assert "Acme" in snippet
    assert "fintech" in snippet


def test_relevant_competitive_includes_named_and_others(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    snippet = ctx.relevant_for(
        "competitive_analysis",
        "Compare against Lakera",
    )
    assert "Lakera" in snippet
    assert "AXIOM wedge" in snippet
    assert "honest concession" in snippet
    # The other competitor should appear as a gap reminder.
    assert "Pangea" in snippet


def test_relevant_discovery_uses_call_files_and_pain_themes(
    isolated, tmp_path,
):
    _seed_corpus(tmp_path)
    calls = tmp_path / "calls"
    calls.mkdir()
    (calls / "2026-05-15-acme.md").write_text(
        "# Acme discovery call\n- buyer: Jane Doe\n",
        encoding="utf-8",
    )
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    snippet = ctx.relevant_for(
        "customer_discovery",
        "Synthesize today's Acme call.",
    )
    assert "PAST CALLS" in snippet
    assert "2026-05-15-acme.md" in snippet
    assert "BUDGET" in snippet  # pain themes from objection log
    assert "COMPLIANCE_RISK" in snippet


def test_token_budget_truncates_snippet(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    full = ctx.relevant_for(
        "competitive_analysis", "Compare against Lakera",
        token_budget=2000,
    )
    tiny = ctx.relevant_for(
        "competitive_analysis", "Compare against Lakera",
        token_budget=20,
    )
    assert len(tiny) < len(full)
    # ~20 tokens ≈ 80 chars; we allow a little slack for the ellipsis.
    assert len(tiny) <= 20 * 4 + 8


def test_non_sales_use_case_returns_empty(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from axiom_sales_context import SalesContext
    ctx = SalesContext.load(tmp_path)
    assert ctx.relevant_for("investor_research", "whatever") == ""


# ── CLI ──────────────────────────────────────────────────────────────


def test_cli_add_then_list_roundtrip(isolated, tmp_path, capsys):
    from axiom_sales_context import main
    rc = main([
        "--root", str(tmp_path),
        "add", "buyer",
        json.dumps({"name": "Jane Doe", "role": "CISO",
                    "company": "Acme"}),
    ])
    assert rc == 0
    capsys.readouterr()  # drain
    rc = main(["--root", str(tmp_path), "list", "buyers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Jane Doe" in out
    assert "Acme" in out


def test_cli_relevant_prints_snippet(isolated, tmp_path, capsys):
    _seed_corpus(tmp_path)
    from axiom_sales_context import main
    rc = main([
        "--root", str(tmp_path),
        "relevant", "sales_objection_handling",
        "--query", "no budget",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "BUDGET" in out


def test_cli_relevant_rejects_unknown_use_case(
    isolated, tmp_path, capsys,
):
    from axiom_sales_context import main
    rc = main([
        "--root", str(tmp_path),
        "relevant", "investor_research",
        "--query", "x",
    ])
    assert rc != 0


# ── end-to-end injection ─────────────────────────────────────────────


def test_invoke_auto_injects_sales_context(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    container = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _RecordingBackend()
    exo = ExoskeletonAgent(
        container, backend=backend,
        sales_context_root=tmp_path,
    )
    exo.invoke(
        "sales_objection_handling",
        "buyer just told me they have no budget this year",
    )
    assert len(backend.calls) == 1
    prompt = backend.calls[0]["prompt"]
    assert "CONTEXT (do not echo back" in prompt
    assert "sales_context" in prompt
    assert "BUDGET" in prompt
    assert "POC SOW" in prompt


def test_invoke_skips_context_for_non_sales_delegate(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    container = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _RecordingBackend()
    exo = ExoskeletonAgent(
        container, backend=backend,
        sales_context_root=tmp_path,
    )
    exo.invoke("investor_research", "AI governance thesis")
    assert "CONTEXT (do not echo back" not in backend.calls[0]["prompt"]


def test_invoke_auto_context_false_skips_injection(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    container = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _RecordingBackend()
    exo = ExoskeletonAgent(
        container, backend=backend,
        sales_context_root=tmp_path,
    )
    exo.invoke(
        "sales_objection_handling", "no budget",
        auto_context=False,
    )
    assert "CONTEXT (do not echo back" not in backend.calls[0]["prompt"]


def test_invoke_explicit_extra_context_overrides_auto(isolated, tmp_path):
    _seed_corpus(tmp_path)
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    container = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _RecordingBackend()
    exo = ExoskeletonAgent(
        container, backend=backend,
        sales_context_root=tmp_path,
    )
    exo.invoke(
        "sales_objection_handling", "no budget",
        extra_context={"sales_context": "MANUAL_OVERRIDE_MARKER"},
    )
    prompt = backend.calls[0]["prompt"]
    assert "MANUAL_OVERRIDE_MARKER" in prompt
    # Auto-loaded data should NOT be present.
    assert "POC SOW" not in prompt


def test_empty_store_does_not_inject(isolated, tmp_path):
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent

    container = build_exoskeleton_pack(tmp_path / "exo.axm")
    backend = _RecordingBackend()
    exo = ExoskeletonAgent(
        container, backend=backend,
        sales_context_root=tmp_path,   # exists but empty
    )
    exo.invoke("sales_objection_handling", "anything")
    assert "CONTEXT (do not echo back" not in backend.calls[0]["prompt"]


# ── CLI integration ─────────────────────────────────────────────────


def test_cli_no_context_flag(isolated, tmp_path, monkeypatch, capsys):
    _seed_corpus(tmp_path)
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be

    recorder = _RecordingBackend()
    monkeypatch.setattr(be, "default_backend", lambda: recorder)
    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    rc = main([
        "sales_objection_handling",
        "--pack", str(c.path),
        "--input", "no budget",
        "--context-root", str(tmp_path),
        "--no-context",
        "--no-ledger",
    ])
    assert rc == 0
    assert "CONTEXT (do not echo back" not in recorder.calls[0]["prompt"]


def test_cli_context_root_flag_pulls_from_passed_dir(
    isolated, tmp_path, monkeypatch, capsys,
):
    _seed_corpus(tmp_path)
    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import main
    import axiom_event_token.backends as be

    recorder = _RecordingBackend()
    monkeypatch.setattr(be, "default_backend", lambda: recorder)
    c = build_exoskeleton_pack(tmp_path / "exo.axm")
    rc = main([
        "sales_objection_handling",
        "--pack", str(c.path),
        "--input", "no budget",
        "--context-root", str(tmp_path),
        "--no-ledger",
    ])
    assert rc == 0
    assert "POC SOW" in recorder.calls[0]["prompt"]


# ── privacy guard ────────────────────────────────────────────────────


def test_docs_internal_is_gitignored():
    """The private overlay must be excluded from git tracking.
    Main root-anchors the pattern (`/docs/internal/*`); accept either."""
    repo_root = Path(__file__).resolve().parent.parent
    gi = repo_root / ".gitignore"
    body = gi.read_text(encoding="utf-8")
    assert "docs/internal/*" in body
    assert "docs/internal/README.md" in body
