"""
AXIOM v1.8 Export Bundle Builder
Generates axiom-lang-1.8.0/ with all open source files,
MANIFEST.md with SHA256 hashes, CHANGELOG.md, and cert reports.

Run from project root:
  python build_bundle.py
  python build_bundle.py --certs i:/vsCode/promt-agent/certs/
  python build_bundle.py --output D:/releases/

Excludes proprietary files:
  axiom_domain_seeder.py
  domain_hardener.py
  .env, .history/, .snapshots/, .reviews/, .honesty/
  axiom_lab/results/
  certs/ (included separately as signed artifacts)
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.8.0"
BUNDLE_NAME = f"axiom-lang-{VERSION}"

# ── Files to include ───────────────────────────────────────────
OPEN_SOURCE_FILES = [
    # Core language
    "axiom_files/parser.py",
    "axiom_files/validator.py",
    "axiom_files/concepts.axiom",
    # Agent definitions
    "axiom_files/worker.axiom",
    "axiom_files/evaluator.axiom",
    "axiom_files/rewriter.axiom",
    "axiom_files/sandbox.axiom",
    "axiom_files/teacher.axiom",
    "axiom_files/session.axiom",
    "axiom_files/conversation_monitor.axiom",
    "axiom_files/agent_factory.axiom",
    "axiom_files/skill_builder.axiom",
    "axiom_files/game_watcher.axiom",
    "axiom_files/pattern_agent.axiom",
    "axiom_files/dos_watcher.axiom",
    # Domain packages
    "axiom_files/domains/government.axiom",
    "axiom_files/domains/finance.axiom",
    "axiom_files/domains/healthcare.axiom",
    "axiom_files/domains/domain_index.json",
    # Runtime
    "axiom/client.py",
    "axiom/evolution.py",
    "axiom/session.py",
    "axiom/__init__.py",
    # Agents (axiom/agents/ — runtime wrappers)
    "axiom/agents/worker.py",
    "axiom/agents/evaluator.py",
    "axiom/agents/rewriter.py",
    "axiom/agents/sandbox.py",
    "axiom/agents/sandbox_content.py",
    "axiom/agents/__init__.py",
    # Runtime modules (axiom/ — live at axiom/ not axiom/agents/)
    "axiom/teacher.py",
    "axiom/conversation_monitor.py",
    "axiom/agent_factory.py",
    "axiom/experience_store.py",
    "axiom/integrity_check.py",
    # Lab tools (at project root)
    "run_axiom.py",
    "axiom_certify.py",
    "axiom_review.py",
    # Lab config
    "axiom_lab/configs/axiom_v1_4.json",
    # Server + CLI
    "axiom_server.py",
    "cli.py",
    "pyproject.toml",
    # Docs
    "AXIOM_SPEC.md",
    "AXIOM_ALIGN_PACK.md",
    "DEPLOYER_GUIDE.md",
    "AXIOM_DATA_GOVERNANCE.md",
    "README.md",
    "LICENSE",
]

# Benchmark suites — include all .json files
BENCHMARK_DIRS = [
    "axiom_lab/benchmarks/",
]

# ── Proprietary exclusions ──────────────────────────────────────
EXCLUDE_PATTERNS = [
    "axiom_domain_seeder.py",
    "domain_hardener.py",
    ".env",
    ".history",
    ".snapshots",
    ".reviews",
    ".honesty",
    "axiom_lab/results",
    "__pycache__",
    "*.pyc",
    ".git",
]

# ── SHA256 ─────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

# ── Bundle builder ─────────────────────────────────────────────
def build_bundle(
    project_root: Path,
    output_dir: Path,
    certs_dir: Path = None,
) -> Path:
    ts = datetime.now(timezone.utc)
    timestamp = ts.strftime("%Y%m%d_%H%M%S")
    bundle_dir = output_dir / BUNDLE_NAME
    manifest_entries = []
    missing_files = []

    print(f"\n{'═'*60}")
    print(f"  AXIOM v{VERSION} Export Bundle")
    print(f"  {timestamp}")
    print(f"{'═'*60}\n")

    # Clean previous bundle
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    # ── Copy open source files ─────────────────────────────────
    print("  Copying open source files...")
    copied = 0
    for rel_path in OPEN_SOURCE_FILES:
        src = project_root / rel_path
        dst = bundle_dir / rel_path

        if not src.exists():
            missing_files.append(rel_path)
            print(f"  ⚠ Missing: {rel_path}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        file_hash = sha256_file(dst)
        manifest_entries.append({
            "path": rel_path,
            "sha256": file_hash,
            "size": dst.stat().st_size,
            "category": "open_source",
        })
        copied += 1

    print(f"  ✅ Copied {copied} files ({len(missing_files)} missing)")

    # ── Copy benchmark suites ──────────────────────────────────
    print("  Copying benchmark suites...")
    bench_count = 0
    for bench_dir in BENCHMARK_DIRS:
        src_dir = project_root / bench_dir
        if not src_dir.exists():
            print(f"  ⚠ Benchmark dir missing: {bench_dir}")
            continue
        for src in src_dir.rglob("*.json"):
            rel = src.relative_to(project_root)
            dst = bundle_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            manifest_entries.append({
                "path": str(rel),
                "sha256": sha256_file(dst),
                "size": dst.stat().st_size,
                "category": "benchmark",
            })
            bench_count += 1
    print(f"  ✅ Copied {bench_count} benchmark files")

    # ── Copy certification reports ─────────────────────────────
    cert_count = 0
    if certs_dir and certs_dir.exists():
        print("  Copying certification reports...")
        cert_bundle_dir = bundle_dir / "certs"
        cert_bundle_dir.mkdir(exist_ok=True)

        for cert_file in sorted(certs_dir.glob("*.json")):
            dst = cert_bundle_dir / cert_file.name
            shutil.copy2(cert_file, dst)
            manifest_entries.append({
                "path": f"certs/{cert_file.name}",
                "sha256": sha256_file(dst),
                "size": dst.stat().st_size,
                "category": "certification",
            })
            cert_count += 1

        for cert_file in sorted(certs_dir.glob("*.pdf")):
            dst = cert_bundle_dir / cert_file.name
            shutil.copy2(cert_file, dst)
            manifest_entries.append({
                "path": f"certs/{cert_file.name}",
                "sha256": sha256_file(dst),
                "size": dst.stat().st_size,
                "category": "certification_pdf",
            })
            cert_count += 1

        print(f"  ✅ Copied {cert_count} certification artifacts")
    else:
        print("  — No certs directory specified or found")

    # ── Generate MANIFEST.md ───────────────────────────────────
    print("  Generating MANIFEST.md...")
    manifest_lines = [
        f"# AXIOM v{VERSION} — Bundle Manifest",
        f"Generated: {ts.isoformat()}",
        f"Bundle: {BUNDLE_NAME}",
        "",
        "## File Integrity",
        "",
        "| File | SHA256 | Size | Category |",
        "|------|--------|------|----------|",
    ]
    for e in manifest_entries:
        manifest_lines.append(
            f"| `{e['path']}` | `{e['sha256'][:16]}...` | {e['size']:,}b | {e['category']} |"
        )

    manifest_lines += [
        "",
        "## Bundle Integrity",
        "",
        f"Files included: {len(manifest_entries)}",
        f"Missing files:  {len(missing_files)}",
        f"Cert artifacts: {cert_count}",
        "",
        "## Proprietary Exclusions",
        "",
        "The following files are NOT included in this bundle:",
        "- `axiom_domain_seeder.py` — NIM-powered domain generation",
        "- `domain_hardener.py` — security hardening pipeline",
        "- `axiom_files/.history/` — mutation logs (private)",
        "- `axiom_files/.snapshots/` — evolved agent states (private)",
        "- `axiom_files/.reviews/` — review queue (private)",
        "- `axiom_files/.honesty/` — honesty ledger (private)",
        "- `axiom_lab/results/` — internal benchmark runs",
        "- `.env` — API keys",
        "",
        "## Verification",
        "",
        "Verify any file against this manifest:",
        "```bash",
        "python -c \"import hashlib; print(hashlib.sha256(open('FILE','rb').read()).hexdigest())\"",
        "```",
    ]

    manifest_text = "\n".join(manifest_lines)
    manifest_path = bundle_dir / "MANIFEST.md"
    manifest_path.write_text(manifest_text, encoding="utf-8")

    # Also write machine-readable manifest
    manifest_json = {
        "version": VERSION,
        "timestamp": ts.isoformat(),
        "bundle_name": BUNDLE_NAME,
        "file_count": len(manifest_entries),
        "missing_count": len(missing_files),
        "cert_count": cert_count,
        "files": manifest_entries,
        "missing": missing_files,
        "manifest_hash": sha256_str(manifest_text),
    }
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(manifest_json, indent=2), encoding="utf-8"
    )
    print("  ✅ MANIFEST.md + MANIFEST.json generated")

    # ── Generate CHANGELOG.md ──────────────────────────────────
    print("  Generating CHANGELOG.md...")
    changelog = f"""# AXIOM Changelog

## v1.8.0 — April 2026

### Language Constructs Added
- `THRESHOLDS` block — warn/block/decay values for drift detection
- `SIGNALS` block — weighted signal definitions for ConversationMonitor
- `DRIFT_LEVELS` block — CLEAN/WARN/BLOCK classification rules
- `HONESTY_CRITERIA` block — named cheating pattern definitions
- `TOOLS` block — strict mode tool permission declarations
- `HUMAN_REVIEW` block — 9-trigger human approval gate
- `DRIFT_LEVELS` semantic class added to block reference

### Agents Added
- `teacher.axiom` — honesty + fairness evaluation agent
- `session.axiom` — Layer 1-4 integration specification
- `conversation_monitor.axiom` — 8-signal drift detection spec
- `dos_watcher.axiom` — DoS detection and rate limiting
- `skill_builder.axiom` v1.1 — experience-driven promotion

### Security Stack
- Option C tiered drift flagging — WARN (0.4) + BLOCK (0.6)
- WatermarkIntegrity CONCEPT — EU AI Act Article 50
- Layer 0-4 all spec-driven — no hardcoded thresholds
- TOOLS block with Phase 3n validator — LLM07 complete

### Honesty + Fairness System
- Teacher-student honesty evaluation — 100% on final run
- Teacher-student fairness evaluation — 85% baseline
- 3 genuine bias signals documented:
  T1-D1: raise email — name dimension
  T5-D1: complaint letter — name dimension  
  T5-D4: complaint letter — location dimension
- Empty response guard — prevents tainted ledger writes
- evaluation_data_tainted — 9th HUMAN_REVIEW trigger

### Validator Phases Added
- Phase 3h — SIGNALS weight range 0.3-3.0
- Phase 3i — warn_threshold < block_threshold
- Phase 3j — HONESTY_CRITERIA entry format
- Phase 3k — HONESTY_CRITERIA SIGNALS weight range
- Phase 3l — honesty_criteria in CANNOT_MUTATE
- Phase 3m — SECURITY append-only ledger rule
- Phase 3n — TOOLS execute/delete sandbox enforcement
- Phase 3o — HUMAN_REVIEW block_on_timeout + triggers

### HUMAN_REVIEW Triggers (9 total)
1. security_modification
2. trust_level_change
3. semantic_drift > 0.20
4. bulk_constraint_change > 3
5. external_agent_import
6. score_below_snapshot with pending_rewrite
7. cannot_mutate_expansion
8. watermark_manipulation_detected
9. evaluation_data_tainted

### EU AI Act Compliance
- FRIA template auto-generated by axiom-certify
- AXIOM_DATA_GOVERNANCE.md — Article 10 data governance
- ai_disclosure field in every response — Article 50
- WatermarkIntegrity — Article 50 watermark protection
- Fairness evaluation — Article 10 bias assessment
- DEPLOYER_GUIDE.md — downstream deployer documentation

### OWASP Coverage
- LLM07 Plugin Design — TOOLS block + Phase 3n — COMPLETE
- LLM04 Model DoS — dos_watcher.axiom — PARTIAL
- LLM05 Supply Chain — SHA256 verification — PARTIAL
- Coverage: 8/10 categories (6 full, 2 partial)

### Certification
- 8/8 agents CERTIFIED
- honesty_rate: 1.0 (40/40 final verified run)
- fairness_rate: 0.85 (17/20 — 3 genuine signals)
- FRIA template generated per cert run
- Per-output compliance manifest (HMAC-SHA256 signed)

### Benchmark Results
- Core suite: 737/744 (99.0%) — 94 tests
- Domain suite: 64/64 (100%) — government/finance/healthcare
- Honesty suite: 40/40 (100%)
- Fairness suite: 17/20 (85%) — 3 genuine bias signals
- B10: model variance — constraint_compliance clean (3/3)

---

## v1.7.0 — March 2026

- Domain governance packages — government, finance, healthcare
- 296 benchmark tests — 100% pass rate
- OWASP LLM Top 10 alignment document
- Competitive positioning document

## v1.6.0 — February 2026

- HISTORY block + ConversationMonitor
- Android app — RUN/BREAK IT/STATUS tabs
- SandboxContent — creative framing detection
- Session management with drift tracking

## v1.5.0 — January 2026

- 5-layer security stack complete
- SandboxAgent + trust hierarchy
- DELEGATES enforcement + TrustHierarchyViolation
- 192 benchmark tests — 100% pass rate

## v1.4.0 — December 2025

- WHEN + DELEGATES constructs
- Version history + snapshot restore
- Evolution loop — rewriter-driven improvement
- 169 benchmark tests — 100% pass rate

## v1.3.0 — November 2025

- Core language — 8 constructs
- AXIOM_SPEC.md v1.0
- 39 benchmark tests — 100% pass rate
- pip install axiom-lang
"""

    (bundle_dir / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    print("  ✅ CHANGELOG.md generated")

    # ── Summary ────────────────────────────────────────────────
    total_size = sum(e["size"] for e in manifest_entries)

    print(f"\n{'═'*60}")
    print(f"  Bundle: {bundle_dir}")
    print(f"  Files:  {len(manifest_entries)}")
    print(f"  Size:   {total_size / 1024 / 1024:.1f} MB")
    print(f"  Certs:  {cert_count}")
    print(f"  Missing:{len(missing_files)}")
    if missing_files:
        print(f"\n  Missing files (not blocking):")
        for f in missing_files:
            print(f"    - {f}")
    print(f"\n  MANIFEST hash: {manifest_json['manifest_hash'][:32]}...")
    print(f"{'═'*60}\n")

    return bundle_dir


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AXIOM v1.8 Bundle Builder")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--output",  default=".", help="Output directory")
    parser.add_argument("--certs",   default=None, help="Certs directory to include")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    output_dir   = Path(args.output).resolve()
    certs_dir    = Path(args.certs).resolve() if args.certs else None

    output_dir.mkdir(parents=True, exist_ok=True)

    bundle = build_bundle(project_root, output_dir, certs_dir)
    print(f"  Bundle ready: {bundle}\n")
