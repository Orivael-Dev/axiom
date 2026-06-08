"""Dedicated autonomous-agent workspace backend (aui.autonomous)."""
import json
import time

import pytest

from aui import autonomous


@pytest.fixture(autouse=True)
def _clean_jobs(monkeypatch):
    autonomous._JOBS.clear()
    monkeypatch.delenv("AXIOM_REPO", raising=False)   # default: unreachable
    yield
    autonomous._JOBS.clear()


def test_available_false_without_repo():
    assert autonomous.available()["available"] is False


def test_available_true_when_repo_present(monkeypatch, tmp_path):
    (tmp_path / "axiom_autonomous_agent.py").write_text("# stub", encoding="utf-8")
    monkeypatch.setenv("AXIOM_REPO", str(tmp_path))
    a = autonomous.available()
    assert a["available"] is True and a["repo"] == str(tmp_path)


def test_submit_fails_soft_when_unavailable():
    r = autonomous.submit("do a thing")
    assert r["ok"] is False and r["reason"] == "autonomous_unavailable"


def test_submit_rejects_empty_task():
    assert autonomous.submit("   ")["reason"] == "empty_task"


def test_parse_result_whole_and_last_line():
    payload = {"run_id": "auto_x", "success": True, "steps": 3}
    assert autonomous._parse_result(json.dumps(payload))["steps"] == 3
    noisy = f"booting agent...\nstep 1\n{json.dumps(payload)}\n"
    assert autonomous._parse_result(noisy)["run_id"] == "auto_x"
    assert autonomous._parse_result("no json here") is None


def _seed_job(run_id="job_t"):
    autonomous._JOBS[run_id] = {
        "id": run_id, "task": "t", "status": "running", "workdir": "/tmp/x",
        "started_at": time.time(), "finished_at": None, "result": None, "error": ""}
    return run_id


def test_run_job_success(monkeypatch, tmp_path):
    rid = _seed_job()
    result = {"run_id": "auto_1", "success": True, "steps": 2,
              "plan": {"subgoals": [{"description": "write file", "done": True}]}}
    monkeypatch.setattr(autonomous, "_launch",
                        lambda cmd, cwd, timeout: (0, json.dumps(result), ""))
    autonomous._run_job(rid, "/repo", "t", tmp_path,
                        budget_steps=10, wall_seconds=60, sandbox="local")
    j = autonomous.get_run(rid)
    assert j["status"] == "done" and j["result"]["steps"] == 2 and j["finished_at"]


def test_run_job_blocked_by_governance(monkeypatch, tmp_path):
    rid = _seed_job()
    result = {"run_id": "auto_2", "success": False,
              "aborted_reason": "intent_gate: HARM"}
    monkeypatch.setattr(autonomous, "_launch",
                        lambda cmd, cwd, timeout: (2, json.dumps(result), ""))
    autonomous._run_job(rid, "/repo", "t", tmp_path,
                        budget_steps=10, wall_seconds=60, sandbox="local")
    assert autonomous.get_run(rid)["status"] == "blocked"


def test_run_job_unparseable_is_error(monkeypatch, tmp_path):
    rid = _seed_job()
    monkeypatch.setattr(autonomous, "_launch",
                        lambda cmd, cwd, timeout: (1, "boom", "traceback: kaput"))
    autonomous._run_job(rid, "/repo", "t", tmp_path,
                        budget_steps=10, wall_seconds=60, sandbox="local")
    j = autonomous.get_run(rid)
    assert j["status"] == "error" and "kaput" in j["error"]


def test_submit_launches_and_completes(monkeypatch, tmp_path):
    (tmp_path / "axiom_autonomous_agent.py").write_text("# stub", encoding="utf-8")
    monkeypatch.setenv("AXIOM_REPO", str(tmp_path))
    result = {"run_id": "auto_3", "success": True, "steps": 1, "plan": {"subgoals": []}}
    monkeypatch.setattr(autonomous, "_launch",
                        lambda cmd, cwd, timeout: (0, json.dumps(result), ""))
    r = autonomous.submit("write a file", sandbox="local")
    assert r["ok"] is True and r["status"] == "running"
    rid = r["run_id"]
    for _ in range(50):                       # the stubbed launch returns instantly
        if autonomous.get_run(rid)["status"] != "running":
            break
        time.sleep(0.02)
    assert autonomous.get_run(rid)["status"] == "done"
