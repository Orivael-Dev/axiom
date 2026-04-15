# axiom/parser.py
# Reads .axiom files and converts them to system prompts

import os

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
        "purpose": "",
        "goal": "",
        "constraints": [],
        "rules": [],
        "process": [],
        "check": [],
        "output": [],
        "success": {},
        "tools": []
    }
    
    current_section = None
    
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        
        # Detect section headers
        if line.startswith("AGENT "):
            parsed["agent"] = line.replace("AGENT ", "").strip()
        elif line.startswith("PURPOSE "):
            parsed["purpose"] = line.replace("PURPOSE ", "").strip()
        elif line.startswith("GOAL "):
            parsed["goal"] = line.replace("GOAL ", "").strip()
        elif line.startswith("CONSTRAINT "):
            parsed["constraints"].append(line.replace("CONSTRAINT ", "").strip())
        elif line == "PROCESS":
            current_section = "process"
        elif line == "CHECK":
            current_section = "check"
        elif line == "OUTPUT":
            current_section = "output"
        elif line == "RULES":
            current_section = "rules"
        elif line == "TOOLS":
            current_section = "tools"
        elif line == "SUCCESS":
            current_section = "success"
        elif line.startswith("- ") and current_section:
            if current_section == "success":
                pass  # handled below
            else:
                parsed[current_section].append(line[2:].strip())
        elif ":" in line and current_section == "success":
            key, val = line.split(":", 1)
            parsed["success"][key.strip()] = float(val.strip())

    # Deduplicate all list sections
    parsed["check"] = list(dict.fromkeys(parsed["check"]))
    parsed["process"] = list(dict.fromkeys(parsed["process"]))
    parsed["constraints"] = list(dict.fromkeys(parsed["constraints"]))

    return parsed


def to_system_prompt(parsed: dict) -> str:
    """Convert parsed .axiom dict into a system prompt string."""
    parts = []
    
    if parsed["purpose"]:
        parts.append(f"You are {parsed['agent']}. {parsed['purpose']}.")
    
    if parsed["goal"]:
        parts.append(f"\nYour goal: {parsed['goal']}.")
    
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
    
    return "\n".join(parts)


def get_prompt(agent_name: str) -> str:
    """One-call shortcut — load .axiom and return system prompt."""
    parsed = load_axiom(agent_name)
    return to_system_prompt(parsed)


def save_axiom(agent_name: str, parsed: dict):
    """Write a modified .axiom back to disk — this is how agents rewrite themselves."""
    path = os.path.join(AXIOM_DIR, f"{agent_name.lower()}.axiom")
    
    lines = []
    lines.append(f"AGENT {parsed['agent']}")
    
    if parsed["purpose"]:
        lines.append(f"PURPOSE {parsed['purpose']}")
    if parsed["goal"]:
        lines.append(f"GOAL {parsed['goal']}")
    
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
    
    if parsed["success"]:
        lines.append("")
        lines.append("SUCCESS")
        for metric, weight in parsed["success"].items():
            lines.append(f"{metric}: {weight}")
    
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