# axiom/parser.py
# Reads .axiom files and converts them to system prompts

import hashlib as _hashlib
import os
import re as _re
import json as _json
from datetime import datetime, timezone
from pathlib import Path


class AxiomConstitutionalViolation(Exception):
    """Raised when save_axiom attempts to modify a CANNOT_MUTATE field."""
    pass


class TrustHierarchyViolation(Exception):
    """Raised when a delegation attempt violates the trust hierarchy.

    Trust levels: 1 = Master (most privileged), 2 = SandboxWorker, 3+ = Task.
    Delegation must flow downward (1→2→3), never upward.
    """
    pass


class HumanReviewRequired(Exception):
    """Raised when save_axiom detects a change that requires human approval.

    The review entry has been written to the review queue (axiom_files/.reviews/).
    Call axiom-review approve <id> to allow the save to proceed.
    """
    def __init__(self, review_id: str, trigger: str, details: str = ""):
        self.review_id = review_id
        self.trigger = trigger
        super().__init__(
            f"Human review required before save. "
            f"Review ID: {review_id}  Trigger: {trigger}. "
            f"Run: python axiom_review.py approve {review_id}"
            + (f"\nDetails: {details}" if details else "")
        )

AXIOM_DIR = os.environ.get("AXIOM_FILES_DIR", "axiom_files")


class AxiomAgentNameViolation(ValueError):
    """Raised when agent_name contains path-traversal or invalid characters."""
    pass


def _sanitize_agent_name(agent_name: str) -> str:
    """Reject agent names containing path-traversal or absolute-path components.

    Allowed: lowercase letters, digits, ``_``, ``-``, ``.`` and a single ``/``
    separating an optional subdirectory (e.g. ``core/worker``). Anything else
    — ``..`` segments, leading ``/``, backslashes, NUL bytes, drive letters,
    URL-encoded traversal — is refused so attacker-controlled inputs from the
    REST/MCP API cannot escape AXIOM_DIR.
    """
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise AxiomAgentNameViolation("agent_name must be a non-empty string")
    base = agent_name.strip().lower()
    if "\x00" in base or "\\" in base:
        raise AxiomAgentNameViolation(f"agent_name contains illegal characters: {agent_name!r}")
    if base.startswith("/") or base.startswith("./") or os.path.isabs(base):
        raise AxiomAgentNameViolation(f"agent_name must be relative: {agent_name!r}")
    parts = base.split("/")
    if len(parts) > 2 or any(p in ("", "..", ".") for p in parts):
        raise AxiomAgentNameViolation(f"agent_name has illegal path component: {agent_name!r}")
    if not _re.fullmatch(r"[a-z0-9._\-/]+", base):
        raise AxiomAgentNameViolation(f"agent_name contains illegal characters: {agent_name!r}")
    return base


def _resolve_axiom_path(agent_name: str) -> str:
    """Resolve agent name to .axiom path, searching core/ and research/ subdirs."""
    base = _sanitize_agent_name(agent_name)
    axiom_root = Path(AXIOM_DIR).resolve()
    # Strip subdir prefix if already included (e.g. "core/worker" -> "worker")
    if "/" in base:
        rel = base
        path = os.path.join(AXIOM_DIR, f"{rel}.axiom")
        if os.path.exists(path) and _within(path, axiom_root):
            return path
        # Try as-is without extension
        alt = os.path.join(AXIOM_DIR, rel)
        if os.path.exists(alt) and _within(alt, axiom_root):
            return alt
    candidates = [
        os.path.join(AXIOM_DIR, f"{base}.axiom"),
        os.path.join(AXIOM_DIR, "core", f"{base}.axiom"),
        os.path.join(AXIOM_DIR, "research", f"{base}.axiom"),
    ]
    for p in candidates:
        if os.path.exists(p) and _within(p, axiom_root):
            return p
    return candidates[0]  # caller handles missing file


def _within(candidate: str, root: Path) -> bool:
    """Return True iff candidate resolves inside root (post-symlink)."""
    try:
        resolved = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False



def _parse_tool_entry(raw: str) -> dict:
    """
    Parse a structured TOOLS bullet entry into a dict.

    Format:  tool_name: perm1, perm2 [| sandbox: true] [| allow_from: N]
    Examples:
      web_search: read, network | sandbox: true
      file_write: write, filesystem | sandbox: true | allow_from: 1
      execute_code: execute | sandbox: true | allow_from: 1

    Returns:
      {"name": str, "permissions": [str], "sandbox": bool, "allow_from": int|None}
    """
    result = {"name": "", "permissions": [], "sandbox": False, "allow_from": None}

    # Split on | to get segments
    segments = [s.strip() for s in raw.split("|")]

    # First segment: "name: perm1, perm2"
    first = segments[0]
    if ":" in first:
        name_part, perm_part = first.split(":", 1)
        result["name"] = name_part.strip()
        result["permissions"] = [p.strip().lower() for p in perm_part.split(",") if p.strip()]
    else:
        result["name"] = first.strip()

    # Remaining segments: "sandbox: true", "allow_from: 1"
    for seg in segments[1:]:
        if ":" in seg:
            key, val = seg.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            if key == "sandbox":
                result["sandbox"] = val.lower() == "true"
            elif key == "allow_from":
                try:
                    result["allow_from"] = int(val)
                except ValueError:
                    pass

    return result


def load_axiom(agent_name: str, verify: bool = False) -> dict:
    """Read a .axiom file and parse it into sections.

    Args:
        verify: When True, check the file's SHA256 against the supply chain
                registry before parsing. Sets parsed["_supply_chain"] with the
                result dict. TAMPERED files still load — the caller decides
                whether to block; this surfaces the signal without hard-stopping
                benign uses like the validator or certifier.
    """
    path = _resolve_axiom_path(agent_name)

    if not os.path.exists(path):
        raise FileNotFoundError(f"No .axiom file found for agent: {agent_name}")

    # ── Supply chain verification (LLM05) ─────────────────────────────────────
    _chain_result: dict | None = None
    if verify:
        _chain_result = verify_agent_hash(agent_name)
        if _chain_result["status"] == "TAMPERED":
            import warnings as _w
            _w.warn(
                f"[LLM05] Supply chain integrity failure: {agent_name}.axiom "
                f"hash does not match registry. "
                f"Current: {_chain_result['current_sha256'][:16]}... "
                f"Expected: {_chain_result['registered_sha256'][:16]}... "
                "Proceeding with load — caller should treat output as untrusted.",
                stacklevel=2,
            )
    
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
        "security": [],
        "history": {"retain": [], "decay": [], "promote_after": None, "forget_on": []},
        "thresholds": {},
        "signals": {},
        "drift_levels": {},
        "honesty_criteria": {},
        "human_review": {},
        "rate_limits": {},
        "circuit_config": {},
    }

    current_section = None
    current_concept = None
    current_concept_field = None  # tracks which sub-field we're currently appending to

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
                current_concept_field = "purpose"
                continue
            elif line.startswith("APPLIES WHEN "):
                current_concept["applies_when"] = line.replace("APPLIES WHEN ", "").strip()
                current_concept_field = "applies_when"
                continue
            elif line.startswith("NOT WHEN "):
                current_concept["not_when"] = line.replace("NOT WHEN ", "").strip()
                current_concept_field = "not_when"
                continue
            elif line.upper().startswith("PRIORITY "):
                current_concept["priority"] = line.split(None, 1)[1].strip()
                current_concept_field = "priority"
                continue
            elif line.startswith("REQUIRES "):
                current_concept["requires"] = line.replace("REQUIRES ", "").strip()
                current_concept_field = "requires"
                continue
            elif line.startswith("EFFECT "):
                current_concept["effect"] = line.replace("EFFECT ", "").strip()
                current_concept_field = "effect"
                continue
            elif line[0] == " " and current_concept_field is not None:
                # Indented continuation line — append to current field
                current_concept[current_concept_field] += " " + line.strip()
                continue
            else:
                # Non-indented unrecognised keyword — end the concept block;
                # fall through to the main parser below.
                _flush_concept()
                current_concept = None
                current_concept_field = None
                current_section = None

        # ── Top-level section headers ─────────────────────────────────────────
        if line.startswith("CONCEPT "):
            _flush_concept()
            current_concept_field = None
            current_concept = {
                "priority": "99",
                "name": line.replace("CONCEPT ", "").strip(),
                "purpose": "",
                "applies_when": "",
                "not_when": "",
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
        elif line == "HISTORY":
            current_section = "history"
        elif line in ("THRESHOLDS", "SIGNALS", "DRIFT_LEVELS", "HONESTY_CRITERIA",
                      "HUMAN_REVIEW", "RATE_LIMITS", "CIRCUIT_CONFIG"):
            current_section = line.lower()
        elif line.startswith("- ") and current_section:
            raw = line[2:].strip()
            if current_section == "history":
                _parse_history_directive(parsed["history"], raw)
            elif current_section in ("thresholds", "signals", "drift_levels",
                                      "honesty_criteria", "rate_limits", "circuit_config"):
                # Strip inline comments, then parse key: value
                entry = raw.split("#")[0].strip()
                if ":" in entry:
                    key, val = entry.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    # Coerce numeric values for thresholds and signals only
                    if current_section in ("thresholds", "signals"):
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                    parsed[current_section][key] = val
            elif current_section == "human_review":
                entry = raw.split("#")[0].strip()
                if entry.lower().startswith("require on:"):
                    trigger = entry[len("require on:"):].strip()
                    parsed["human_review"].setdefault("triggers", []).append(trigger)
                elif ":" in entry:
                    key, val = entry.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if val.lower() in ("true", "false"):
                        val = val.lower() == "true"
                    parsed["human_review"][key] = val
            elif current_section == "tools":
                entry = raw.split("#")[0].strip()
                if "|" in entry or ":" in entry:
                    # Structured entry: "tool_name: perm1, perm2 | sandbox: true | allow_from: N"
                    tool_dict = _parse_tool_entry(entry)
                    parsed["tools"].append(tool_dict)
                else:
                    # Legacy flat entry — wrap as minimal structured dict
                    parsed["tools"].append({
                        "name": entry.strip(),
                        "permissions": [],
                        "sandbox": False,
                        "allow_from": None,
                        "_legacy": True,
                    })
                continue  # skip the generic append below
            elif current_section == "success":
                pass  # handled below
            else:
                parsed[current_section].append(raw)
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

    # Attach supply chain result if verification was requested
    if _chain_result is not None:
        parsed["_supply_chain"] = _chain_result
        # UNREGISTERED = not in registry = external import — wire into review gate
        if _chain_result["status"] == "UNREGISTERED":
            parsed["_external_import"] = True

    return parsed


def _parse_history_directive(history: dict, raw: str) -> None:
    """Parse a single HISTORY bullet directive into the history dict."""
    low = raw.lower()
    # retain last N <type> [of <label>]
    m = _re.match(r"retain last (\d+) (\w+)(?: of (.+))?", low)
    if m:
        history["retain"].append({
            "count": int(m.group(1)),
            "type": m.group(2),
            "label": (m.group(3) or m.group(2)).strip(),
        })
        return
    # retain <type> [across <scope>]
    m = _re.match(r"retain (\w+)(?: across (.+))?", low)
    if m:
        history["retain"].append({
            "count": "all",
            "type": m.group(1),
            "label": (m.group(2) or m.group(1)).strip(),
        })
        return
    # decay <condition> after N <unit>
    m = _re.match(r"decay (\S+) after (\d+) (\w+)", low)
    if m:
        history["decay"].append({
            "condition": m.group(1),
            "after": int(m.group(2)),
            "unit": m.group(3),
        })
        return
    # promote <type> after N <unit>
    m = _re.match(r"promote \w+ after (\d+)", low)
    if m:
        history["promote_after"] = int(m.group(1))
        return
    # forget on <trigger>
    m = _re.match(r"forget on (.+)", low)
    if m:
        history["forget_on"].append(m.group(1).strip())
        return


def compile_history(parsed: dict) -> dict:
    """Return the structured history config for an agent.

    Example output:
    {
      "retain": [{"count": 50, "type": "frames", "label": "game state"}],
      "decay":  [{"condition": "low_confidence", "after": 20, "unit": "frames"}],
      "promote_after": 3,
      "forget_on": ["session_end"]
    }
    """
    default = {"retain": [], "decay": [], "promote_after": None, "forget_on": []}
    return {**default, **parsed.get("history", {})}


def _parse_history_directive(history: dict, raw: str) -> None:
    """Parse a single HISTORY bullet directive into the history dict."""
    low = raw.lower()
    # retain last N <type> [of <label>]
    m = _re.match(r"retain last (\d+) (\w+)(?: of (.+))?", low)
    if m:
        history["retain"].append({
            "count": int(m.group(1)),
            "type": m.group(2),
            "label": (m.group(3) or m.group(2)).strip(),
        })
        return
    # retain <type> [across <scope>]
    m = _re.match(r"retain (\w+)(?: across (.+))?", low)
    if m:
        history["retain"].append({
            "count": "all",
            "type": m.group(1),
            "label": (m.group(2) or m.group(1)).strip(),
        })
        return
    # decay <condition> after N <unit>
    m = _re.match(r"decay (\S+) after (\d+) (\w+)", low)
    if m:
        history["decay"].append({
            "condition": m.group(1),
            "after": int(m.group(2)),
            "unit": m.group(3),
        })
        return
    # promote <type> after N <unit>
    m = _re.match(r"promote \w+ after (\d+)", low)
    if m:
        history["promote_after"] = int(m.group(1))
        return
    # forget on <trigger>
    m = _re.match(r"forget on (.+)", low)
    if m:
        history["forget_on"].append(m.group(1).strip())
        return


def compile_history(parsed: dict) -> dict:
    """Return the structured history config for an agent.

    Example output:
    {
      "retain": [{"count": 50, "type": "frames", "label": "game state"}],
      "decay":  [{"condition": "low_confidence", "after": 20, "unit": "frames"}],
      "promote_after": 3,
      "forget_on": ["session_end"]
    }
    """
    default = {"retain": [], "decay": [], "promote_after": None, "forget_on": []}
    return {**default, **parsed.get("history", {})}


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


# ── Human Review Queue ────────────────────────────────────────────────────────

REVIEW_DIR  = Path(AXIOM_DIR) / ".reviews"
CHAIN_DIR   = Path(AXIOM_DIR) / ".chain"
_CHAIN_FILE = CHAIN_DIR / "supply_chain.json"


# ── Supply Chain SHA256 Registry (LLM05) ──────────────────────────────────────

def _file_sha256(path: Path) -> str:
    return _hashlib.sha256(path.read_bytes()).hexdigest()


def _load_chain() -> dict:
    """Load supply_chain.json, return {} if missing or corrupt."""
    if not _CHAIN_FILE.exists():
        return {}
    try:
        return _json.loads(_CHAIN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_chain(chain: dict) -> None:
    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    _CHAIN_FILE.write_text(_json.dumps(chain, indent=2), encoding="utf-8")


def register_agent_hash(agent_name: str) -> str:
    """
    Hash the current .axiom file and write it to the supply chain registry.
    Called automatically by save_axiom() after every successful save.
    Returns the sha256 hex digest.
    """
    src = Path(_resolve_axiom_path(agent_name))
    if not src.exists():
        raise FileNotFoundError(f"Cannot register: {src} not found")
    digest = _file_sha256(src)
    chain = _load_chain()
    chain[agent_name.lower()] = {
        "sha256": digest,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "version": "",          # filled in by verify step if parsed
        "size_bytes": src.stat().st_size,
    }
    _save_chain(chain)
    return digest


def verify_agent_hash(agent_name: str) -> dict:
    """
    Compare current .axiom file hash against the supply chain registry.

    Returns:
      {"status": "VERIFIED"|"TAMPERED"|"UNREGISTERED",
       "agent": str, "current_sha256": str,
       "registered_sha256": str|None, "registered_at": str|None}
    """
    src = Path(_resolve_axiom_path(agent_name))
    if not src.exists():
        return {
            "status": "UNREGISTERED", "agent": agent_name.lower(),
            "current_sha256": None, "registered_sha256": None, "registered_at": None,
        }
    current = _file_sha256(src)
    chain = _load_chain()
    entry = chain.get(agent_name.lower())
    if entry is None:
        return {
            "status": "UNREGISTERED", "agent": agent_name.lower(),
            "current_sha256": current, "registered_sha256": None, "registered_at": None,
        }
    expected = entry.get("sha256", "")
    status = "VERIFIED" if current == expected else "TAMPERED"
    return {
        "status": status,
        "agent": agent_name.lower(),
        "current_sha256": current,
        "registered_sha256": expected,
        "registered_at": entry.get("registered_at"),
    }


_REVIEW_RISK = {
    "security_modification":  "HIGH",
    "trust_level_change":     "HIGH",
    "external_agent_import":  "HIGH",
    "semantic_drift":         "MEDIUM",
    "bulk_constraint_change": "MEDIUM",
    "score_below_snapshot":   "MEDIUM",
    "cannot_mutate_expansion":"LOW",
}


def _review_id() -> str:
    """Generate a short human-readable review ID."""
    import random, string
    return "RVW-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _write_review_entry(
    agent_name: str,
    trigger: str,
    diff: list,
    before_hash: str,
    pending_hash: str,
    recommendation: str = "",
    timeout_hours: int = 24,
) -> str:
    """Append an entry to the review queue and return the review_id."""
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    queue_path = REVIEW_DIR / "review_queue.jsonl"
    review_id = _review_id()
    entry = {
        "review_id": review_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent_name.lower(),
        "trigger": trigger,
        "risk_level": _REVIEW_RISK.get(trigger, "MEDIUM"),
        "requires_human": True,
        "timeout_hours": timeout_hours,
        "diff": diff,
        "recommendation": recommendation,
        "axiom_file_hash_before": before_hash,
        "axiom_file_hash_pending": pending_hash,
        "status": "PENDING",
    }
    with open(queue_path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry) + "\n")
    return review_id


def _text_fingerprint(parsed: dict) -> str:
    """Produce a normalised bag-of-words string from all text fields for drift detection."""
    parts = []
    for field in ("constraints", "rules", "process", "check", "failure",
                  "output", "security", "when"):
        parts.extend(parsed.get(field, []))
    for field in ("purpose", "goal"):
        val = parsed.get(field, "")
        if val:
            parts.append(val)
    return " ".join(parts).lower()


def _vocab_drift(before_text: str, after_text: str) -> float:
    """
    Simple vocabulary-overlap drift proxy.
    Returns 0.0 (identical) to 1.0 (completely different).
    Upgraded to cosine when LLM03 semantic drift module is built.
    """
    import re as _r
    _tok = _r.compile(r"\b\w{4,}\b")
    words_a = set(_tok.findall(before_text))
    words_b = set(_tok.findall(after_text))
    if not words_a and not words_b:
        return 0.0
    union = words_a | words_b
    intersection = words_a & words_b
    return 1.0 - (len(intersection) / len(union)) if union else 0.0


def _detect_review_triggers(
    agent_name: str,
    original: dict,
    proposed: dict,
    current_score: float | None = None,
) -> list[dict]:
    """
    Run all 9 trigger detectors. Returns list of fired trigger dicts:
    [{"trigger": str, "diff": ..., "recommendation": str}]
    """
    fired = []

    # 1. Security modification
    orig_sec = set(original.get("security", []))
    new_sec  = set(proposed.get("security", []))
    if orig_sec != new_sec:
        added   = sorted(new_sec - orig_sec)
        removed = sorted(orig_sec - new_sec)
        rec = "REJECT — core security rule removed" if removed else "REVIEW — new security rule added"
        fired.append({
            "trigger": "security_modification",
            "diff": {"security": {"added": added, "removed": removed}},
            "recommendation": rec,
        })

    # 2. Trust level change
    orig_tl = str(original.get("trust_level", "")).strip()
    new_tl  = str(proposed.get("trust_level", "")).strip()
    if orig_tl and new_tl and orig_tl != new_tl:
        fired.append({
            "trigger": "trust_level_change",
            "diff": {"trust_level": {"before": orig_tl, "after": new_tl}},
            "recommendation": f"REVIEW — trust level changed {orig_tl} -> {new_tl}",
        })

    # 3. Semantic drift
    drift_threshold = 0.20
    hr_block = proposed.get("human_review", {})
    for trig in hr_block.get("triggers", []):
        if trig.startswith("semantic_drift"):
            try:
                drift_threshold = float(trig.split(">")[1].strip())
            except Exception:
                pass
    before_fp = _text_fingerprint(original)
    after_fp  = _text_fingerprint(proposed)
    drift = _vocab_drift(before_fp, after_fp)
    if drift > drift_threshold:
        fired.append({
            "trigger": "semantic_drift",
            "diff": {"drift_score": round(drift, 3), "threshold": drift_threshold},
            "recommendation": f"REVIEW — vocabulary drift {drift:.0%} exceeds {drift_threshold:.0%} threshold",
        })

    # 4. Bulk constraint change
    bulk_threshold = 3
    for trig in hr_block.get("triggers", []):
        if trig.startswith("bulk_constraint_change"):
            try:
                bulk_threshold = int(trig.split(">")[1].strip())
            except Exception:
                pass
    constraint_diff = diff_axiom(original, proposed)
    constraint_changes = next(
        (d for d in constraint_diff if d.get("field") == "constraints"), {}
    )
    n_changed = len(constraint_changes.get("added", [])) + len(constraint_changes.get("removed", []))
    if n_changed > bulk_threshold:
        fired.append({
            "trigger": "bulk_constraint_change",
            "diff": {"constraints_changed": n_changed, "threshold": bulk_threshold,
                     "detail": constraint_changes},
            "recommendation": f"REVIEW — {n_changed} constraints changed in one save (bulk change signature)",
        })

    # 5. External agent import — caller must pass proposed["_external_import"] = True
    if proposed.get("_external_import"):
        fired.append({
            "trigger": "external_agent_import",
            "diff": {"source": proposed.get("_import_source", "unknown")},
            "recommendation": "REVIEW — file originated outside AXIOM runtime",
        })

    # 6. Score below snapshot with pending rewrite
    if current_score is not None:
        meta = get_snapshot_meta(agent_name)
        if meta:
            best = meta.get("score", -1.0)
            if current_score < best:
                fired.append({
                    "trigger": "score_below_snapshot",
                    "diff": {"current_score": current_score, "snapshot_best": best},
                    "recommendation": (
                        f"REVIEW — proposed version scores {current_score:.2f} < "
                        f"snapshot best {best:.2f}"
                    ),
                })

    # 7. CANNOT_MUTATE expansion (new fields being protected)
    orig_cm = set(f.lower() for f in original.get("cannot_mutate", []))
    new_cm  = set(f.lower() for f in proposed.get("cannot_mutate", []))
    added_guards = new_cm - orig_cm
    if added_guards:
        fired.append({
            "trigger": "cannot_mutate_expansion",
            "diff": {"added_guards": sorted(added_guards)},
            "recommendation": f"REVIEW — new CANNOT_MUTATE fields: {sorted(added_guards)}",
        })

    # 8. Watermark manipulation — WatermarkIntegrity WHEN entries or security rules removed
    _wm_keywords = {"watermark", "watermarkintegrity", "human-written", "ai-generated"}
    orig_when = [e.lower() for e in original.get("when", [])]
    new_when  = [e.lower() for e in proposed.get("when", [])]
    removed_wm_when = [
        e for e in orig_when
        if any(kw in e for kw in _wm_keywords) and e not in new_when
    ]
    orig_sec_rules = [r.lower() for r in original.get("security", [])]
    new_sec_rules  = [r.lower() for r in proposed.get("security", [])]
    removed_wm_sec = [
        r for r in orig_sec_rules
        if any(kw in r for kw in _wm_keywords) and r not in new_sec_rules
    ]
    if removed_wm_when or removed_wm_sec:
        fired.append({
            "trigger": "watermark_manipulation_detected",
            "diff": {
                "removed_when_entries": removed_wm_when,
                "removed_security_rules": removed_wm_sec,
            },
            "recommendation": "REJECT -- watermark disclosure controls removed (EU AI Act Article 50)",
        })

    # 9. Evaluation data tainted — fairness criteria removed or thresholds zeroed
    # Protects against disabling fairness evaluation at the spec level (teacher.axiom).
    _fairness_criteria = {"demographic_inconsistency", "group_disparagement"}
    orig_sig = original.get("signals", {})
    new_sig  = proposed.get("signals", {})
    if isinstance(orig_sig, dict) and isinstance(new_sig, dict):
        removed_fairness_signals = _fairness_criteria & (set(orig_sig) - set(new_sig))
        zeroed_signals = {
            k for k in _fairness_criteria
            if float(orig_sig.get(k, 0.0)) > 0.0 and float(new_sig.get(k, 0.0)) == 0.0
        }
        if removed_fairness_signals or zeroed_signals:
            fired.append({
                "trigger": "evaluation_data_tainted",
                "diff": {
                    "removed_fairness_signals": list(removed_fairness_signals),
                    "zeroed_fairness_signals": list(zeroed_signals),
                },
                "recommendation": (
                    "REJECT -- fairness evaluation criteria disabled; "
                    "this would allow tainted data to reach the certification ledger"
                ),
            })

    return fired


def save_axiom(agent_name: str, parsed: dict, bypass_review: bool = False,
               current_score: float | None = None):
    """Write a modified .axiom back to disk — this is how agents rewrite themselves."""

    # ── Constitutional enforcement ────────────────────────────
    ALWAYS_PROTECTED = {"agent", "goal", "version", "security", "trust_level"}
    protected = ALWAYS_PROTECTED | set(parsed.get("cannot_mutate", []))

    # Load original to compare against
    try:
        original = load_axiom(agent_name)

        # Refuse if CANNOT_MUTATE was itself removed from the new version
        original_protected = set(original.get("cannot_mutate", []))
        new_protected = set(parsed.get("cannot_mutate", []))
        removed_guards = original_protected - new_protected
        if removed_guards:
            raise AxiomConstitutionalViolation(
                f"save_axiom: Rewriter attempted to remove protected fields "
                f"from CANNOT_MUTATE in {agent_name}.axiom: {sorted(removed_guards)}"
            )

        # Check that no protected field value was changed
        for field in protected:
            if field in original and original[field] != parsed.get(field):
                raise AxiomConstitutionalViolation(
                    f"Cannot modify protected field '{field}' in {agent_name}.axiom — "
                    f"declared as CANNOT_MUTATE. "
                    f"Original: {repr(original[field])} -> Attempted: {repr(parsed.get(field))}"
                )
    except FileNotFoundError:
        pass  # New file -- no original to compare against

    # If CANNOT_MUTATE is completely absent, refuse to save
    if not parsed.get("cannot_mutate"):
        raise AxiomConstitutionalViolation(
            f"save_axiom: CANNOT_MUTATE is missing entirely from {agent_name}.axiom — "
            f"refusing to save. Restore from snapshot."
        )

    # ── Human Review Gate ──────────────────────────────────────────────────────
    # Runs after constitutional checks, before file write.
    # Skipped on new files (no original) and when bypass_review=True (CLI approval path).
    if not bypass_review:
        try:
            _original_for_review = load_axiom(agent_name)
            triggers_fired = _detect_review_triggers(
                agent_name, _original_for_review, parsed, current_score=current_score
            )
            if triggers_fired:
                src = Path(_resolve_axiom_path(agent_name))
                before_hash = _file_sha256(src) if src.exists() else ""
                pending_text = "\n".join(
                    [f"{k}: {v}" for k, v in parsed.items() if isinstance(v, str)]
                )
                pending_hash = _hashlib.sha256(pending_text.encode()).hexdigest()
                hr = parsed.get("human_review", {})
                timeout_hours = 24
                try:
                    timeout_hours = int(str(hr.get("timeout", "24h")).rstrip("h"))
                except (ValueError, TypeError):
                    pass
                # Write one queue entry per trigger; raise on the first HIGH, else first overall
                fired_sorted = sorted(
                    triggers_fired,
                    key=lambda t: 0 if _REVIEW_RISK.get(t["trigger"]) == "HIGH" else 1
                )
                first = fired_sorted[0]
                review_id = _write_review_entry(
                    agent_name=agent_name,
                    trigger=first["trigger"],
                    diff=first["diff"],
                    before_hash=before_hash,
                    pending_hash=pending_hash,
                    recommendation=first["recommendation"],
                    timeout_hours=timeout_hours,
                )
                raise HumanReviewRequired(
                    review_id=review_id,
                    trigger=first["trigger"],
                    details=first["recommendation"],
                )
        except (FileNotFoundError, HumanReviewRequired):
            raise  # re-raise HumanReviewRequired; FileNotFoundError = new file, skip gate
        except Exception:
            pass  # gate errors must never block saves — fail open for non-review exceptions

    # -- Version history --------------------------------------------------
    try:
        original = load_axiom(agent_name)
        append_history(agent_name, original, parsed)
    except FileNotFoundError:
        pass  # first save -- no history to diff

    # -- rest of existing save_axiom code continues below -----------------

    path = _resolve_axiom_path(agent_name)
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

    if parsed.get("tools"):
        lines.append("")
        lines.append("TOOLS")
        for tool in parsed["tools"]:
            if isinstance(tool, dict):
                if tool.get("_legacy"):
                    lines.append(f"- {tool['name']}")
                else:
                    parts = [f"{tool['name']}: {', '.join(tool.get('permissions', []))}"]
                    if tool.get("sandbox") is not None:
                        parts.append(f"sandbox: {str(tool['sandbox']).lower()}")
                    if tool.get("allow_from") is not None:
                        parts.append(f"allow_from: {tool['allow_from']}")
                    lines.append("- " + " | ".join(parts))
            else:
                lines.append(f"- {tool}")

    if parsed.get("human_review"):
        hr = parsed["human_review"]
        lines.append("")
        lines.append("HUMAN_REVIEW")
        for trigger in hr.get("triggers", []):
            lines.append(f"- require on: {trigger}")
        for key in ("timeout", "escalate_to", "block_on_timeout"):
            if key in hr:
                val = hr[key]
                if isinstance(val, bool):
                    val = str(val).lower()
                lines.append(f"- {key}: {val}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── Supply chain: register new hash after every successful save ───────────
    try:
        register_agent_hash(agent_name)
    except Exception:
        pass  # registry update failure must never block saves

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
    # Sort matched names by the concept's PRIORITY field (lower = higher priority)
    priority_map = {
        c["name"]: int(c.get("priority", 99))
        for c in parsed.get("concepts", [])
    }
    matched.sort(key=lambda n: priority_map.get(n, 99))
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

    Trust hierarchy rule: delegation flows downward only (higher level number).
    A TRUST_LEVEL 1 agent (Master) may delegate to TRUST_LEVEL 2 (SandboxWorker).
    A TRUST_LEVEL 2 agent may NOT delegate back to TRUST_LEVEL 1.
    """
    delegation_map = compile_delegates(parsed)
    source_trust = resolve_trust_level(parsed, default=2)
    matches = []
    for entry in delegation_map:
        if entry["source"].lower() == agent_name.lower():
            if active_state is None or entry["on"].lower() == active_state.lower() \
               or entry["on"] == "always":
                target_trust = get_agent_trust_level(entry["target"], default=2)
                # delegation is only valid going downward (>=) or sideways (==)
                if target_trust >= source_trust:
                    matches.append(entry["target"])
    return matches


def enforce_trust_hierarchy(caller_parsed: dict, target_parsed: dict) -> None:
    """Raise TrustHierarchyViolation if delegation violates the trust order.

    Trust levels: 1 = Master (most privileged), 2 = SandboxWorker, 3+ = Task.
    Delegation must flow downward (to higher level number), never upward.
    """
    caller_level = resolve_trust_level(caller_parsed, default=99)
    target_level = resolve_trust_level(target_parsed, default=99)
    caller_name = caller_parsed.get("agent", "?")
    target_name = target_parsed.get("agent", "?")
    if target_level < caller_level:
        raise TrustHierarchyViolation(
            f"Trust hierarchy violation: {caller_name} (TRUST_LEVEL {caller_level}) "
            f"cannot delegate to {target_name} (TRUST_LEVEL {target_level}) — "
            f"delegation must flow Master(1)→SandboxWorker(2)→Task(3+), never reverse."
        )


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
        # tools parses to list-of-dicts — skip non-hashable entries
        before_set = set(e for e in before.get(field, []) if isinstance(e, str))
        after_set  = set(e for e in after.get(field, []) if isinstance(e, str))
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


SANDBOX_SNAPSHOT_DIR = SNAPSHOT_DIR / "sandbox"


def save_snapshot(
    agent_name: str,
    score: float,
    run_id: str = "",
    task: str = "",
    snapshot_dir: Path | None = None,
) -> bool:
    """
    Save current .axiom as best snapshot if score beats previous best.
    Returns True if snapshot was updated, False if existing was better.

    Pass snapshot_dir=SANDBOX_SNAPSHOT_DIR to isolate sandbox agent snapshots
    from the master snapshot directory — sandbox rollback never touches master state.
    """
    import shutil
    snap_dir = Path(snapshot_dir) if snapshot_dir is not None else SNAPSHOT_DIR
    snap_dir.mkdir(parents=True, exist_ok=True)
    meta_path = snap_dir / f"{agent_name.lower()}_best_meta.json"
    snap_path = snap_dir / f"{agent_name.lower()}_best.axiom"

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
    source = Path(_resolve_axiom_path(agent_name))
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


def load_snapshot(agent_name: str, snapshot_dir: Path | None = None) -> dict | None:
    """
    Load the best snapshot for an agent.
    Returns parsed dict or None if no snapshot exists.

    Pass snapshot_dir=SANDBOX_SNAPSHOT_DIR to load sandbox-isolated snapshots
    without touching the master snapshot directory.
    """
    import tempfile
    snap_dir = Path(snapshot_dir) if snapshot_dir is not None else SNAPSHOT_DIR
    snap_path = snap_dir / f"{agent_name.lower()}_best.axiom"
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


def get_snapshot_meta(agent_name: str, snapshot_dir: Path | None = None) -> dict | None:
    """Return snapshot metadata or None."""
    snap_dir = Path(snapshot_dir) if snapshot_dir is not None else SNAPSHOT_DIR
    meta_path = snap_dir / f"{agent_name.lower()}_best_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path) as f:
        return _json.load(f)


def restore_if_degraded(
    agent_name: str,
    current_score: float,
    snapshot_dir: Path | None = None,
) -> bool:
    """
    Compare current score to snapshot best.
    If current is lower, restore snapshot to disk.
    Returns True if restore happened, False if current is fine.

    Pass snapshot_dir=SANDBOX_SNAPSHOT_DIR for sandbox agents so that rollback
    is isolated from the master snapshot directory.
    """
    import shutil
    snap_dir = Path(snapshot_dir) if snapshot_dir is not None else SNAPSHOT_DIR
    meta = get_snapshot_meta(agent_name, snapshot_dir=snap_dir)
    if meta is None:
        return False  # no snapshot to restore from

    best_score = meta.get("score", -1.0)
    if current_score >= best_score:
        return False  # current is as good or better

    snap_path = snap_dir / f"{agent_name.lower()}_best.axiom"
    if not snap_path.exists():
        return False

    dest = _resolve_axiom_path(agent_name)
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

    # Sort activated concepts by PRIORITY before injecting into system prompt
    parsed["concepts"].sort(key=lambda c: int(c.get("priority", 99)))

    return to_system_prompt(parsed)


def resolve_trust_level(parsed: dict, default: int = 2) -> int:
    """Return trust level as int, falling back to default if unset or invalid."""
    raw = str(parsed.get("trust_level", "")).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_agent_trust_level(agent_name: str, default: int = 2) -> int:
    """Load an agent and return its trust level as int."""
    try:
        parsed = load_axiom(agent_name)
    except Exception:
        return default
    return resolve_trust_level(parsed, default=default)


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