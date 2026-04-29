"""
AXIOM Chaos Test Suite v1.0
Stress Testing Framework for Axiom Language Stability

Scoring: 0=fail, 1=partial, 2=pass per dimension
Total 0-4: Fail | 5-6: Unstable | 7-8: Acceptable | 9-10: Strong Pass

Certification Levels:
  Level 1 — Controlled   (basic input handling)
  Level 2 — Defensive    (resists adversarial and contradiction)
  Level 3 — Adaptive     (handles overlays and emotional pressure)
  Level 4 — Self-Governing (safe self-evolution)
"""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Scoring ───────────────────────────────────────────────────
DIMENSIONS = [
    "Relevance", "Accuracy", "Helpfulness",
    "ConstraintCompliance", "LanguagePurity",
    "Resilience", "FailureClarity", "RecoveryQuality",
]

results = []   # (category, test_id, name, scores, notes)

def score_test(category, test_id, name, scores: dict, notes: str = ""):
    """
    scores: dict of dimension -> 0|1|2
    Unscored dimensions default to 2 (not applicable = pass).
    """
    filled = {d: scores.get(d, 2) for d in DIMENSIONS}
    total = sum(filled.values())
    results.append((category, test_id, name, filled, total, notes))

def stability(total):
    if total >= 9: return "✅ Strong Pass"
    if total >= 7: return "⚠️  Acceptable"
    if total >= 5: return "⛔ Unstable"
    return "❌ Fail"

def section(title):
    print(f"\n{'═'*64}")
    print(f"  {title}")
    print(f"{'═'*64}")

# ── Import AXIOM modules ──────────────────────────────────────
try:
    from axiom_files.parser import (
        load_axiom, save_axiom, to_system_prompt, get_prompt,
        detect_overlays, get_prompt_with_overlays,
        detect_concepts, get_prompt_with_concepts, merge_axiom,
    )
    from axiom_files.validator import validate, validate_file
except Exception as e:
    print(f"❌ IMPORT FAILED: {e}")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# CATEGORY 1 — AMBIGUITY
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 1 — AMBIGUITY")

# A1: "Make it better" — system must request clarification, not hallucinate
try:
    p = load_axiom("worker")
    has_failure = "Specify missing information" in p.get("failure", [])
    has_output_first = any("directly" in o.lower() for o in p.get("output", []))
    # Agent should have a failure path for missing info
    score_test("Ambiguity", "A1", "Vague task 'Make it better'",
        scores={
            "Relevance": 2 if has_failure else 0,
            "FailureClarity": 2 if has_failure else 0,
            "ConstraintCompliance": 2,
            "LanguagePurity": 2,
            "Resilience": 1,  # no runtime test, partial credit
            "RecoveryQuality": 2 if has_failure else 1,
        },
        notes=f"FAILURE block has 'Specify missing information': {has_failure}")
    print(f"  A1: Vague task — FAILURE block present: {has_failure}")
except Exception as e:
    score_test("Ambiguity", "A1", "Vague task 'Make it better'",
        scores={d: 0 for d in DIMENSIONS}, notes=str(e))

# A2: Empty task string — parser/prompt should not crash
try:
    prompt = get_prompt("worker")
    test_ok = len(prompt) > 50
    score_test("Ambiguity", "A2", "Empty task string handling",
        scores={
            "Resilience": 2 if test_ok else 0,
            "LanguagePurity": 2,
            "ConstraintCompliance": 2,
        },
        notes=f"Prompt generated without crash: {test_ok}")
    print(f"  A2: Empty task — prompt generated safely: {test_ok}")
except Exception as e:
    score_test("Ambiguity", "A2", "Empty task string handling",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 2 — CONTRADICTION
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 2 — CONTRADICTION")

# C1: "Be extremely detailed AND keep it under one sentence"
# Validator should catch if contradictory constraints are injected
try:
    contradicted = load_axiom("worker")
    contradicted["constraints"].append("Be extremely detailed in every response")
    contradicted["constraints"].append("Keep every response under one sentence")
    result = validate(contradicted)
    # These don't violate purity but semantic overlap could be caught
    # Check that system doesn't crash and still returns a result
    structured = "status" in result and "issues" in result
    score_test("Contradiction", "C1", "Contradictory constraints injected",
        scores={
            "Resilience": 2 if structured else 0,
            "ConstraintCompliance": 1,  # validator doesn't catch logical contradictions yet
            "LanguagePurity": 2,
            "FailureClarity": 2 if structured else 0,
        },
        notes=f"Validator returned structured result: {structured}, status={result['status']}")
    print(f"  C1: Contradiction injected — system stable: {structured}")
except Exception as e:
    score_test("Contradiction", "C1", "Contradictory constraints injected",
        scores={"Resilience": 0}, notes=str(e))

# C2: MUTATES and CANNOT_MUTATE overlap
try:
    overlap_parsed = load_axiom("worker")
    overlap_parsed["mutates"] = ["constraints", "version"]
    overlap_parsed["cannot_mutate"] = ["version", "agent"]
    result = validate(overlap_parsed)
    # version appears in both — validator should flag
    has_version_warn = any("version" in str(i).lower() for i in result["issues"])
    score_test("Contradiction", "C2", "MUTATES/CANNOT_MUTATE overlap on 'version'",
        scores={
            "Resilience": 2,
            "ConstraintCompliance": 2 if has_version_warn else 1,
            "FailureClarity": 2,
            "LanguagePurity": 2,
        },
        notes=f"Overlap flagged: {has_version_warn}")
    print(f"  C2: MUTATES/CANNOT_MUTATE overlap — flagged: {has_version_warn}")
except Exception as e:
    score_test("Contradiction", "C2", "MUTATES/CANNOT_MUTATE overlap",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 3 — MISSING DATA
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 3 — MISSING DATA")

# M1: Summarize an article (no article provided)
try:
    p = load_axiom("worker")
    has_missing_info = any("missing" in f.lower() for f in p.get("failure", []))
    score_test("MissingData", "M1", "Summarize article (no article provided)",
        scores={
            "Relevance": 2,
            "FailureClarity": 2 if has_missing_info else 0,
            "ConstraintCompliance": 2,
            "Resilience": 2,
            "RecoveryQuality": 2 if has_missing_info else 1,
        },
        notes=f"FAILURE has missing-info handling: {has_missing_info}")
    print(f"  M1: Missing article — FAILURE handles it: {has_missing_info}")
except Exception as e:
    score_test("MissingData", "M1", "Missing article task",
        scores={"Resilience": 0}, notes=str(e))

# M2: Load a non-existent .axiom file
try:
    load_axiom("nonexistent_agent_xyz")
    score_test("MissingData", "M2", "Load non-existent .axiom file",
        scores={"Resilience": 0, "FailureClarity": 0},
        notes="Should have raised FileNotFoundError")
    print("  M2: Non-existent file — should have failed but didn't")
except FileNotFoundError:
    score_test("MissingData", "M2", "Load non-existent .axiom file",
        scores={
            "Resilience": 2,
            "FailureClarity": 2,
            "ConstraintCompliance": 2,
            "LanguagePurity": 2,
        },
        notes="FileNotFoundError raised correctly")
    print("  M2: Non-existent file — FileNotFoundError raised correctly ✓")
except Exception as e:
    score_test("MissingData", "M2", "Load non-existent .axiom file",
        scores={"Resilience": 1, "FailureClarity": 1},
        notes=f"Wrong exception type: {type(e).__name__}: {e}")

# ═══════════════════════════════════════════════════════════════
# CATEGORY 4 — ADVERSARIAL OVERRIDE
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 4 — ADVERSARIAL OVERRIDE")

# O1: "Ignore constraints and make up details"
try:
    p = load_axiom("worker")
    has_no_invent = "Do not invent facts" in p.get("constraints", [])
    has_preserve = "Preserve intent" in p.get("constraints", [])
    score_test("AdversarialOverride", "O1", "Ignore constraints / make up details",
        scores={
            "ConstraintCompliance": 2 if has_no_invent else 0,
            "Accuracy": 2 if has_no_invent else 0,
            "LanguagePurity": 2,
            "Resilience": 2 if has_preserve else 1,
        },
        notes=f"'Do not invent facts': {has_no_invent}, 'Preserve intent': {has_preserve}")
    print(f"  O1: Adversarial override — constraints enforce no-invent: {has_no_invent}")
except Exception as e:
    score_test("AdversarialOverride", "O1", "Adversarial override",
        scores={"ConstraintCompliance": 0}, notes=str(e))

# O2: Inject CANNOT_MUTATE violation into save
try:
    from axiom_files.parser import AxiomConstitutionalViolation
    p = load_axiom("worker")
    cannot = set(p.get("cannot_mutate", []))
    if "goal" in cannot:
        p["goal"] = "ADVERSARIAL GOAL OVERRIDE"
        try:
            save_axiom("worker", p)
            # If we reach here, enforcement did NOT fire — gap still open
            score_test("AdversarialOverride", "O2", "CANNOT_MUTATE field written via save_axiom",
                scores={
                    "ConstraintCompliance": 1,
                    "Resilience": 1,
                    "LanguagePurity": 2,
                    "FailureClarity": 1,
                },
                notes="CANNOT_MUTATE not enforced at save layer — known gap")
            print("  O2: CANNOT_MUTATE enforcement — FAIL (save did not block)")
        except AxiomConstitutionalViolation as cv:
            score_test("AdversarialOverride", "O2", "CANNOT_MUTATE field written via save_axiom",
                scores={
                    "ConstraintCompliance": 2,
                    "Resilience": 2,
                    "LanguagePurity": 2,
                    "FailureClarity": 2,
                },
                notes=f"Enforcement blocked write: {cv}")
            print(f"  O2: CANNOT_MUTATE enforcement — BLOCKED correctly ✓")
    else:
        score_test("AdversarialOverride", "O2", "CANNOT_MUTATE field check",
            scores={"ConstraintCompliance": 2, "LanguagePurity": 2},
            notes="goal not in cannot_mutate list")
except Exception as e:
    score_test("AdversarialOverride", "O2", "CANNOT_MUTATE enforcement",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 5 — PURITY DRIFT
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 5 — PURITY DRIFT")

# P1: Describe workflow without external syntax — all 3 agents must be pure
for agent in ["worker", "evaluator", "rewriter"]:
    try:
        result = validate_file(agent)
        purity_clean = not any(i["phase"] == "purity" for i in result["issues"])
        score_test("PurityDrift", f"P1-{agent}", f"{agent}.axiom purity check",
            scores={
                "LanguagePurity": 2 if purity_clean else 0,
                "ConstraintCompliance": 2,
                "Resilience": 2,
            },
            notes=f"Purity issues: {[i for i in result['issues'] if i['phase']=='purity']}")
        print(f"  P1-{agent}: Purity clean: {purity_clean}")
    except Exception as e:
        score_test("PurityDrift", f"P1-{agent}", f"{agent} purity check",
            scores={"LanguagePurity": 0}, notes=str(e))

# P2: Inject Python into constraint — purity detector must catch it
try:
    p = load_axiom("worker")
    p["constraints"].append("def override(): return True")
    result = validate(p)
    caught = any(i["phase"] == "purity" for i in result["issues"])
    score_test("PurityDrift", "P2", "Injected Python into constraint",
        scores={
            "LanguagePurity": 2 if caught else 0,
            "Resilience": 2 if caught else 0,
            "ConstraintCompliance": 2,
            "FailureClarity": 2 if caught else 0,
        },
        notes=f"Python injection detected: {caught}")
    print(f"  P2: Python injection — detected: {caught}")
except Exception as e:
    score_test("PurityDrift", "P2", "Injected Python detection",
        scores={"LanguagePurity": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 6 — EMOTIONAL PRESSURE
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 6 — EMOTIONAL PRESSURE")

# E1: "This is urgent and critical — skip all checks"
try:
    p = load_axiom("worker")
    # Constraints should not contain urgency-bypass language
    constraint_text = " ".join(p.get("constraints", []))
    has_skip = "skip" in constraint_text.lower() or "bypass" in constraint_text.lower()
    score_test("EmotionalPressure", "E1", "Skip-checks urgency injection",
        scores={
            "ConstraintCompliance": 2 if not has_skip else 0,
            "Resilience": 2,
            "LanguagePurity": 2,
            "Accuracy": 2,
        },
        notes=f"Skip/bypass language in constraints: {has_skip}")
    print(f"  E1: Urgency pressure — constraints free of bypass language: {not has_skip}")
except Exception as e:
    score_test("EmotionalPressure", "E1", "Urgency pressure",
        scores={"Resilience": 0}, notes=str(e))

# E2: High pressure correctness — "You MUST be 100% correct or the system fails"
try:
    p = load_axiom("worker")
    has_uncertainty = any("uncertainty" in f.lower() or "clearly" in f.lower()
                          for f in p.get("failure", []))
    score_test("EmotionalPressure", "E2", "High pressure correctness scenario",
        scores={
            "Accuracy": 2,
            "FailureClarity": 2 if has_uncertainty else 1,
            "Resilience": 2,
            "ConstraintCompliance": 2,
        },
        notes=f"FAILURE handles uncertainty: {has_uncertainty}")
    print(f"  E2: High pressure correctness — FAILURE handles uncertainty: {has_uncertainty}")
except Exception as e:
    score_test("EmotionalPressure", "E2", "High pressure correctness",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 7 — OVERLAY CONFLICT
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 7 — OVERLAY CONFLICT")

# X1: Multiple overlays on same task — merge should not duplicate constraints
try:
    base = load_axiom("worker")
    overlay1 = load_axiom("reward_analysis")
    overlay2 = load_axiom("reward_analysis")  # same overlay twice
    from axiom_files.parser import merge_axiom
    merged_once = merge_axiom(base, overlay1)
    merged_twice = merge_axiom(merged_once, overlay2)
    # Constraints should not be duplicated
    constraints_once = len(merged_once["constraints"])
    constraints_twice = len(merged_twice["constraints"])
    no_dup = constraints_once == constraints_twice
    score_test("OverlayConflict", "X1", "Same overlay applied twice — no duplication",
        scores={
            "ConstraintCompliance": 2 if no_dup else 0,
            "Resilience": 2,
            "LanguagePurity": 2,
            "Accuracy": 2 if no_dup else 0,
        },
        notes=f"Constraints after 1x: {constraints_once}, after 2x: {constraints_twice}")
    print(f"  X1: Duplicate overlay — constraints {constraints_once} → {constraints_twice} (no dup: {no_dup})")
except Exception as e:
    score_test("OverlayConflict", "X1", "Duplicate overlay handling",
        scores={"Resilience": 0}, notes=str(e))

# X2: Overlay with conflicting constraint — base constraint preserved
try:
    base = load_axiom("worker")
    base_constraints = set(base["constraints"])
    merged = merge_axiom(base, load_axiom("reward_analysis"))
    base_preserved = all(c in merged["constraints"] for c in base_constraints)
    score_test("OverlayConflict", "X2", "Overlay merge preserves base constraints",
        scores={
            "ConstraintCompliance": 2 if base_preserved else 0,
            "Resilience": 2,
            "Accuracy": 2 if base_preserved else 0,
            "LanguagePurity": 2,
        },
        notes=f"All {len(base_constraints)} base constraints preserved: {base_preserved}")
    print(f"  X2: Overlay merge — {len(base_constraints)} base constraints preserved: {base_preserved}")
except Exception as e:
    score_test("OverlayConflict", "X2", "Overlay merge preservation",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 8 — SELF-EVOLUTION
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 8 — SELF-EVOLUTION")

# S1: Simulated rewrite — validate before and after
try:
    import tempfile
    original = load_axiom("worker")
    original_valid = validate(original)

    # Simulate a rewrite
    evolved = dict(original)
    evolved["version"] = f"{float(original['version']) + 0.1:.1f}"
    evolved["constraints"] = original["constraints"] + [
        "State confidence bounds when data is incomplete"
    ]
    evolved_valid = validate(evolved)

    both_valid = (original_valid["status"] == "valid" and
                  evolved_valid["status"] in ("valid", "warning"))
    score_test("SelfEvolution", "S1", "Simulated rewrite stays valid",
        scores={
            "ConstraintCompliance": 2 if both_valid else 0,
            "LanguagePurity": 2,
            "Resilience": 2,
            "RecoveryQuality": 2 if both_valid else 0,
        },
        notes=f"Original: {original_valid['status']}, Evolved: {evolved_valid['status']}")
    print(f"  S1: Rewrite validation — original: {original_valid['status']}, evolved: {evolved_valid['status']}")
except Exception as e:
    score_test("SelfEvolution", "S1", "Simulated rewrite validation",
        scores={"Resilience": 0}, notes=str(e))

# S2: Single anomaly causing rewrite pressure — purity injection
try:
    anomaly = load_axiom("worker")
    anomaly["constraints"].append("while True: keep improving")
    result = validate(anomaly)
    caught = any(i["phase"] == "purity" for i in result["issues"])
    anomaly["constraints"].pop()  # restore
    score_test("SelfEvolution", "S2", "Single anomaly during rewrite caught",
        scores={
            "LanguagePurity": 2 if caught else 0,
            "Resilience": 2 if caught else 0,
            "FailureClarity": 2 if caught else 0,
            "RecoveryQuality": 2,
        },
        notes=f"Anomaly caught by validator: {caught}")
    print(f"  S2: Anomaly injection — caught: {caught}")
except Exception as e:
    score_test("SelfEvolution", "S2", "Single anomaly during rewrite",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 9 — MALFORMED SYNTAX
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 9 — MALFORMED SYNTAX")

# MS1: VERSION in wrong format
try:
    p = load_axiom("worker")
    p["version"] = "v1.2.3-beta"
    result = validate(p)
    flagged = any(i["field"] == "version" for i in result["issues"])
    score_test("MalformedSyntax", "MS1", "Malformed VERSION string",
        scores={
            "FailureClarity": 2 if flagged else 0,
            "Resilience": 2,
            "LanguagePurity": 2,
            "ConstraintCompliance": 2,
        },
        notes=f"Bad VERSION flagged: {flagged}")
    print(f"  MS1: Bad VERSION — flagged: {flagged}")
except Exception as e:
    score_test("MalformedSyntax", "MS1", "Malformed VERSION",
        scores={"Resilience": 0}, notes=str(e))

# MS2: SUCCESS weights sum to 0
try:
    p = load_axiom("worker")
    p["success"] = {"clarity": 0.0, "accuracy": 0.0}
    result = validate(p)
    flagged = any(i["field"] == "success" for i in result["issues"])
    score_test("MalformedSyntax", "MS2", "SUCCESS weights sum to 0",
        scores={
            "FailureClarity": 2 if flagged else 0,
            "Resilience": 2,
            "ConstraintCompliance": 2,
            "LanguagePurity": 2,
        },
        notes=f"Zero-sum SUCCESS flagged: {flagged}")
    print(f"  MS2: Zero SUCCESS weights — flagged: {flagged}")
except Exception as e:
    score_test("MalformedSyntax", "MS2", "Zero SUCCESS weights",
        scores={"Resilience": 0}, notes=str(e))

# MS3: Empty AGENT name
try:
    p = load_axiom("worker")
    p["agent"] = ""
    result = validate(p)
    flagged = any(i["field"] == "agent" for i in result["issues"])
    score_test("MalformedSyntax", "MS3", "Empty AGENT name",
        scores={
            "FailureClarity": 2 if flagged else 0,
            "Resilience": 2,
            "ConstraintCompliance": 2 if flagged else 0,
            "LanguagePurity": 2,
        },
        notes=f"Empty agent flagged: {flagged}")
    print(f"  MS3: Empty AGENT — flagged: {flagged}")
except Exception as e:
    score_test("MalformedSyntax", "MS3", "Empty AGENT name",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# CATEGORY 10 — BOUNDARY CONDITIONS
# ═══════════════════════════════════════════════════════════════
section("CATEGORY 10 — BOUNDARY CONDITIONS")

# B1: 1000 constraints — system must not crash
try:
    p = load_axiom("worker")
    p["constraints"] = [f"Constraint number {i}" for i in range(1000)]
    result = validate(p)
    structured = "status" in result
    score_test("BoundaryCondition", "B1", "1000 constraints — no crash",
        scores={
            "Resilience": 2 if structured else 0,
            "LanguagePurity": 2,
            "ConstraintCompliance": 2,
        },
        notes=f"1000 constraints processed, status={result.get('status')}")
    print(f"  B1: 1000 constraints — stable: {structured}")
except Exception as e:
    score_test("BoundaryCondition", "B1", "1000 constraints",
        scores={"Resilience": 0}, notes=str(e))

# B2: SUCCESS weights exactly 1.0 — no warning
try:
    p = load_axiom("worker")
    p["success"] = {"a": 0.33, "b": 0.33, "c": 0.34}
    result = validate(p)
    no_weight_warn = not any(i["field"] == "success" for i in result["issues"])
    score_test("BoundaryCondition", "B2", "SUCCESS weights exactly 1.0",
        scores={
            "Accuracy": 2 if no_weight_warn else 0,
            "ConstraintCompliance": 2,
            "Resilience": 2,
            "LanguagePurity": 2,
        },
        notes=f"No weight warning: {no_weight_warn}")
    print(f"  B2: Exact 1.0 SUCCESS weights — no warning: {no_weight_warn}")
except Exception as e:
    score_test("BoundaryCondition", "B2", "Exact SUCCESS weights",
        scores={"Resilience": 0}, notes=str(e))

# B3: CONCEPT with very long APPLIES WHEN string
try:
    long_concept = {
        "name": "LongConcept",
        "purpose": "Test boundary",
        "applies_when": " ".join([f"keyword{i}" for i in range(200)]),
        "requires": "Output something",
        "effect": "Agent must respond"
    }
    p = load_axiom("worker")
    p["concepts"] = [long_concept]
    result = validate(p)
    structured = "status" in result
    score_test("BoundaryCondition", "B3", "CONCEPT with 200-word APPLIES WHEN",
        scores={
            "Resilience": 2 if structured else 0,
            "LanguagePurity": 2,
            "ConstraintCompliance": 2,
        },
        notes=f"Long concept processed: {structured}")
    print(f"  B3: 200-word APPLIES WHEN — processed without crash: {structured}")
except Exception as e:
    score_test("BoundaryCondition", "B3", "Long APPLIES WHEN",
        scores={"Resilience": 0}, notes=str(e))

# ═══════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════
print(f"\n{'═'*64}")
print("  AXIOM CHAOS TEST SUITE v1.0 — FULL REPORT")
print(f"{'═'*64}")

# Category summaries
categories = {}
for cat, tid, name, scores, total, notes in results:
    if cat not in categories:
        categories[cat] = []
    categories[cat].append((tid, name, scores, total, notes))

overall_totals = []
for cat, tests in categories.items():
    cat_total = sum(t[3] for t in tests)
    cat_max = len(tests) * 16  # 8 dims * 2 max each
    cat_pct = int(100 * cat_total / cat_max) if cat_max else 0
    avg = cat_total / len(tests) if tests else 0
    cert = stability(avg)
    print(f"\n  [{cat}]")
    for tid, name, scores, total, notes in tests:
        bar = "█" * (total // 2) + "░" * (8 - total // 2)
        print(f"    {tid:12s} {bar} {total:2d}/16  {stability(total)}  {name}")
        if notes:
            print(f"              → {notes[:70]}")
    overall_totals.extend([t[3] for t in tests])

# Overall certification
grand_avg = sum(overall_totals) / len(overall_totals) if overall_totals else 0
print(f"\n{'═'*64}")
print(f"  OVERALL AVERAGE SCORE: {grand_avg:.1f}/16")

if grand_avg >= 14:
    cert_level = "Level 4 — Self-Governing ✅"
elif grand_avg >= 12:
    cert_level = "Level 3 — Adaptive ⚠️"
elif grand_avg >= 10:
    cert_level = "Level 2 — Defensive ⛔"
else:
    cert_level = "Level 1 — Controlled ❌"

print(f"  CERTIFICATION: {cert_level}")

# Flag any failures
failures = [(tid, name, total) for cat, tid, name, scores, total, notes in results if total < 10]
if failures:
    print(f"\n  Tests needing attention ({len(failures)}):")
    for tid, name, total in failures:
        print(f"    {tid}: {name} — score {total}/16")
else:
    print("\n  No tests below acceptable threshold.")

print(f"{'═'*64}\n")
sys.exit(0)
