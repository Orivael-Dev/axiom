# axiom/parser.py
# Reads .axiom files and converts them to system prompts

import os
import json as _json
from datetime import datetime, timezone
from pathlib import Path


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
        "trust_level": "",
        "sandbox_agent": "",
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
        "when": [],
        "delegates": [],
        "security": []
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
        elif line.startswith("TRUST_LEVEL "):
            parsed["trust_level"] = line.replace("TRUST_LEVEL ", "").strip()
        elif line.startswith("SANDBOX_AGENT "):
            parsed["sandbox_agent"] = line.replace("SANDBOX_AGENT ", "").strip()
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
        elif line == "DELEGATES":
            current_section = "delegates"
        elif line == "SECURITY":
            current_section = "security"
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

    if parsed.get("trust_level"):
        parts.append(f"\nTrust level: {parsed['trust_level']}.")

    if parsed.get("sandbox_agent"):
        parts.append(f"Sandbox agent: {parsed['sandbox_agent']}.")
    
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

    if parsed.get("security"):
        parts.append("\nSecurity rules you cannot override:")
        for s in parsed["security"]:
            parts.append(f"  - {s}")

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
            pass  # New file -- no original to compare against

    # -- Version history --------------------------------------------------
    try:
        original = load_axiom(agent_name)
        append_history(agent_name, original, parsed)
    except FileNotFoundError:
        pass  # first save -- no history to diff

    # -- rest of existing save_axiom code continues below -----------------

    path = os.path.join(AXIOM_DIR, f"{agent_name.lower()}.axiom")
    lines = []
    lines.append(f"AGENT {parsed['agent']}")
    if parsed.get("version"):
        lines.append(f"VERSION {parsed['version']}")
    if parsed.get("trust_level"):
        lines.append(f"TRUST_LEVEL {parsed['trust_level']}")
    if parsed.get("sandbox_agent"):
        lines.append(f"SANDBOX_AGENT {parsed['sandbox_agent']}")

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

    if parsed.get("security"):
        lines.append("")
        lines.append("SECURITY")
        for rule in parsed["security"]:
            lines.append(f"- {rule}")

    if parsed.get("delegates"):
        lines.append("")
        lines.append("DELEGATES")
        for rule in parsed["delegates"]:
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


# -- DELEGATES construct -- declarative agent routing ---------------------------

def compile_delegates(parsed: dict) -> list:
    """
    Compile DELEGATES block into structured delegation map.
    Format: "Source -> Target (on: trigger)"
    Returns: [{"source": str, "target": str, "on": str}]
    """
    delegation_map = []
    for rule in parsed.get("delegates", []):
        try:
            if "->" not in rule:
                continue
            source, rest = rule.split("->", 1)
            source = source.strip()
            if "(on:" in rest:
                target_part, trigger_part = rest.split("(on:", 1)
                target = target_part.strip()
                trigger = trigger_part.rstrip(")").strip()
            else:
                target = rest.strip()
                trigger = "always"
            delegation_map.append({
                "source": source,
                "target": target,
                "on": trigger,
            })
        except Exception:
            continue
    return delegation_map


def get_delegates_for(agent_name: str, parsed: dict, active_state: str = None) -> list:
    """
    Return list of valid delegation targets for agent_name
    given the current active state (concept or condition name).
    """
    delegation_map = compile_delegates(parsed)
    matches = []
    for entry in delegation_map:
        if entry["source"].lower() == agent_name.lower():
            if active_state is None or entry["on"].lower() == active_state.lower() \
               or entry["on"] == "always":
                matches.append(entry["target"])
    return matches


# -- Version history -- diff log of .axiom mutations ---------------------------

HISTORY_DIR = Path(AXIOM_DIR) / ".history"


def diff_axiom(before: dict, after: dict) -> list:
    """
    Compare two parsed .axiom dicts.
    Returns list of {field, added, removed} for changed list fields
    and {field, before, after} for changed scalar fields.
    """
    diffs = []
    list_fields = [
        "constraints", "rules", "process", "check",
        "failure", "output", "tools", "when", "delegates"
    ]
    scalar_fields = ["agent", "version", "trust_level", "sandbox_agent", "purpose", "goal"]

    for field in list_fields:
        before_set = set(before.get(field, []))
        after_set  = set(after.get(field, []))
        added   = sorted(after_set - before_set)
        removed = sorted(before_set - after_set)
        if added or removed:
            diffs.append({
                "field": field,
                "added": added,
                "removed": removed,
            })

    for field in scalar_fields:
        b = before.get(field, "")
        a = after.get(field, "")
        if b != a:
            diffs.append({
                "field": field,
                "before": b,
                "after": a,
            })

    return diffs


def append_history(agent_name: str, before: dict, after: dict):
    """
    Write a diff entry to .history/{agent}_history.jsonl.
    Called automatically by save_axiom().
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    log_path = HISTORY_DIR / f"{agent_name.lower()}_history.jsonl"

    diffs = diff_axiom(before, after)
    if not diffs:
        return  # nothing changed -- skip

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent_name.lower(),
        "version_before": before.get("version", "?"),
        "version_after": after.get("version", "?"),
        "diffs": diffs,
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry) + "\n")


def read_history(agent_name: str) -> list:
    """Return full history log for an agent as a list of dicts."""
    log_path = HISTORY_DIR / f"{agent_name.lower()}_history.jsonl"
    if not log_path.exists():
        return []
    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(_json.loads(line))
                except Exception:
                    continue
    return entries


# -- Snapshot/restore -- best-state preservation and degradation recovery ------

SNAPSHOT_DIR = Path(AXIOM_DIR) / ".snapshots"


def save_snapshot(
    agent_name: str,
    score: float,
    run_id: str = "",
    task: str = "",
) -> bool:
    """
    Save current .axiom as best snapshot if score beats previous best.
    Returns True if snapshot was updated, False if existing was better.
    """
    import shutil
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = SNAPSHOT_DIR / f"{agent_name.lower()}_best_meta.json"
    snap_path = SNAPSHOT_DIR / f"{agent_name.lower()}_best.axiom"

    # Check existing best score
    existing_score = -1.0
    if meta_path.exists():
        try:
            with open(meta_path) as f:
                existing_score = _json.load(f).get("score", -1.0)
        except Exception:
            pass

    if score <= existing_score:
        return False  # existing snapshot is better

    # Save the .axiom file as snapshot
    source = Path(AXIOM_DIR) / f"{agent_name.lower()}.axiom"
    if not source.exists():
        return False

    shutil.copy2(source, snap_path)

    # Save meta
    parsed = load_axiom(agent_name)
    meta = {
        "agent": agent_name.lower(),
        "version": parsed.get("version", "?"),
        "score": score,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "task": task[:120],
    }
    with open(meta_path, "w") as f:
        _json.dump(meta, f, indent=2)

    print(f"✓ Snapshot saved -- {agent_name} v{parsed.get('version')} score={score}")
    return True


def load_snapshot(agent_name: str) -> dict | None:
    """
    Load the best snapshot for an agent.
    Returns parsed dict or None if no snapshot exists.
    """
    import tempfile
    snap_path = SNAPSHOT_DIR / f"{agent_name.lower()}_best.axiom"
    if not snap_path.exists():
        return None
    snap_str = snap_path.read_text(encoding="utf-8")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".axiom",
        dir=AXIOM_DIR, delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(snap_str)
        tmp_name = os.path.basename(tmp.name).replace(".axiom", "")
    try:
        parsed = load_axiom(tmp_name)
    finally:
        os.remove(os.path.join(AXIOM_DIR, f"{tmp_name}.axiom"))
    return parsed


def get_snapshot_meta(agent_name: str) -> dict | None:
    """Return snapshot metadata or None."""
    meta_path = SNAPSHOT_DIR / f"{agent_name.lower()}_best_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path) as f:
        return _json.load(f)


def restore_if_degraded(
    agent_name: str,
    current_score: float,
) -> bool:
    """
    Compare current score to snapshot best.
    If current is lower, restore snapshot to disk.
    Returns True if restore happened, False if current is fine.
    """
    import shutil
    meta = get_snapshot_meta(agent_name)
    if meta is None:
        return False  # no snapshot to restore from

    best_score = meta.get("score", -1.0)
    if current_score >= best_score:
        return False  # current is as good or better

    snap_path = SNAPSHOT_DIR / f"{agent_name.lower()}_best.axiom"
    if not snap_path.exists():
        return False

    dest = os.path.join(AXIOM_DIR, f"{agent_name.lower()}.axiom")
    shutil.copy2(snap_path, dest)
    print(
        f"Warning: Degradation detected -- {agent_name} score {current_score:.1f} < "
        f"snapshot {best_score:.1f}. Restored v{meta.get('version', '?')}."
    )
    return True


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


def resolve_trust_level(parsed: dict, default: int = 2) -> int:
    """Return trust level as int, falling back to default if unset or invalid."""
    raw = str(parsed.get("trust_level", "")).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _has_high_risk_concept(task: str, parsed: dict) -> bool:
    """Return True if HighRiskInput is detected via WHEN or concept keywords."""
    table = compile_decision_table(parsed)
    activated = apply_decision_table(task, table)
    if "HighRiskInput" in activated:
        return True

    # Merge concept library if present for keyword detection fallback
    concepts = list(parsed.get("concepts", []))
    concepts_path = os.path.join(AXIOM_DIR, "concepts.axiom")
    if os.path.exists(concepts_path):
        lib = load_axiom("concepts")
        existing = {c.get("name") for c in concepts}
        for c in lib.get("concepts", []):
            if c.get("name") not in existing:
                concepts.append(c)

    detected = detect_concepts(task, {"concepts": concepts})
    return "HighRiskInput" in detected


def should_route_to_sandbox(task: str, parsed: dict, trust_threshold: int = 2) -> bool:
    """Decide whether to route the task to the sandbox agent."""
    if not parsed.get("sandbox_agent"):
        return False

    trust_level = resolve_trust_level(parsed, default=trust_threshold)
    if trust_level >= trust_threshold:
        return False

    return _has_high_risk_concept(task, parsed)