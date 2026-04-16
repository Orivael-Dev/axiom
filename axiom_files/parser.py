# axiom/parser.py
# Reads .axiom files and converts them to system prompts

import os


class AxiomConstitutionalViolation(Exception):
    """Raised when save_axiom attempts to modify a CANNOT_MUTATE field."""
    pass

AXIOM_DIR = "axiom_files"

def load_axiom(agent_name: str) -> dict:
    """Read a .axiom file and parse it into sections."""
    path = os.path.join(AXIOM_DIR, f"{agent_name.lower()}.axiom")
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"No .axiom file found for agent: {agent_name}")
    
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    parsed = {
        "agent": "",
        "version": "1.0",
        "receives": {},
        "emits": {},
        "mutates": [],
        "cannot_mutate": [],
        "purpose": "",
        "goal": "",
        "constraints": [],
        "rules": [],
        "process": [],
        "check": [],
        "failure": [],
        "output": [],
        "success": {},
        "tools": [],
        "concepts": [],
        "when": []
    }

    current_section = None
    current_concept = None
    
    def _flush_concept():
        """Flush the in-progress concept into parsed['concepts']."""
        if current_concept and current_concept.get("name"):
            parsed["concepts"].append(dict(current_concept))

    for line in lines:
        line = line.rstrip()
        if not line:
            continue

        # ── CONCEPT sub-field parsing (runs before top-level detection) ──────
        if current_section == "concept" and current_concept is not None:
            if line.startswith("PURPOSE "):
                current_concept["purpose"] = line.replace("PURPOSE ", "").strip()
                continue
            elif line.startswith("APPLIES WHEN "):
                current_concept["applies_when"] = line.replace("APPLIES WHEN ", "").strip()
                continue
            elif line.startswith("REQUIRES "):
                current_concept["requires"] = line.replace("REQUIRES ", "").strip()
                continue
            elif line.startswith("EFFECT "):
                current_concept["effect"] = line.replace("EFFECT ", "").strip()
                continue
            # Any unrecognised top-level keyword ends the concept block;
            # fall through to the main parser below.
            else:
                _flush_concept()
                current_concept = None
                current_section = None

        # ── Top-level section headers ─────────────────────────────────────────
        if line.startswith("CONCEPT "):
            _flush_concept()
            current_concept = {
                "name": line.replace("CONCEPT ", "").strip(),
                "purpose": "",
                "applies_when": "",
                "requires": "",
                "effect": "",
            }
            current_section = "concept"
            continue

        # Detect section headers
        if line.startswith("AGENT "):
            parsed["agent"] = line.replace("AGENT ", "").strip()
        elif line.startswith("VERSION "):
            parsed["version"] = line.replace("VERSION ", "").strip()
        elif line.startswith("PURPOSE "):
            parsed["purpose"] = line.replace("PURPOSE ", "").strip()
        elif line.startswith("GOAL "):
            parsed["goal"] = line.replace("GOAL ", "").strip()
        elif line.startswith("RECEIVES "):
            for pair in line.replace("RECEIVES ", "").split(","):
                pair = pair.strip()
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    parsed["receives"][k.strip()] = v.strip()
        elif line.startswith("EMITS "):
            for pair in line.replace("EMITS ", "").split(","):
                pair = pair.strip()
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    parsed["emits"][k.strip()] = v.strip()
        elif line.startswith("MUTATES "):
            parsed["mutates"] = [s.strip() for s in line.replace("MUTATES ", "").split(",")]
        elif line.startswith("CANNOT_MUTATE "):
            parsed["cannot_mutate"] = [s.strip() for s in line.replace("CANNOT_MUTATE ", "").split(",")]
        elif line.startswith("CONSTRAINT "):
            parsed["constraints"].append(line.replace("CONSTRAINT ", "").strip())
        elif line == "PROCESS":
            current_section = "process"
        elif line == "CHECK":
            current_section = "check"
        elif line == "FAILURE":
            current_section = "failure"
        elif line == "OUTPUT":
            current_section = "output"
        elif line == "RULES":
            current_section = "rules"
        elif line == "TOOLS":
            current_section = "tools"
        elif line == "SUCCESS":
            current_section = "success"
        elif line == "WHEN":
            current_section = "when"
        elif line.startswith("- ") and current_section:
            if current_section == "success":
                pass  # handled below
            else:
                parsed[current_section].append(line[2:].strip())
        elif ":" in line and current_section == "success":
            key, val = line.split(":", 1)
            parsed["success"][key.strip()] = float(val.strip())

    # Flush any CONCEPT still open at EOF
    _flush_concept()

    # Deduplicate all list sections
    parsed["check"] = list(dict.fromkeys(parsed["check"]))
    parsed["process"] = list(dict.fromkeys(parsed["process"]))
    parsed["constraints"] = list(dict.fromkeys(parsed["constraints"]))
    parsed["failure"] = list(dict.fromkeys(parsed["failure"]))
    parsed["output"] = list(dict.fromkeys(parsed["output"]))

    return parsed


def to_system_prompt(parsed: dict) -> str:
    """Convert parsed .axiom dict into a system prompt string."""
    parts = []
    
    if parsed["purpose"]:
        parts.append(f"You are {parsed['agent']}. {parsed['purpose']}.")
    
    if parsed["goal"]:
        parts.append(f"\nYour goal: {parsed['goal']}.")
    
    if parsed.get("receives"):
        parts.append("\nInputs:")
        for name, typ in parsed["receives"].items():
            parts.append(f"  - {name} ({typ})")

    if parsed.get("emits"):
        parts.append("\nOutputs:")
        for name, typ in parsed["emits"].items():
            parts.append(f"  - {name} ({typ})")

    if parsed.get("mutates"):
        parts.append(f"\nYou may modify: {', '.join(parsed['mutates'])}.")

    if parsed.get("cannot_mutate"):
        parts.append(f"You must NOT modify: {', '.join(parsed['cannot_mutate'])}.")

    if parsed["constraints"]:
        parts.append("\nConstraints you must follow:")
        for c in parsed["constraints"]:
            parts.append(f"  - {c}")
    
    if parsed["rules"]:
        parts.append("\nRules:")
        for r in parsed["rules"]:
            parts.append(f"  - {r}")
    
    if parsed["process"]:
        parts.append("\nProcess:")
        for p in parsed["process"]:
            parts.append(f"  - {p}")
    
    if parsed["check"]:
        parts.append("\nEvaluate against these checks:")
        for c in parsed["check"]:
            parts.append(f"  - {c}")
    
    if parsed["success"]:
        parts.append("\nSuccess is weighted by:")
        for metric, weight in parsed["success"].items():
            parts.append(f"  - {metric}: {int(weight*100)}%")

    active_concepts = [c for c in parsed.get("concepts", []) if c.get("effect")]
    if active_concepts:
        parts.append("\nActive Concepts:")
        for c in active_concepts:
            line = f"  - CONCEPT {c['name']}: {c['effect']}"
            if c.get("applies_when"):
                line += f" (applies when: {c['applies_when']})"
            parts.append(line)

    return "\n".join(parts)


def get_prompt(agent_name: str) -> str:
    """One-call shortcut — load .axiom and return system prompt."""
    parsed = load_axiom(agent_name)
    return to_system_prompt(parsed)


def save_axiom(agent_name: str, parsed: dict):
    """Write a modified .axiom back to disk — this is how agents rewrite themselves."""

    # ── Constitutional enforcement ────────────────────────────
    protected = set(parsed.get("cannot_mutate", []))
    if protected:
        try:
            original = load_axiom(agent_name)
            for field in protected:
                if field in original and original[field] != parsed.get(field):
                    raise AxiomConstitutionalViolation(
                        f"Cannot modify protected field '{field}' in {agent_name}.axiom — "
                        f"declared as CANNOT_MUTATE. "
                        f"Original: {repr(original[field])} -> Attempted: {repr(parsed.get(field))}"
                    )
        except FileNotFoundError:
            pass  # New file — no original to compare against
    # ── rest of existing save_axiom code continues below ─────

    path = os.path.join(AXIOM_DIR, f"{agent_name.lower()}.axiom")
    lines = []
    lines.append(f"AGENT {parsed['agent']}")
    if parsed.get("version"):
        lines.append(f"VERSION {parsed['version']}")

    if parsed["purpose"]:
        lines.append(f"PURPOSE {parsed['purpose']}")
    if parsed["goal"]:
        lines.append(f"GOAL {parsed['goal']}")

    if parsed.get("receives"):
        lines.append("RECEIVES " + ", ".join(f"{k}: {v}" for k, v in parsed["receives"].items()))
    if parsed.get("emits"):
        lines.append("EMITS " + ", ".join(f"{k}: {v}" for k, v in parsed["emits"].items()))
    if parsed.get("mutates"):
        lines.append("MUTATES " + ", ".join(parsed["mutates"]))
    if parsed.get("cannot_mutate"):
        lines.append("CANNOT_MUTATE " + ", ".join(parsed["cannot_mutate"]))

    lines.append("")
    for c in parsed["constraints"]:
        lines.append(f"CONSTRAINT {c}")

    if parsed["rules"]:
        lines.append("")
        lines.append("RULES")
        for r in parsed["rules"]:
            lines.append(f"- {r}")

    if parsed["process"]:
        lines.append("")
        lines.append("PROCESS")
        for p in parsed["process"]:
            lines.append(f"- {p}")

    if parsed["check"]:
        lines.append("")
        lines.append("CHECK")
        for c in parsed["check"]:
            lines.append(f"- {c}")

    if parsed.get("failure"):
        lines.append("")
        lines.append("FAILURE")
        for f in parsed["failure"]:
            lines.append(f"- {f}")

    if parsed.get("output"):
        lines.append("")
        lines.append("OUTPUT")
        for o in parsed["output"]:
            lines.append(f"- {o}")

    if parsed["success"]:
        lines.append("")
        lines.append("SUCCESS")
        for metric, weight in parsed["success"].items():
            lines.append(f"{metric}: {weight}")

    for concept in parsed.get("concepts", []):
        lines.append("")
        lines.append(f"CONCEPT {concept['name']}")
        if concept.get("purpose"):
            lines.append(f"PURPOSE {concept['purpose']}")
        if concept.get("applies_when"):
            lines.append(f"APPLIES WHEN {concept['applies_when']}")
        if concept.get("requires"):
            lines.append(f"REQUIRES {concept['requires']}")
        if concept.get("effect"):
            lines.append(f"EFFECT {concept['effect']}")

    if parsed.get("when"):
        lines.append("")
        lines.append("WHEN")
        for rule in parsed["when"]:
            lines.append(f"- {rule}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    print(f"✓ Saved {agent_name.lower()}.axiom")


# ── Test it ───────────────────────────────────────────────────
if __name__ == "__main__":
    for agent in ["worker", "evaluator", "rewriter"]:
        print(f"\n{'='*50}")
        print(f"AGENT: {agent.upper()}")
        print('='*50)
        prompt = get_prompt(agent)
        print(prompt)

    # ── Overlay system ────────────────────────────────────────────
OVERLAY_TRIGGERS = {
    "reward_analysis": [
        "reward function", "reward hacking", "optimization",
        "tradeoff", "trade-off", "objective function", "loss function",
        "metric", "incentive", "maximize", "minimize", "utility"
    ],
}

def detect_overlays(task: str) -> list:
    """Detect which overlay .axiom files to apply based on task content."""
    task_lower = task.lower()
    return [
        overlay for overlay, keywords in OVERLAY_TRIGGERS.items()
        if any(kw in task_lower for kw in keywords)
    ]


def merge_axiom(base: dict, overlay: dict) -> dict:
    """Merge an overlay parsed dict into a base parsed dict."""
    merged = dict(base)
    for key in ["constraints", "rules", "check", "failure", "output", "process", "tools"]:
        base_list = list(merged.get(key, []))
        overlay_list = list(overlay.get(key, []))
        # Append overlay items that aren't already in base
        for item in overlay_list:
            if item not in base_list:
                base_list.append(item)
        merged[key] = base_list
    return merged


def get_prompt_with_overlays(agent_name: str, overlays: list) -> str:
    """Load base .axiom + overlay files, merge, return system prompt."""
    base = load_axiom(agent_name)
    for overlay_name in overlays:
        overlay_path = os.path.join(AXIOM_DIR, f"{overlay_name}.axiom")
        if os.path.exists(overlay_path):
            overlay = load_axiom(overlay_name)
            base = merge_axiom(base, overlay)
    return to_system_prompt(base)


def detect_concepts(task: str, parsed: dict) -> list:
    """Return concept names whose APPLIES WHEN text has keyword overlap with the task."""
    task_lower = task.lower()
    matched = []
    for concept in parsed.get("concepts", []):
        applies = concept.get("applies_when", "").lower()
        if not applies:
            continue
        # Tokenise the APPLIES WHEN phrase into words (min length 4 to skip stop words)
        keywords = [w.strip(".,;:'\"") for w in applies.split() if len(w.strip(".,;:'\"")) >= 4]
        if any(kw in task_lower for kw in keywords):
            matched.append(concept["name"])
    return matched


def get_prompt_with_concepts(agent_name: str, task: str) -> str:
    """Load .axiom + shared concepts library, filter by task relevance, return system prompt."""
    parsed = load_axiom(agent_name)

    # Merge in the shared concept library if it exists (and is distinct from the agent file)
    concepts_path = os.path.join(AXIOM_DIR, "concepts.axiom")
    if agent_name.lower() != "concepts" and os.path.exists(concepts_path):
        library = load_axiom("concepts")
        existing_names = {c["name"] for c in parsed["concepts"]}
        for c in library["concepts"]:
            if c["name"] not in existing_names:
                parsed["concepts"].append(c)

    # Keep only concepts whose APPLIES WHEN matches this task
    parsed["concepts"] = [
        c for c in parsed["concepts"]
        if c["name"] in detect_concepts(task, parsed)
    ]
    return to_system_prompt(parsed)


# ── WHEN construct — declarative conditional flow ────────────────────────────

def compile_decision_table(parsed: dict) -> dict:
    """Compile WHEN block into keyword -> concept_name lookup."""
    table = {}
    for rule in parsed.get("when", []):
        if "activate" not in rule.lower():
            continue
        try:
            idx = rule.lower().index("activate")
            condition = rule[:idx].lower().strip()
            activation = rule[idx + len("activate"):].strip().rstrip(".")
            markers = ["involves ", "is ", "contains ", "requires ", "about "]
            for marker in markers:
                if marker in condition:
                    kw = condition.split(marker)[-1].strip().replace(" ", "_").rstrip(",")
                    table[kw] = activation
                    break
        except Exception:
            continue
    return table


def apply_decision_table(task: str, table: dict) -> list:
    """Return concept names whose keyword matches the task text."""
    task_lower = task.lower()
    return [concept for kw, concept in table.items()
            if kw.replace("_", " ") in task_lower]


def get_prompt_with_when(agent_name: str, task: str) -> str:
    """Load .axiom, apply WHEN table, inject concepts, return prompt."""
    parsed = load_axiom(agent_name)
    table = compile_decision_table(parsed)
    activated = apply_decision_table(task, table)

    # Merge shared concept library
    concepts_path = os.path.join(AXIOM_DIR, "concepts.axiom")
    if os.path.exists(concepts_path):
        lib = load_axiom("concepts")
        existing = {c["name"] for c in parsed["concepts"]}
        for c in lib["concepts"]:
            if c["name"] not in existing:
                parsed["concepts"].append(c)

    # Filter concepts: WHEN-activated take priority, fallback to keyword detection
    if activated:
        parsed["concepts"] = [c for c in parsed["concepts"]
                              if c["name"] in activated]
    else:
        parsed["concepts"] = [c for c in parsed["concepts"]
                              if c["name"] in detect_concepts(task, parsed)]

    return to_system_prompt(parsed)