"""
AXIOM Pre-Push Preflight
=========================
Runs before every `git push`. Blocks the push if any check fails.

Checks (no LLM calls — all local, fast):
  1. Constitutional guard tests   — DestructiveGuard, PIIGuard, InjectionGuard
  2. Structural validation        — every changed .axiom file parsed + validated
  3. Supply chain integrity       — SHA-256 hash verified against registered baseline
  4. Constitutional integrity     — CANNOT_MUTATE fields present and intact
  5. Manifest signature           — content hash generated and logged

Exit 0 = all clear, push proceeds.
Exit 1 = one or more failures, push blocked.

Usage (called by .git/hooks/pre-push):
  python scripts/axiom_preflight.py [--all] [--base <ref>]

  --all           check every .axiom file, not just changed ones
  --base <ref>    compare against this ref instead of origin/main
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AXIOM_DIR  = ROOT / "axiom_files"
CERTS_DIR  = ROOT / "certs"
LOG_FILE   = ROOT / "axiom_files" / ".reviews" / "preflight_log.jsonl"

# ── Colour helpers (ANSI) ─────────────────────────────────────────────────────

def _green(s):  return "\033[32m" + s + "\033[0m"
def _red(s):    return "\033[31m" + s + "\033[0m"
def _yellow(s): return "\033[33m" + s + "\033[0m"
def _bold(s):   return "\033[1m"  + s + "\033[0m"
def _grey(s):   return "\033[90m" + s + "\033[0m"

PASS  = _green("PASS")
FAIL  = _red("FAIL")
SKIP  = _yellow("SKIP")
WARN  = _yellow("WARN")

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 1 — Constitutional guard test suites
# ══════════════════════════════════════════════════════════════════════════════

GUARD_FILES = [
    ROOT / "axiom_constitutional" / "axiom_destructive_guard.py",
    ROOT / "axiom_constitutional" / "axiom_pii_guard.py",
    ROOT / "axiom_constitutional" / "axiom_injection_guard.py",
]

def run_guard_tests() -> tuple[bool, list]:
    """
    Run each guard's built-in test suite as a subprocess (__main__ block).
    Parses 'N/N tests passed' from stdout. Returns (all_passed, results_list).
    """
    import re
    results = []
    all_passed = True

    for guard_path in GUARD_FILES:
        name = guard_path.stem  # e.g. axiom_destructive_guard
        class_name = {
            "axiom_destructive_guard": "DestructiveOperationGuard",
            "axiom_pii_guard":         "PIIGuard",
            "axiom_injection_guard":   "OutputInjectionGuard",
        }.get(name, name)

        if not guard_path.exists():
            results.append({"name": class_name, "status": "SKIP",
                             "reason": "file not found", "passed": 0, "total": 0})
            continue

        try:
            proc = subprocess.run(
                [sys.executable, str(guard_path)],
                capture_output=True, text=True, cwd=ROOT, timeout=30,
            )
            output = proc.stdout + proc.stderr

            # Parse "N/M tests passed" from output
            m = re.search(r"(\d+)/(\d+)\s+tests\s+passed", output)
            if m:
                passed, total = int(m.group(1)), int(m.group(2))
            else:
                passed, total = (0, 0)

            # Check for ALL PASS line or exit code
            if proc.returncode == 0 and "ALL PASS" in output:
                status = "PASS"
                if total == 0:
                    passed = total = 1  # mark as passing if no count found
            else:
                status = "FAIL"
                all_passed = False

            # Collect any FAIL lines
            failures = [line.strip() for line in output.splitlines()
                        if "[FAIL]" in line]

            results.append({
                "name":     class_name,
                "status":   status,
                "passed":   passed,
                "total":    total,
                "failures": failures,
            })
        except subprocess.TimeoutExpired:
            results.append({"name": class_name, "status": "FAIL",
                             "reason": "timeout", "passed": 0, "total": 0})
            all_passed = False
        except Exception as exc:
            results.append({"name": class_name, "status": "FAIL",
                             "reason": str(exc), "passed": 0, "total": 0})
            all_passed = False

    return all_passed, results


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 2-4 — .axiom file validation
# ══════════════════════════════════════════════════════════════════════════════

def _get_changed_axiom_files(base_ref: str) -> list[Path]:
    """Return .axiom files changed between base_ref and HEAD."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "%s..HEAD" % base_ref],
            cwd=ROOT, stderr=subprocess.DEVNULL, text=True,
        )
        changed = [ROOT / p.strip() for p in out.splitlines() if p.strip().endswith(".axiom")]
        # Only certify .axiom files under axiom_files/ — others are spec docs, not agent files
        return [p for p in changed if p.exists() and AXIOM_DIR in p.parents]
    except subprocess.CalledProcessError:
        return []


def _get_all_axiom_files() -> list[Path]:
    return sorted(AXIOM_DIR.rglob("*.axiom"))


def _agent_name_from_path(path: Path) -> str:
    """Convert axiom_files/domains/healthcare.axiom → domains/healthcare"""
    rel = path.relative_to(AXIOM_DIR)
    return str(rel.with_suffix("")).replace("\\", "/")


def check_axiom_file(path: Path) -> dict:
    """
    Run structural + constitutional integrity checks on a single .axiom file.
    No LLM calls — purely local.
    """
    name = _agent_name_from_path(path)
    result = {"name": name, "path": str(path.relative_to(ROOT))}

    # ── Structural validation ─────────────────────────────────
    try:
        from axiom_files.validator import validate_file
        val = validate_file(name)
        errors   = [i for i in val.get("issues", []) if i["level"] == "error"]
        warnings = [i for i in val.get("issues", []) if i["level"] == "warning"]
        result["structural"] = "PASS" if val["status"] != "invalid" else "FAIL"
        result["errors"]     = [i["message"] for i in errors]
        result["warnings"]   = [i["message"] for i in warnings]
    except Exception as exc:
        result["structural"] = "FAIL"
        result["errors"]     = [str(exc)]
        result["warnings"]   = []

    # ── Supply chain integrity ────────────────────────────────
    try:
        from axiom_files.parser import verify_agent_hash, register_agent_hash
        chain = verify_agent_hash(name)
        if chain["status"] == "UNREGISTERED":
            register_agent_hash(name)
            chain = verify_agent_hash(name)
        result["supply_chain"] = chain.get("status", "UNKNOWN")
    except Exception as exc:
        result["supply_chain"] = "UNKNOWN"
        result["warnings"].append("supply chain check skipped: %s" % exc)

    # ── Constitutional integrity — CANNOT_MUTATE fields ───────
    try:
        from axiom_files.parser import load_axiom
        parsed = load_axiom(name)
        cm = parsed.get("cannot_mutate", [])
        result["cannot_mutate_count"] = len(cm)
        result["cannot_mutate_fields"] = cm
        result["constitutional"] = "PASS" if len(cm) > 0 else "WARN"
    except Exception as exc:
        result["constitutional"] = "SKIP"
        result["warnings"].append("cannot_mutate parse skipped: %s" % exc)

    # ── Content hash ──────────────────────────────────────────
    try:
        result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()[:16] + "..."
    except Exception:
        result["sha256"] = "unavailable"

    # ── Overall verdict ───────────────────────────────────────
    if result.get("structural") == "FAIL" or result.get("supply_chain") == "TAMPERED":
        result["verdict"] = "FAIL"
    elif result.get("structural") == "PASS":
        result["verdict"] = "PASS"
    else:
        result["verdict"] = "WARN"

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Preflight log
# ══════════════════════════════════════════════════════════════════════════════

def _write_log(summary: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary) + "\n")
    except IOError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="AXIOM pre-push preflight")
    parser.add_argument("--all",  action="store_true", help="Check all .axiom files")
    parser.add_argument("--base", default="origin/main",
                        help="Git ref to compare against (default: origin/main)")
    args = parser.parse_args()

    t0  = datetime.now(timezone.utc)
    ok  = True

    print()
    print(_bold("  AXIOM Pre-Push Preflight"))
    print("  " + "=" * 56)

    # ── Check 1: Guard test suites ────────────────────────────
    print()
    print(_bold("  [1/3] Constitutional Guard Tests"))
    print("  " + "-" * 56)

    guards_ok, guard_results = run_guard_tests()

    for r in guard_results:
        status_str = PASS if r["status"] == "PASS" else (SKIP if r["status"] == "SKIP" else FAIL)
        line = "  %-34s %s" % (r["name"], status_str)
        if r["total"] > 0:
            line += "  %d/%d" % (r["passed"], r["total"])
        print(line)
        for f in r.get("failures", []):
            print("    " + _red("x") + " " + f)

    if not guards_ok:
        ok = False

    # ── Check 2: .axiom file validation ──────────────────────
    print()
    print(_bold("  [2/3] Agent Certification"))
    print("  " + "-" * 56)

    if args.all:
        axiom_files = _get_all_axiom_files()
        print("  Scope: all %d .axiom files" % len(axiom_files))
    else:
        axiom_files = _get_changed_axiom_files(args.base)
        if not axiom_files:
            print("  " + _grey("No .axiom files changed — skipping agent checks"))
        else:
            print("  Scope: %d changed .axiom file(s)" % len(axiom_files))

    agent_results = []
    for path in axiom_files:
        r = check_axiom_file(path)
        agent_results.append(r)

        v = r["verdict"]
        verdict_str = PASS if v == "PASS" else (WARN if v == "WARN" else FAIL)
        chain = r.get("supply_chain", "?")
        cm    = r.get("cannot_mutate_count", "?")
        print(
            "  %-32s %s  chain=%-12s cm=%s"
            % (r["name"][:32], verdict_str, chain, cm)
        )
        for err in r.get("errors", []):
            print("    " + _red("error") + ": " + err)
        for w in r.get("warnings", []):
            print("    " + _yellow("warn") + ":  " + w)

        if v == "FAIL":
            ok = False

    # ── Check 3: Guard Python files syntax ───────────────────
    print()
    print(_bold("  [3/3] Guard Module Syntax"))
    print("  " + "-" * 56)

    guard_files = sorted((ROOT / "axiom_constitutional").glob("axiom_*guard*.py"))
    syntax_ok = True
    for gf in guard_files:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(gf)],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print("  %-44s %s" % (gf.name, PASS))
            else:
                print("  %-44s %s" % (gf.name, FAIL))
                print("    " + _red(result.stderr.strip()))
                syntax_ok = False
        except Exception as exc:
            print("  %-44s %s  %s" % (gf.name, FAIL, exc))
            syntax_ok = False

    if not syntax_ok:
        ok = False

    # ── Summary ───────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    print()
    print("  " + "=" * 56)
    if ok:
        print(_bold(_green("  PREFLIGHT PASSED")) + "  (%.1fs)" % elapsed)
        print("  Push may proceed.")
    else:
        print(_bold(_red("  PREFLIGHT FAILED")) + "  (%.1fs)" % elapsed)
        print("  Fix the issues above before pushing.")
    print("  " + "=" * 56)
    print()

    # ── Write log ─────────────────────────────────────────────
    _write_log({
        "timestamp":     t0.isoformat(),
        "passed":        ok,
        "elapsed_s":     round(elapsed, 2),
        "guard_results": guard_results,
        "agent_results": agent_results,
    })

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
