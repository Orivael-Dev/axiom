"""
AXIOM Agent v1.1
=================
Constitutional AI development agent.
Runtime implementation of axiom_files/core/axiom_agent.axiom.

Modes:
  FEATURE       — write spec → tests → implementation
  BUG_HUNT      — scan files → rank by severity → propose fixes
  EFFICIENCY    — profile pipeline → measure baseline → optimize
  REASONING_LAB — propose experiment with ISOLATION flag

Usage:
  python axiom_agent.py --task "write a guard for X" --mode feature
  python axiom_agent.py --task "scan for BUG-001" --mode bug_hunt
  python axiom_agent.py --task "profile guard pipeline" --mode efficiency
  python axiom_agent.py --task "test new branch strategy" --mode reasoning_lab
  python axiom_agent.py --profile   (show pipeline profile)
  python axiom_agent.py --bugs      (list known bug patterns)

API:
  from axiom_agent import AxiomAgent
  agent = AxiomAgent()
  result = agent.run_task("write a guard for X", mode="feature")
"""

import sys
import os
import json
import hmac
import hashlib
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from axiom_files.parser import load_axiom, to_system_prompt
    PARSER_AVAILABLE = True
except ImportError:
    PARSER_AVAILABLE = False

from axiom_signing import derive_key
SIGNING_KEY = derive_key(b"axiom-agent-v1.1")
MEMORY_FILE = Path("axiom_agent_memory.jsonl")
PROJECT_ROOT = Path(__file__).resolve().parent

MODES = {
    "feature":       "FEATURE — Write specification, then tests, then implementation",
    "bug_hunt":      "BUG_HUNT — Scan files, rank by severity, propose fixes",
    "efficiency":    "EFFICIENCY — Profile pipeline, measure baseline, optimize",
    "reasoning_lab": "REASONING_LAB — Propose experiment with ISOLATION flag",
}

KNOWN_BUGS = [
    {"id": "BUG-001", "pattern": "pattern_noun_between_verb_object",
     "desc": "Pattern misses intermediate words between verb and target noun",
     "fix": "Allow zero to two words between verb and target noun"},
    {"id": "BUG-002", "pattern": "stale_module_imports",
     "desc": "Module path referencing old name after rename",
     "fix": "Update all module paths to reflect current name"},
    {"id": "BUG-003", "pattern": "output_encoding_undeclared",
     "desc": "Terminal output without encoding declaration",
     "fix": "Declare output encoding at start of every CLI tool"},
    {"id": "BUG-004", "pattern": "dual_signature_immediate_execution",
     "desc": "Level 4 termination executing without waiting for second signature",
     "fix": "Level 4 always pending — never execute without dual signature"},
    {"id": "BUG-005", "pattern": "supply_chain_hash_path_sensitive",
     "desc": "Hash registered before file move — mismatch after reorganization",
     "fix": "Re-register hash after any file path change"},
    {"id": "BUG-006", "pattern": "goal_framing_suppresses_clarification",
     "desc": "Primary GOAL conflicts with clarification rules",
     "fix": "Goal must state that clarification is completion"},
    {"id": "BUG-007", "pattern": "signing_call_incomplete",
     "desc": "Signing operation missing digest finalization step",
     "fix": "Always finalize signing operation before using result"},
    {"id": "BUG-008", "pattern": "multi_byte_characters_in_patterns",
     "desc": "Pattern strings contain multi-byte characters that fail on narrow encodings",
     "fix": "Use ASCII-only characters in all pattern strings"},
    {"id": "BUG-009", "pattern": "domain_file_path_resolution",
     "desc": "CLI domain lookup uses pre-reorganization path",
     "fix": "Use full resolved path when looking up domain specifications"},
    {"id": "BUG-010", "pattern": "unchecked_content_access",
     "desc": "Accessing first element of response content without checking length",
     "fix": "Always verify content list is non-empty before accessing first element"},
]

PIPELINE_PROFILE = {
    "guards": 7,
    "total_patterns": 163,
    "guard_stack": [
        {"name": "DestructiveOperationGuard", "patterns": 17, "avg_ms": 0.3},
        {"name": "PIIGuard",                  "patterns": 12, "avg_ms": 0.2},
        {"name": "OutputInjectionGuard",      "patterns": 14, "avg_ms": 0.3},
        {"name": "AgencyGuard",               "patterns": 28, "avg_ms": 0.4},
        {"name": "SecurityGuards",            "patterns": 52, "avg_ms": 0.6},
        {"name": "RedactGuard",               "patterns": 29, "avg_ms": 0.4},
        {"name": "ReviewQueue",               "patterns": 11, "avg_ms": 0.2},
    ],
    "bottleneck": "SecurityGuards — 52 patterns, 0.6ms average",
    "opportunities": [
        "Cheapest guard first ordering already applied",
        "Pattern deduplication across guards could save 8-12 patterns",
        "Regex precompilation already active on all guards",
    ],
    "security_note": "Never trade security for latency without HUMAN_REVIEW",
}


def _sign_manifest(data: dict) -> str:
    payload = json.dumps(data, sort_keys=True).encode()
    sig = hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{sig[:32]}..."


def _load_system_prompt() -> str:
    """Load axiom_agent.axiom as system prompt via parser."""
    if PARSER_AVAILABLE:
        try:
            parsed = load_axiom("core/axiom_agent")
            return to_system_prompt(parsed)
        except Exception:
            pass
    # Fallback: load spec directly
    spec_path = PROJECT_ROOT / "axiom_files" / "core" / "axiom_agent.axiom"
    if not spec_path.exists():
        spec_path = PROJECT_ROOT / "axiom_agent.axiom"
    if spec_path.exists():
        return spec_path.read_text(encoding="utf-8")
    return "You are the AXIOM Agent — a constitutional AI development assistant."


def _save_memory(entry: dict):
    entry["signature"] = _sign_manifest(entry)
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class AxiomAgent:
    """AXIOM Agent runtime — implements axiom_agent.axiom."""

    def __init__(self, api_key: str = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=key) if ANTHROPIC_AVAILABLE and key else None
        self.system_prompt = _load_system_prompt()

    def run_task(self, task: str, mode: str = "feature", context: str = "") -> dict:
        """Run a task through the agent."""
        mode = mode.lower().replace(" ", "_")
        if mode not in MODES:
            mode = "feature"

        # Check relevant bug patterns
        relevant_bugs = self._check_bugs(task)

        user_msg = f"MODE: {mode.upper()}\nTASK: {task}"
        if context:
            user_msg += f"\n\nCONTEXT:\n{context}"
        if relevant_bugs:
            user_msg += f"\n\nRELEVANT BUG PATTERNS:\n"
            for b in relevant_bugs:
                user_msg += f"  {b['id']}: {b['desc']}\n"

        if not self.client:
            result = {
                "status": "offline",
                "mode": mode,
                "task": task,
                "relevant_bugs": [b["id"] for b in relevant_bugs],
                "note": "No API key — task logged for review",
            }
        else:
            resp = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            content = resp.content[0].text if resp.content else ""
            result = {
                "status": "complete",
                "mode": mode,
                "task": task,
                "response": content,
                "relevant_bugs": [b["id"] for b in relevant_bugs],
                "model": "claude-sonnet-4-6",
            }

        # Sign and log
        manifest_id = f"AA-{mode[:3].upper()}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        result["manifest_id"] = manifest_id
        result["signature"] = _sign_manifest(result)

        _save_memory({
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "manifest_id": manifest_id,
            "mode": mode,
            "task": task,
            "status": result["status"],
        })

        return result

    def _check_bugs(self, task: str) -> list:
        """Check which known bugs are relevant to this task."""
        task_lower = task.lower()
        relevant = []
        for bug in KNOWN_BUGS:
            keywords = bug["pattern"].replace("_", " ").split()
            if any(kw in task_lower for kw in keywords):
                relevant.append(bug)
        return relevant

    def get_profile(self) -> dict:
        """Pipeline efficiency profile."""
        return PIPELINE_PROFILE

    def get_bugs(self) -> list:
        """Known bug patterns."""
        return KNOWN_BUGS

    def get_memory_stats(self) -> dict:
        """Memory file stats."""
        if not MEMORY_FILE.exists():
            return {"entries": 0, "file": str(MEMORY_FILE)}
        count = sum(1 for _ in MEMORY_FILE.open(encoding="utf-8"))
        return {"entries": count, "file": str(MEMORY_FILE)}

    def propose_experiment(self, hypothesis: str, module: str) -> dict:
        """Propose a reasoning experiment (ISOLATION mode)."""
        exp_id = f"LatentExperiment-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        experiment = {
            "experiment_id": exp_id,
            "hypothesis": hypothesis,
            "module": module,
            "isolation": True,
            "human_review_required": True,
            "status": "PROPOSED",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        }
        experiment["signature"] = _sign_manifest(experiment)
        _save_memory(experiment)
        return experiment


def main():
    parser = argparse.ArgumentParser(
        prog="axiom_agent",
        description="AXIOM Agent v1.1 — Constitutional AI development agent",
    )
    parser.add_argument("--task", help="Task to run")
    parser.add_argument("--mode", default="feature", choices=list(MODES.keys()),
                        help="Agent mode (default: feature)")
    parser.add_argument("--profile", action="store_true", help="Show pipeline profile")
    parser.add_argument("--bugs", action="store_true", help="List known bug patterns")
    parser.add_argument("--memory", action="store_true", help="Show memory stats")
    args = parser.parse_args()

    agent = AxiomAgent()

    if args.profile:
        profile = agent.get_profile()
        print(f"\n  Pipeline Profile")
        print(f"  {'='*50}")
        print(f"  Guards: {profile['guards']}  Patterns: {profile['total_patterns']}")
        print(f"  Bottleneck: {profile['bottleneck']}")
        for g in profile["guard_stack"]:
            print(f"    {g['name']:<30} {g['patterns']:>3} patterns  {g['avg_ms']:.1f}ms")
        print(f"\n  Opportunities:")
        for o in profile["opportunities"]:
            print(f"    - {o}")
        print(f"\n  {profile['security_note']}")
        return

    if args.bugs:
        print(f"\n  Known Bug Patterns ({len(KNOWN_BUGS)})")
        print(f"  {'='*50}")
        for b in KNOWN_BUGS:
            print(f"  {b['id']}  {b['desc']}")
            print(f"          Fix: {b['fix']}")
        return

    if args.memory:
        stats = agent.get_memory_stats()
        print(f"\n  Memory: {stats['entries']} entries")
        print(f"  File: {stats['file']}")
        return

    if args.task:
        result = agent.run_task(args.task, mode=args.mode)
        print(f"\n  Mode: {result['mode'].upper()}")
        print(f"  Manifest: {result['manifest_id']}")
        if result.get("relevant_bugs"):
            print(f"  Bug checks: {', '.join(result['relevant_bugs'])}")
        if result["status"] == "offline":
            print(f"\n  {result['note']}")
        else:
            print(f"\n{result.get('response', '')}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
