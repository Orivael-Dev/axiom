"""
AXIOM HumanEval Benchmark
=========================
Runs HumanEval (164 Python coding problems) in two modes:
  1. Baseline   — no system prompt
  2. AXIOM      — worker.axiom constitutional system prompt

Reports pass@1, tokens-per-call, and tokens-per-correct-answer for
each mode, plus the improvement delta. The tokens-per-correct-answer
column answers the central question from docs/ANF_TOKEN_ECONOMICS.md
§10: does AXIOM produce more correct answers per token spent?

Usage (Nano / any machine with API key):
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python axiom_humaneval_run.py                    # all 164 problems
    python axiom_humaneval_run.py --problems 20      # quick smoke test
    python axiom_humaneval_run.py --model claude-haiku-4-5-20251001  # default
    python axiom_humaneval_run.py --baseline-only    # skip AXIOM run
    python axiom_humaneval_run.py --axiom-only       # skip baseline run

Requirements:
    pip install anthropic
    No GPU needed — all API calls. Works on Jetson Nano / ARM64.
"""

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# ── HumanEval dataset ──────────────────────────────────────────────────────────

HUMANEVAL_URL = (
    "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
)
DATASET_CACHE = Path(__file__).parent / "humaneval_cache.jsonl"


def _download_humaneval() -> list[dict]:
    if DATASET_CACHE.exists():
        problems = [json.loads(l) for l in DATASET_CACHE.read_text().splitlines() if l.strip()]
        return problems

    print("  Downloading HumanEval dataset...")
    with urllib.request.urlopen(HUMANEVAL_URL) as r:
        data = gzip.decompress(r.read()).decode()

    DATASET_CACHE.write_text(data)
    problems = [json.loads(l) for l in data.splitlines() if l.strip()]
    print(f"  Downloaded {len(problems)} problems → {DATASET_CACHE.name}\n")
    return problems


# ── AXIOM system prompt ────────────────────────────────────────────────────────

_AXIOM_SYSTEM_CODING = """\
You are a constitutional AI coding assistant governed by the AXIOM framework.

CONSTRAINTS
- Do not invent facts or fabricate API calls that do not exist
- Provide specific, working solutions
- If a problem is ambiguous, state your assumption explicitly
- Never produce broken or partial code without explaining what is missing
- Prioritize correctness over cleverness

RULES
- Return ONLY the function body implementation — no extra explanation unless asked
- Do not restate the function signature or docstring
- Implement exactly what the docstring specifies
- Handle edge cases the docstring implies (empty inputs, zero values, negative numbers)
- Use only Python standard library unless the problem explicitly requires otherwise
- Never add placeholder comments like "# TODO" or "# implement this"

OUTPUT
- Return the complete function implementation, indented correctly
- No markdown fences — raw Python code only
- No explanatory text before or after the code

FAILURE
- If the problem is genuinely unsolvable as stated, say so in one sentence
- Do not guess — return a minimal correct solution or state what is missing

CANNOT_MUTATE: correctness, function_signature, edge_case_handling
"""

_BASELINE_SYSTEM = ""  # no system prompt


# ── Claude API ─────────────────────────────────────────────────────────────────

def _call_claude(prompt: str, system: str, model: str, retries: int = 3) -> tuple[str, int, int]:
    """Return (response_text, input_tokens, output_tokens).
    On error returns ("# ERROR: ...", 0, 0)."""
    import anthropic
    client = anthropic.Anthropic()

    for attempt in range(retries):
        try:
            kwargs: dict = {
                "model":      model,
                "max_tokens": 512,
                "messages":   [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system

            msg = client.messages.create(**kwargs)
            return (
                msg.content[0].text.strip(),
                msg.usage.input_tokens,
                msg.usage.output_tokens,
            )
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"    [retry {attempt+1}] {e} — waiting {wait}s")
                time.sleep(wait)
            else:
                return (f"# ERROR: {e}", 0, 0)


# ── Code extraction + execution ────────────────────────────────────────────────

def _extract_code(response: str, prompt: str) -> str:
    """Pull the function body out of the model response."""
    # Strip markdown fences if model added them
    response = re.sub(r"```python\s*", "", response)
    response = re.sub(r"```\s*", "", response)

    # If the model returned the full function (with def line), use as-is
    if re.search(r"^\s*def ", response, re.MULTILINE):
        return response.strip()

    # Otherwise reconstruct: signature from prompt + returned body
    # Find the function signature in the prompt
    sig_match = re.search(r"(def .+?:)\s*\n", prompt, re.DOTALL)
    if sig_match:
        sig = sig_match.group(1)
        # Indent the body
        body_lines = []
        for line in response.splitlines():
            if line.strip():
                body_lines.append("    " + line if not line.startswith("    ") else line)
            else:
                body_lines.append("")
        return f"{sig}\n" + "\n".join(body_lines)

    return response.strip()


def _run_tests(completion: str, test_cases: str, entry_point: str, timeout: int = 10) -> bool:
    """Execute the generated function against HumanEval test cases."""
    code = f"""
{completion}

{test_cases}

check({entry_point})
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Benchmark runner ──────────────────────────────────────────────────────────

def _build_prompt(problem: dict) -> str:
    """Ask the model to complete the function."""
    return (
        f"Complete the following Python function. "
        f"Return ONLY the implementation — no explanation, no markdown:\n\n"
        f"{problem['prompt']}"
    )


def run_mode(
    problems: list[dict],
    system: str,
    model: str,
    mode_name: str,
    delay: float = 0.5,
) -> dict:
    passed = 0
    failed = 0
    errors = 0
    total_input_tokens = 0
    total_output_tokens = 0
    results = []

    print(f"\n  {'─'*56}")
    print(f"  Mode: {mode_name}   model: {model}   n={len(problems)}")
    print(f"  {'─'*56}")

    for i, problem in enumerate(problems):
        task_id    = problem["task_id"]
        prompt     = _build_prompt(problem)
        entry      = problem["entry_point"]
        tests      = problem["test"]

        response, in_tok, out_tok = _call_claude(prompt, system, model)
        total_input_tokens  += in_tok
        total_output_tokens += out_tok

        completion = _extract_code(response, problem["prompt"])
        ok         = _run_tests(completion, tests, entry)

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"  [{i+1:>3}/{len(problems)}] {task_id:<30} {status}  "
              f"({in_tok}+{out_tok} tok)")

        results.append({
            "task_id":       task_id,
            "passed":        ok,
            "input_tokens":  in_tok,
            "output_tokens": out_tok,
            "response":      response[:300],
            "completion":    completion[:500],
        })

        if delay > 0:
            time.sleep(delay)

    total        = passed + failed
    pass_rate    = passed / total if total else 0.0
    total_tokens = total_input_tokens + total_output_tokens
    tpc          = total_tokens / total  if total  else 0.0  # tokens / call
    tpca         = total_tokens / passed if passed else float("inf")  # / correct

    print(f"\n  {'─'*56}")
    print(f"  {mode_name} — pass@1: {passed}/{total} = {pass_rate:.1%}")
    print(f"  tokens: {total_tokens:,} total  "
          f"({total_input_tokens:,} in + {total_output_tokens:,} out)")
    print(f"  tokens/call: {tpc:.1f}   tokens/correct: "
          f"{('∞' if tpca == float('inf') else f'{tpca:.1f}')}")
    print(f"  {'─'*56}")

    return {
        "mode":                       mode_name,
        "model":                      model,
        "n":                          total,
        "passed":                     passed,
        "failed":                     failed,
        "pass_at_1":                  pass_rate,
        "total_input_tokens":         total_input_tokens,
        "total_output_tokens":        total_output_tokens,
        "total_tokens":               total_tokens,
        "tokens_per_call":            round(tpc, 2),
        "tokens_per_correct_answer": (None if tpca == float("inf")
                                      else round(tpca, 2)),
        "results":                    results,
    }


# ── Report ─────────────────────────────────────────────────────────────────────

def _print_report(baseline: dict | None, axiom: dict | None):
    border = "=" * 58
    print(f"\n  {border}")
    print(f"  AXIOM HUMANEVAL BENCHMARK REPORT")
    print(f"  {border}")

    def _fmt_tpca(v):
        return "∞" if v is None else f"{v:.1f}"

    if baseline:
        print(f"  Baseline (no system prompt)")
        print(f"    pass@1                 : {baseline['passed']}/{baseline['n']}  ({baseline['pass_at_1']:.1%})")
        print(f"    tokens (total / in / out): "
              f"{baseline['total_tokens']:,} / "
              f"{baseline['total_input_tokens']:,} / "
              f"{baseline['total_output_tokens']:,}")
        print(f"    tokens / call          : {baseline['tokens_per_call']:.1f}")
        print(f"    tokens / correct answer: {_fmt_tpca(baseline['tokens_per_correct_answer'])}")

    if axiom:
        print(f"  AXIOM constitutional system prompt")
        print(f"    pass@1                 : {axiom['passed']}/{axiom['n']}  ({axiom['pass_at_1']:.1%})")
        print(f"    tokens (total / in / out): "
              f"{axiom['total_tokens']:,} / "
              f"{axiom['total_input_tokens']:,} / "
              f"{axiom['total_output_tokens']:,}")
        print(f"    tokens / call          : {axiom['tokens_per_call']:.1f}")
        print(f"    tokens / correct answer: {_fmt_tpca(axiom['tokens_per_correct_answer'])}")

    if baseline and axiom:
        delta      = axiom["pass_at_1"] - baseline["pass_at_1"]
        delta_n    = axiom["passed"] - baseline["passed"]
        direction  = "▲" if delta > 0 else ("▼" if delta < 0 else "═")
        print(f"\n  Delta")
        print(f"    pass@1     : {direction}  {delta:+.1%}  ({delta_n:+d} additional problems solved)")

        # Token-economics delta — the central question from §10 of the doc.
        # Lower tokens/correct = more efficient at producing correct answers.
        b_tpca = baseline["tokens_per_correct_answer"]
        a_tpca = axiom["tokens_per_correct_answer"]
        if b_tpca is not None and a_tpca is not None:
            tpca_delta = a_tpca - b_tpca
            tpca_pct   = (tpca_delta / b_tpca) * 100 if b_tpca else 0.0
            tpca_dir   = "▼" if tpca_delta < 0 else ("▲" if tpca_delta > 0 else "═")
            verdict    = ("AXIOM more efficient" if tpca_delta < 0
                          else "baseline more efficient" if tpca_delta > 0
                          else "tie")
            print(f"    tokens/correct: {tpca_dir}  {tpca_delta:+.1f}  "
                  f"({tpca_pct:+.1f}%)   → {verdict}")
        else:
            print(f"    tokens/correct: n/a (one mode passed zero problems)")

        # Per-problem breakdown
        b_map = {r["task_id"]: r["passed"] for r in baseline["results"]}
        a_map = {r["task_id"]: r["passed"] for r in axiom["results"]}
        gained = [t for t in a_map if a_map[t] and not b_map.get(t)]
        lost   = [t for t in a_map if not a_map[t] and b_map.get(t)]

        if gained:
            print(f"\n  Problems solved by AXIOM but not baseline ({len(gained)}):")
            for t in gained[:10]:
                print(f"    + {t}")
            if len(gained) > 10:
                print(f"    ... and {len(gained)-10} more")

        if lost:
            print(f"\n  Problems solved by baseline but not AXIOM ({len(lost)}):")
            for t in lost[:10]:
                print(f"    - {t}")

    print(f"\n  {border}\n")


def _save_results(baseline: dict | None, axiom: dict | None, out_path: Path):
    out = {
        "benchmark":  "HumanEval",
        "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline":   baseline,
        "axiom":      axiom,
    }
    out_path.write_text(json.dumps(out, indent=2))
    print(f"  Results saved → {out_path}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AXIOM HumanEval Benchmark — baseline vs constitutional system prompt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python axiom_humaneval_run.py --problems 20\n"
            "  python axiom_humaneval_run.py --model claude-haiku-4-5-20251001\n"
            "  python axiom_humaneval_run.py --axiom-only --problems 50\n"
            "  python axiom_humaneval_run.py --output results/humaneval_nano.json\n"
        ),
    )
    parser.add_argument("--problems",      type=int,  default=None,
                        help="Number of problems to run (default: all 164)")
    parser.add_argument("--model",         default="claude-haiku-4-5-20251001",
                        help="Model ID (default: claude-haiku-4-5-20251001 for Nano)")
    parser.add_argument("--delay",         type=float, default=0.3,
                        help="Seconds between API calls (default: 0.3)")
    parser.add_argument("--baseline-only", action="store_true",
                        help="Run baseline only, skip AXIOM mode")
    parser.add_argument("--axiom-only",    action="store_true",
                        help="Run AXIOM mode only, skip baseline")
    parser.add_argument("--output",        default="axiom_humaneval_results.json",
                        help="Output JSON path (default: axiom_humaneval_results.json)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[ERROR] ANTHROPIC_API_KEY not set.")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("[ERROR] anthropic package not installed.")
        print("  pip install anthropic")
        sys.exit(1)

    problems = _download_humaneval()

    if args.problems:
        problems = problems[: args.problems]
        print(f"  Running subset: {len(problems)} problems")
    else:
        print(f"  Running full benchmark: {len(problems)} problems")

    print(f"  Model: {args.model}")

    baseline_result = None
    axiom_result    = None

    if not args.axiom_only:
        baseline_result = run_mode(
            problems, _BASELINE_SYSTEM, args.model, "BASELINE", args.delay
        )

    if not args.baseline_only:
        axiom_result = run_mode(
            problems, _AXIOM_SYSTEM_CODING, args.model, "AXIOM", args.delay
        )

    _print_report(baseline_result, axiom_result)
    _save_results(baseline_result, axiom_result, Path(args.output))


if __name__ == "__main__":
    main()
