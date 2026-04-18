"""
AXIOM CLI — command line entry points for axiom-lang package

axiom-validate worker
axiom-run "design a reward function"
axiom-server
"""
import argparse
import json
import os
import sys
from pathlib import Path


def _find_project_root() -> Path:
    env_dir = os.environ.get("AXIOM_FILES_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.exists():
            return p.parent
    p = Path(__file__).resolve()
    for _ in range(5):
        if (p / "axiom_files").exists():
            return p
        p = p.parent
    # Fall back to bundled data dir
    return Path(__file__).parent


def _setup_paths():
    root = _find_project_root()
    sys.path.insert(0, str(root))
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
    return root


def validate_cmd():
    """axiom-validate <agent>"""
    parser = argparse.ArgumentParser(
        description="Validate an AXIOM agent definition"
    )
    parser.add_argument("agent", help="Agent name (e.g. worker, evaluator)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    _setup_paths()
    from axiom_files.validator import validate_file

    result = validate_file(args.agent)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    icon = "✅" if result["status"] == "valid" else "❌"
    print(f"\n{icon} {args.agent}.axiom — {result['status'].upper()}")

    if result["issues"]:
        print(f"\n  Issues ({len(result['issues'])}):")
        for issue in result["issues"]:
            level_icon = "⚠️ " if issue["level"] == "warning" else "❌"
            print(f"    {level_icon} [{issue['phase']}] {issue['message']}")

    if result.get("suggestions"):
        print(f"\n  Suggestions:")
        for s in result["suggestions"]:
            print(f"    → {s}")

    print()
    sys.exit(0 if result["status"] == "valid" else 1)


def run_cmd():
    """axiom-run <prompt>"""
    parser = argparse.ArgumentParser(
        description="Run a prompt through the AXIOM runtime"
    )
    parser.add_argument("prompt", help="Task or prompt to run")
    parser.add_argument(
        "--agent", default="worker", help="Agent to use (default: worker)"
    )
    parser.add_argument(
        "--temperature", type=float, default=0.5, help="Model temperature"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    _setup_paths()
    from axiom_files.parser import (
        get_prompt_with_when, load_axiom,
        compile_decision_table, apply_decision_table,
        detect_concepts,
    )
    from axiom_files.validator import validate_file
    from axiom import client as nim

    # Validate first
    val = validate_file(args.agent)
    if val["status"] == "invalid":
        print(f"❌ {args.agent}.axiom is invalid — fix before running")
        sys.exit(1)

    # Build prompt
    system_prompt = get_prompt_with_when(args.agent, args.prompt)

    # Detect concepts
    parsed = load_axiom(args.agent)
    table = compile_decision_table(parsed)
    from axiom_files.parser import apply_decision_table
    concepts = apply_decision_table(args.prompt, table)
    if not concepts:
        concepts = detect_concepts(args.prompt, parsed)

    print(f"\n  Running: {args.prompt[:60]}...")
    if concepts:
        print(f"  Concepts: {', '.join(concepts)}")

    # Call model
    response = nim.chat(system_prompt, args.prompt, temperature=args.temperature)

    if args.json:
        print(json.dumps({
            "prompt": args.prompt,
            "agent": args.agent,
            "response": response,
            "concepts_fired": concepts,
        }, indent=2))
    else:
        print(f"\n{'─'*60}")
        print(response)
        print(f"{'─'*60}\n")


def cmd_server():
    """axiom-server — start the FastAPI REST server."""
    import subprocess
    root = _find_project_root()
    env = os.environ.copy()
    env.setdefault("AXIOM_FILES_DIR", str(root / "axiom_files"))
    env["PYTHONPATH"] = str(root)
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "axiom_server:app", "--host", "0.0.0.0", "--port", "8000",
    ], cwd=str(root), env=env)


def main():
    """Entry point dispatcher."""
    print("AXIOM v1.5.0")
    print("Commands: axiom-run, axiom-validate, axiom-server")
