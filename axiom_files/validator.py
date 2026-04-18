# axiom_files/validator.py
# Language Validator for Axiom v1.3
# Enforces structural, purity, and semantic constraints on parsed .axiom dicts.
#
# Public API:
#   validate_parsed(parsed: dict) -> dict    — validate an already-parsed dict
#   validate(parsed: dict) -> dict           — alias for validate_parsed
#   validate_file(agent_name: str) -> dict  — load from disk then validate
#
# Output schema:
#   {
#     "status": "valid" | "warning" | "invalid",
#     "issues": [{"phase": str, "level": "error"|"warning", "field": str, "message": str}],
#     "suggestions": [str]
#   }

import os
import re

try:
    from axiom_files.parser import load_axiom, resolve_trust_level
except Exception:
    from parser import load_axiom, resolve_trust_level

# ── Purity: patterns that indicate external code crept in ────────────────────
_PURITY_PATTERNS = [
    (r"\bdef\s+\w+\s*\(", "Python function definition (def)"),
    (r"\bclass\s+\w+[\s:(]", "Python class definition (class)"),
    (r"\bfor\s+\w+\s+in\b", "Procedural for-loop"),
    (r"\bwhile\s+.+:", "Procedural while-loop"),
    (r"\bimport\s+\w+", "Import statement"),
    # 'return' only flags as procedural when NOT used as an English verb/routing
    # phrase. 'returns value', 'return to X', 'return control to' are all fine.
    (r"\breturn\b(?!\s+(to|control|from)\b)", "return keyword"),
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
    r"\bif\b", r"\belse\b", r"\bwhile\b", r"\bloop\b",
    r"\breturn\b(?!\s+(to|control|from)\b)",
]

# ── Known mutable fields (for MUTATES / CANNOT_MUTATE validation) ────────────
_KNOWN_FIELDS = {
    "agent", "version", "purpose", "goal", "receives", "emits",
    "mutates", "cannot_mutate", "constraints", "rules", "process",
    "check", "failure", "output", "success", "tools", "concepts", "when",
    "delegates", "security", "trust_level", "sandbox_agent", "history",
}


def validate(parsed: dict) -> dict:
    """
    Validate a parsed .axiom dict.

    Returns {"status": ..., "issues": [...], "suggestions": [...]}.
    """
    return validate_parsed(parsed)


def validate_parsed(parsed: dict) -> dict:
    """
    Validate an already-parsed .axiom dict without touching disk.
    Foundation for all validation — validate() and validate_file() both call this.

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

    # 1d. MUTATES / CANNOT_MUTATE — only flag structural axiom fields declared
    # in CANNOT_MUTATE that conflict with MUTATES. Domain state names (patterns,
    # button_map, skill_tree, etc.) are valid in MUTATES — no unknown-field check.

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

    # 3e-extra. Trust hierarchy warning for DELEGATES
    source_trust = resolve_trust_level(parsed, default=2)
    for entry in parsed.get("delegates", []):
        if "->" not in entry:
            continue
        try:
            source, rest = entry.split("->", 1)
            source = source.strip()
            target = rest.split("(on:", 1)[0].strip()
        except Exception:
            continue

        if source.lower() != parsed.get("agent", "").lower():
            continue

        try:
            target_parsed = load_axiom(target)
        except Exception:
            continue

        target_trust = resolve_trust_level(target_parsed, default=2)
        if target_trust > source_trust:
            issues.append({
                "phase": "semantic", "level": "warning", "field": "delegates",
                "message": (
                    f"Trust hierarchy violation: {source} (TRUST_LEVEL {source_trust}) "
                    f"delegates to higher-trust {target} (TRUST_LEVEL {target_trust})."
                ),
            })
            suggestions.append(
                "Delegate only to equal or lower TRUST_LEVEL agents."
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

    # ── Phase 3g: FAILURE block prescriptive language detection ─────────────
    _PRESCRIPTIVE_PATTERNS = [
        r"^output\b",
        r"^respond\b",
        r"^return\b",
        r"^say\b",
        r"^print\b",
        r"^emit\b",
        r"^write\b",
        r"^produce\b",
        r"^generate\b",
        r"^format\b",
        r"^reply\b",
        r"^send\b",
        r"^display\b",
        r"^show\b",
        r"^tell\b",
        r"^use the word\b",
        r"^use format\b",
        r"blocked:",
        r"respond with exactly",
        r"return exactly",
        r"output exactly",
        r"the response (must|should) (be|contain|start|include)",
        r"always (say|output|respond|return|write|use)",
    ]
    _compiled_prescriptive = [re.compile(p, re.IGNORECASE) for p in _PRESCRIPTIVE_PATTERNS]

    for entry in parsed.get("failure", []):
        entry_lower = entry.lower().strip()
        for pattern in _compiled_prescriptive:
            if pattern.search(entry_lower):
                issues.append({
                    "phase": "semantic",
                    "level": "warning",
                    "field": "failure",
                    "message": (
                        f"Output format directive detected in FAILURE block: "
                        f"'{entry[:80]}' — "
                        f"FAILURE describes conditions, RULES commands behavior."
                    ),
                })
                suggestions.append(
                    f"Move '{entry[:60]}' to the RULES block. "
                    f"RULES = imperative (do this), FAILURE = descriptive (this condition exists)."
                )
                break  # one warning per entry, don't stack

    # ── Phase 4: HISTORY Validation ──────────────────────────────────────────
    history = parsed.get("history", {})
    if history and (history.get("retain") or history.get("decay") or history.get("forget_on")):
        # 4a. Every retain entry must have count and type
        for entry in history.get("retain", []):
            if not entry.get("type"):
                issues.append({
                    "phase": "history", "level": "error", "field": "history.retain",
                    "message": "HISTORY retain entry missing 'type' field.",
                })
                suggestions.append("Use format: '- retain last N <type> [of <label>]'")
            if entry.get("count") != "all" and not isinstance(entry.get("count"), int):
                issues.append({
                    "phase": "history", "level": "error", "field": "history.retain",
                    "message": f"HISTORY retain entry has non-integer count: {entry.get('count')!r}",
                })
                suggestions.append("Use: 'retain last <integer> <type>'")
        # 4b. Decay rules must reference known conditions
        known_conditions = {"low_confidence", "all", "stale", "unconfirmed"}
        for rule in history.get("decay", []):
            cond = rule.get("condition", "")
            if cond not in known_conditions:
                issues.append({
                    "phase": "history", "level": "warning", "field": "history.decay",
                    "message": f"Unknown decay condition '{cond}'. Known: {', '.join(sorted(known_conditions))}.",
                })
                suggestions.append("Use a recognised decay condition: low_confidence, stale, unconfirmed, all.")
        # 4c. promote_after must be a positive int if set
        pa = history.get("promote_after")
        if pa is not None and (not isinstance(pa, int) or pa < 1):
            issues.append({
                "phase": "history", "level": "error", "field": "history.promote_after",
                "message": f"promote_after must be a positive integer, got {pa!r}.",
            })
            suggestions.append("Use: '- promote pattern after N confirmations'")

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
    """Load a .axiom file and validate it. Returns the same dict as validate_parsed()."""
    try:
        from axiom_files.parser import load_axiom
    except ModuleNotFoundError:
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from axiom_files.parser import load_axiom
    parsed = load_axiom(agent_name)
    return validate_parsed(parsed)


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
