"""Tests for axiom_ollama_coder — the LLM-backed dev agent.

Hermetic: every test uses StubLLMClient or a callable LLM so no
network call is ever made. The dev agent's review pipeline is
NOT mocked — it actually runs the 4 layers — but the input is
deterministic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", "test" + "0" * 60)
    for mod in list(sys.modules):
        if mod.startswith(("axiom_signing", "axiom_dev_agent",
                            "axiom_research", "axiom_ollama_coder")):
            sys.modules.pop(mod, None)
    yield


# ─── _strip_codefence ───────────────────────────────────────────────────


def test_strip_codefence_removes_backticks(isolated):
    from axiom_ollama_coder import _strip_codefence
    assert _strip_codefence("```python\ndef f(): pass\n```") == "def f(): pass"
    assert _strip_codefence("```py\nx = 1\n```") == "x = 1"
    assert _strip_codefence("```\nhello\n```") == "hello"


def test_strip_codefence_leaves_unfenced_alone(isolated):
    from axiom_ollama_coder import _strip_codefence
    src = "def helper():\n    return 42\n"
    assert _strip_codefence(src) == src


# ─── _build_prompt ──────────────────────────────────────────────────────


def test_build_prompt_includes_task_and_file_contents(isolated):
    from axiom_ollama_coder import _build_prompt
    out = _build_prompt(
        description="add a helper",
        artifact_path="x.py",
        file_text="def existing(): pass",
        prior_feedback="",
    )
    assert "add a helper" in out
    assert "x.py" in out
    assert "def existing(): pass" in out
    assert "Feedback from the previous attempt" not in out


def test_build_prompt_includes_feedback_on_retry(isolated):
    from axiom_ollama_coder import _build_prompt
    out = _build_prompt(
        description="x", artifact_path="x.py", file_text="",
        prior_feedback="reflex refusals: syntax error",
    )
    assert "Feedback from the previous attempt" in out
    assert "syntax error" in out


# ─── OllamaCoder.code() — happy paths ───────────────────────────────────


def test_clean_code_from_untrained_agent_softens(isolated):
    """A fresh AxiomDevAgentV2 has competence 0 on every task_class,
    so its first review of any clean novel diff SOFTENs (forecast
    0.30 < min_safe 0.80). The coder surfaces the code with a
    warning rather than treating SOFTEN as a hard rejection — this
    is the realistic on-device experience until the agent builds a
    track record."""
    from axiom_ollama_coder import OllamaCoder
    from axiom_research.synthesize import StubLLMClient
    clean = (
        'def sha256_hex(text: str) -> str:\n'
        '    """Return the SHA-256 hex digest of a UTF-8 string."""\n'
        '    import hashlib\n'
        '    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n'
    )
    coder = OllamaCoder(StubLLMClient(response=clean), max_retries=0)
    result = coder.code("sha256 helper", "axiom_hash_utils.py")
    assert result.merged is False
    assert result.accepted_with_warnings is True
    assert result.final.final_verdict == "SOFTEN_REQUESTED"
    assert result.accepted_code is not None
    assert "sha256_hex" in result.accepted_code
    assert len(result.attempts) == 1
    # Reviewer should surface concrete advice for the softening
    assert len(result.final.review.softening_advice) > 0


def test_trained_agent_merges_clean_code(isolated):
    """Once the agent has built up competence on a task_class via
    on_outcome(), its forecast crosses min_safe and the same diff
    that previously SOFTENed now MERGES. This is the curriculum
    learning loop the AXIOM thesis depends on."""
    from axiom_dev_agent_v2 import AxiomDevAgentV2
    from axiom_ollama_coder import OllamaCoder
    from axiom_research.synthesize import StubLLMClient
    clean = (
        'def shout(text: str) -> str:\n'
        '    """Return text upper-cased."""\n'
        '    return text.upper()\n'
    )
    agent = AxiomDevAgentV2()
    # Hand-train: 10 successful CIs on "FEATURE" → competence ≈ 0.5+
    for _ in range(10):
        agent.reviewer.on_outcome("FEATURE", ci_passed=True)
    coder = OllamaCoder(StubLLMClient(response=clean),
                        agent=agent, max_retries=0)
    result = coder.code("shout helper", "axiom_strings.py", task_class="FEATURE")
    assert result.merged is True, (
        f"trained agent should MERGE clean code; got {result.final.final_verdict}"
    )
    assert result.final.final_verdict == "MERGED"
    assert "shout" in result.accepted_code


# ─── OllamaCoder.code() — reflex refusal ────────────────────────────────


def test_coder_reflex_refuses_os_system(isolated):
    from axiom_ollama_coder import OllamaCoder
    from axiom_research.synthesize import StubLLMClient
    bad = (
        'def run_it(cmd: str) -> None:\n'
        '    """Run a shell command."""\n'
        '    import os\n'
        '    os.system(cmd)\n'
    )
    coder = OllamaCoder(StubLLMClient(response=bad), max_retries=0)
    result = coder.code("shell runner", "axiom_shell.py")
    assert result.merged is False
    assert result.final.final_verdict == "REFLEX_REFUSED"
    assert result.accepted_code is None
    assert any("os.system" in r or "shell" in r.lower()
               for r in result.final.reflex.reasons)


# ─── Retry feedback loop ────────────────────────────────────────────────


def test_coder_retries_and_feeds_back_reasons(isolated):
    """First attempt is REFLEX_REFUSED, second is clean — the LLM
    should see the previous-attempt reasons in the retry prompt,
    and the coder should accept the second attempt (with warnings
    for an untrained agent, or merged for a trained one)."""
    from axiom_ollama_coder import OllamaCoder
    bad = 'def run(c):\n    """."""\n    import os\n    os.system(c)\n'
    good = ('def safe_run(text: str) -> str:\n'
            '    """Return text uppercased."""\n'
            '    return text.upper()\n')

    seen_prompts: list[str] = []

    class TwoAttemptLLM:
        name = "test/two-attempt"
        def __init__(self): self.calls = 0
        def generate(self, prompt, *, max_tokens=1024):
            seen_prompts.append(prompt)
            self.calls += 1
            return bad if self.calls == 1 else good

    coder = OllamaCoder(TwoAttemptLLM(), max_retries=1)
    result = coder.code("safe helper", "axiom_text.py")

    assert len(result.attempts) == 2
    assert result.attempts[0].outcome.final_verdict == "REFLEX_REFUSED"
    # Second attempt clears reflex; on a fresh agent this is
    # SOFTEN_REQUESTED, which the coder surfaces with warnings.
    assert result.attempts[1].outcome.final_verdict == "SOFTEN_REQUESTED"
    assert result.accepted_with_warnings is True
    assert "safe_run" in result.accepted_code

    # Second prompt must reference the previous refusal
    assert "Feedback from the previous attempt" in seen_prompts[1]
    assert "reflex" in seen_prompts[1].lower()


def test_coder_max_retries_exhausted_returns_last_outcome(isolated):
    """If every attempt is rejected, coder returns the last one with
    merged=False — does NOT raise."""
    from axiom_ollama_coder import OllamaCoder
    from axiom_research.synthesize import StubLLMClient
    bad = 'def run(c):\n    """."""\n    import os\n    os.system(c)\n'
    coder = OllamaCoder(StubLLMClient(response=bad), max_retries=2)
    result = coder.code("shell runner", "axiom_shell.py")
    assert result.merged is False
    assert len(result.attempts) == 3  # max_retries=2 means 1 initial + 2 retries
    assert all(a.outcome.final_verdict == "REFLEX_REFUSED" for a in result.attempts)
    assert result.accepted_code is None


# ─── _read_safely doesn't crash on missing files ────────────────────────


def test_read_safely_returns_empty_for_missing_path(isolated):
    from axiom_ollama_coder import _read_safely
    assert _read_safely("/no/such/file/anywhere.py") == ""


def test_read_safely_reads_existing_file(isolated, tmp_path):
    from axiom_ollama_coder import _read_safely
    p = tmp_path / "x.py"
    p.write_text("hello", encoding="utf-8")
    assert _read_safely(str(p)) == "hello"


# ─── CLI entry point ────────────────────────────────────────────────────


def test_cli_requires_master_key(isolated, tmp_path):
    """Run via subprocess because AXIOM_MASTER_KEY is required at
    import time (axiom_signing raises on import) — we cannot test
    this in-process after the module has been imported once."""
    import subprocess
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("AXIOM_MASTER_KEY", None)
    r = subprocess.run(
        [sys.executable, str(repo_root / "axiom_ollama_coder.py"),
         "--backend", "stub", "--task", "x", "--path", "y.py"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert r.returncode != 0
    # The error mentions the missing key in either stderr or stdout
    combined = r.stderr + r.stdout
    assert "AXIOM_MASTER_KEY" in combined


def test_cli_one_shot_with_stub_backend(isolated, capsys):
    from axiom_ollama_coder import main
    # StubLLMClient default response is non-Python prose — REFLEX_REFUSED
    # → exit 2 per the documented contract:
    #   0 = MERGED, 1 = accepted with warnings, 2 = rejected
    rc = main(["axiom_ollama_coder.py", "--backend", "stub",
               "--task", "anything", "--path", "x.py"])
    out = capsys.readouterr().out
    assert "Final verdict" in out
    assert rc == 2


def test_cli_requires_task_and_path_outside_repl(isolated):
    from axiom_ollama_coder import main
    with pytest.raises(SystemExit) as exc:
        main(["axiom_ollama_coder.py", "--backend", "stub"])
    assert "task" in str(exc.value).lower() or "repl" in str(exc.value).lower()
