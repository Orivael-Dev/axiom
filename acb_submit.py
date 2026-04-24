"""
AXIOM Constitutional Benchmark — Submission Validator v1.0
==========================================================
Validates a submitted ACB manifest and adds it to the leaderboard.

Usage:
  python acb_submit.py axiom_acb_manifest.json
  python acb_submit.py axiom_acb_manifest.json --leaderboard leaderboard.json
  python acb_submit.py --show-leaderboard
"""

import sys, json, hashlib, hmac, os
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

LEADERBOARD_FILE = "acb_leaderboard.json"
REQUIRED_TESTS = 75
ACB_VERSION = "1.0"

REQUIRED_MODULES = [
    "A_honesty", "B_ambiguity", "C_pressure",
    "D_consistency", "E_identity", "F_calibration"
]

REQUIRED_GATES = ["honesty", "ambiguity", "pressure", "consistency"]


def validate_manifest(manifest):
    """
    Validate a submitted manifest.
    Returns (valid: bool, errors: list, warnings: list)
    """
    errors = []
    warnings = []

    # Version check
    if manifest.get("acb_version") != ACB_VERSION:
        errors.append(f"ACB version mismatch: got {manifest.get('acb_version')} expected {ACB_VERSION}")

    # Required fields
    for field in ["model_name", "run_timestamp", "overall_pct",
                  "certification", "module_scores", "gates", "scores", "signature"]:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    if errors:
        return False, errors, warnings

    # Test count
    scores = manifest.get("scores", {})
    if len(scores) < REQUIRED_TESTS:
        errors.append(f"Only {len(scores)} tests present — {REQUIRED_TESTS} required")

    # Check all ACB test IDs present
    expected_prefixes = ["ACB-A", "ACB-B", "ACB-C", "ACB-D", "ACB-E", "ACB-F"]
    for prefix in expected_prefixes:
        found = sum(1 for k in scores if k.startswith(prefix))
        if found == 0:
            errors.append(f"No tests found for prefix {prefix}")

    # Module scores present
    for mod in REQUIRED_MODULES:
        if mod not in manifest.get("module_scores", {}):
            errors.append(f"Missing module score: {mod}")

    # Gates present and valid
    for gate in REQUIRED_GATES:
        gate_data = manifest.get("gates", {}).get(gate)
        if not gate_data:
            errors.append(f"Missing gate: {gate}")
        else:
            if "score" not in gate_data or "required" not in gate_data or "passed" not in gate_data:
                errors.append(f"Gate {gate} missing score/required/passed fields")

    # Verify scores are consistent
    if scores and "total_score" in manifest:
        calc_total = sum(r.get("score", 0) for r in scores.values())
        stated_total = manifest.get("total_score", 0)
        if abs(calc_total - stated_total) > 1:
            errors.append(f"Score mismatch: calculated {calc_total} stated {stated_total}")

    # Verify overall percentage
    if scores:
        calc_pct = round(sum(r.get("score", 0) for r in scores.values()) / (len(scores) * 2) * 100, 1)
        stated_pct = manifest.get("overall_pct", 0)
        if abs(calc_pct - stated_pct) > 1.0:
            warnings.append(f"Percentage may be off: calculated {calc_pct}% stated {stated_pct}%")

    # Verify signature
    try:
        check = {k: v for k, v in manifest.items() if k not in ("scores", "signature")}
        sig_str = json.dumps(check, sort_keys=True)
        expected_sig = hmac.new(b"axiom-acb-v1", sig_str.encode(), hashlib.sha256).hexdigest()
        if manifest.get("signature") != expected_sig:
            warnings.append("Signature mismatch — manifest may have been modified after signing")
    except Exception as e:
        warnings.append(f"Could not verify signature: {e}")

    return len(errors) == 0, errors, warnings


def format_entry(manifest):
    """Format a manifest as a leaderboard entry."""
    gates = manifest.get("gates", {})
    gate_count = sum(1 for g in gates.values() if g.get("passed"))
    gate_str = f"{gate_count}/4 {'✅' if gate_count == 4 else '❌'}"

    return {
        "model_name":     manifest.get("model_name", "Unknown"),
        "endpoint":       manifest.get("endpoint", "Unknown"),
        "overall_pct":    manifest.get("overall_pct", 0),
        "certification":  manifest.get("certification", "Unknown"),
        "module_scores":  manifest.get("module_scores", {}),
        "gates_passed":   gate_count,
        "gate_str":       gate_str,
        "run_timestamp":  manifest.get("run_timestamp", ""),
        "runner_version": manifest.get("runner_version", ""),
        "acb_version":    manifest.get("acb_version", ""),
    }


def load_leaderboard(path):
    """Load existing leaderboard or create new one."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "acb_version": ACB_VERSION,
        "created": datetime.now().isoformat(),
        "entries": []
    }


def save_leaderboard(leaderboard, path):
    """Save leaderboard sorted by overall score."""
    leaderboard["entries"].sort(
        key=lambda x: (x.get("gates_passed", 0), x.get("overall_pct", 0)),
        reverse=True
    )
    leaderboard["last_updated"] = datetime.now().isoformat()
    with open(path, "w") as f:
        json.dump(leaderboard, f, indent=2)


def print_leaderboard(leaderboard):
    """Print the leaderboard in a readable format."""
    print()
    print("AXIOM Constitutional Benchmark — Leaderboard")
    print("=" * 80)
    print(f"{'Model':<30} {'Overall':>8} {'Honesty':>8} {'Ambig':>7} {'Press':>7} {'Consist':>8} {'Gates':>6} {'Cert'}")
    print("-" * 80)

    entries = leaderboard.get("entries", [])
    if not entries:
        print("  No submissions yet. Be the first!")
        print("  Run: python acb_runner.py --model your-model")
        print("  Then: python acb_submit.py axiom_acb_manifest.json")
        return

    for entry in entries:
        mods = entry.get("module_scores", {})
        print(
            f"  {entry['model_name']:<28} "
            f"{entry['overall_pct']:>7.1f}% "
            f"{mods.get('A_honesty', 0):>7.1f}% "
            f"{mods.get('B_ambiguity', 0):>7.1f}% "
            f"{mods.get('C_pressure', 0):>6.1f}% "
            f"{mods.get('D_consistency', 0):>7.1f}% "
            f"  {entry['gate_str']:>6}  "
            f"{entry['certification']}"
        )


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--show-leaderboard":
        lb_path = sys.argv[2] if len(sys.argv) > 2 else LEADERBOARD_FILE
        leaderboard = load_leaderboard(lb_path)
        print_leaderboard(leaderboard)
        return

    manifest_file = sys.argv[1]
    lb_path = LEADERBOARD_FILE

    # Parse args
    for i, arg in enumerate(sys.argv):
        if arg == "--leaderboard" and i + 1 < len(sys.argv):
            lb_path = sys.argv[i + 1]

    # Load manifest
    if not os.path.exists(manifest_file):
        print(f"File not found: {manifest_file}")
        sys.exit(1)

    with open(manifest_file) as f:
        manifest = json.load(f)

    print()
    print("AXIOM Constitutional Benchmark — Submission Validator")
    print("=" * 60)
    print(f"File:  {manifest_file}")
    print(f"Model: {manifest.get('model_name', 'Unknown')}")
    print()

    # Validate
    valid, errors, warnings = validate_manifest(manifest)

    if errors:
        print("❌ VALIDATION FAILED")
        for e in errors:
            print(f"  ERROR: {e}")
        sys.exit(1)

    if warnings:
        print("⚠️  WARNINGS:")
        for w in warnings:
            print(f"  WARNING: {w}")
        print()

    print("✅ VALIDATION PASSED")
    print()

    # Show results
    print(f"Overall score:   {manifest.get('overall_pct')}%")
    print(f"Certification:   {manifest.get('certification')}")
    print()

    gates = manifest.get("gates", {})
    print("Gates:")
    for gate, data in gates.items():
        icon = "✅" if data.get("passed") else "❌"
        print(f"  {icon} {gate:<15s} {data.get('score')}% / {data.get('required')}%")
    print()

    # Add to leaderboard
    leaderboard = load_leaderboard(lb_path)
    entry = format_entry(manifest)

    # Check for duplicate
    existing = [e for e in leaderboard["entries"]
                if e["model_name"] == entry["model_name"]]
    if existing:
        print(f"Model '{entry['model_name']}' already in leaderboard.")
        old_score = existing[0]["overall_pct"]
        if entry["overall_pct"] > old_score:
            print(f"New score {entry['overall_pct']}% > old score {old_score}%. Updating.")
            leaderboard["entries"] = [e for e in leaderboard["entries"]
                                      if e["model_name"] != entry["model_name"]]
            leaderboard["entries"].append(entry)
        else:
            print(f"Old score {old_score}% >= new score {entry['overall_pct']}%. Keeping old.")
    else:
        leaderboard["entries"].append(entry)
        print(f"Added '{entry['model_name']}' to leaderboard.")

    save_leaderboard(leaderboard, lb_path)
    print(f"Leaderboard saved: {lb_path}")
    print()

    # Print updated leaderboard
    print_leaderboard(leaderboard)


if __name__ == "__main__":
    main()
