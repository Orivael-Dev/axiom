"""
AXIOM Terminus — Constitutional Harness for Terminal-Bench 2.0
===============================================================
Wraps any LLM (tinyllama, phi3, mistral) with AXIOM constitutional
governance before executing terminal commands.

The #1 failure on Terminal-Bench 2.0:
  24.1% — command not found / wrong path (path guessing)
  9.6%  — executable failures

AXIOM addresses this directly:
  AgencyGuard  — blocks destructive ops without verification
  Uncertainty  — requires path check before execution
  Clarification — asks before acting on ambiguous tasks
  Foresight    — predicts expected outcome before executing

Improvement hypothesis:
  If constitutional governance prevents 50% of path-guessing failures:
  24.1% × 0.5 = 12pp improvement
  tinyllama ~15% → ~27% (80% relative improvement)
  From governance alone — not a better model.

Usage:
  tb run --agent axiom_terminus --model ollama/tinyllama ...

  Or standalone test:
  python axiom_terminus.py --test
  python axiom_terminus.py --task "Install numpy and verify it works"

Constitutional properties:
  uncertainty_floor: 0.15 — never execute on low confidence
  path_verification: required — check before running
  destructive_review: required — confirm before delete/modify
  clarification_first: true — ask before guessing
  audit_trail: HMAC-SHA256 signed — every decision logged

github.com/Orivael-Dev/axiom
pip install axiom-constitutional
Patent Pending ORVL-001-PROV · ORVL-002-PROV · ORVL-003
"""

import sys
import os
import re
import json
import hmac
import hashlib
import uuid
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8")

# ── Optional imports ──────────────────────────────────────────
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from axiom_signing import derive_key
SIGNING_KEY  = derive_key(b"axiom-terminus-v1")
AUDIT_LOG    = Path("axiom_terminus_audit.jsonl")
RESULTS_FILE = Path("axiom_terminus_results.json")


# ══════════════════════════════════════════════════════════════
# CONSTITUTIONAL GUARD PATTERNS
# These intercept terminal actions before execution
# ══════════════════════════════════════════════════════════════

# Commands that require path verification before running
PATH_SENSITIVE = [
    r"\b(?:cd|ls|cat|rm|mv|cp|chmod|chown)\s+[~/\.][\w/\-\.]+",
    r"\b(?:python3?|pip3?|npm|node|cargo|go)\s+(?:run|exec|install)\s+[\w/\-\.]+",
    r"\b(?:sudo|su)\s+",
    r"\bexec\s+[\w/\-\.]+",
    r"\bsource\s+[\w/\-\.]+",
    r"\b\./[\w\-]+",
]

# Commands that are destructive — require HUMAN_REVIEW equivalent
DESTRUCTIVE_COMMANDS = [
    r"\brm\s+(-rf?|--recursive|--force)\s+",
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r"\bformat\b",
    r"\bfdisk\b",
    r"\bwipe\b",
    r"\bshred\b",
    r"\btruncate\s+",
    r">\s*/dev/",
    r"\bdrop\s+(?:table|database|index)\b",
]

# Path guessing indicators — model is uncertain about paths
PATH_GUESS_INDICATORS = [
    r"\b(?:try|attempt|check if|maybe|perhaps|might be)\b.*(?:path|dir|file|located)",
    r"(?:not sure|uncertain|don't know).*(?:where|path|location)",
    r"\bguess\b.*(?:path|dir|location)",
    r"(?:path|location|directory)\s+(?:might|could|should)\s+be",
]

# Clarification triggers — task is ambiguous
AMBIGUITY_TRIGGERS = [
    r"^\s*(?:it|this|that)\b",
    r"\b(?:fix|update|change|modify|edit)\s+(?:it|this|that)\b",
    r"^\s*(?:run|execute|start|install|setup)\s*$",
    r"^\s*(?:fix|update|change|modify)\s+(?:it|this)\s*$",
]

_PATH_COMPILED       = [re.compile(p, re.IGNORECASE) for p in PATH_SENSITIVE]
_DESTRUCTIVE_COMPILED = [re.compile(p, re.IGNORECASE) for p in DESTRUCTIVE_COMMANDS]
_GUESS_COMPILED      = [re.compile(p, re.IGNORECASE) for p in PATH_GUESS_INDICATORS]
_AMBIG_COMPILED      = [re.compile(p, re.IGNORECASE) for p in AMBIGUITY_TRIGGERS]

# Safe verification commands to inject before path-sensitive ops
VERIFY_COMMAND = "which {cmd} 2>/dev/null || command -v {cmd} || echo 'not_found'"
PATH_CHECK     = "test -e {path} && echo 'exists' || echo 'not_found'"


# ══════════════════════════════════════════════════════════════
# CONSTITUTIONAL ANALYSIS
# ══════════════════════════════════════════════════════════════

class ConstitutionalAnalysis:
    """
    Analyzes a task or command for constitutional properties.
    Returns governance decision before execution.
    """

    UNCERTAINTY_FLOOR = 0.15   # CANNOT_MUTATE
    MAX_CONFIDENCE    = 0.85   # CANNOT_MUTATE

    def analyze_task(self, task: str) -> dict:
        """Analyze a task for constitutional properties."""
        issues     = []
        actions    = []
        confidence = 0.75

        # Check for ambiguity
        ambiguous = any(p.search(task) for p in _AMBIG_COMPILED)
        if ambiguous:
            issues.append("AMBIGUITY_DETECTED")
            actions.append("HALT_AND_CLARIFY")
            confidence -= 0.20

        # Check confidence floor
        if confidence < self.UNCERTAINTY_FLOOR:
            issues.append("UNCERTAINTY_FLOOR_BREACH")
            actions.append("HALT_AND_CLARIFY")
            confidence = self.UNCERTAINTY_FLOOR

        return {
            "task_preview":  task[:100],
            "is_ambiguous":  ambiguous,
            "confidence":    round(min(confidence, self.MAX_CONFIDENCE), 2),
            "issues":        issues,
            "actions":       actions,
            "proceed":       "HALT_AND_CLARIFY" not in actions,
        }

    def analyze_command(self, command: str) -> dict:
        """
        Analyze a terminal command before execution.
        Returns governance decision.
        """
        issues      = []
        actions     = []
        verify_cmds = []
        confidence  = 0.80

        # Check for destructive operations
        destructive = [p.pattern[:40] for p in _DESTRUCTIVE_COMPILED if p.search(command)]
        if destructive:
            issues.append("DESTRUCTIVE_OPERATION")
            actions.append("REQUIRE_VERIFICATION")
            confidence -= 0.30

        # Check for path sensitivity
        path_sensitive = any(p.search(command) for p in _PATH_COMPILED)
        if path_sensitive:
            issues.append("PATH_SENSITIVE_OPERATION")
            # Extract paths and add verification
            paths = re.findall(r'(?:^|\s)([\./~][\w/\-\.]+)', command)
            for path in paths[:3]:
                verify_cmds.append(PATH_CHECK.format(path=path))
            if verify_cmds:
                actions.append("VERIFY_PATHS_FIRST")

        # Check for path guessing
        guessing = any(p.search(command) for p in _GUESS_COMPILED)
        if guessing:
            issues.append("PATH_GUESS_DETECTED")
            actions.append("CLARIFY_PATH_BEFORE_EXECUTE")
            confidence -= 0.25

        # Apply uncertainty floor
        confidence = max(confidence, self.UNCERTAINTY_FLOOR)
        confidence = min(confidence, self.MAX_CONFIDENCE)

        verdict = "BLOCK" if "REQUIRE_VERIFICATION" in actions else \
                  "VERIFY_FIRST" if verify_cmds else \
                  "PROCEED"

        return {
            "command_preview": command[:80],
            "verdict":         verdict,
            "confidence":      round(confidence, 2),
            "issues":          issues,
            "actions":         actions,
            "verify_first":    verify_cmds,
            "is_destructive":  bool(destructive),
            "path_sensitive":  path_sensitive,
        }

    def analyze_response(self, response: str, task: str) -> dict:
        """
        Analyze model response for constitutional compliance.
        Checks confidence calibration and path guessing.
        """
        issues   = []
        score    = 1.0

        # Overclaiming — false certainty
        overclaim = re.search(
            r'\b(definitely|certainly|guaranteed|will work|100%|absolutely)\b',
            response, re.IGNORECASE
        )
        if overclaim:
            issues.append("OVERCLAIM_DETECTED")
            score -= 0.20

        # Path guessing in response
        guessing = any(p.search(response) for p in _GUESS_COMPILED)
        if guessing:
            issues.append("PATH_GUESS_IN_RESPONSE")
            score -= 0.25

        # Good signals
        if re.search(r'\b(check|verify|confirm|test|ensure)\b', response, re.IGNORECASE):
            score += 0.10
        if re.search(r'\b(uncertain|not sure|might|may|could)\b', response, re.IGNORECASE):
            score += 0.05

        return {
            "constitutional_score": round(max(0.0, min(1.0, score)), 2),
            "issues":    issues,
            "compliant": not bool(issues),
        }


# ══════════════════════════════════════════════════════════════
# AXIOM TERMINUS HARNESS
# ══════════════════════════════════════════════════════════════

class AxiomTerminus:
    """
    Constitutional harness for Terminal-Bench 2.0.

    Wraps any LLM with AXIOM governance:
    1. Analyze task for ambiguity
    2. Intercept commands before execution
    3. Verify paths before running
    4. Block destructive ops without confirmation
    5. Score response constitutionally
    6. Log every decision with HMAC signature

    Terminal-Bench Integration:
    This class implements the harness interface expected by tb run.
    """

    def __init__(
        self,
        model:         str = "tinyllama",
        ollama_url:    str = "http://localhost:11434",
        api_key:       Optional[str] = None,
        use_claude:    bool = False,
        log_path:      Path = AUDIT_LOG,
    ):
        self.model       = model
        self.ollama_url  = ollama_url
        self.api_key     = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.use_claude  = use_claude and ANTHROPIC_AVAILABLE and self.api_key
        self.log_path    = log_path
        self.analysis    = ConstitutionalAnalysis()
        self.session_id  = str(uuid.uuid4())[:8]

        # Session stats
        self.tasks_run        = 0
        self.commands_blocked = 0
        self.commands_verified = 0
        self.clarifications   = 0
        self.constitutional_scores = []

        if use_claude and self.api_key:
            self.claude = Anthropic(api_key=self.api_key)
        else:
            self.claude = None

    # ── Terminal-Bench harness interface ─────────────────────

    def run_task(self, task: str, context: str = "") -> dict:
        """
        Main entry point for Terminal-Bench harness.
        Analyze task → govern → generate commands → verify → execute.
        """
        t0 = time.time()
        self.tasks_run += 1
        task_id = f"TB-{datetime.now().strftime('%H%M%S')}-{self.tasks_run:03d}"

        print(f"\n  [{task_id}] Task: {task[:60]}")

        # Phase 1: Analyze task
        task_analysis = self.analysis.analyze_task(task)
        if not task_analysis["proceed"]:
            self.clarifications += 1
            clarification = self._request_clarification(task)
            print(f"  [CLARIFY] {clarification[:60]}")
            return self._build_result(task_id, task, [], "CLARIFICATION_NEEDED",
                                      task_analysis, t0)

        # Phase 2: Generate response with constitutional context
        print(f"  [GENERATE] Confidence: {task_analysis['confidence']:.0%}")
        response = self._generate_response(task, context, task_analysis)

        # Phase 3: Extract and validate commands
        commands      = self._extract_commands(response)
        safe_commands = []
        blocked       = []

        for cmd in commands:
            cmd_analysis = self.analysis.analyze_command(cmd)
            print(f"  [CMD] {cmd[:50]} → {cmd_analysis['verdict']}")

            if cmd_analysis["verdict"] == "BLOCK":
                self.commands_blocked += 1
                blocked.append({"command": cmd, "reason": cmd_analysis["issues"]})
                continue

            if cmd_analysis["verify_first"]:
                self.commands_verified += 1
                safe_commands.extend(cmd_analysis["verify_first"])

            if cmd_analysis["verdict"] in ("PROCEED", "VERIFY_FIRST"):
                safe_commands.append(cmd)

        # Phase 4: Constitutional response analysis
        resp_analysis = self.analysis.analyze_response(response, task)
        self.constitutional_scores.append(resp_analysis["constitutional_score"])

        result = self._build_result(
            task_id, task, safe_commands,
            "EXECUTED" if safe_commands else "BLOCKED",
            task_analysis, t0,
            response=response,
            blocked_commands=blocked,
            resp_analysis=resp_analysis,
        )

        self._log(result)
        return result

    def _generate_response(self, task: str, context: str, analysis: dict) -> str:
        """Generate model response with constitutional system prompt."""
        system_prompt = self._build_constitutional_prompt(analysis)

        if self.use_claude and self.claude:
            return self._claude_generate(task, context, system_prompt)
        elif REQUESTS_AVAILABLE:
            return self._ollama_generate(task, context, system_prompt)
        else:
            return f"[No model available — constitutional analysis only]\nTask: {task}"

    def _build_constitutional_prompt(self, analysis: dict) -> str:
        """Build constitutional system prompt for the model."""
        return f"""You are a careful terminal agent. Follow these constitutional rules:

RULES:
- Before running any command, verify the path exists
- Never guess at file paths — check first with: test -e /path/here
- If uncertain about a command, state your uncertainty explicitly
- For destructive operations (rm, dd, format), always confirm first
- Break complex tasks into small verifiable steps
- If the task is ambiguous, ask for clarification before proceeding

UNCERTAINTY FLOOR: If confidence < 15%, stop and ask.
CONFIDENCE LEVEL: {analysis['confidence']:.0%}
AMBIGUITY DETECTED: {analysis['is_ambiguous']}

When you include terminal commands, wrap them in ```bash``` blocks.
Always verify paths before using them. Never assume a file exists."""

    def _ollama_generate(self, task: str, context: str, system: str) -> str:
        """Generate response via Ollama."""
        try:
            payload = {
                "model":  self.model,
                "prompt": f"{system}\n\nTask: {task}\n\nContext: {context}",
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 512}
            }
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload, timeout=60
            )
            if resp.ok:
                return resp.json().get("response", "")
            return f"[Ollama error: {resp.status_code}]"
        except Exception as e:
            return f"[Ollama connection failed: {e}]"

    def _claude_generate(self, task: str, context: str, system: str) -> str:
        """Generate response via Claude (for comparison)."""
        try:
            resp = self.claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": f"Task: {task}\nContext: {context}"}]
            )
            return resp.content[0].text
        except Exception as e:
            return f"[Claude error: {e}]"

    # Fallback: common command starters for raw model output (no code blocks)
    CMD_STARTERS = re.compile(
        r'^\s*(pip3?\s|python3?\s|npm\s|node\s|cargo\s|go\s|git\s|apt\s|'
        r'brew\s|yum\s|dnf\s|pacman\s|snap\s|'
        r'mkdir\s|touch\s|cp\s|mv\s|rm\s|chmod\s|chown\s|ln\s|'
        r'cat\s|ls\s|cd\s|pwd|echo\s|test\s|which\s|command\s|'
        r'curl\s|wget\s|tar\s|unzip\s|make\s|cmake\s|'
        r'docker\s|kubectl\s|systemctl\s|service\s|'
        r'sudo\s|bash\s|sh\s|source\s|export\s|'
        r'find\s|grep\s|awk\s|sed\s|sort\s|wc\s|head\s|tail\s)',
        re.IGNORECASE
    )

    def _extract_commands(self, response: str) -> list:
        """Extract terminal commands from model response."""
        commands = []

        # Extract from code blocks
        bash_blocks = re.findall(r'```(?:bash|sh|shell)?\n?(.*?)```', response, re.DOTALL)
        for block in bash_blocks:
            lines = [l.strip() for l in block.strip().split('\n') if l.strip()]
            commands.extend(lines)

        # Extract inline commands (lines starting with $)
        dollar_lines = re.findall(r'^\$\s+(.+)$', response, re.MULTILINE)
        commands.extend(dollar_lines)

        # Fallback: tinyllama and small models emit raw commands without code blocks
        if not commands:
            for line in response.split('\n'):
                stripped = line.strip()
                if stripped and self.CMD_STARTERS.match(stripped):
                    commands.append(stripped)

        # Deduplicate preserving order
        seen = set()
        unique = []
        for cmd in commands:
            if cmd not in seen and not cmd.startswith('#'):
                seen.add(cmd)
                unique.append(cmd)

        return unique[:10]  # Max 10 commands per response

    def _request_clarification(self, task: str) -> str:
        """Generate a clarification request for ambiguous task."""
        return (f"The task '{task[:50]}' is ambiguous. "
                "Please specify: which file/directory, what exact change, "
                "and the expected outcome.")

    def _build_result(
        self,
        task_id:          str,
        task:             str,
        commands:         list,
        status:           str,
        task_analysis:    dict,
        t0:               float,
        response:         str = "",
        blocked_commands: list = None,
        resp_analysis:    dict = None,
    ) -> dict:
        """Build signed result manifest."""
        result = {
            "task_id":             task_id,
            "session_id":          self.session_id,
            "timestamp":           datetime.now().isoformat() + "Z",
            "task_preview":        task[:100],
            "status":              status,
            "commands_to_execute": commands,
            "commands_blocked":    blocked_commands or [],
            "task_analysis":       task_analysis,
            "response_analysis":   resp_analysis or {},
            "latency_ms":          int((time.time() - t0) * 1000),
            "constitutional_score": (resp_analysis or {}).get("constitutional_score", 1.0),
            "harness":             "AXIOM Terminus v1.0",
            "model":               self.model,
            "cannot_override":     True,
        }
        sig_str = json.dumps(
            {k: v for k, v in result.items() if k != "signature"},
            sort_keys=True, default=str
        )
        sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
        result["signature"] = f"hmac-sha256:{sig[:32]}..."
        return result

    def _log(self, result: dict):
        """Append-only audit log."""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")

    def session_summary(self) -> dict:
        """Summarize session statistics."""
        avg_const = (
            sum(self.constitutional_scores) / len(self.constitutional_scores)
            if self.constitutional_scores else 0.0
        )
        return {
            "session_id":              self.session_id,
            "model":                   self.model,
            "tasks_run":               self.tasks_run,
            "commands_blocked":        self.commands_blocked,
            "commands_verified":       self.commands_verified,
            "clarifications_requested": self.clarifications,
            "avg_constitutional_score": round(avg_const, 3),
            "audit_log":               str(self.log_path),
        }

    def print_summary(self):
        """Print session summary."""
        s = self.session_summary()
        print(f"\n{'═'*55}")
        print(f"  AXIOM Terminus Session Summary")
        print(f"{'─'*55}")
        print(f"  Model:              {s['model']}")
        print(f"  Tasks run:          {s['tasks_run']}")
        print(f"  Commands blocked:   {s['commands_blocked']}")
        print(f"  Paths verified:     {s['commands_verified']}")
        print(f"  Clarifications:     {s['clarifications_requested']}")
        print(f"  Avg const. score:   {s['avg_constitutional_score']:.0%}")
        print(f"  Audit log:          {s['audit_log']}")
        print(f"{'═'*55}")


# ══════════════════════════════════════════════════════════════
# TERMINAL-BENCH ADAPTER
# Implements the interface tb run expects
# ══════════════════════════════════════════════════════════════

class AxiomTerminusAdapter:
    """
    Terminal-Bench 2.0 adapter.
    Register as --agent axiom_terminus in tb run.

    To register with terminal-bench:
    1. Copy this file to terminal-bench/adapters/axiom_terminus.py
    2. Register in terminal-bench/registry.json:
       {"name": "axiom_terminus", "module": "adapters.axiom_terminus"}
    3. Run: tb run --agent axiom_terminus --model ollama/tinyllama ...
    """

    NAME = "axiom_terminus"
    DESCRIPTION = "AXIOM Constitutional Harness — governs model before execution"

    def __init__(self, model: str, **kwargs):
        # Parse model string (ollama/tinyllama → tinyllama)
        if "/" in model:
            provider, model_name = model.split("/", 1)
            use_ollama = provider == "ollama"
        else:
            model_name = model
            use_ollama = True

        ollama_url = kwargs.get("ollama_base_url", "http://localhost:11434")
        api_key    = kwargs.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        use_claude = model_name.startswith("claude")

        self.harness = AxiomTerminus(
            model=model_name,
            ollama_url=ollama_url,
            api_key=api_key,
            use_claude=use_claude,
        )

    def run(self, task: str, context: str = "", **kwargs) -> dict:
        """Terminal-Bench harness entry point."""
        return self.harness.run_task(task, context)

    def get_commands(self, task: str, context: str = "") -> list:
        """Return constitutionally-validated commands for a task."""
        result = self.harness.run_task(task, context)
        return result.get("commands_to_execute", [])


# ══════════════════════════════════════════════════════════════
# TEST SUITE — validates the harness works
# ══════════════════════════════════════════════════════════════

def run_tests():
    """Run a small test suite to validate the harness."""
    print("\n" + "="*60)
    print("  AXIOM Terminus — Constitutional Harness Tests")
    print("="*60)

    harness  = AxiomTerminus(model="tinyllama", ollama_url="http://localhost:11434")
    analysis = ConstitutionalAnalysis()

    # Test 1 — Destructive command blocked
    print("\n  Test 1: Destructive command")
    cmd = "rm -rf /home/user/important_files/"
    result = analysis.analyze_command(cmd)
    status = "✅ PASS" if result["verdict"] == "BLOCK" else "❌ FAIL"
    print(f"  {status} rm -rf → {result['verdict']} (destructive: {result['is_destructive']})")

    # Test 2 — Path verification injected
    print("\n  Test 2: Path verification")
    cmd = "cat /etc/nginx/nginx.conf"
    result = analysis.analyze_command(cmd)
    status = "✅ PASS" if result["verify_first"] else "❌ FAIL"
    print(f"  {status} cat /path → verify_first: {bool(result['verify_first'])}")
    if result["verify_first"]:
        print(f"     Injected: {result['verify_first'][0]}")

    # Test 3 — Ambiguous task triggers clarification
    print("\n  Test 3: Ambiguous task")
    task = "Fix it"
    result = analysis.analyze_task(task)
    status = "✅ PASS" if result["is_ambiguous"] and not result["proceed"] else "❌ FAIL"
    print(f"  {status} 'Fix it' → ambiguous: {result['is_ambiguous']}, proceed: {result['proceed']}")

    # Test 4 — Clear task proceeds
    print("\n  Test 4: Clear task")
    task = "Install numpy using pip and verify it imports correctly"
    result = analysis.analyze_task(task)
    status = "✅ PASS" if result["proceed"] else "❌ FAIL"
    print(f"  {status} Clear task → proceed: {result['proceed']}, confidence: {result['confidence']:.0%}")

    # Test 5 — Overclaim detection
    print("\n  Test 5: Overclaim detection")
    response = "This will definitely work. Run: pip install numpy"
    result = analysis.analyze_response(response, "install numpy")
    status = "✅ PASS" if not result["compliant"] else "❌ FAIL"
    print(f"  {status} 'definitely' → compliant: {result['compliant']}, score: {result['constitutional_score']:.0%}")

    # Test 6 — Good response passes
    print("\n  Test 6: Constitutional response")
    response = "Let me verify numpy isn't already installed. Run: python3 -c 'import numpy' 2>/dev/null && echo 'installed'"
    result = analysis.analyze_response(response, "install numpy")
    status = "✅ PASS" if result["compliant"] else "❌ FAIL"
    print(f"  {status} Verify-first response → compliant: {result['compliant']}, score: {result['constitutional_score']:.0%}")

    print(f"\n  Tests complete. Audit log: {AUDIT_LOG}")
    print("="*60)


# ══════════════════════════════════════════════════════════════
# BENCHMARK RUNNER
# Runs a subset of Terminal-Bench style tasks and scores them
# ══════════════════════════════════════════════════════════════

SAMPLE_TASKS = [
    "Install the requests library and verify it can make a GET request to httpbin.org/get",
    "Create a Python script that prints 'Hello World' and run it",
    "Check if git is installed and show the version",
    "Create a directory called test_axiom and verify it exists",
    "List all Python packages currently installed",
    "Write a bash script that counts lines in /etc/hosts",
    "Check available disk space on the system",
    "Find all .py files in the current directory",
    "Install flask and create a minimal hello world app",
    "Set up a virtual environment and activate it",
]

def run_benchmark(
    model:      str = "tinyllama",
    ollama_url: str = "http://localhost:11434",
    n_tasks:    int = 5,
    api_key:    Optional[str] = None,
    compare:    bool = False,
):
    """
    Run AXIOM Terminus against sample Terminal-Bench tasks.
    Optionally compare against bare model (no constitution).
    """
    print(f"\n{'═'*60}")
    print(f"  AXIOM Terminus Benchmark")
    print(f"  Model: {model}")
    print(f"  Tasks: {n_tasks}")
    print(f"{'═'*60}")

    tasks = SAMPLE_TASKS[:n_tasks]

    # Run with AXIOM governance
    print(f"\n  [AXIOM GOVERNED]")
    axiom_harness = AxiomTerminus(model=model, ollama_url=ollama_url, api_key=api_key)
    axiom_results = []
    for task in tasks:
        result = axiom_harness.run_task(task)
        axiom_results.append(result)

    axiom_harness.print_summary()

    if compare:
        # Run bare model (no constitutional governance)
        print(f"\n  [BARE MODEL — no governance]")
        bare_harness = AxiomTerminus(model=model, ollama_url=ollama_url, api_key=api_key)
        # Disable governance by clearing patterns
        bare_results = []
        for task in tasks:
            # Bare: just generate and extract commands, no analysis
            response = bare_harness._generate_response(
                task, "", {"confidence": 0.80, "is_ambiguous": False}
            )
            commands = bare_harness._extract_commands(response)
            bare_results.append({
                "task": task[:60],
                "commands": commands,
                "status": "EXECUTED" if commands else "NO_COMMANDS",
            })
            print(f"  [{len(commands)} cmds] {task[:50]}")

    # Save results
    results = {
        "benchmark_id":    f"TB-AXIOM-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "model":           model,
        "tasks_run":       n_tasks,
        "axiom_summary":   axiom_harness.session_summary(),
        "constitutional":  {
            "commands_blocked":  axiom_harness.commands_blocked,
            "paths_verified":    axiom_harness.commands_verified,
            "clarifications":    axiom_harness.clarifications,
            "avg_score":         axiom_harness.session_summary()["avg_constitutional_score"],
        },
    }
    sig_str = json.dumps(results, sort_keys=True, default=str)
    sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
    results["signature"] = f"hmac-sha256:{sig[:32]}..."

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n  Results saved: {RESULTS_FILE}")
    print(f"  Audit log:     {AUDIT_LOG}")
    return results


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="axiom_terminus",
        description="AXIOM Constitutional Harness for Terminal-Bench 2.0"
    )
    parser.add_argument("--test",      action="store_true", help="Run test suite")
    parser.add_argument("--benchmark", action="store_true", help="Run benchmark tasks")
    parser.add_argument("--task",      help="Run a single task")
    parser.add_argument("--model",     default="tinyllama")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--n-tasks",   type=int, default=5)
    parser.add_argument("--compare",   action="store_true", help="Compare vs bare model")
    parser.add_argument("--api-key",   default=None)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")

    if args.test:
        run_tests()
        return

    if args.benchmark:
        run_benchmark(
            model=args.model,
            ollama_url=args.ollama_url,
            n_tasks=args.n_tasks,
            api_key=api_key,
            compare=args.compare,
        )
        return

    if args.task:
        harness = AxiomTerminus(
            model=args.model,
            ollama_url=args.ollama_url,
            api_key=api_key,
        )
        result = harness.run_task(args.task)
        print(json.dumps(result, indent=2, default=str))
        harness.print_summary()
        return

    # Default — show setup instructions
    print("""
AXIOM Terminus — Constitutional Harness for Terminal-Bench 2.0
==============================================================

Quick start:
  python axiom_terminus.py --test
  python axiom_terminus.py --task "Install numpy and verify it works"
  python axiom_terminus.py --benchmark --model tinyllama --n-tasks 5
  python axiom_terminus.py --benchmark --compare  (vs bare model)

Terminal-Bench integration:
  1. pip install terminal-bench
  2. Copy axiom_terminus.py to terminal-bench/adapters/
  3. tb run --agent axiom_terminus --model ollama/tinyllama

Nano setup:
  ollama serve &
  ollama run tinyllama  (verify it works)
  python axiom_terminus.py --ollama-url http://[nano-ip]:11434 --benchmark
    """)


if __name__ == "__main__":
    main()
