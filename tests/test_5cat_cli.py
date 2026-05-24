"""CLI tests — argparse parsing, spend-guard, run+verify round-trip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_5cat_benchmark.cli import (
    _build_parser, cmd_list_adapters, cmd_list_categories, main,
)


def test_parser_run_requires_models():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["run"])


def test_parser_run_minimal_invocation():
    p = _build_parser()
    args = p.parse_args(["run", "--models", "stub:demo"])
    assert args.cmd == "run"
    assert args.models == ["stub:demo"]
    # Defaults:
    assert args.trials == 5
    assert args.temperature == 0.0
    assert args.seed == 1729
    assert args.categories == [1, 2, 3, 4, 5]


def test_parser_categories_parses_comma_list():
    p = _build_parser()
    args = p.parse_args(["run", "--models", "stub:m", "--categories", "1,3,5"])
    assert args.categories == [1, 3, 5]


def test_parser_rejects_empty_categories():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["run", "--models", "stub:m", "--categories", ""])


def test_cli_run_writes_signed_results_with_stub(tmp_path, capsys):
    out = tmp_path / "results.json"
    rc = main([
        "run", "--models", "stub:demo",
        "--categories", "1", "--trials", "2",
        "--stub", "--output", str(out),
    ])
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    # Top-level keys benchmark_v1_0.py expects:
    for k in ("raw_avg", "axiom_avg", "improvement_pct",
              "axiom_wins", "total_tests", "criteria_met", "tests"):
        assert k in data
    # Meta block signed:
    assert "signature" in data["meta"]
    assert data["meta"]["signature"].startswith("hmac-sha256:")
    # Cat 1 trials emitted:
    assert data["total_tests"] == 2
    assert all(t["category"] == "EpistemicHumility" for t in data["tests"])


def test_cli_verify_round_trip(tmp_path):
    out = tmp_path / "results.json"
    rc = main([
        "run", "--models", "stub:demo",
        "--categories", "1", "--trials", "2",
        "--stub", "--output", str(out),
    ])
    assert rc == 0
    rc2 = main(["verify", "--input", str(out)])
    assert rc2 == 0


def test_cli_verify_rejects_tampered_results(tmp_path):
    out = tmp_path / "results.json"
    main([
        "run", "--models", "stub:demo",
        "--categories", "1", "--trials", "2",
        "--stub", "--output", str(out),
    ])
    data = json.loads(out.read_text(encoding="utf-8"))
    # Tamper with a trial score:
    data["tests"][0]["axiom_total"] = 999
    out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    rc = main(["verify", "--input", str(out)])
    assert rc == 1


def test_cli_spend_guard_blocks_non_stub_multi_trial(tmp_path, monkeypatch):
    """Non-stub adapter with --trials > 1 must refuse without
    --allow-spend. We don't actually need the SDK installed because
    build_adapter is never reached when the guard trips."""
    out = tmp_path / "r.json"
    with pytest.raises(SystemExit) as exc:
        main([
            "run", "--models", "anthropic:claude-foo",
            "--categories", "1", "--trials", "3",
            "--output", str(out),
        ])
    assert exc.value.code == 2


def test_cli_spend_guard_allows_stub_multi_trial(tmp_path):
    out = tmp_path / "r.json"
    rc = main([
        "run", "--models", "stub:demo",
        "--categories", "1", "--trials", "5",
        "--stub", "--output", str(out),
    ])
    assert rc == 0


def test_cli_list_categories_runs(capsys):
    import argparse as _ap
    rc = cmd_list_categories(_ap.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "EpistemicHumility" in out


def test_cli_list_adapters_runs(capsys):
    import argparse as _ap
    rc = cmd_list_adapters(_ap.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    for p in ("stub", "anthropic", "openai", "local"):
        assert p in out


def test_cli_report_md_render(tmp_path, capsys):
    out = tmp_path / "results.json"
    main([
        "run", "--models", "stub:demo",
        "--categories", "1", "--trials", "2",
        "--stub", "--output", str(out),
    ])
    capsys.readouterr()   # discard run output
    rc = main(["report", "--input", str(out), "--format", "md"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "axiom_commit" in text
    assert "per-category" in text
