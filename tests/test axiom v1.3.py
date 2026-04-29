"""
AXIOM v1.3 Test Suite
Validation, Purity, and Behavior Testing Framework
"""
import sys
import os
import json
import tempfile

# ── Path setup ────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"

results = []

def test(name: str, passed: bool, note: str = ""):
    status = PASS if passed else FAIL
    results.append((status, name, note))
    print(f"  {status}  {name}" + (f" — {note}" if note else ""))

def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ── Import AXIOM modules ──────────────────────────────────────
try:
    from axiom_files.parser import (
        load_axiom, save_axiom, to_system_prompt, get_prompt,
        detect_overlays, get_prompt_with_overlays,
        detect_concepts, get_prompt_with_concepts,
    )
    from axiom_files.validator import validate, validate_file
    IMPORTS_OK = True
except Exception as e:
    print(f"❌ IMPORT FAILED: {e}")
    IMPORTS_OK = False
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# 1. STRUCTURAL TESTS
# ═══════════════════════════════════════════════════════════════
section("1. STRUCTURAL TESTS")

# 1.1 Valid file with all required sections
try:
    p = load_axiom("worker")
    test("Valid worker.axiom loads without error",
         bool(p["agent"] and p["purpose"]))
except Exception as e:
    test("Valid worker.axiom loads without error", False, str(e))

# 1.2 Missing GOAL triggers no error (PURPOSE covers it) but missing BOTH should
minimal_ok = validate({"agent": "TestAgent", "purpose": "Do something", "goal": "",
                        "constraints": [], "rules": [], "process": [], "check": [],
                        "failure": [], "output": [], "success": {}, "tools": [],
                        "concepts": [], "version": "1.0", "receives": {}, "emits": {},
                        "mutates": [], "cannot_mutate": []})
test("Agent with PURPOSE but no GOAL is valid",
     minimal_ok["status"] == "valid")

missing_both = validate({"agent": "TestAgent", "purpose": "", "goal": "",
                          "constraints": [], "rules": [], "process": [], "check": [],
                          "failure": [], "output": [], "success": {}, "tools": [],
                          "concepts": [], "version": "1.0", "receives": {}, "emits": {},
                          "mutates": [], "cannot_mutate": []})
test("Agent missing both PURPOSE and GOAL triggers error",
     missing_both["status"] == "invalid",
     f"status={missing_both['status']}")

# 1.3 Unknown blocks — parser should ignore gracefully (not crash)
try:
    with tempfile.NamedTemporaryFile(mode='w', suffix='.axiom',
                                      dir='axiom_files', delete=False) as f:
        f.write("AGENT UnknownBlockAgent\nVERSION 1.0\nPURPOSE Test unknown blocks\n")
        f.write("FUNCTION doSomething\n- step one\n")
        fname = os.path.basename(f.name).replace('.axiom', '')
    p2 = load_axiom(fname)
    test("Unknown block (FUNCTION) parsed without crash", True)
    os.remove(f.name)
except Exception as e:
    test("Unknown block (FUNCTION) parsed without crash", False, str(e))

# 1.4 Malformed SUCCESS weights trigger warning
bad_weights = validate({"agent": "A", "purpose": "B", "goal": "C",
                          "constraints": [], "rules": [], "process": [], "check": [],
                          "failure": [], "output": [],
                          "success": {"clarity": 0.3, "accuracy": 0.3},
                          "tools": [], "concepts": [], "version": "1.0",
                          "receives": {}, "emits": [], "mutates": [], "cannot_mutate": []})
weight_warn = any(i["field"] == "success" for i in bad_weights["issues"])
test("Malformed SUCCESS weights (sum != 1.0) triggers warning", weight_warn,
     f"status={bad_weights['status']}")

# 1.5 Duplicate constraint/rule overlap flagged
overlap = validate({"agent": "A", "purpose": "B", "goal": "",
                     "constraints": ["Be concise"], "rules": ["Be concise"],
                     "process": [], "check": [], "failure": [], "output": [],
                     "success": {}, "tools": [], "concepts": [], "version": "1.0",
                     "receives": {}, "emits": {}, "mutates": [], "cannot_mutate": []})
overlap_found = any("overlap" in i["message"].lower() or "duplicate" in i["message"].lower()
                     for i in overlap["issues"])
test("Duplicate constraint/rule overlap flagged", overlap_found)

# ═══════════════════════════════════════════════════════════════
# 2. PURITY TESTS
# ═══════════════════════════════════════════════════════════════
section("2. PURITY TESTS")

def purity_check(text: str) -> bool:
    """Returns True if purity violation detected."""
    parsed = {"agent": "A", "purpose": "B", "goal": "",
              "constraints": [text], "rules": [], "process": [],
              "check": [], "failure": [], "output": [], "success": {},
              "tools": [], "concepts": [], "version": "1.0",
              "receives": {}, "emits": {}, "mutates": [], "cannot_mutate": []}
    result = validate(parsed)
    return any(i["phase"] == "purity" for i in result["issues"])

test("Detect Python 'def' keyword", purity_check("def process_task(input):"))
test("Detect Python 'class' keyword", purity_check("class MyAgent:"))
test("Detect Python 'return' keyword", purity_check("return the result"))
test("Detect Python 'for' loop", purity_check("for item in list: process"))
test("Detect 'import' statement", purity_check("import json"))
test("Detect 'lambda' expression", purity_check("lambda x: x + 1"))
test("Detect walrus operator ':='", purity_check("value := compute()"))

# Clean declarative PROCESS should pass
clean_parsed = {"agent": "A", "purpose": "B", "goal": "",
                "constraints": [], "rules": [],
                "process": ["Understand the task", "Produce the answer",
                             "Evaluate quality"],
                "check": [], "failure": [], "output": [], "success": {},
                "tools": [], "concepts": [], "version": "1.0",
                "receives": {}, "emits": {}, "mutates": [], "cannot_mutate": []}
clean_result = validate(clean_parsed)
purity_clean = not any(i["phase"] == "purity" for i in clean_result["issues"])
test("Clean declarative PROCESS passes purity", purity_clean)

# Procedural drift in PROCESS
proc_parsed = {"agent": "A", "purpose": "B", "goal": "",
               "constraints": [], "rules": [],
               "process": ["if task is unclear ask for clarification"],
               "check": [], "failure": [], "output": [], "success": {},
               "tools": [], "concepts": [], "version": "1.0",
               "receives": {}, "emits": {}, "mutates": [], "cannot_mutate": []}
proc_result = validate(proc_parsed)
proc_flagged = any(i["phase"] == "semantic" and i["field"] == "process"
                    for i in proc_result["issues"])
test("Procedural 'if' in PROCESS flagged as semantic error", proc_flagged)

# ═══════════════════════════════════════════════════════════════
# 3. CONCEPT TESTS
# ═══════════════════════════════════════════════════════════════
section("3. CONCEPT TESTS")

# 3.1 Valid CONCEPT with all fields
valid_concept = {
    "name": "TestConcept",
    "purpose": "Test purposes",
    "applies_when": "test scenario example",
    "requires": "Provide a test-specific output",
    "effect": "Agent must include test-specific validation"
}
full_parsed = {"agent": "A", "purpose": "B", "goal": "",
               "constraints": [], "rules": [], "process": [], "check": [],
               "failure": [], "output": [], "success": {}, "tools": [],
               "concepts": [valid_concept], "version": "1.0",
               "receives": {}, "emits": {}, "mutates": [], "cannot_mutate": []}
concept_result = validate(full_parsed)
concept_ok = not any(i["field"].startswith("concepts") for i in concept_result["issues"])
test("Valid CONCEPT with all 4 fields passes validation", concept_ok)

# 3.2 Incomplete CONCEPT missing EFFECT
incomplete_concept = {
    "name": "IncompleteC",
    "purpose": "Something",
    "applies_when": "some keywords",
    "requires": "Something required",
    "effect": ""  # missing
}
inc_parsed = {**full_parsed, "concepts": [incomplete_concept]}
inc_result = validate(inc_parsed)
inc_flagged = any("IncompleteC" in i["field"] for i in inc_result["issues"])
test("CONCEPT missing EFFECT triggers error", inc_flagged)

# 3.3 Multiple CONCEPT blocks parse correctly
try:
    lib = load_axiom("concepts")
    test("concepts.axiom loads with multiple concepts",
         len(lib["concepts"]) >= 2,
         f"found {len(lib['concepts'])} concepts")
except Exception as e:
    test("concepts.axiom loads with multiple concepts", False, str(e))

# 3.4 detect_concepts matches keywords
try:
    lib = load_axiom("concepts")
    matched = detect_concepts("design a reward function to maximize utility", lib)
    test("RewardGuard concept detected for reward/utility task",
         "RewardGuard" in matched, f"matched={matched}")
except Exception as e:
    test("RewardGuard concept detected for reward/utility task", False, str(e))

# 3.5 detect_concepts returns empty for non-matching task
try:
    lib = load_axiom("concepts")
    matched2 = detect_concepts("write a poem about autumn leaves", lib)
    test("No concept injected for unrelated task",
         len(matched2) == 0, f"matched={matched2}")
except Exception as e:
    test("No concept injected for unrelated task", False, str(e))

# ═══════════════════════════════════════════════════════════════
# 4. BEHAVIOR TESTS
# ═══════════════════════════════════════════════════════════════
section("4. BEHAVIOR TESTS")

# 4.1 Simple task — worker prompt loads and contains goal
try:
    prompt = get_prompt("worker")
    test("Worker prompt generated for simple task",
         "Complete the user's request" in prompt)
except Exception as e:
    test("Worker prompt generated for simple task", False, str(e))

# 4.2 Analytical task — reward_analysis overlay activates
try:
    overlays = detect_overlays("define a reward function to maximize user retention")
    test("reward_analysis overlay detected for analytical task",
         "reward_analysis" in overlays, f"overlays={overlays}")
except Exception as e:
    test("reward_analysis overlay detected for analytical task", False, str(e))

# 4.3 Trap task — Python function request does NOT inject Python
try:
    prompt_with_concepts = get_prompt_with_concepts(
        "worker", "write a Python function to sort a list"
    )
    has_python = "def " in prompt_with_concepts or "import " in prompt_with_concepts
    test("Prompt for Python request does not inject Python code",
         not has_python)
except Exception as e:
    test("Prompt for Python request does not inject Python code", False, str(e))

# 4.4 Ambiguous task — FAILURE section present to handle clarification
try:
    p = load_axiom("worker")
    has_failure = len(p.get("failure", [])) > 0
    test("Worker has FAILURE block to handle ambiguous tasks",
         has_failure, f"failure items={len(p.get('failure', []))}")
except Exception as e:
    test("Worker has FAILURE block to handle ambiguous tasks", False, str(e))

# 4.5 Output follows constraints — OUTPUT section present
try:
    p = load_axiom("worker")
    has_output = len(p.get("output", [])) > 0
    test("Worker has OUTPUT block defining response format",
         has_output, f"output items={len(p.get('output', []))}")
except Exception as e:
    test("Worker has OUTPUT block defining response format", False, str(e))

# ═══════════════════════════════════════════════════════════════
# 5. VALIDATOR OUTPUT TESTS
# ═══════════════════════════════════════════════════════════════
section("5. VALIDATOR OUTPUT TESTS")

# 5.1 Valid file returns status=valid
r = validate_file("worker")
test("validate_file('worker') returns status=valid",
     r["status"] == "valid", f"status={r['status']}")

# 5.2 Issues list is present (even if empty)
test("validate_file result contains 'issues' key",
     "issues" in r)

# 5.3 Suggestions list is present
test("validate_file result contains 'suggestions' key",
     "suggestions" in r)

# 5.4 Invalid file returns meaningful feedback
bad = validate({"agent": "", "purpose": "", "goal": "",
                "constraints": [], "rules": [], "process": [], "check": [],
                "failure": [], "output": [], "success": {}, "tools": [],
                "concepts": [], "version": "1.0", "receives": {}, "emits": {},
                "mutates": [], "cannot_mutate": []})
test("Invalid agent returns meaningful issues list",
     len(bad["issues"]) > 0 and bad["status"] == "invalid")

# 5.5 Consistency — same file validates same way twice
r1 = validate_file("evaluator")
r2 = validate_file("evaluator")
test("Validator is deterministic (same result on repeat runs)",
     r1["status"] == r2["status"] and len(r1["issues"]) == len(r2["issues"]))

# ═══════════════════════════════════════════════════════════════
# 6. SELF-EVOLUTION TESTS
# ═══════════════════════════════════════════════════════════════
section("6. SELF-EVOLUTION TESTS")

# 6.1 All three core agents validate clean
for agent in ["worker", "evaluator", "rewriter"]:
    r = validate_file(agent)
    test(f"{agent}.axiom validates clean (status=valid)",
         r["status"] == "valid", f"status={r['status']}, issues={len(r['issues'])}")

# 6.2 save_axiom roundtrip preserves structure
try:
    original = load_axiom("worker")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.axiom',
                                      dir='axiom_files', delete=False,
                                      prefix='roundtrip_') as f:
        tmpname = os.path.basename(f.name).replace('.axiom', '')
    save_axiom(tmpname, original)
    reloaded = load_axiom(tmpname)
    os.remove(os.path.join('axiom_files', f'{tmpname}.axiom'))
    test("save_axiom → load_axiom roundtrip preserves agent name",
         reloaded["agent"] == original["agent"])
    test("save_axiom → load_axiom roundtrip preserves constraints count",
         len(reloaded["constraints"]) == len(original["constraints"]))
    test("save_axiom → load_axiom roundtrip preserves version",
         reloaded["version"] == original["version"])
except Exception as e:
    test("save_axiom roundtrip", False, str(e))

# 6.3 Evolved file still validates (simulate a version bump)
try:
    evolved = load_axiom("worker")
    evolved["version"] = "1.9"
    evolved["constraints"].append("Validate output against all known constraints")
    evolved_result = validate(evolved)
    test("Evolved worker definition still validates",
         evolved_result["status"] in ("valid", "warning"),
         f"status={evolved_result['status']}")
except Exception as e:
    test("Evolved worker definition still validates", False, str(e))

# 6.4 No external code introduced during evolution simulation
try:
    evolved2 = load_axiom("worker")
    # Simulate a bad rewrite that introduces Python
    evolved2["constraints"].append("def validate(x): return x > 0")
    bad_evolved = validate(evolved2)
    test("Purity check catches external code in evolved definition",
         any(i["phase"] == "purity" for i in bad_evolved["issues"]))
except Exception as e:
    test("Purity check catches external code in evolved definition", False, str(e))

# 6.5 CONCEPT used for new ideas (concepts.axiom is valid)
try:
    c_result = validate_file("concepts")
    test("concepts.axiom itself validates cleanly",
         c_result["status"] in ("valid", "warning"),
         f"status={c_result['status']}")
except Exception as e:
    test("concepts.axiom itself validates cleanly", False, str(e))

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print("  AXIOM v1.3 TEST SUITE RESULTS")
print(f"{'='*60}")

passed = sum(1 for s, _, _ in results if s == PASS)
failed = sum(1 for s, _, _ in results if s == FAIL)
total = len(results)

print(f"\n  Passed: {passed}/{total}")
print(f"  Failed: {failed}/{total}")

if failed > 0:
    print("\n  Failed tests:")
    for status, name, note in results:
        if status == FAIL:
            print(f"    ❌ {name}" + (f" — {note}" if note else ""))

print(f"\n{'='*60}")
if failed == 0:
    print("  ✅ ALL TESTS PASSED — AXIOM v1.3 IS STABLE")
else:
    print(f"  ⚠️  {failed} TEST(S) FAILED — REVIEW ABOVE")
print(f"{'='*60}\n")

sys.exit(0 if failed == 0 else 1)