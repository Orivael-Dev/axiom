"""QRF Hypothesis Loop — Arbor-style iterative optimization with multi-branch pruning.

Maps directly to the Arbor framework (Renmin University / Microsoft Research):

  Arbor concept                  QRF equivalent
  ─────────────────────────────────────────────────────────────────────
  Hypothesis-Tree Refinement  →  QRF branch pool (N weighted hypotheses)
  git-worktree isolation       →  subprocess + temp dir per hypothesis
  Coordinator agent            →  HypothesisLoop orchestrator (this file)
  Executor agents              →  branch-strategy-prompted model calls
  Cumulative learning          →  winning code carried forward each round
  Agent Skill Suite            →  BRANCH_STRATEGIES dict (loaded per round)
  Prune failed hypotheses      →  kill branches scoring 0 (constitutional gate)

How it works
────────────
Round 0  →  Run N branches on the initial (broken/slow) code.
             Each branch uses a different improvement strategy:
               edge_case / efficiency / error_handling / simplify / rewrite
             Score each by: test_pass_rate × confidence.
             Kill 0-score branches (Arbor-style pruning).
             Winner becomes the new baseline.
Round K  →  Repeat with winner as input. Each round, the solution improves.

Two conditions compared
───────────────────────
  BASELINE  — single model call per round, no branching, takes first output.
  QRF LOOP  — N branches per round, scored, pruned, best carries forward.

Key metrics (matching Arbor's paper framing)
────────────────────────────────────────────
  pass_rate_at_round_K  — fraction of tests passing after K rounds
  tokens_to_100pct      — total tokens to reach 100% test pass (or never)
  efficiency_x          — QRF quality-per-token / baseline quality-per-token
  rounds_to_solve       — rounds until first 100% pass (-1 if never)

Supported tasks
───────────────
  buggy-binary-search   — off-by-one and wrong boundary in binary search
  slow-palindrome       — O(n²) palindrome with a correctness bug
  broken-merge-sort     — merge sort with a merge phase bug
  leaky-rate-limiter    — rate limiter that doesn't enforce the window

Usage
─────
  # Dry-run (built-in stub, no model):
  python3 axiom_lab/benchmarks/qrf_hypothesis_loop.py --dry-run

  # Against Ollama:
  python3 axiom_lab/benchmarks/qrf_hypothesis_loop.py \\
      --backend ollama --model gemma2:2b --n-branches 4 --n-rounds 5

  # Single task, verbose:
  python3 axiom_lab/benchmarks/qrf_hypothesis_loop.py \\
      --dry-run --task buggy-binary-search --verbose

  # Write signed JSON report:
  python3 axiom_lab/benchmarks/qrf_hypothesis_loop.py \\
      --dry-run --report qrf_hypothesis_results.json
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── Branch strategies (= QRF domain branches, one per executor agent) ─────────

BRANCH_STRATEGIES: dict[str, str] = {
    "edge_case":      "Focus on fixing edge cases and boundary conditions. "
                      "Think carefully about empty inputs, single elements, and overflow.",
    "efficiency":     "Optimize time and space complexity. "
                      "Replace O(n²) with O(n log n) or O(n) where possible.",
    "error_handling": "Add robust input validation and error handling. "
                      "Raise clear exceptions for invalid inputs.",
    "simplify":       "Simplify the logic. Remove redundancy, shorten the code, "
                      "use built-ins where they are more readable.",
    "rewrite":        "Completely rewrite the function from scratch for maximum "
                      "correctness and clarity. Ignore the original implementation.",
}

# ── Optimization task bank ────────────────────────────────────────────────────

@dataclass
class OptTask:
    task_id:      str
    name:         str
    description:  str
    initial_code: str     # broken/slow starting point
    test_code:    str     # embeddable test assertions; uses the function directly
    n_tests:      int     # total number of assertions in test_code
    signature:    str     # function signature hint for the prompt


TASK_BANK: list[OptTask] = [

    OptTask(
        task_id="buggy-binary-search",
        name="Buggy Binary Search",
        description="Fix an off-by-one error and wrong boundary condition in binary search.",
        initial_code=textwrap.dedent("""\
            def binary_search(arr, target):
                left, right = 0, len(arr)   # bug: should be len(arr) - 1
                while left < right:          # bug: should be <=
                    mid = (left + right) // 2
                    if arr[mid] == target:
                        return mid
                    elif arr[mid] < target:
                        left = mid           # bug: should be mid + 1
                    else:
                        right = mid
                return -1
        """),
        test_code=textwrap.dedent("""\
            assert binary_search([], 1) == -1,                        "empty"
            assert binary_search([1], 1) == 0,                        "single match"
            assert binary_search([1], 2) == -1,                       "single no match"
            assert binary_search([1, 3, 5, 7, 9], 1) == 0,           "left edge"
            assert binary_search([1, 3, 5, 7, 9], 9) == 4,           "right edge"
            assert binary_search([1, 3, 5, 7, 9], 5) == 2,           "middle"
            assert binary_search([1, 3, 5, 7, 9], 4) == -1,          "not found"
            assert binary_search(list(range(0, 1000, 2)), 500) == 250,"large even"
            assert binary_search(list(range(100)), 0) == 0,           "zero index"
            assert binary_search(list(range(100)), 99) == 99,         "last index"
        """),
        n_tests=10,
        signature="def binary_search(arr: list, target) -> int",
    ),

    OptTask(
        task_id="slow-palindrome",
        name="Slow + Buggy Palindrome",
        description="Fix a correctness bug and replace O(n²) approach with O(n).",
        initial_code=textwrap.dedent("""\
            def is_palindrome(s):
                s = s.lower()
                # bug: includes spaces and punctuation in comparison
                result = ""
                for i in range(len(s) - 1, -1, -1):   # O(n²) string concat
                    result += s[i]
                return result == s
        """),
        test_code=textwrap.dedent("""\
            assert is_palindrome("racecar") == True,            "simple"
            assert is_palindrome("hello") == False,             "not palindrome"
            assert is_palindrome("") == True,                   "empty"
            assert is_palindrome("A") == True,                  "single"
            assert is_palindrome("Aba") == True,                "mixed case"
            assert is_palindrome("A man a plan a canal Panama") == True,  "phrase"
            assert is_palindrome("Was it a car or a cat I saw") == True,  "phrase 2"
            assert is_palindrome("No lemon no melon") == True,  "phrase 3"
            assert is_palindrome("abc") == False,               "short no"
            assert is_palindrome("abba") == True,               "even length"
        """),
        n_tests=10,
        signature="def is_palindrome(s: str) -> bool",
    ),

    OptTask(
        task_id="broken-merge-sort",
        name="Broken Merge Sort",
        description="Fix the merge phase that drops elements when sublists are unequal length.",
        initial_code=textwrap.dedent("""\
            def merge_sort(arr):
                if len(arr) <= 1:
                    return arr
                mid = len(arr) // 2
                left  = merge_sort(arr[:mid])
                right = merge_sort(arr[mid:])
                return _merge(left, right)

            def _merge(left, right):
                result = []
                i = j = 0
                while i < len(left) and j < len(right):
                    if left[i] <= right[j]:
                        result.append(left[i]); i += 1
                    else:
                        result.append(right[j]); j += 1
                # bug: missing remainder — elements after one list is exhausted are dropped
                return result
        """),
        test_code=textwrap.dedent("""\
            assert merge_sort([]) == [],                                "empty"
            assert merge_sort([1]) == [1],                             "single"
            assert merge_sort([2, 1]) == [1, 2],                      "two"
            assert merge_sort([3, 1, 4, 1, 5, 9, 2, 6]) == [1,1,2,3,4,5,6,9], "standard"
            assert merge_sort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5],   "reversed"
            assert merge_sort([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5],   "already sorted"
            assert merge_sort([-3, -1, -4, -1]) == [-4, -3, -1, -1], "negatives"
            assert merge_sort([1, 1, 1, 1]) == [1, 1, 1, 1],         "duplicates"
            assert merge_sort(list(range(9, -1, -1))) == list(range(10)), "range reversed"
            assert len(merge_sort(list(range(100)))) == 100,           "length preserved"
        """),
        n_tests=10,
        signature="def merge_sort(arr: list) -> list",
    ),

    OptTask(
        task_id="leaky-rate-limiter",
        name="Leaky Rate Limiter",
        description="Fix a rate limiter that resets the entire window instead of sliding it.",
        initial_code=textwrap.dedent("""\
            import time as _time

            class RateLimiter:
                def __init__(self, max_calls, period):
                    self.max_calls = max_calls
                    self.period = period
                    self.calls = 0
                    self.window_start = _time.monotonic()

                def allow(self):
                    now = _time.monotonic()
                    elapsed = now - self.window_start
                    if elapsed >= self.period:
                        # bug: resets to now, losing fractional window time
                        self.window_start = now
                        self.calls = 0
                    if self.calls < self.max_calls:
                        self.calls += 1
                        return True
                    return False
        """),
        test_code=textwrap.dedent("""\
            import time as _t
            rl = RateLimiter(3, 1.0)
            assert rl.allow() == True,   "call 1"
            assert rl.allow() == True,   "call 2"
            assert rl.allow() == True,   "call 3"
            assert rl.allow() == False,  "call 4 blocked"
            assert rl.allow() == False,  "call 5 still blocked"
            _t.sleep(1.05)
            assert rl.allow() == True,   "after window reset"
            assert rl.allow() == True,   "second call in new window"
            assert rl.allow() == True,   "third call in new window"
            assert rl.allow() == False,  "fourth blocked again"
            rl2 = RateLimiter(1, 0.5)
            assert rl2.allow() == True,  "single-call limiter"
            assert rl2.allow() == False, "second blocked"
        """),
        n_tests=11,
        signature="class RateLimiter  (max_calls: int, period: float)  →  .allow() -> bool",
    ),

    OptTask(
        task_id="precedence-calculator",
        name="Arithmetic Precedence Calculator",
        description="A calculator that evaluates strictly left-to-right, ignoring "
                    "operator precedence and parentheses. Fix it to honor * / over "
                    "+ - and to evaluate parenthesised sub-expressions.",
        initial_code=textwrap.dedent("""\
            def calc(expr: str) -> float:
                # naive: left-to-right, ignores precedence and parentheses
                tokens = expr.replace(" ", "")
                total = 0
                num = ""
                op = "+"
                for ch in tokens:
                    if ch.isdigit():
                        num += ch
                    else:
                        if op == "+": total += int(num)
                        elif op == "-": total -= int(num)
                        num = ""
                        op = ch
                if num:
                    if op == "+": total += int(num)
                    elif op == "-": total -= int(num)
                return total
        """),
        test_code=textwrap.dedent("""\
            assert calc("2 + 3 * 4") == 14,            "precedence"
            assert calc("(2 + 3) * 4") == 20,          "parentheses"
            assert calc("10 / 2 - 3") == 2,            "div then sub"
            assert calc("2 * 3 + 4 * 5") == 26,        "two products"
            assert calc("100 - 50 / 5 / 5") == 98,     "left-assoc div"
            assert calc("(1 + 2) * (3 + 4)") == 21,    "two groups"
            assert calc("3 * (4 - 2) + 1") == 7,       "nested mix"
            assert calc("((2))") == 2,                 "redundant parens"
            assert calc("7") == 7,                     "single number"
            assert calc("8 / 4 + 6 / 3") == 4,         "two quotients"
        """),
        n_tests=10,
        signature="def calc(expr: str) -> float",
    ),

    OptTask(
        task_id="semver-compare",
        name="Semantic Version Compare",
        description="Compare two SemVer strings returning -1/0/1. Must handle "
                    "pre-release precedence (1.0.0-alpha < 1.0.0), numeric-vs-"
                    "alphanumeric identifier ordering, more-fields-wins, and ignore "
                    "build metadata after '+'. The naive start crashes on any '-' tag.",
        initial_code=textwrap.dedent("""\
            def compare_versions(a: str, b: str) -> int:
                # naive: compares numeric release parts only; crashes on a
                # pre-release tag and ignores all SemVer precedence rules
                pa = [int(x) for x in a.split(".")]
                pb = [int(x) for x in b.split(".")]
                if pa < pb:
                    return -1
                if pa > pb:
                    return 1
                return 0
        """),
        test_code=textwrap.dedent("""\
            assert compare_versions("1.0.0", "1.0.1") == -1,                    "patch"
            assert compare_versions("1.2.0", "1.1.9") == 1,                     "minor beats patch"
            assert compare_versions("1.0.0", "1.0.0") == 0,                     "equal"
            assert compare_versions("1.0.0-alpha", "1.0.0") == -1,              "pre < release"
            assert compare_versions("1.0.0", "1.0.0-beta") == 1,                "release > pre"
            assert compare_versions("1.0.0-alpha", "1.0.0-alpha.1") == -1,      "more fields wins"
            assert compare_versions("1.0.0-alpha.1", "1.0.0-alpha.beta") == -1, "numeric < alnum"
            assert compare_versions("1.0.0-beta", "1.0.0-alpha") == 1,          "beta > alpha"
            assert compare_versions("1.0.0+b1", "1.0.0+b2") == 0,               "build ignored"
            assert compare_versions("1.0.0-rc.1+x", "1.0.0-rc.1") == 0,         "pre equal, build ignored"
        """),
        n_tests=10,
        signature="def compare_versions(a: str, b: str) -> int",
    ),

    OptTask(
        task_id="lru-ttl-cache",
        name="LRU Cache with TTL",
        description="A capacity-bounded cache that must (a) evict the LEAST-recently-"
                    "used key, (b) refresh recency on get(), and (c) expire entries "
                    "older than ttl (return None and remove them). The starting code "
                    "does none of the three correctly — it is FIFO, never refreshes "
                    "recency, and never expires.",
        initial_code=textwrap.dedent("""\
            class TTLCache:
                def __init__(self, capacity, ttl, time_fn=None):
                    self.capacity = capacity
                    self.ttl = ttl
                    self.time_fn = time_fn or (lambda: 0.0)
                    self.store = {}      # key -> (value, timestamp)
                    self.order = []      # insertion order only (bug: no recency)

                def get(self, key):
                    # bug: ignores TTL and does not refresh recency
                    if key in self.store:
                        return self.store[key][0]
                    return None

                def put(self, key, value):
                    now = self.time_fn()
                    if key not in self.store and len(self.store) >= self.capacity:
                        oldest = self.order.pop(0)   # bug: FIFO, not LRU
                        del self.store[oldest]
                    self.store[key] = (value, now)
                    self.order.append(key)
        """),
        test_code=textwrap.dedent("""\
            clock = {"t": 0.0}
            c = TTLCache(2, 10.0, time_fn=lambda: clock["t"])
            c.put("a", 1)
            c.put("b", 2)
            assert c.get("a") == 1,    "a present, now most-recently-used"
            c.put("c", 3)
            assert c.get("b") is None, "b was LRU, evicted by c"
            assert c.get("a") == 1,    "a survived eviction"
            assert c.get("c") == 3,    "c present"
            clock["t"] = 5.0
            c.put("d", 4)
            assert c.get("a") is None, "a is now LRU, evicted by d"
            clock["t"] = 20.0
            assert c.get("c") is None, "c expired past ttl"
            assert c.get("d") is None, "d expired past ttl"
            c.put("e", 5)
            assert c.get("e") == 5,    "fresh entry after expiries"
        """),
        n_tests=8,
        signature="class TTLCache  (capacity: int, ttl: float, time_fn=None)  →  .get(key), .put(key, value)",
    ),
]

_TASK_BY_ID: dict[str, OptTask] = {t.task_id: t for t in TASK_BANK}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class HypothesisResult:
    strategy:    str
    code:        str
    tests_passed: int
    n_tests:     int
    pass_rate:   float
    score:       float    # pass_rate × confidence (we use pass_rate directly)
    tokens_used: int
    killed:      bool     # True if score == 0 (constitutional prune)
    latency_ms:  float


@dataclass
class RoundResult:
    round_num:     int
    condition:     str     # "baseline" | "qrf"
    hypotheses:    list[HypothesisResult]
    winner:        HypothesisResult
    pruned_count:  int
    total_tokens:  int
    best_pass_rate: float


@dataclass
class BenchmarkRun:
    task_id:         str
    condition:       str
    rounds:          list[RoundResult]
    final_pass_rate: float
    total_tokens:    int
    rounds_to_solve: int     # -1 if never reached 100%
    quality_auc:     float   # area under pass_rate curve / n_rounds


# ── Test execution (subprocess isolation = Arbor git-worktree equivalent) ──────

def _run_tests(code: str, task: OptTask) -> tuple[int, int]:
    """Execute hypothesis code + test suite in a subprocess.

    Returns (tests_passed, n_tests).
    Subprocess isolation prevents a bad hypothesis from crashing the runner.
    """
    test_script = textwrap.dedent(f"""\
        import sys, traceback
        passed = 0
        failed = 0
        try:
{textwrap.indent(code, '            ')}
        except Exception as e:
            print(f"CODE_ERROR: {{e}}", file=sys.stderr)
            sys.exit({task.n_tests})

        # Run test lines in order in ONE shared namespace so stateful
        # setup (e.g. `rl = RateLimiter(...)`) persists across asserts.
        # Only `assert` lines count toward the test total; setup lines
        # (imports, assignments) just execute. A failing setup line is a
        # hard CODE_ERROR — it means the candidate code is unusable.
        src = {repr(task.test_code)}
        lines = [l.strip() for l in src.splitlines() if l.strip()]
        for line in lines:
            is_assert = line.startswith("assert ")
            try:
                exec(compile(line, '<test>', 'exec'), globals())
                if is_assert:
                    passed += 1
            except AssertionError:
                failed += 1
            except Exception as e:
                if is_assert:
                    failed += 1
                else:
                    print(f"CODE_ERROR: setup line failed: {{e}}", file=sys.stderr)
                    sys.exit({task.n_tests})
        print(f"PASSED={{passed}} FAILED={{failed}}")
        sys.exit(failed)
    """)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     delete=False, encoding="utf-8") as f:
        f.write(test_script)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stdout.splitlines():
            if line.startswith("PASSED="):
                parts = dict(p.split("=") for p in line.split())
                return int(parts.get("PASSED", 0)), task.n_tests
        return 0, task.n_tests
    except subprocess.TimeoutExpired:
        return 0, task.n_tests
    except Exception:
        return 0, task.n_tests
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Model calls ───────────────────────────────────────────────────────────────

_IMPROVE_PROMPT = """\
You are an expert Python programmer. Improve the following Python code.
Strategy: {strategy}

Current code:
```python
{code}
```

Return ONLY the improved Python code. No explanation. No markdown fences.
The function signature must remain: {signature}
"""


def _call_ollama(prompt: str, model: str, max_tokens: int = 512,
                 url: str = "http://localhost:11434") -> tuple[str, int]:
    payload = json.dumps({
        "model": model, "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.3},
    }).encode()
    req = urllib.request.Request(f"{url}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
    return body.get("response", ""), body.get("eval_count", max_tokens)


def _call_llamacpp(prompt: str, max_tokens: int = 512,
                   url: str = "http://localhost:8080") -> tuple[str, int]:
    payload = json.dumps({
        "prompt": prompt, "n_predict": max_tokens,
        "temperature": 0.3, "stop": ["```"],
    }).encode()
    req = urllib.request.Request(f"{url}/completion", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
    return body.get("content", ""), body.get("tokens_predicted", max_tokens)


def _call_nim(prompt: str, model: str, max_tokens: int = 512,
              base_url: str = "https://integrate.api.nvidia.com/v1",
              api_key: Optional[str] = None) -> tuple[str, int]:
    """NVIDIA NIM (hosted, OpenAI-compatible chat-completions).

    Reads the key from NVIDIA_NIM_API_KEY (or NVIDIA_API_KEY) when not
    passed explicitly. Token count comes from the API `usage` field.
    """
    key = (api_key or os.environ.get("NVIDIA_NIM_API_KEY")
           or os.environ.get("NVIDIA_API_KEY"))
    if not key:
        raise RuntimeError(
            "NIM backend needs NVIDIA_NIM_API_KEY (or NVIDIA_API_KEY) "
            "in the environment")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "Accept": "application/json"})
    last_err: Optional[Exception] = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read())
            text   = body["choices"][0]["message"]["content"]
            tokens = body.get("usage", {}).get("completion_tokens", max_tokens)
            return text, tokens
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503):       # rate-limited / overloaded
                time.sleep(2 ** attempt)   # 1, 2, 4, 8, 16s backoff
                continue
            raise
    raise RuntimeError(f"NIM rate-limited after retries: {last_err}")


def _extract_code(raw: str, task: OptTask) -> str:
    """Strip markdown fences from model output."""
    raw = raw.strip()
    if "```python" in raw:
        raw = raw.split("```python", 1)[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```")[0]
    return raw.strip()


# ── Dry-run stub ──────────────────────────────────────────────────────────────

# Simulated improvement curves per task (pass_rate by round for each strategy).
# Models the effect of more tokens on harder problems.
_STUB_IMPROVEMENT: dict[str, dict[str, list[float]]] = {
    "buggy-binary-search": {
        "edge_case":      [0.3, 0.7, 0.9, 1.0, 1.0],
        "efficiency":     [0.2, 0.5, 0.8, 0.9, 1.0],
        "error_handling": [0.1, 0.4, 0.6, 0.8, 0.9],
        "simplify":       [0.5, 0.8, 1.0, 1.0, 1.0],
        "rewrite":        [0.4, 0.9, 1.0, 1.0, 1.0],
    },
    "slow-palindrome": {
        "edge_case":      [0.3, 0.5, 0.7, 0.9, 1.0],
        "efficiency":     [0.4, 0.7, 0.9, 1.0, 1.0],
        "error_handling": [0.2, 0.4, 0.6, 0.8, 0.9],
        "simplify":       [0.5, 0.8, 1.0, 1.0, 1.0],
        "rewrite":        [0.6, 0.9, 1.0, 1.0, 1.0],
    },
    "broken-merge-sort": {
        "edge_case":      [0.4, 0.7, 0.9, 1.0, 1.0],
        "efficiency":     [0.3, 0.6, 0.8, 0.9, 1.0],
        "error_handling": [0.2, 0.4, 0.6, 0.8, 0.9],
        "simplify":       [0.5, 0.8, 1.0, 1.0, 1.0],
        "rewrite":        [0.7, 1.0, 1.0, 1.0, 1.0],
    },
    "leaky-rate-limiter": {
        "edge_case":      [0.3, 0.5, 0.7, 0.8, 0.9],
        "efficiency":     [0.2, 0.4, 0.6, 0.8, 0.9],
        "error_handling": [0.4, 0.6, 0.8, 0.9, 1.0],
        "simplify":       [0.3, 0.6, 0.8, 1.0, 1.0],
        "rewrite":        [0.5, 0.8, 1.0, 1.0, 1.0],
    },
}

# Baseline (single-shot, no branching) — lower and slower
_STUB_BASELINE: dict[str, list[float]] = {
    "buggy-binary-search": [0.3, 0.5, 0.7, 0.8, 0.9],
    "slow-palindrome":     [0.4, 0.6, 0.7, 0.9, 0.9],
    "broken-merge-sort":   [0.4, 0.6, 0.7, 0.8, 0.9],
    "leaky-rate-limiter":  [0.3, 0.5, 0.6, 0.8, 0.9],
}


def _stub_hypothesis(task: OptTask, strategy: str, round_num: int,
                     current_pass_rate: float) -> tuple[str, int, float]:
    """Return (improved_code_stub, tokens_used, simulated_pass_rate)."""
    curves = _STUB_IMPROVEMENT.get(task.task_id, {})
    curve  = curves.get(strategy, [0.3, 0.5, 0.7, 0.9, 1.0])
    idx    = min(round_num, len(curve) - 1)
    # Cumulative: can only improve from current baseline, never regress much
    rate   = max(current_pass_rate - 0.05, curve[idx])
    tokens = 300 + round_num * 40  # models use more tokens as they refine
    return f"# stub: {strategy} round {round_num}\n{task.initial_code}", tokens, rate


def _stub_baseline(task: OptTask, round_num: int,
                   current_pass_rate: float) -> tuple[str, int, float]:
    """Single-shot baseline stub."""
    curve = _STUB_BASELINE.get(task.task_id, [0.3, 0.5, 0.7, 0.8, 0.9])
    idx   = min(round_num, len(curve) - 1)
    rate  = max(current_pass_rate, curve[idx])
    tokens = 450  # flat, no routing benefit
    return f"# baseline stub round {round_num}\n{task.initial_code}", tokens, rate


# ── Core loop ─────────────────────────────────────────────────────────────────

def run_hypothesis_round(
    task: OptTask,
    current_code: str,
    current_pass_rate: float,
    round_num: int,
    condition: str,         # "baseline" | "qrf"
    strategies: list[str],  # 1 strategy for baseline, N for QRF
    *,
    backend: str,
    model: str,
    ollama_url: str,
    llamacpp_url: str,
    nim_base_url: str,
    dry_run: bool,
) -> RoundResult:
    hypotheses: list[HypothesisResult] = []

    for strategy in strategies:
        t0 = time.monotonic()

        if dry_run:
            if condition == "baseline":
                code, tokens, sim_rate = _stub_baseline(task, round_num, current_pass_rate)
                passed = round(sim_rate * task.n_tests)
            else:
                code, tokens, sim_rate = _stub_hypothesis(
                    task, strategy, round_num, current_pass_rate)
                passed = round(sim_rate * task.n_tests)
        else:
            prompt = _IMPROVE_PROMPT.format(
                strategy=BRANCH_STRATEGIES[strategy],
                code=current_code,
                signature=task.signature,
            )
            if backend == "ollama":
                raw, tokens = _call_ollama(prompt, model, url=ollama_url)
            elif backend == "nim":
                raw, tokens = _call_nim(prompt, model, base_url=nim_base_url)
            else:
                raw, tokens = _call_llamacpp(prompt, url=llamacpp_url)
            code = _extract_code(raw, task)
            passed, _ = _run_tests(code, task)

        latency_ms = (time.monotonic() - t0) * 1000
        pass_rate  = passed / task.n_tests
        score      = pass_rate  # constitutional score = test pass rate

        hypotheses.append(HypothesisResult(
            strategy=strategy,
            code=code,
            tests_passed=passed,
            n_tests=task.n_tests,
            pass_rate=pass_rate,
            score=score,
            tokens_used=tokens,
            killed=(score == 0.0),   # prune zero-score branches
            latency_ms=latency_ms,
        ))

    live = [h for h in hypotheses if not h.killed]
    winner = max(live or hypotheses, key=lambda h: h.score)
    pruned = sum(1 for h in hypotheses if h.killed)

    return RoundResult(
        round_num=round_num,
        condition=condition,
        hypotheses=hypotheses,
        winner=winner,
        pruned_count=pruned,
        total_tokens=sum(h.tokens_used for h in hypotheses),
        best_pass_rate=winner.pass_rate,
    )


def run_benchmark_on_task(
    task: OptTask,
    n_rounds: int,
    n_branches: int,
    *,
    backend: str,
    model: str,
    ollama_url: str,
    llamacpp_url: str,
    nim_base_url: str,
    dry_run: bool,
    verbose: bool,
) -> tuple[BenchmarkRun, BenchmarkRun]:
    """Run both baseline and QRF conditions on one task.

    Returns (baseline_run, qrf_run).
    """
    strategies = list(BRANCH_STRATEGIES.keys())[:n_branches]

    def _run_condition(condition: str) -> BenchmarkRun:
        rounds: list[RoundResult] = []
        current_code      = task.initial_code
        current_pass_rate = 0.0
        total_tokens      = 0
        rounds_to_solve   = -1
        strats = strategies if condition == "qrf" else [strategies[0]]

        for rnum in range(n_rounds):
            rr = run_hypothesis_round(
                task, current_code, current_pass_rate, rnum,
                condition, strats,
                backend=backend, model=model,
                ollama_url=ollama_url, llamacpp_url=llamacpp_url,
                nim_base_url=nim_base_url,
                dry_run=dry_run,
            )
            rounds.append(rr)
            total_tokens      += rr.total_tokens
            current_code       = rr.winner.code
            current_pass_rate  = rr.winner.pass_rate

            if verbose:
                strategies_used = "+".join(s[:5] for s in strats)
                print(f"      [{rnum}] {condition:<9} "
                      f"pass={rr.best_pass_rate*100:.0f}%  "
                      f"tokens={rr.total_tokens}  "
                      f"pruned={rr.pruned_count}  "
                      f"winner={rr.winner.strategy}")

            if rounds_to_solve == -1 and current_pass_rate >= 1.0:
                rounds_to_solve = rnum

        quality_auc = sum(r.best_pass_rate for r in rounds) / n_rounds
        return BenchmarkRun(
            task_id=task.task_id,
            condition=condition,
            rounds=rounds,
            final_pass_rate=rounds[-1].best_pass_rate if rounds else 0.0,
            total_tokens=total_tokens,
            rounds_to_solve=rounds_to_solve,
            quality_auc=quality_auc,
        )

    return _run_condition("baseline"), _run_condition("qrf")


# ── Report / display ──────────────────────────────────────────────────────────

def _print_task_comparison(task: OptTask,
                            baseline: BenchmarkRun,
                            qrf: BenchmarkRun) -> None:
    W = 70
    print(f"\n  {'─'*W}")
    print(f"  Task: {task.name}  [{task.task_id}]")
    print(f"  {'─'*W}")
    print(f"  {'Round':<7}  {'Baseline':>9}  {'QRF':>8}  {'Δ':>6}")
    print(f"  {'─'*34}")
    for b_rnd, q_rnd in zip(baseline.rounds, qrf.rounds):
        delta = q_rnd.best_pass_rate - b_rnd.best_pass_rate
        marker = " ▲" if delta > 0 else (" ▼" if delta < 0 else "")
        print(f"  Round {b_rnd.round_num:<2}  "
              f"{b_rnd.best_pass_rate*100:>8.0f}%  "
              f"{q_rnd.best_pass_rate*100:>7.0f}%  "
              f"{delta*100:>+5.0f}%{marker}")
    print(f"  {'─'*34}")

    eff = _efficiency(baseline.quality_auc, qrf.quality_auc)
    tok_saving = ((1 - qrf.total_tokens / baseline.total_tokens) * 100
                  if baseline.total_tokens else 0.0)

    def _solve(run: BenchmarkRun) -> str:
        return f"round {run.rounds_to_solve}" if run.rounds_to_solve >= 0 else "never"

    print(f"  Final pass    : baseline {baseline.final_pass_rate*100:.0f}%  "
          f"→ QRF {qrf.final_pass_rate*100:.0f}%")
    print(f"  Quality AUC   : baseline {baseline.quality_auc:.3f}  "
          f"→ QRF {qrf.quality_auc:.3f}  ({_fmt_eff(eff)} efficiency)")
    print(f"  Solved at     : baseline {_solve(baseline)}"
          f"  /  QRF {_solve(qrf)}")
    print(f"  Token cost    : baseline {baseline.total_tokens:,}"
          f"  /  QRF {qrf.total_tokens:,}  ({tok_saving:+.1f}%)")


def _efficiency(baseline_auc: float, qrf_auc: float) -> float:
    """QRF quality-AUC ÷ baseline quality-AUC.

    Returns ``inf`` when QRF rescues a task baseline scored 0 on — that is
    QRF's STRONGEST possible win, not a 0.0× regression (the old
    ``... else 0.0`` inverted its meaning). Returns 1.0 when both scored 0
    (no change). Callers exclude ``inf`` from ratio means and count it as a
    "rescue" so one un-dividable task can't drag the average to nonsense.
    """
    if baseline_auc > 0:
        return qrf_auc / baseline_auc
    return float("inf") if qrf_auc > 0 else 1.0


def _fmt_eff(eff: float) -> str:
    return "rescue" if eff == float("inf") else f"{eff:.2f}×"


def _aggregate_eff(ratios: list[float]) -> tuple[float, float]:
    """Mean and (outlier-robust) median of finite efficiency ratios.

    The ratio mean is fragile: a near-zero baseline AUC sends one task's
    ratio toward infinity and lets it dominate the average. The median is
    the honest headline. Returns (0.0, 0.0) for an empty list.
    """
    if not ratios:
        return 0.0, 0.0
    return sum(ratios) / len(ratios), statistics.median(ratios)


def _print_summary(task_pairs: list[tuple[OptTask, BenchmarkRun, BenchmarkRun]]) -> None:
    W = 70
    print(f"\n{'═'*W}")
    print("  QRF HYPOTHESIS LOOP — SUMMARY (Arbor-style comparison)")
    print(f"{'─'*W}")
    print(f"  {'Task':<28}  {'Base AUC':>9}  {'QRF AUC':>8}  "
          f"{'Effic.':>7}  {'Tok saving':>11}")
    print(f"  {'─'*66}")

    ratios: list[float] = []
    rescued = 0
    for task, baseline, qrf in task_pairs:
        eff = _efficiency(baseline.quality_auc, qrf.quality_auc)
        tok_s = ((1 - qrf.total_tokens / baseline.total_tokens) * 100
                 if baseline.total_tokens else 0.0)
        if eff == float("inf"):
            rescued += 1
        else:
            ratios.append(eff)
        print(f"  {task.name:<28}  {baseline.quality_auc:>8.3f}  "
              f"{qrf.quality_auc:>7.3f}  {_fmt_eff(eff):>7}  {tok_s:>+10.1f}%")

    mean_eff, med_eff = _aggregate_eff(ratios)
    print(f"  {'─'*66}")
    print(f"  {'MEAN efficiency':<28}  {'':>9}  {'':>8}  {mean_eff:>6.2f}×")
    print(f"  {'MEDIAN (outlier-robust)':<28}  {'':>9}  {'':>8}  {med_eff:>6.2f}×")
    if rescued:
        print(f"  (+ {rescued} task(s) rescued from baseline 0% — excluded from both)")
    print()
    # Median is the honest headline: the ratio mean blows up when a
    # baseline AUC is near zero (a weak model), so one outlier can
    # dominate it. Flag that case explicitly.
    if mean_eff > med_eff * 1.8 and mean_eff > 2.0:
        print(f"  △ Median {med_eff:.2f}× is the honest headline — the {mean_eff:.2f}× mean is")
        print(f"    inflated by near-zero-baseline tasks (ratio explodes as baseline→0).")
    elif med_eff >= 1.5:
        print(f"  ✓ QRF shows a robust {med_eff:.2f}× median improvement.")
    elif med_eff > 1.01:
        print(f"  ✓ QRF shows a modest {med_eff:.2f}× median improvement.")
    else:
        print(f"  △ {med_eff:.2f}× median — little measurable QRF gain on this set/model.")
    print(f"{'═'*W}")


# ── HMAC report signing ───────────────────────────────────────────────────────

def _sign(payload: dict) -> str:
    try:
        from axiom_signing import derive_key
        key = derive_key(b"axiom-qrf-hyp-loop-v1")
    except Exception:
        key = hashlib.sha256(
            os.environ.get("AXIOM_MASTER_KEY", "insecure").encode()
        ).digest()
    body = json.dumps(payload, sort_keys=True).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QRF Hypothesis Loop — Arbor-style iterative optimization benchmark"
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Built-in stub, no model needed")
    p.add_argument("--backend", choices=["ollama", "llamacpp", "nim"], default="ollama")
    p.add_argument("--model", default="gemma2:2b",
                   help="Model id (ollama default: gemma2:2b; for --backend nim "
                        "defaults to meta/llama-3.1-8b-instruct)")
    p.add_argument("--ollama-url",   default="http://localhost:11434")
    p.add_argument("--llamacpp-url", default="http://localhost:8080")
    p.add_argument("--nim-base-url",
                   default="https://integrate.api.nvidia.com/v1",
                   help="NVIDIA NIM OpenAI-compatible base URL "
                        "(needs NVIDIA_NIM_API_KEY or NVIDIA_API_KEY in env)")
    p.add_argument("--n-rounds", type=int, default=5,
                   help="Optimization rounds per task (default: 5)")
    p.add_argument("--n-branches", type=int, default=4,
                   help="QRF branches per round (1-5, default: 4)")
    p.add_argument("--task", default=None,
                   choices=list(_TASK_BY_ID.keys()),
                   help="Run one specific task (default: all)")
    p.add_argument("--report", type=Path, default=None,
                   help="Write signed JSON report to this path")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-round detail")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.n_branches = max(1, min(5, args.n_branches))

    # For NIM, the ollama default model id is meaningless — pick a real
    # NVIDIA-hosted model unless the user overrode --model.
    if args.backend == "nim" and args.model == "gemma2:2b":
        args.model = os.environ.get("NIM_MODEL", "meta/llama-3.1-8b-instruct")

    tasks = ([_TASK_BY_ID[args.task]] if args.task
             else list(TASK_BANK))

    print(f"\nQRF Hypothesis Loop  (Arbor-style iterative optimization)")
    print(f"  Tasks     : {len(tasks)}")
    print(f"  Rounds    : {args.n_rounds}")
    print(f"  Branches  : {args.n_branches}  "
          f"(strategies: {', '.join(list(BRANCH_STRATEGIES)[:args.n_branches])})")
    if args.dry_run:
        print(f"  Mode      : DRY-RUN (stub)")
    else:
        print(f"  Backend   : {args.backend}  model={args.model}")
    print()

    task_pairs: list[tuple[OptTask, BenchmarkRun, BenchmarkRun]] = []
    all_results: list[dict] = []

    for task in tasks:
        print(f"  ▶ {task.name}  [{task.task_id}]")
        if args.verbose:
            print()

        baseline_run, qrf_run = run_benchmark_on_task(
            task,
            n_rounds=args.n_rounds,
            n_branches=args.n_branches,
            backend=args.backend,
            model=args.model,
            ollama_url=args.ollama_url,
            llamacpp_url=args.llamacpp_url,
            nim_base_url=args.nim_base_url,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        task_pairs.append((task, baseline_run, qrf_run))

        if args.verbose:
            _print_task_comparison(task, baseline_run, qrf_run)
        else:
            b_final = baseline_run.final_pass_rate * 100
            q_final = qrf_run.final_pass_rate * 100
            eff = _efficiency(baseline_run.quality_auc, qrf_run.quality_auc)
            print(f"    baseline={b_final:.0f}%  qrf={q_final:.0f}%  "
                  f"efficiency={_fmt_eff(eff)}  "
                  f"tokens: {baseline_run.total_tokens:,}→{qrf_run.total_tokens:,}")

        all_results.append({
            "task_id": task.task_id,
            "baseline": asdict(baseline_run),
            "qrf": asdict(qrf_run),
        })

    _print_summary(task_pairs)

    if args.report:
        _ratios = [_efficiency(b.quality_auc, q.quality_auc)
                   for _, b, q in task_pairs]
        _finite = [e for e in _ratios if e != float("inf")]
        avg_eff, med_eff = _aggregate_eff(_finite)
        rescued_n = sum(1 for e in _ratios if e == float("inf"))

        report = {
            "benchmark": "qrf_hypothesis_loop",
            "version": "1.0",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config": {
                "backend": args.backend if not args.dry_run else "dry_run",
                "model": args.model,
                "n_rounds": args.n_rounds,
                "n_branches": args.n_branches,
                "branch_strategies": list(BRANCH_STRATEGIES.keys())[:args.n_branches],
            },
            "tasks": all_results,
            "summary": {
                "avg_efficiency_x": round(avg_eff, 3),
                "median_efficiency_x": round(med_eff, 3),
                "rescued_tasks": rescued_n,
                "n_tasks": len(tasks),
            },
        }
        report["hmac_signature"] = _sign(report)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\n  Report → {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
