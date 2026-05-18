"""AXIOM Ollama Coder — dev agent + local LLM.

Composes:

  axiom_research.synthesize.OllamaClient   the LLM that proposes code
  axiom_dev_agent_v2.AxiomDevAgentV2       the 4-layer constitutional reviewer
                                           (reflex / reviewer / curriculum / examiner)

The LLM proposes a candidate Python snippet (or unified diff). The
constitutional pipeline reviews it and emits one of four final
verdicts:

  MERGED              CodeReflex + reviewer + CI all green; the
                      snippet is safe to apply.
  SOFTEN_REQUESTED    reviewer wants the diff scaled back.
  VETO                reviewer or CI rejected it.
  REFLEX_REFUSED      static checks (AST parse, forbidden patterns)
                      refused outright — never reaches the reviewer.

The coder loops on non-MERGED outcomes, feeding the previous round's
refusal reasons back into the prompt as guidance — up to `max_retries`.

CLI:

    AXIOM_MASTER_KEY=<hex> python3 axiom_ollama_coder.py \\
        --ollama-url   http://localhost:11434 \\
        --model        qwen2.5:1.5b \\
        --task         "function that returns the SHA-256 of a string" \\
        --path         axiom_hash_utils.py

Or REPL mode:

    AXIOM_MASTER_KEY=<hex> python3 axiom_ollama_coder.py --repl

The REPL persists the dev agent's curriculum across turns so the
reviewer's competence improves over a session — same shape as a
human developer learning from PR feedback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from axiom_dev_agent_v2 import (
    AxiomDevAgentV2, DevHandleOutcome, DevTask, ReflexResult,
    ReviewVerdict, TASK_CLASSES,
)
from axiom_research.synthesize import (
    ClaudeClient, LLMClient, OllamaClient, StubLLMClient,
)


# ─── Prompt shape ───────────────────────────────────────────────────────


CODER_SYSTEM = """You are a senior Python engineer working on the AXIOM codebase.

You will receive a description of a code task and the current contents
of the target file. Respond with PYTHON CODE ONLY — no backticks, no
markdown, no prose, no preamble. Write only the new symbol(s) the
task asks for; do not rewrite the whole file.

Rules the AXIOM constitutional reviewer enforces — your code MUST
satisfy them or it will be rejected:

  - No `os.system`, no `subprocess` with shell=True, no `eval`/`exec`.
  - No bare `except:` clauses.
  - No silent broad-exception swallowing.
  - All public functions and classes get docstrings.
  - All imports go at the top of the file.

If you receive prior-attempt feedback, address every cited reason.
"""


def _build_prompt(
    description: str,
    artifact_path: str,
    file_text: str,
    prior_feedback: str,
) -> str:
    parts = [
        CODER_SYSTEM,
        "",
        f"# Target file: {artifact_path}",
        "",
        "## Task",
        description,
        "",
    ]
    if file_text.strip():
        parts.append("## Current file contents")
        parts.append(file_text[:4000])
        parts.append("")
    if prior_feedback.strip():
        parts.append("## Feedback from the previous attempt")
        parts.append("Your last attempt was REJECTED for these reasons. "
                     "Address every one of them in this attempt:")
        parts.append(prior_feedback)
        parts.append("")
    parts.append("## Now write the new code (Python only, no markdown):")
    return "\n".join(parts)


_FENCE_RE = re.compile(r"^\s*```(?:python|py)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_codefence(text: str) -> str:
    """Strip ``` fences if the LLM ignored the no-markdown rule.

    Even instruction-tuned 1-3B models will sometimes fence their
    output — this is the cheap recovery instead of bouncing them
    through CodeReflex.
    """
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text


# ─── Coder ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CoderAttempt:
    """One generation+review round."""
    attempt:      int
    generated:    str
    outcome:      DevHandleOutcome


@dataclass(frozen=True)
class CoderResult:
    """Outcome of a code() call — may include multiple attempts.

    Three useful states:
      merged                  reviewer + CI both green; the code is
                              cleared without reservation.
      accepted_with_warnings  reflex passed + reviewer SOFTEN'd; the
                              code is structurally fine but the
                              agent wants more competence/citations.
                              We surface accepted_code anyway so a
                              human can inspect.
      rejected                VETO or REFLEX_REFUSED — nothing safe
                              to surface; only reasons.

    Why warnings matter: a fresh AxiomDevAgentV2 has competence 0 on
    every task_class, so its first review of any new class will
    SOFTEN. Treating that as a hard rejection would make the coder
    unusable until the agent had been "trained" first. The dev-agent
    curriculum updates competence as outcomes come in — so over time
    the same task moves from SOFTEN to PASS.
    """
    attempts:                Tuple[CoderAttempt, ...]
    final:                   DevHandleOutcome
    merged:                  bool
    accepted_with_warnings:  bool
    accepted_code:           Optional[str]


class OllamaCoder:
    """Wraps a 4-layer dev agent with an LLM-backed code proposer.

    The agent's reflex / reviewer / examiner are stateless across
    handle_task() calls EXCEPT for the curriculum, which is part of
    the reviewer — its `competence` score moves with outcomes. We
    deliberately reuse the same AxiomDevAgentV2 instance across the
    coder's retry loop so the curriculum sees every attempt.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        agent: Optional[AxiomDevAgentV2] = None,
        max_retries: int = 1,
    ) -> None:
        self.llm = llm
        self.agent = agent or AxiomDevAgentV2()
        self.max_retries = max_retries

    def code(
        self,
        description: str,
        artifact_path: str,
        *,
        task_class: str = "FEATURE",
    ) -> CoderResult:
        if task_class not in TASK_CLASSES:
            raise ValueError(
                f"task_class={task_class!r} not in {TASK_CLASSES}"
            )
        file_text = _read_safely(artifact_path)
        feedback = ""
        attempts: list[CoderAttempt] = []
        for i in range(self.max_retries + 1):
            prompt = _build_prompt(description, artifact_path, file_text, feedback)
            generated = self.llm.generate(prompt, max_tokens=1024).strip()
            generated = _strip_codefence(generated)
            task = DevTask(
                id=f"coder_{uuid.uuid4().hex[:10]}",
                description=description,
                task_class=task_class,
                artifact_path=artifact_path,
                proposed_diff=generated,
                cited_patterns=(),
            )
            outcome = self.agent.handle_task(task)
            attempts.append(CoderAttempt(
                attempt=i + 1, generated=generated, outcome=outcome,
            ))
            # Stop retrying once we've reached MERGED or SOFTEN_REQUESTED —
            # both clear reflex, so the code is structurally fine. Keep
            # going only when we got VETO or REFLEX_REFUSED.
            if outcome.final_verdict in ("MERGED", "SOFTEN_REQUESTED"):
                break
            feedback = _outcome_to_feedback(outcome)
        final = attempts[-1].outcome
        merged = final.final_verdict == "MERGED"
        accepted_with_warnings = final.final_verdict == "SOFTEN_REQUESTED"
        return CoderResult(
            attempts=tuple(attempts),
            final=final,
            merged=merged,
            accepted_with_warnings=accepted_with_warnings,
            accepted_code=(
                attempts[-1].generated
                if (merged or accepted_with_warnings) else None
            ),
        )


def _read_safely(path: str) -> str:
    try:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8")
    except OSError:
        pass
    return ""


def _outcome_to_feedback(outcome: DevHandleOutcome) -> str:
    """Render the agent's rejection reasons as feedback for the next prompt."""
    bits = [f"verdict: {outcome.final_verdict}"]
    if outcome.reflex and not outcome.reflex.ok:
        bits.append("reflex refusals: " + "; ".join(outcome.reflex.reasons))
    if outcome.review:
        bits.append(f"reviewer verdict: {outcome.review.verdict}")
        if outcome.review.reasons:
            bits.append("reviewer reasons: " + "; ".join(outcome.review.reasons))
    if outcome.ci:
        if outcome.ci.checks_failed > 0:
            bits.append(f"ci: {outcome.ci.checks_failed} check(s) failed")
    return " | ".join(bits)


# ─── CLI ────────────────────────────────────────────────────────────────


def _build_llm(args: argparse.Namespace) -> LLMClient:
    if args.backend == "stub":
        return StubLLMClient()
    if args.backend == "claude":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY not set — required for --backend claude.")
        return ClaudeClient(model=args.model or "claude-haiku-4-5-20251001")
    # default: ollama
    return OllamaClient(
        model=args.model or "qwen2.5:1.5b",
        host=args.ollama_url,
        temperature=args.temperature,
        timeout_s=args.timeout,
    )


def _print_outcome(result: CoderResult, *, verbose: bool) -> None:
    print()
    for a in result.attempts:
        print(f"── attempt {a.attempt}  →  {a.outcome.final_verdict}")
        if verbose or a.outcome.final_verdict != "MERGED":
            print(f"   reflex.ok={a.outcome.reflex.ok}  "
                  f"review={a.outcome.review.verdict if a.outcome.review else '-'}  "
                  f"ci_failed={a.outcome.ci.checks_failed if a.outcome.ci else '-'}")
            if not a.outcome.reflex.ok:
                print(f"   reflex reasons: {'; '.join(a.outcome.reflex.reasons)}")
        if verbose:
            print("   ─── generated ─────────")
            for line in a.generated.splitlines()[:60]:
                print(f"   | {line}")
            print("   ───────────────────────")
    print()
    print(f"Final verdict: {result.final.final_verdict}")
    if result.merged or result.accepted_with_warnings:
        banner = "── accepted code ──────"
        if result.accepted_with_warnings:
            banner = "── accepted with warnings — reviewer wants softening ──"
            print()
            print("Softening advice:")
            for a in (result.final.review.softening_advice if result.final.review else ()):
                print(f"  - {a}")
        print()
        print(banner)
        print(result.accepted_code)
        print("───────────────────────")
    else:
        print("(no code accepted — final attempt was rejected)")


def _repl(coder: OllamaCoder, *, verbose: bool) -> None:
    print("AXIOM Ollama Coder — REPL")
    print(f"  LLM: {coder.llm.name}")
    print("  Commands: code <path> <description>  |  status  |  quit")
    print()
    while True:
        try:
            line = input("coder> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            return
        if line == "status":
            print(json.dumps(coder.agent.status(), indent=2, default=str))
            continue
        if line.startswith("code "):
            rest = line[5:].strip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                print("usage: code <path> <description>")
                continue
            path, desc = parts
            result = coder.code(desc, path)
            _print_outcome(result, verbose=verbose)
            continue
        print(f"unknown command: {line}")


def build_parser() -> argparse.ArgumentParser:
    """Built separately from main() so tests can assert on the env-var
    fallback contract without going through main()'s side effects."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--backend", choices=["ollama", "claude", "stub"],
                   default=os.environ.get("AXIOM_CODER_BACKEND", "ollama"),
                   help="LLM backend. Default: $AXIOM_CODER_BACKEND or ollama.")
    p.add_argument("--ollama-url",
                   default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                   help="Ollama host URL. Default: $OLLAMA_URL or "
                        "http://localhost:11434.")
    p.add_argument("--model",
                   default=os.environ.get("OLLAMA_MODEL", None),
                   help="Model name. Defaults: $OLLAMA_MODEL, else "
                        "qwen2.5:1.5b (ollama), "
                        "claude-haiku-4-5-20251001 (claude).")
    p.add_argument("--temperature", type=float, default=0.2,
                   help="LLM temperature. Default: 0.2 (deterministic-ish).")
    p.add_argument("--timeout", type=int, default=120,
                   help="LLM call timeout in seconds. Default: 120.")
    p.add_argument("--max-retries", type=int, default=1,
                   help="How many times to retry on non-MERGED verdicts. Default: 1.")
    p.add_argument("--task", help="Task description (one-shot mode).")
    p.add_argument("--path", help="Target file path (one-shot mode).")
    p.add_argument("--task-class", choices=list(TASK_CLASSES), default="FEATURE",
                   help="Dev-agent task class. Default: FEATURE.")
    p.add_argument("--repl", action="store_true",
                   help="Drop into interactive REPL instead of one-shot.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print every attempt's generated code.")
    return p


def main(argv: list[str]) -> int:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit("AXIOM_MASTER_KEY must be set — "
                 "the dev agent's signing chain derives from it.")
    args = build_parser().parse_args(argv[1:])

    llm = _build_llm(args)
    coder = OllamaCoder(llm, max_retries=args.max_retries)

    if args.repl:
        _repl(coder, verbose=args.verbose)
        return 0

    if not args.task or not args.path:
        sys.exit("--task and --path are required unless --repl is set.")

    result = coder.code(args.task, args.path, task_class=args.task_class)
    _print_outcome(result, verbose=args.verbose)
    # 0 = MERGED, 1 = accepted with warnings (SOFTEN), 2 = rejected
    if result.merged:
        return 0
    if result.accepted_with_warnings:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
