"""
AXIOM Dev Agent v1.0
=====================
Constitutional AI development assistant.
Knows AXIOM patterns, guard syntax, known bugs.
Generates training data for Option B (Mistral fine-tune).

Usage:
  python axiom_dev_agent.py
  python axiom_dev_agent.py --task "write a new guard for X"
  python axiom_dev_agent.py --bug "describe the bug"
  python axiom_dev_agent.py --review path/to/file.py
  python axiom_dev_agent.py --history  (show training data collected)

Every good interaction saved to axiom_dev_training.jsonl
That becomes the Mistral fine-tune dataset later.
"""

import sys
import os
import json
import hmac
import hashlib
import uuid
import argparse
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

SIGNING_KEY   = b"axiom-dev-agent-v1"
TRAINING_FILE = Path("axiom_dev_training.jsonl")
BUG_FILE      = Path("axiom_bugs.json")

# Load axiom_dev.axiom as the system prompt
DEV_AXIOM_PATH = Path(__file__).parent / "axiom_dev.axiom"


def load_system_prompt() -> str:
    """Load axiom_dev.axiom as system context."""
    base = """You are the AXIOM Dev Agent — a constitutional AI development assistant
specialized in the AXIOM governance framework.

You know:
  - .axiom constitutional spec syntax and conventions
  - CANNOT_MUTATE field rules — never modify these
  - Guard module pattern: patterns → compile → check() → HMAC sign → return
  - Standard test pattern: test_cases list with BLOCKED/PASSED examples
  - Known bug patterns (check these before writing code)
  - Manifest signing: HMAC-SHA256 on every decision
  - sys.stdout.reconfigure(encoding="utf-8") on all CLI tools (BUG-003)
  - axiom_constitutional module name (not axiom) (BUG-002)
  - Regex: allow words between verb and noun (BUG-001)
  - Level 4 termination is ALWAYS pending dual signature (BUG-004)
  - Supply chain hashes are path-sensitive — re-register after moves (BUG-005)
  - GOAL framing overrides rules — clarification must align with GOAL (BUG-006)
  - ACB content[0] guard against empty list (BUG-010)

Current AXIOM version: 1.8.6
Module: axiom_constitutional
Guards: axiom_constitutional/guards/
Domains: axiom_files/domains/
Core: axiom_files/core/
Research: axiom_files/research/

Guard stack: 74/74 tests passing
ACB score: 88.7% keyword / 99.3% semantic
OWASP Agentic Top 10: 89% covered

Rules you follow:
  1. Write tests before implementation
  2. Check known bug patterns before writing any regex
  3. Every manifest is HMAC-SHA256 signed
  4. Confidence ceiling 0.85 — never claim certainty
  5. Always state the rival approach
  6. Preflight must pass 43/43 before recommending push
  7. When you find a new bug pattern — document it

Format your responses:
  CODE: (the implementation)
  TESTS: (test cases)
  EXPLANATION: (why this approach)
  CONFIDENCE: (0.0-0.85)
  RIVAL: (alternative approach considered)
  BUG_RISKS: (which known bugs to watch for)
"""

    if DEV_AXIOM_PATH.exists():
        axiom_content = DEV_AXIOM_PATH.read_text(encoding="utf-8")
        base += f"\n\nFull constitutional spec:\n{axiom_content}"

    return base


def load_bug_history() -> str:
    """Load known bugs for context."""
    if not BUG_FILE.exists():
        return ""
    try:
        bugs = json.loads(BUG_FILE.read_text())
        if not bugs:
            return ""
        recent = bugs[-10:]  # Last 10 bugs
        lines = "\n".join(
            f"  {b['bug_id']}: {b['component']} — {b['description']}"
            for b in recent
        )
        return f"\nRecent bugs to avoid:\n{lines}\n"
    except Exception:
        return ""


def save_training_example(task: str, response: str, rating: str = "good"):
    """Save interaction as training data for Mistral fine-tune."""
    entry = {
        "timestamp":  datetime.now().isoformat() + "Z",
        "instruction": task,
        "input":       "",
        "output":      response,
        "rating":      rating,
        "source":      "axiom_dev_agent_v1",
    }
    sig = hmac.new(SIGNING_KEY,
                   json.dumps(entry, sort_keys=True).encode(),
                   hashlib.sha256).hexdigest()
    entry["signature"] = f"hmac-sha256:{sig[:32]}..."

    with open(TRAINING_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_bug(component: str, description: str, fix: str, commit: str = ""):
    """Add a new bug to the bug registry."""
    bugs = []
    if BUG_FILE.exists():
        try:
            bugs = json.loads(BUG_FILE.read_text())
        except Exception:
            bugs = []

    bug_id = f"BUG-{len(bugs)+1:03d}"
    bugs.append({
        "bug_id":      bug_id,
        "component":   component,
        "description": description,
        "fix":         fix,
        "commit":      commit,
        "timestamp":   datetime.now().isoformat() + "Z",
        "status":      "LOGGED",
    })

    BUG_FILE.write_text(json.dumps(bugs, indent=2, ensure_ascii=False))
    print(f"\n  Bug logged: {bug_id} — {component}")
    return bug_id


def run_task(task: str, context: str = "", client=None) -> str:
    """Run a development task through the dev agent."""
    if not client:
        return f"[No API key — task logged for training data]\nTask: {task}"

    bug_history = load_bug_history()
    system      = load_system_prompt()
    user_msg    = f"Task: {task}"
    if context:
        user_msg += f"\n\nContext:\n{context}"
    if bug_history:
        user_msg += f"\n\n{bug_history}"

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user_msg}]
    )

    return resp.content[0].text


def review_file(filepath: str, client=None) -> str:
    """Review a file for known bug patterns."""
    path = Path(filepath)
    if not path.exists():
        return f"File not found: {filepath}"

    content = path.read_text(encoding="utf-8", errors="ignore")
    task = f"""Review this file for AXIOM known bug patterns.

Check each of these specifically:
  BUG-001: regex allowing words between verb and noun
  BUG-002: imports using axiom not axiom_constitutional
  BUG-003: CLI output without utf-8 reconfigure
  BUG-004: Level 4 termination — is it always pending?
  BUG-005: Supply chain hash re-registration needed?
  BUG-008: Emoji in regex patterns

File: {path.name}

Content:
{content[:4000]}"""

    return run_task(task, client=client)


def interactive_mode(client=None):
    """Interactive dev agent session."""
    print("\n" + "═"*60)
    print("  AXIOM Dev Agent v1.0")
    print("  Constitutional AI development assistant")
    print("  Type 'help' for commands, 'quit' to exit")
    print("═"*60)
    print()

    history = []

    while True:
        try:
            user_input = input("  axiom-dev> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Session ended.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("  Session ended.")
            break

        if user_input.lower() == "help":
            print("""
  Commands:
    help              Show this help
    quit              Exit

    bug <description> Log a new bug
    review <file>     Review file for bug patterns
    history           Show training data collected
    bugs              Show bug registry

  Or just type your development task:
    "write a guard for detecting X"
    "fix the regex that misses nouns"
    "add a new test for the agency guard"
    "write an axiom spec for a new agent"
""")
            continue

        if user_input.lower().startswith("bug "):
            description = user_input[4:].strip()
            component   = input("  Component: ").strip() or "unknown"
            fix         = input("  Fix: ").strip() or "TBD"
            bug_id      = log_bug(component, description, fix)
            save_training_example(
                f"Log this bug: {description}",
                f"Bug {bug_id} logged in {component}. Fix: {fix}",
                rating="good"
            )
            continue

        if user_input.lower() == "history":
            if not TRAINING_FILE.exists():
                print("  No training data yet.")
            else:
                count = sum(1 for _ in TRAINING_FILE.open(encoding="utf-8"))
                print(f"  Training examples collected: {count}")
                print(f"  File: {TRAINING_FILE}")
            continue

        if user_input.lower() == "bugs":
            if not BUG_FILE.exists():
                print("  No bugs logged yet.")
            else:
                bugs = json.loads(BUG_FILE.read_text())
                print(f"\n  Bug Registry ({len(bugs)} bugs)")
                print(f"  {'─'*50}")
                for b in bugs:
                    print(f"  {b['bug_id']}  {b['component']:20s}  {b['description'][:40]}")
            continue

        if user_input.lower().startswith("review "):
            filepath = user_input[7:].strip()
            print(f"\n  Reviewing: {filepath}")
            response = review_file(filepath, client=client)
        else:
            print(f"\n  Processing...")
            response = run_task(user_input, client=client)

        print()
        print(response)
        print()

        # Save to training data
        save_training_example(user_input, response)
        history.append({"task": user_input, "response": response})

        # Ask for rating
        rating_input = input("  Rate this response (good/bad/skip) [good]: ").strip().lower()
        if rating_input == "bad":
            # Mark last entry as bad
            entries = TRAINING_FILE.read_text().strip().split("\n")
            last    = json.loads(entries[-1])
            last["rating"] = "bad"
            entries[-1] = json.dumps(last, ensure_ascii=False)
            TRAINING_FILE.write_text("\n".join(entries) + "\n")
            print("  Marked as bad example — excluded from training data")
        elif rating_input not in ("skip", "s"):
            print("  Saved as good training example")
        print()


def main():
    parser = argparse.ArgumentParser(
        prog="axiom_dev_agent",
        description="AXIOM constitutional AI development assistant"
    )
    parser.add_argument("--task",    help="Run a single task")
    parser.add_argument("--bug",     help="Log a bug")
    parser.add_argument("--review",  help="Review a file for bug patterns")
    parser.add_argument("--history", action="store_true", help="Show training data stats")
    parser.add_argument("--bugs",    action="store_true", help="Show bug registry")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client  = Anthropic(api_key=api_key) if ANTHROPIC_AVAILABLE and api_key else None

    if not client:
        print("  ⚠️  No API key — running in lite mode")
        print("  Set ANTHROPIC_API_KEY for full dev agent")
        print()

    if args.history:
        if not TRAINING_FILE.exists():
            print("No training data yet.")
        else:
            count = sum(1 for _ in TRAINING_FILE.open(encoding="utf-8"))
            print(f"Training examples: {count}")
            print(f"File: {TRAINING_FILE}")
        return

    if args.bugs:
        if not BUG_FILE.exists():
            print("No bugs logged.")
        else:
            bugs = json.loads(BUG_FILE.read_text())
            for b in bugs:
                print(f"{b['bug_id']}  {b['component']}  {b['description']}")
        return

    if args.task:
        response = run_task(args.task, client=client)
        print(response)
        save_training_example(args.task, response)
        return

    if args.bug:
        log_bug("unknown", args.bug, "TBD")
        return

    if args.review:
        response = review_file(args.review, client=client)
        print(response)
        return

    # Default — interactive mode
    interactive_mode(client=client)


if __name__ == "__main__":
    main()
