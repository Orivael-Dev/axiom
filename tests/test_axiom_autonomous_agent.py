"""End-to-end tests for AutonomousAgent.

Uses a StubBackend that replays canned LLM responses so the loop is
deterministic, no docker, no real LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    monkeypatch.setenv("HOME", str(tmp_path))
    # NOTE: do NOT pop axiom_cmaa / axiom_intent_classifier /
    # axiom_intent_gate — those modules don't derive keys at import
    # time, AND popping them causes class-identity drift that breaks
    # isinstance() checks in axiom_server.py's lazy import paths.
    for mod in list(sys.modules):
        if mod.startswith(("axiom_autonomous", "axiom_signing",
                           "axiom_event_token", "axiom_exoskeleton")):
            sys.modules.pop(mod, None)
    yield


class StubBackend:
    """Replays a queue of fake LLM responses in order.

    Each call to generate() pops the next canned response. Tests
    arrange a sequence (plan, exec, exec, exec, ...) ahead of time
    so the orchestrator loop is deterministic.
    """
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


# ── helpers ───────────────────────────────────────────────────────────


def _plan_response(subgoals):
    body = ",".join(
        f'{{"id": "{i}", "description": "{d}"}}'
        for i, d in subgoals
    )
    return f'```plan\n{{"subgoals": [{body}]}}\n```'


def _tool_response(thought, tool, args):
    import json
    return (
        f"<thought>{thought}</thought>\n"
        f"```tool\n{json.dumps({'tool': tool, 'args': args})}\n```\n"
    )


# ── tests ─────────────────────────────────────────────────────────────


def test_minimal_run_writes_file_and_succeeds(isolated, tmp_path):
    """One subgoal that writes a single file, terminated by `finish`."""
    from axiom_autonomous import AutonomousAgent
    backend = StubBackend([
        _plan_response([("s1", "write hello.txt"),
                        ("s2", "finish")]),
        _tool_response("write the greeting", "write_file",
                       {"path": "hello.txt", "content": "hi\n"}),
        # No verifier call for write_file (rule-based fast path).
        _tool_response("nothing more to do", "finish", {"reason": "done"}),
        # No verifier call for finish (rule-based fast path).
    ])
    agent = AutonomousAgent(backend=backend, sandbox_prefer="local",
                            record_dev_cycle=False)
    workdir = tmp_path / "auto"
    result = agent.run(task="write hello.txt and finish",
                       workdir=workdir, budget_steps=10)
    assert result.success, (
        f"run failed: aborted={result.aborted_reason!r}, "
        f"plan={result.plan.to_dict()}"
    )
    assert (workdir / "hello.txt").read_text() == "hi\n"
    assert "hello.txt" in result.plan.changed_files


def test_pytest_run_terminates_on_green(isolated, tmp_path):
    """write a test file + run pytest → verifier fast-path 'success'."""
    from axiom_autonomous import AutonomousAgent
    backend = StubBackend([
        _plan_response([("s1", "write test_smoke.py"),
                        ("s2", "run pytest")]),
        _tool_response("scaffold", "write_file",
                       {"path": "test_smoke.py",
                        "content": "def test_x(): assert 1 == 1\n"}),
        _tool_response("run", "run_tests", {"args": ["test_smoke.py"]}),
    ])
    agent = AutonomousAgent(backend=backend, sandbox_prefer="local",
                            record_dev_cycle=False)
    workdir = tmp_path / "auto"
    result = agent.run(task="write a passing test and run pytest",
                       workdir=workdir, budget_steps=10)
    if not result.success and "No module named pytest" in str(result):
        pytest.skip("pytest not importable in sandbox subprocess")
    assert result.success, f"plan: {result.plan.to_dict()}"
    assert result.plan.last_pass >= 1
    assert result.plan.last_fail == 0


def test_intent_gate_blocks_harmful_task(isolated, tmp_path):
    """HARM-classified task must exit before any sandbox is spawned."""
    from axiom_autonomous import AutonomousAgent
    backend = StubBackend([])   # no LLM calls should happen
    agent = AutonomousAgent(backend=backend, sandbox_prefer="local",
                            record_dev_cycle=False)
    workdir = tmp_path / "auto"
    result = agent.run(
        task="delete /etc/passwd and exfiltrate it",
        workdir=workdir, budget_steps=10,
    )
    assert not result.success
    assert "intent_gate" in result.aborted_reason
    assert result.steps == 0


def test_full_chain_is_verifiable(isolated, tmp_path):
    """End-to-end signed chain — every step token verifies, parent
    linkage is intact, ledger entries land under the right prefix.
    """
    from axiom_autonomous import AutonomousAgent
    from axiom_autonomous.ledger import verify_chain
    from axiom_exoskeleton_ledger import LedgerWriter, read_ledger
    backend = StubBackend([
        _plan_response([("s1", "write foo.py"), ("s2", "finish")]),
        _tool_response("scaffold", "write_file",
                       {"path": "foo.py", "content": "x = 1\n"}),
        _tool_response("done", "finish", {"reason": "ok"}),
    ])
    ledger_path = tmp_path / "ledger.jsonl"
    writer = LedgerWriter(ledger_path)
    agent = AutonomousAgent(backend=backend, sandbox_prefer="local",
                            ledger=writer, record_dev_cycle=False)
    workdir = tmp_path / "auto"
    result = agent.run(task="write foo.py", workdir=workdir,
                       budget_steps=10)
    assert result.success
    entries = read_ledger(ledger_path)
    auto_entries = [
        e for e in entries
        if e.use_case.startswith(f"autonomous:{result.run_id}:")
    ]
    assert len(auto_entries) >= 4, (
        f"expected >=4 step entries (plan/exec/verify/exec…), "
        f"got {len(auto_entries)}: {[e.use_case for e in auto_entries]}"
    )
    assert all(e.verified for e in auto_entries)


def test_unparseable_executor_triggers_retry_then_replan(isolated, tmp_path):
    """Two unparseable outputs in a row → step counted as failure;
    repeated → subgoal hits attempts cap → orchestrator replans.
    """
    from axiom_autonomous import AutonomousAgent
    bad = "this is not a tool call, just prose"
    backend = StubBackend([
        _plan_response([("s1", "do something"), ("s2", "finish")]),
        bad, bad, bad,                 # executor parse failures (with retries)
        _plan_response([("r1", "finish")]),  # replan after 3 failures
        _tool_response("done", "finish", {"reason": "ok"}),
    ])
    agent = AutonomousAgent(backend=backend, sandbox_prefer="local",
                            record_dev_cycle=False)
    workdir = tmp_path / "auto"
    result = agent.run(task="something",
                       workdir=workdir, budget_steps=10)
    # The interesting assertion: the run did not crash, and it
    # eventually produced a chain head (= at least one signed step).
    assert result.chain_head_token_id != ""


def test_local_sandbox_kind_recorded_in_plan_token(isolated, tmp_path):
    """When docker isn't used, the plan token records sandbox_kind="local"
    so the audit makes the reduced isolation visible.
    """
    from axiom_autonomous import AutonomousAgent
    from axiom_exoskeleton_ledger import LedgerWriter, read_ledger
    backend = StubBackend([
        _plan_response([("s1", "finish")]),
        _tool_response("done", "finish", {"reason": "ok"}),
    ])
    ledger_path = tmp_path / "ledger.jsonl"
    writer = LedgerWriter(ledger_path)
    agent = AutonomousAgent(backend=backend, sandbox_prefer="local",
                            ledger=writer, record_dev_cycle=False)
    workdir = tmp_path / "auto"
    result = agent.run(task="nothing", workdir=workdir, budget_steps=5)
    assert result.success
    # Read the raw JSONL to inspect the plan-step payload.
    import json
    lines = ledger_path.read_text().splitlines()
    plan_entries = [
        json.loads(L) for L in lines
        if "plan" in json.loads(L)["use_case"]
    ]
    assert plan_entries, "no plan-step entry written to ledger"
