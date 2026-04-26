"""
axiom/init.py -- Drop-in initializer for axiom-constitutional projects.

Copies real .axiom files from the installed package source directory
(not embedded templates) so new projects always get the latest definitions.

Entry points:
    axiom-init  ->  axiom.init:main
    axiom-add   ->  axiom.init:add_main

Environment:
    AXIOM_FILES_DIR  Override the source axiom_files/ directory
                     (useful for local dev: set to i:/vsCode/promt-agent/axiom_files)

Usage:
    axiom init [--dir TARGET_DIR]
    axiom add <domain> [--dir TARGET_DIR]

    python axiom/init.py            --dir C:/temp/axiom_test
    python axiom/init.py add hipaa  --dir C:/temp/axiom_test
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Source-directory resolution
# ---------------------------------------------------------------------------

def _find_source_dir() -> Path:
    """
    Locate the axiom_files/ source directory.

    Priority:
      1. AXIOM_FILES_DIR env var (developer override)
      2. Sibling of this file's package root (installed editable)
      3. Installed package-data via importlib.resources
    """
    env_override = os.environ.get("AXIOM_FILES_DIR")
    if env_override:
        p = Path(env_override).resolve()
        if p.is_dir():
            return p
        print(f"[!] AXIOM_FILES_DIR set but not found: {p}")

    # Walk up from this file to find axiom_files/ alongside axiom/
    here = Path(__file__).resolve().parent          # axiom/
    for candidate in [here.parent / "axiom_files",  # project root
                      here / "axiom_files"]:        # inside package
        if candidate.is_dir():
            return candidate

    # Try importlib.resources (installed package)
    try:
        import importlib.resources as pkg
        ref = pkg.files("axiom_files")
        p = Path(str(ref)).resolve()
        if p.is_dir():
            return p
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Domain alias map
# ---------------------------------------------------------------------------

_DOMAIN_ALIASES: dict[str, str] = {
    "hipaa":       "healthcare",
    "healthcare":  "healthcare",
    "finance":     "finance",
    "fintech":     "finance",
    "banking":     "finance",
    "government":  "government",
    "gov":         "government",
    "public":      "government",
    "robotics":    "robotics",   # v2.0 preview -- see add_cmd
    "iot":         "robotics",
    "industrial":  "robotics",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _validate(target_dir: Path, agent: str = "worker") -> bool:
    """Run the AXIOM validator on an agent file inside target_dir."""
    axiom_file = target_dir / "axiom_files" / f"{agent}.axiom"
    if not axiom_file.exists():
        return True  # nothing to validate yet

    # Add target_dir to sys.path so validator can import concepts
    original_path = list(sys.path)
    sys.path.insert(0, str(target_dir))
    try:
        from axiom_files.validator import validate_file
        result = validate_file(agent)
        # validate_file returns a dict: {status, issues, suggestions}
        if isinstance(result, dict):
            status = result.get("status", "unknown")
            issues = result.get("issues", [])
            suggestions = result.get("suggestions", [])
            if issues:
                print(f"  [validator] {agent}.axiom -- {status}")
                for issue in issues:
                    print(f"    [!] {issue}")
                for s in suggestions:
                    print(f"    [?] {s}")
                return status == "valid"
            else:
                print(f"  [validator] {agent}.axiom -- {status}")
                return True
        # Legacy: list of error strings
        elif result:
            print(f"  [validator] {agent}.axiom -- ISSUES")
            for e in result:
                print(f"    [!] {e}")
            return False
        else:
            print(f"  [validator] {agent}.axiom -- valid")
            return True
    except ImportError:
        # Validator not available in target env -- skip silently
        return True
    finally:
        sys.path[:] = original_path


# ---------------------------------------------------------------------------
# axiom init
# ---------------------------------------------------------------------------

CORE_FILES = [
    "worker.axiom",
    "concepts.axiom",
    "sandbox.axiom",
    "teacher.axiom",
]

ENV_EXAMPLE = """\
# AXIOM environment variables
# Copy to .env and fill in your keys

# Required: OpenAI-compatible API endpoint + key
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
MODEL_NAME=meta/llama-3.3-70b-instruct

# Optional: separate teacher model (defaults to MODEL_NAME)
TEACHER_MODEL=meta/llama-3.1-70b-instruct

# Optional: separate evaluator model
EVALUATOR_MODEL=meta/llama-3.1-70b-instruct

# Optional: axiom-review email escalation
OPERATOR_EMAIL=your@email.com

# Optional: HMAC key for manifest signatures
AXIOM_HMAC_SECRET=change_me_before_production
"""

README_TEMPLATE = """\
# AXIOM Agent -- Quickstart

## 1. Install

```bash
pip install axiom-constitutional
```

## 2. Configure

```bash
cp .env.example .env
# Edit .env -- add your API key
```

## 3. Validate

```bash
axiom validate worker
```

## 4. Run

```bash
axiom run worker "What is 2 + 2?"
```

## 5. Benchmark

```bash
axiom benchmark run worker
```

## 6. Certify

```bash
axiom certify --agent worker --output certs/
```

## Domain Packages

```bash
axiom add healthcare   # HIPAA + clinical safety rules
axiom add finance      # SOX + FINRA trading guardrails
axiom add government   # FISMA + CJIS law enforcement rules
```

## ABP Conformance

AXIOM implements the AXIOM Benchmark Protocol (ABP v1.0):
- Pillar I   -- Uncheatable evaluation (teacher-student)
- Pillar II  -- Full ledger transparency (append-only)
- Pillar III -- Reproducible certification (HMAC-SHA256)

```bash
axiom certify --agent worker     # ABP-VERIFIED report
axiom verify  --cert certs/worker_cert.json
```

See AXIOM_SPEC.md and DEPLOYER_GUIDE.md for full documentation.
"""


def init_cmd(target_dir: Path) -> int:
    """
    axiom init: scaffold a new AXIOM project.

    Copies core .axiom files from the real source directory,
    creates .env.example and AXIOM_README.md, validates worker.axiom.
    """
    src = _find_source_dir()
    if src is None:
        print("[ERROR] Cannot locate axiom_files/ source directory.")
        print("        Set AXIOM_FILES_DIR=<path to axiom_files/> and retry.")
        return 1

    print(f"\nAXIOM Init")
    print(f"  source : {src}")
    print(f"  target : {target_dir}")
    print()

    target_dir.mkdir(parents=True, exist_ok=True)
    axiom_target = target_dir / "axiom_files"
    axiom_target.mkdir(parents=True, exist_ok=True)
    (axiom_target / "domains").mkdir(exist_ok=True)

    # -- Copy core .axiom files -------------------------------------------
    copied = []
    missing = []
    for fname in CORE_FILES:
        src_file = src / fname
        if not src_file.exists():
            missing.append(fname)
            print(f"  [!] Source not found: {src_file}")
            continue
        dst_file = axiom_target / fname
        _copy(src_file, dst_file)
        print(f"  [+] {fname}")
        copied.append(fname)

    # -- .env.example --------------------------------------------------------
    env_path = target_dir / ".env.example"
    env_path.write_text(ENV_EXAMPLE, encoding="utf-8")
    print(f"  [+] .env.example")

    # -- AXIOM_README.md -----------------------------------------------------
    readme_path = target_dir / "AXIOM_README.md"
    readme_path.write_text(README_TEMPLATE, encoding="utf-8")
    print(f"  [+] AXIOM_README.md")

    # -- Validate worker.axiom -----------------------------------------------
    print()
    _validate(target_dir)

    # -- Summary + next steps ------------------------------------------------
    print()
    print("=" * 56)
    print("  AXIOM project initialized.")
    print()
    print("  Next steps:")
    print()
    print("  1. cp .env.example .env")
    print("     Add your API key to .env")
    print()
    print("  2. axiom validate worker")
    print("     Confirm your agent passes all validation phases")
    print()
    print("  3. axiom run worker \"Hello, AXIOM\"")
    print("     Send your first message to the worker agent")
    print()
    print("  4. axiom benchmark run worker")
    print("     Run the ABP honesty + accuracy benchmark")
    print()
    print("  5. axiom certify --agent worker --output certs/")
    print("     Generate a signed ABP-VERIFIED certificate")
    print()
    print("  Domain packages:")
    print("    axiom add healthcare   # HIPAA + clinical safety")
    print("    axiom add finance      # SOX + FINRA guardrails")
    print("    axiom add government   # FISMA + CJIS rules")
    print("=" * 56)
    print()

    if missing:
        print(f"  Warning: {len(missing)} source file(s) not found: {missing}")
        print(f"  Set AXIOM_FILES_DIR to the full axiom_files/ path.")

    return 0


# ---------------------------------------------------------------------------
# axiom add <domain>
# ---------------------------------------------------------------------------

def add_cmd(domain_arg: str, target_dir: Path) -> int:
    """
    axiom add <domain>: install a domain governance package.

    hipaa / healthcare  -- copies healthcare.axiom from source
    finance / fintech   -- copies finance.axiom from source
    government / gov    -- copies government.axiom from source
    robotics / iot      -- copies dos_watcher.axiom as stub (v2.0 preview)
    """
    domain_key = _DOMAIN_ALIASES.get(domain_arg.lower())
    if domain_key is None:
        print(f"[ERROR] Unknown domain: '{domain_arg}'")
        print(f"        Available: {', '.join(sorted(set(_DOMAIN_ALIASES.values())))}")
        return 1

    src = _find_source_dir()
    if src is None:
        print("[ERROR] Cannot locate axiom_files/ source directory.")
        print("        Set AXIOM_FILES_DIR=<path to axiom_files/> and retry.")
        return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    domains_target = target_dir / "axiom_files" / "domains"
    domains_target.mkdir(parents=True, exist_ok=True)

    # -- Robotics: v2.0 preview stub -----------------------------------------
    if domain_key == "robotics":
        stub_src = src / "dos_watcher.axiom"
        stub_dst = domains_target / "robotics_stub.axiom"

        print(f"\nAXIOM Add -- robotics (v2.0 PREVIEW)")
        print(f"  source : {stub_src}")
        print(f"  target : {stub_dst}")
        print()

        if stub_src.exists():
            _copy(stub_src, stub_dst)
            print(f"  [+] robotics_stub.axiom (based on dos_watcher.axiom)")
        else:
            print(f"  [!] Source dos_watcher.axiom not found -- creating placeholder")
            stub_dst.write_text(
                "# AXIOM Robotics Domain -- v2.0 Preview\n"
                "# Full robotics + IoT governance rules ship in AXIOM v2.0\n"
                "# Reference: GameWatcher pipeline (axiom_files/game_watcher.axiom)\n",
                encoding="utf-8",
            )

        print()
        print("=" * 56)
        print("  Robotics domain -- v2.0 PREVIEW")
        print()
        print("  This is a stub scaffolded from dos_watcher.axiom.")
        print("  Full robotics governance rules are part of AXIOM v2.0,")
        print("  which includes the GameWatcher pipeline for physical-world")
        print("  agent safety monitoring.")
        print()
        print("  What ships in v2.0:")
        print("    - GameWatcher: real-time action monitoring for actuators")
        print("    - PHYSICAL_SAFETY block: kinetic force + proximity rules")
        print("    - IEC 62443 alignment: industrial control system rules")
        print("    - Fail-safe BLOCK_ON_UNCERTAINTY for autonomous systems")
        print()
        print("  For now, use the stub as a starting point and watch for")
        print("  axiom-constitutional v2.0 release at github.com/antonioroberts/axiom-constitutional")
        print("=" * 56)
        print()
        return 0

    # -- Standard domain file ------------------------------------------------
    src_file = src / "domains" / f"{domain_key}.axiom"
    dst_file = domains_target / f"{domain_key}.axiom"

    print(f"\nAXIOM Add -- {domain_key}")
    print(f"  source : {src_file}")
    print(f"  target : {dst_file}")
    print()

    if not src_file.exists():
        print(f"  [ERROR] Domain file not found: {src_file}")
        print(f"          Set AXIOM_FILES_DIR to the full axiom_files/ path.")
        return 1

    _copy(src_file, dst_file)
    print(f"  [+] {domain_key}.axiom")

    # -- Validate the domain file --------------------------------------------
    print()
    _validate(target_dir, f"domains/{domain_key}")

    # -- Domain-specific next steps ------------------------------------------
    print()
    print("=" * 56)

    if domain_key == "healthcare":
        print("  Healthcare domain package active.")
        print()
        print("  Includes:")
        print("    - HIPAA Privacy Rule guardrails (45 CFR Part 164)")
        print("    - Clinical safety constraints (do-no-harm)")
        print("    - PHI handling rules (identify + redact)")
        print("    - Emergency escalation triggers")
        print("    - EU AI Act Article 10 bias assessment")
        print()
        print("  Run:")
        print("    axiom run healthcare \"Patient has chest pain\"")
        print()
        print("  Certify:")
        print("    axiom certify --agent healthcare --output certs/")
        print()
        print("  Benchmark:")
        print("    axiom benchmark run healthcare")
        print()
        print("  Compliance note:")
        print("    Healthcare is an ABP-CERTIFIED domain package.")
        print("    Certificate includes FRIA template for EU AI Act Art. 9.")

    elif domain_key == "finance":
        print("  Finance domain package active.")
        print()
        print("  Includes:")
        print("    - SOX Section 302/906 audit trail rules")
        print("    - FINRA suitability guardrails")
        print("    - PCI-DSS card data handling constraints")
        print("    - No-advice boundary enforcement")
        print("    - Insider-information detection triggers")
        print()
        print("  Run:")
        print("    axiom run finance \"Should I buy this stock?\"")
        print()
        print("  Certify:")
        print("    axiom certify --agent finance --output certs/")
        print()
        print("  Benchmark:")
        print("    axiom benchmark run finance")

    elif domain_key == "government":
        print("  Government domain package active.")
        print()
        print("  Includes:")
        print("    - FISMA security controls alignment")
        print("    - CJIS law enforcement data rules")
        print("    - FOIA response boundary constraints")
        print("    - Classified information detection triggers")
        print("    - ADA accessibility response requirements")
        print()
        print("  Run:")
        print("    axiom run government \"What are FOIA exemptions?\"")
        print()
        print("  Certify:")
        print("    axiom certify --agent government --output certs/")
        print()
        print("  Benchmark:")
        print("    axiom benchmark run government")

    print("=" * 56)
    print()
    return 0


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for axiom-init."""
    parser = argparse.ArgumentParser(
        prog="axiom-init",
        description="Initialize a new AXIOM agent project.",
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Target project directory (default: current directory)",
    )
    args = parser.parse_args()
    sys.exit(init_cmd(Path(args.dir).resolve()))


def add_main() -> None:
    """Entry point for axiom-add."""
    parser = argparse.ArgumentParser(
        prog="axiom-add",
        description="Add a domain governance package to an AXIOM project.",
    )
    parser.add_argument(
        "domain",
        help="Domain name: healthcare, finance, government, robotics (or aliases)",
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Target project directory (default: current directory)",
    )
    args = parser.parse_args()
    sys.exit(add_cmd(args.domain, Path(args.dir).resolve()))


# ---------------------------------------------------------------------------
# Direct invocation: python axiom/init.py [add <domain>] [--dir DIR]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Detect 'add' subcommand by peeking at argv
    if len(sys.argv) >= 2 and sys.argv[1] == "add":
        parser = argparse.ArgumentParser(prog="axiom/init.py add")
        parser.add_argument("domain", help="Domain name")
        parser.add_argument("--dir", default=".", help="Target directory")
        args = parser.parse_args(sys.argv[2:])
        sys.exit(add_cmd(args.domain, Path(args.dir).resolve()))
    else:
        parser = argparse.ArgumentParser(
            prog="axiom/init.py",
            description="AXIOM project initializer",
        )
        parser.add_argument("--dir", default=".", help="Target directory")
        args = parser.parse_args()
        sys.exit(init_cmd(Path(args.dir).resolve()))
