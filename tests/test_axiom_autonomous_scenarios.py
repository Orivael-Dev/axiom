"""Tests for the scenarios harness — library loading, criteria
checks, and runner end-to-end via a stub backend (no Docker, no
real LLM calls)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Same shape as the autonomous-agent test fixtures — a writable
    AXIOM_MASTER_KEY and a fresh per-test workdir."""
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    return tmp_path


# ─── library.py ───────────────────────────────────────────────────────


def test_library_loads_5_scenarios():
    from axiom_autonomous.scenarios import load_library
    lib = load_library()
    ids = [s.id for s in lib]
    assert len(lib) == 5
    assert sorted(ids) == sorted([
        "S001-cli-flag",
        "S002-fix-failing-test",
        "S003-implement-function",
        "S004-extract-helper",
        "S005-write-readme",
    ])


def test_library_every_seed_dir_exists():
    """Every scenario's declared seed must resolve to a real
    directory containing at least one .py file. Catches typos in
    library.jsonl before they fail at runtime."""
    from axiom_autonomous.scenarios import load_library, seeds_root
    for s in load_library():
        seed = seeds_root() / s.seed
        assert seed.is_dir(), f"missing seed dir for {s.id}: {seed}"
        py_files = list(seed.glob("*.py"))
        assert py_files, f"seed for {s.id} has no .py files"


def test_library_rejects_duplicate_ids(tmp_path):
    """Two records with the same id is a library-integrity bug, not
    a runtime ambiguity — must raise at load time."""
    from axiom_autonomous.scenarios.library import load_library
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({
            "id": "X", "title": "x", "task": "x", "seed": "s001_cli_flag",
            "criteria": {},
        }) + "\n" +
        json.dumps({
            "id": "X", "title": "y", "task": "y", "seed": "s001_cli_flag",
            "criteria": {},
        }) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_library(bad)


def test_filter_scenarios_unknown_id_raises():
    from axiom_autonomous.scenarios import load_library
    from axiom_autonomous.scenarios.library import filter_scenarios
    lib = load_library()
    with pytest.raises(ValueError, match="unknown scenario id"):
        filter_scenarios(lib, ["does-not-exist"])


def test_filter_scenarios_empty_returns_all():
    from axiom_autonomous.scenarios import load_library
    from axiom_autonomous.scenarios.library import filter_scenarios
    lib = load_library()
    assert filter_scenarios(lib, None) == lib
    assert filter_scenarios(lib, []) == lib


# ─── criteria.py ──────────────────────────────────────────────────────


def test_criteria_files_must_exist_detects_missing(tmp_path):
    """A scenario that demands README.md must FAIL when the agent
    doesn't write it."""
    from axiom_autonomous.scenarios.criteria import Criteria, check_criteria
    c = Criteria(files_must_exist=("README.md",))
    res = check_criteria(c, tmp_path)
    assert res.passed is False
    assert res.files["README.md"]["present"] is False


def test_criteria_must_not_modify_catches_changed_file(tmp_path):
    """sha256 mismatch on a must_not_modify file MUST fail criteria
    — this is the adversarial check that catches an agent that
    succeeded by editing files it wasn't supposed to touch."""
    from axiom_autonomous.scenarios.criteria import (
        Criteria, check_criteria, snapshot_seed_hashes,
    )
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    seed.mkdir(); work.mkdir()
    (seed / "README.md").write_text("original\n")
    (work / "README.md").write_text("MUTATED BY AGENT\n")

    c = Criteria(must_not_modify=("README.md",))
    hashes = snapshot_seed_hashes(seed, c)
    res = check_criteria(c, work, seed_hashes=hashes)
    assert res.passed is False
    assert res.unmodified["README.md"]["unchanged"] is False
    assert "sha256" in res.unmodified["README.md"]["reason"]


def test_criteria_unmodified_vacuously_passes_when_absent_in_seed(tmp_path):
    """If the file wasn't in the seed, 'must not modify' is vacuous."""
    from axiom_autonomous.scenarios.criteria import (
        Criteria, check_criteria, snapshot_seed_hashes,
    )
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    seed.mkdir(); work.mkdir()
    c = Criteria(must_not_modify=("README.md",))
    hashes = snapshot_seed_hashes(seed, c)
    res = check_criteria(c, work, seed_hashes=hashes)
    assert res.unmodified["README.md"]["unchanged"] is True


def test_criteria_shell_must_succeed_passes_on_exit_0(tmp_path):
    from axiom_autonomous.scenarios.criteria import Criteria, check_criteria
    c = Criteria(shell_must_succeed=("true",))
    res = check_criteria(c, tmp_path)
    assert res.passed is True
    assert res.shell["true"]["exit"] == 0


def test_criteria_shell_must_succeed_fails_on_nonzero(tmp_path):
    from axiom_autonomous.scenarios.criteria import Criteria, check_criteria
    c = Criteria(shell_must_succeed=("false",))
    res = check_criteria(c, tmp_path)
    assert res.passed is False
    assert res.shell["false"]["exit"] != 0


def test_criteria_tests_must_pass_runs_pytest(tmp_path):
    """End-to-end check that the pytest selector path actually runs
    pytest and reports counts. Seed a tiny passing test, then a
    failing one, and verify the boolean rolls up correctly."""
    from axiom_autonomous.scenarios.criteria import Criteria, check_criteria
    (tmp_path / "test_smoke.py").write_text(
        "def test_pass(): assert 1 == 1\n"
        "def test_fail(): assert 1 == 2\n",
        encoding="utf-8",
    )
    c_pass = Criteria(tests_must_pass=("test_pass",))
    res_pass = check_criteria(c_pass, tmp_path)
    assert res_pass.tests["test_pass"]["passed"] == 1
    assert res_pass.tests["test_pass"]["failed"] == 0
    assert res_pass.tests["test_pass"]["ok"] is True
    assert res_pass.passed is True

    c_fail = Criteria(tests_must_pass=("test_fail",))
    res_fail = check_criteria(c_fail, tmp_path)
    assert res_fail.tests["test_fail"]["ok"] is False
    assert res_fail.passed is False


# ─── runner.py end-to-end with a stub backend ─────────────────────────


class _StubBackend:
    """Replays canned LLM responses. Same shape as
    tests/test_axiom_autonomous_agent.py:StubBackend."""

    name = "stub"

    def __init__(self, responses):
        self._queue = list(responses)
        self.model = "stub-model"

    def generate(self, *, system, prompt, max_output_tokens, timeout_s=60.0):
        from axiom_event_token.backends import BackendResult
        if not self._queue:
            text = '```verdict\n{"kind": "abort", "reason": "stub drained"}\n```'
        else:
            text = self._queue.pop(0)
        return BackendResult(
            text=text, input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
            latency_ms=5, backend=self.name, model=self.model,
        )


def _plan_response(subgoals):
    body = ",".join(
        f'{{"id": "{i}", "description": "{d}"}}'
        for i, d in subgoals
    )
    return f'```plan\n{{"subgoals": [{body}]}}\n```'


def _tool_response(thought, tool, args):
    return (
        f"<thought>{thought}</thought>\n"
        f"```tool\n{json.dumps({'tool': tool, 'args': args})}\n```\n"
    )


def test_runner_processes_scenarios_and_signs_report(isolated, monkeypatch):
    """End-to-end: load library, run ONE scenario with a stub
    backend (just touches finish — doesn't actually solve), check
    report shape, signature, and criteria roll-up."""
    from axiom_autonomous.scenarios import load_library
    from axiom_autonomous.scenarios.library import filter_scenarios
    from axiom_autonomous.scenarios.runner import (
        run_scenarios, write_report,
    )

    lib = load_library()
    one = filter_scenarios(lib, ["S001-cli-flag"])

    # Stub: plan a single subgoal, immediately finish. Scenario will
    # naturally FAIL criteria (no --json flag added) — which is what
    # we want to test: the runner reports a failed scenario without
    # crashing, exits with summary intact.
    backend = _StubBackend([
        _plan_response([("s1", "finish immediately")]),
        _tool_response("nothing to do", "finish", {"reason": "stub"}),
    ])
    report = run_scenarios(
        one, sandbox_prefer="local", backend=backend,
        workdir_root=isolated / "scenarios",
    )
    assert len(report.scenarios) == 1
    row = report.scenarios[0]
    assert row.id == "S001-cli-flag"
    # Agent's own verdict (it "finished" cleanly via stub) is True.
    assert row.agent_success is True
    # External criteria check fails (no --json flag added).
    assert row.criteria_passed is False
    # Report writes + signs.
    out = isolated / "report.json"
    write_report(report, out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert "signature" in written
    assert written["signature"].startswith("hmac-sha256:")
    assert written["summary"]["total"] == 1
    assert written["summary"]["criteria_passed"] == 0


def test_runner_isolates_scenario_crashes(isolated, monkeypatch):
    """One scenario crashing (e.g. missing seed) must not kill the
    rest of the run — it lands as a failed row with a runner-error
    aborted_reason."""
    from axiom_autonomous.scenarios.library import Scenario
    from axiom_autonomous.scenarios.criteria import Criteria
    from axiom_autonomous.scenarios.runner import run_scenarios

    bad = Scenario(
        id="X-bad", title="missing seed", task="x",
        seed="does_not_exist_anywhere",
        criteria=Criteria(),
    )
    backend = _StubBackend([])
    report = run_scenarios(
        [bad], sandbox_prefer="local", backend=backend,
        workdir_root=isolated / "scenarios",
    )
    assert len(report.scenarios) == 1
    row = report.scenarios[0]
    assert row.agent_success is False
    assert row.criteria_passed is False
    assert "runner-error" in row.aborted_reason
    assert "FileNotFoundError" in row.aborted_reason


def test_cli_list_prints_library(capsys):
    """`python3 -m axiom_autonomous.scenarios list` prints one line
    per scenario."""
    from axiom_autonomous.scenarios.__main__ import main
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    for sid in (
        "S001-cli-flag", "S002-fix-failing-test",
        "S003-implement-function", "S004-extract-helper",
        "S005-write-readme",
    ):
        assert sid in out


def test_cli_list_json_format_round_trips(capsys):
    from axiom_autonomous.scenarios.__main__ import main
    rc = main(["list", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 5
    for entry in parsed:
        assert {"id", "title", "task", "seed", "criteria"}.issubset(entry)
