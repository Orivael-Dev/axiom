# axiom_files/validator.py
# Language Validator for Axiom v1.3
# Enforces structural, purity, and semantic constraints on parsed .axiom dicts.
#
# Public API:
#   validate(parsed: dict) -> dict
#   validate_file(agent_name: str) -> dict
#
# Output schema:
#   {
#     "status": "valid" | "warning" | "invalid",
#     "issues": [{"phase": str, "level": "error"|"warning", "field": str, "message": str}],
#     "suggestions": [str]
#   }

import os
import re

# ── Purity: patterns that indicate external code crept in ────────────────────
_PURITY_PATTERNS = [
    (r"\bdef\s+\w+\s*\(", "Python function definition (def)"),
    (r"\bclass\s+\w+[\s:(]", "Python class definition (class)"),
    (r"\bfor\s+\w+\s+in\b", "Procedural for-loop"),
    (r"\bwhile\s+.+:", "Procedural while-loop"),
    (r"\bimport\s+\w+", "Import statement"),
    (r"\breturn\b", "return keyword"),
    (r"\bprint\s*\(", "print() call"),
    (r":=", "Walrus operator (:=)"),
    (r"\blambda\b", "Lambda expression"),
]

# ── Semantic: vague qualifiers that lack measurable thresholds ───────────────
_VAGUE_TERMS = [
    "try to", "consider", "if possible", "when needed",
    "appropriate", "reasonable", "as needed", "maybe", "perhaps",
    "generally", "typically", "usually",
]

# ── Semantic: procedural drift patterns forbidden in PROCESS ─────────────────
_PROCEDURAL_DRIFT = [
    r"\bif\b", r"\belse\b", r"\bwhile\b", r"\bloop\b", r"\breturn\b",
]

# ── Known mutable fields (for MUTATES / CANNOT_MUTATE validation) ────────────
_KNOWN_FIELDS = {
    "agent", "version", "purpose", "goal", "receives", "emits",
    "mutates", "cannot_mutate", "constraints", "rules", "process",
    "check", "failure", "output", "success", "tools", "concepts", "when",
    "delegates", "security", "trust_level", "sandbox_agent",
}


def validate(parsed: dict) -> dict:
    """
    Validate a parsed .axiom dict.

    Returns {"status": ..., "issues": [...], "suggestions": [...]}.
    """
    issues = []
    suggestions = []

    # ── Phase 1: Syntax Validation ───────────────────────────────────────────
    # 1a. Required identity fields
    if not parsed.get("agent"):
        issues.append({
            "phase": "syntax", "level": "error", "field": "agent",
            "message": "AGENT field is missing or empty.",
        })
        suggestions.append("Add 'AGENT <Name>' as the first line.")

    if not parsed.get("purpose") and not parsed.get("goal"):
        issues.append({
            "phase": "syntax", "level": "error", "field": "purpose/goal",
            "message": "At least one of PURPOSE or GOAL must be defined.",
        })
        suggestions.append("Add 'PURPOSE <description>' or 'GOAL <objective>'.")

    # 1b. VERSION format
    version = parsed.get("version", "")
    if version and not re.match(r"^\d+\.\d+$", version):
        issues.append({
            "phase": "syntax", "level": "warning", "field": "version",
            "message": f"VERSION '{version}' does not match expected format N.N (e.g. 1.3).",
        })
        suggestions.append("Use a numeric version like '1.3'.")

    # 1c. SUCCESS weights sum to 1.0
    success = parsed.get("success", {})
    if success:
        total = sum(success.values())
        if abs(total - 1.0) > 0.01:
            issues.append({
                "phase": "syntax", "level": "warning", "field": "success",
                "message": f"SUCCESS weights sum to {total:.3f} but should sum to 1.0.",
            })
            suggestions.append(f"Adjust SUCCESS weights to sum to 1.0 (current: {total:.3f}).")

    # 1c-extra. TRUST_LEVEL format and range
    trust_raw = str(parsed.get("trust_level", "")).strip()
    if trust_raw:
        try:
            trust_val = int(trust_raw)
            if trust_val < 0 or trust_val > 3:
                issues.append({
                    "phase": "syntax", "level": "warning", "field": "trust_level",
                    "message": f"TRUST_LEVEL '{trust_raw}' is outside expected range 0-3.",
                })
                suggestions.append("Use TRUST_LEVEL 0-3 (0 = lowest trust, 3 = highest).")
        except ValueError:
            issues.append({
                "phase": "syntax", "level": "warning", "field": "trust_level",
                "message": f"TRUST_LEVEL '{trust_raw}' is not an integer.",
            })
            suggestions.append("Use an integer TRUST_LEVEL like 0, 1, 2, or 3.")

    # 1c-extra. SANDBOX_AGENT existence check
    sandbox_agent = parsed.get("sandbox_agent", "").strip()
    if sandbox_agent:
        axiom_path = os.path.join("axiom_files", f"{sandbox_agent.lower()}.axiom")
        if not os.path.exists(axiom_path):
            issues.append({
                "phase": "syntax", "level": "warning", "field": "sandbox_agent",
                "message": f"SANDBOX_AGENT '{sandbox_agent}' has no matching .axiom file.",
            })
            suggestions.append(
                f"Create axiom_files/{sandbox_agent.lower()}.axiom or update SANDBOX_AGENT."
            )

    # 1d. MUTATES / CANNOT_MUTATE reference real fields
    for directive, field_key in [("MUTATES", "mutates"), ("CANNOT_MUTATE", "cannot_mutate")]:
        for name in parsed.get(field_key, []):
            if name not in _KNOWN_FIELDS:
                issues.append({
                    "phase": "syntax", "level": "warning", "field": field_key,
                    "message": f"{directive} references unknown field '{name}'.",
                })
                suggestions.append(
                    f"'{name}' in {directive} is not a recognised Axiom field. "
                    f"Known fields: {', '.join(sorted(_KNOWN_FIELDS))}."
                )

    # 1e-extra. MUTATES / CANNOT_MUTATE conflict check
    mutates_set = set(parsed.get("mutates", []))
    cannot_set = set(parsed.get("cannot_mutate", []))
    conflicts = mutates_set & cannot_set
    for field in conflicts:
        issues.append({
            "phase": "syntax", "level": "error", "field": "mutates/cannot_mutate",
            "message": f"Field '{field}' appears in both MUTATES and CANNOT_MUTATE — constitutional conflict.",
        })
        suggestions.append(
            f"Remove '{field}' from either MUTATES or CANNOT_MUTATE — a field cannot be both mutable and protected."
        )

    # 1e. CONCEPT blocks must have all 4 sub-fields populated
    for concept in parsed.get("concepts", []):
        name = concept.get("name", "<unnamed>")
        for sub in ("purpose", "applies_when", "requires", "effect"):
            if not concept.get(sub):
                issues.append({
                    "phase": "syntax", "level": "error", "field": f"concepts.{name}",
                    "message": f"CONCEPT '{name}' is missing sub-field '{sub.upper().replace('_', ' ')}'.",
                })
                suggestions.append(
                    f"Add '{sub.upper().replace('_', ' ')} <text>' inside CONCEPT {name}."
                )

    # ── Phase 2: Purity Validation ───────────────────────────────────────────
    # Scan every string value in the parsed dict for external code patterns
    def _iter_strings(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, list):
            for item in obj:
                yield from _iter_strings(item)
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from _iter_strings(v)

    all_text = list(_iter_strings(parsed))
    for text in all_text:
        for pattern, label in _PURITY_PATTERNS:
            if re.search(pattern, text):
                issues.append({
                    "phase": "purity", "level": "error", "field": "content",
                    "message": f"External code pattern detected: {label} — '{text[:80]}'",
                })
                suggestions.append(
                    f"Remove or rewrite '{text[:60]}' using declarative Axiom language."
                )
                break  # one error per string is enough

    # ── Phase 3: Semantic Validation ─────────────────────────────────────────
    # 3a. Vague terms in constraints and rules
    for section in ("constraints", "rules"):
        for entry in parsed.get(section, []):
            entry_lower = entry.lower()
            for term in _VAGUE_TERMS:
                if term in entry_lower:
                    # Only warn if there's no numeric threshold in the same entry
                    has_threshold = bool(re.search(r"\d+\.?\d*\s*(%|points?|score|threshold)", entry_lower))
                    if not has_threshold:
                        issues.append({
                            "phase": "semantic", "level": "warning", "field": section,
                            "message": (
                                f"Vague qualifier '{term}' in {section.upper()} without a measurable threshold: "
                                f"'{entry[:80]}'"
                            ),
                        })
                        suggestions.append(
                            f"Replace '{term}' in '{entry[:60]}' with a specific, measurable criterion."
                        )
                        break  # one warning per entry

    # 3b. Procedural drift in PROCESS
    for entry in parsed.get("process", []):
        entry_lower = entry.lower()
        for pattern in _PROCEDURAL_DRIFT:
            if re.search(pattern, entry_lower):
                issues.append({
                    "phase": "semantic", "level": "error", "field": "process",
                    "message": (
                        f"Procedural construct detected in PROCESS (Axiom PROCESS must be declarative): "
                        f"'{entry[:80]}'"
                    ),
                })
                suggestions.append(
                    f"Rewrite PROCESS step '{entry[:60]}' as a declarative action, not a conditional or loop."
                )
                break

    # 3c. Constraint/rule overlap (exact text appearing in both)
    constraints_set = set(parsed.get("constraints", []))
    for rule in parsed.get("rules", []):
        if rule in constraints_set:
            issues.append({
                "phase": "semantic", "level": "warning", "field": "constraints/rules",
                "message": f"Duplicate entry appears in both CONSTRAINT and RULES: '{rule[:80]}'",
            })
            suggestions.append(
                f"Remove '{rule[:60]}' from either CONSTRAINT or RULES — not both."
            )

    # 3d. WHEN entry validation
    for entry in parsed.get("when", []):
        if "activate" not in entry.lower():
            issues.append({
                "phase": "semantic", "level": "error", "field": "when",
                "message": f"WHEN entry missing activation target: '{entry[:60]}'"
            })
            suggestions.append(
                f"Add ', activate ConceptName' to the WHEN rule."
            )

    # 3e. DELEGATES entry validation
    for entry in parsed.get("delegates", []):
        if "->" not in entry:
            issues.append({
                "phase": "semantic", "level": "error", "field": "delegates",
                "message": f"DELEGATES entry missing '->' routing arrow: '{entry[:60]}'",
            })
            suggestions.append(
                "Use format: '- Source -> Target (on: trigger)'"
            )

    # 3f. SECURITY entry validation
    for entry in parsed.get("security", []):
        entry_lower = entry.lower()
        for term in _VAGUE_TERMS:
            if term in entry_lower:
                has_threshold = bool(re.search(r"\d+", entry_lower))
                if not has_threshold:
                    issues.append({
                        "phase": "semantic", "level": "warning",
                        "field": "security",
                        "message": f"Vague security rule without measurable threshold: '{entry[:80]}'",
                    })
                    suggestions.append(
                        f"Replace '{term}' in security rule with a specific, testable instruction."
                    )
                    break

    # ── Determine overall status ─────────────────────────────────────────────
    levels = {i["level"] for i in issues}
    if "error" in levels:
        status = "invalid"
    elif "warning" in levels:
        status = "warning"
    else:
        status = "valid"

    return {"status": status, "issues": issues, "suggestions": suggestions}


def validate_file(agent_name: str) -> dict:
    """Load a .axiom file and validate it. Returns the same dict as validate()."""
    try:
        from axiom_files.parser import load_axiom
    except ModuleNotFoundError:
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from axiom_files.parser import load_axiom
    parsed = load_axiom(agent_name)
    return validate(parsed)


# ── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    agents = ["worker", "evaluator", "rewriter"]
    status_icon = {"valid": "✅", "warning": "⚠️ ", "invalid": "❌"}

    for agent in agents:
        result = validate_file(agent)
        icon = status_icon.get(result["status"], "?")
        print(f"\n{icon} {agent.upper()}.axiom — STATUS: {result['status'].upper()}")
        if result["issues"]:
            for issue in result["issues"]:
                prefix = "[ERROR]" if issue["level"] == "error" else "[WARN] "
                print(f"   {prefix} [{issue['phase']}] {issue['field']}: {issue['message']}")
        else:
            print("   No issues found.")
        if result["suggestions"]:
            print("  Suggestions:")
            for s in result["suggestions"]:
                print(f"   → {s}")
