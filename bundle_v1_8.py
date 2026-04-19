"""
bundle_v1_8.py
AXIOM v1.8.0 Export Bundle — EU AI Act Article 11 (Technical Documentation)

Collects all source files, governance artifacts, and latest certification
outputs into axiom_v1.8.0_export/, writes MANIFEST.md with SHA256 hashes,
and creates axiom_v1.8.0_export.zip.

Usage:
    python bundle_v1_8.py [--output-dir <path>] [--no-zip]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).parent
VERSION   = "1.8.0"
BUNDLE_NAME = f"axiom_v{VERSION}_export"


# ── Files to include ──────────────────────────────────────────────────────────
# Paths relative to REPO_ROOT. Glob patterns supported.

INCLUDE = {
    # ── AXIOM language spec (core agents) ─────────────────────────
    "spec/agents": [
        "axiom_files/concepts.axiom",
        "axiom_files/worker.axiom",
        "axiom_files/evaluator.axiom",
        "axiom_files/rewriter.axiom",
        "axiom_files/sandbox.axiom",
        "axiom_files/teacher.axiom",
        "axiom_files/dos_watcher.axiom",
        "axiom_files/session.axiom",
        "axiom_files/conversation_monitor.axiom",
        "axiom_files/shared_memory.axiom",
    ],
    "spec/domains": [
        "axiom_files/domains/government.axiom",
        "axiom_files/domains/finance.axiom",
        "axiom_files/domains/healthcare.axiom",
    ],
    # ── Runtime modules ────────────────────────────────────────────
    "runtime/axiom": [
        "axiom/__init__.py",
        "axiom/client.py",
        "axiom/dos_watcher.py",
        "axiom/teacher.py",
        "axiom/integrity_check.py",
        "axiom/session.py",
        "axiom/evolution.py",
        "axiom/rubric.py",
        "axiom/shared_memory.py",
        "axiom/history_store.py",
        "axiom/experience_store.py",
        "axiom/store.py",
        "axiom/composition_graph.py",
        "axiom/conversation_monitor.py",
        "axiom/meta_evolution.py",
        "axiom/agent_factory.py",
    ],
    "runtime/agents": [
        "axiom/agents/__init__.py",
        "axiom/agents/base.py",
        "axiom/agents/worker.py",
        "axiom/agents/evaluator.py",
        "axiom/agents/rewriter.py",
        "axiom/agents/sandbox.py",
        "axiom/agents/sandbox_content.py",
    ],
    "runtime/axiom_files": [
        "axiom_files/parser.py",
        "axiom_files/validator.py",
        "axiom_files/__init__.py",
    ],
    # ── Entry points ───────────────────────────────────────────────
    "tools": [
        "axiom_certify.py",
        "axiom_review.py",
        "axiom_server.py",
        "cli.py",
        "run_axiom.py",
    ],
    # ── Documentation ──────────────────────────────────────────────
    "docs": [
        "AXIOM_SPEC.md",
        "DEPLOYER_GUIDE.md",
        "AXIOM_DATA_GOVERNANCE.md",
        "AXIOM_HONEST_BENCHMARK.md",
        "AXIOM_ALIGN_PACK.md",
        "README.md",
    ],
    # ── Package metadata ───────────────────────────────────────────
    "package": [
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
    ],
}

# Latest cert run to bundle — picks most recent by timestamp in filename
CERT_GLOB = "certs/*_cert_*.json"
FRIA_GLOB = "certs/*_fria_*.json"
CERT_PDF_GLOB = "certs/*_cert_*.pdf"


# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _latest_by_agent(glob_pattern: str) -> list[Path]:
    """
    Return the single most-recent file per agent from a glob.
    Filenames must end in _YYYYMMDD_HHMMSS.{ext}.
    """
    all_files = sorted(REPO_ROOT.glob(glob_pattern))
    by_agent: dict[str, Path] = {}
    for p in all_files:
        # Extract agent name: everything before the last two _-separated timestamp parts
        parts = p.stem.split("_")
        if len(parts) >= 3:
            # last two parts are date + time, rest is agent name
            agent_key = "_".join(parts[:-2])
        else:
            agent_key = p.stem
        by_agent[agent_key] = p  # later (sorted) overwrites earlier
    return sorted(by_agent.values())


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


# ── Bundle builder ────────────────────────────────────────────────────────────

def build_bundle(output_dir: Path) -> dict[str, str]:
    """
    Copy all bundle files into output_dir.
    Returns {bundle_relative_path: sha256} for MANIFEST.
    """
    manifest: dict[str, str] = {}
    missing: list[str] = []

    print(f"  Building bundle in: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Structured source files ────────────────────────────────────
    for bundle_subdir, rel_paths in INCLUDE.items():
        for rel in rel_paths:
            src = REPO_ROOT / rel
            if not src.exists():
                missing.append(rel)
                continue
            dest_rel = Path(bundle_subdir) / Path(rel).name
            dest = output_dir / dest_rel
            copy_file(src, dest)
            manifest[str(dest_rel)] = sha256_file(dest)
            print(f"    [+] {dest_rel}")

    # ── Latest certification artifacts ─────────────────────────────
    for glob, subdir in [
        (CERT_GLOB, "certs"),
        (FRIA_GLOB, "certs"),
        (CERT_PDF_GLOB, "certs"),
    ]:
        for src in _latest_by_agent(glob):
            dest_rel = Path(subdir) / src.name
            dest = output_dir / dest_rel
            copy_file(src, dest)
            manifest[str(dest_rel)] = sha256_file(dest)
            print(f"    [+] {dest_rel}")

    # ── Honesty ledger (append-only audit trail) ───────────────────
    for ledger_rel in [
        "axiom_files/.honesty/honesty_ledger.jsonl",
        "axiom_files/.honesty/fairness_ledger.jsonl",
    ]:
        src = REPO_ROOT / ledger_rel
        if src.exists():
            dest_rel = Path("audit") / src.name
            dest = output_dir / dest_rel
            copy_file(src, dest)
            manifest[str(dest_rel)] = sha256_file(dest)
            print(f"    [+] {dest_rel}")

    if missing:
        print(f"\n  [WARN] {len(missing)} file(s) not found (skipped):")
        for m in missing:
            print(f"    - {m}")

    return manifest


def write_manifest(manifest: dict[str, str], output_dir: Path) -> Path:
    """Write MANIFEST.md with SHA256 hashes — Art. 11 technical documentation."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"# AXIOM v{VERSION} Export Bundle — MANIFEST",
        "",
        f"**Generated:** {now}",
        f"**Version:** {VERSION}",
        f"**EU AI Act Article 11** — Technical Documentation",
        "",
        "All SHA256 hashes computed at bundle creation time.",
        r"Verify integrity: `sha256sum -c <(grep -E '^[a-f0-9]{64}' MANIFEST.md | awk '{print $1, $2}')`",
        "",
        "---",
        "",
        "## File Inventory",
        "",
        f"| Path | SHA256 |",
        f"|------|--------|",
    ]

    for rel_path in sorted(manifest.keys()):
        digest = manifest[rel_path]
        lines.append(f"| `{rel_path}` | `{digest[:16]}...` |")

    lines += [
        "",
        "## Full SHA256 Hashes",
        "",
        "```",
    ]
    for rel_path in sorted(manifest.keys()):
        lines.append(f"{manifest[rel_path]}  {rel_path}")
    lines += [
        "```",
        "",
        "---",
        "",
        "## Bundle Contents Summary",
        "",
        _build_summary(manifest),
        "",
        "---",
        "",
        "## Certification Status",
        "",
        _build_cert_summary(output_dir),
        "",
        "---",
        "",
        "*This manifest was generated automatically by `bundle_v1_8.py`.*",
        "*SHA256 hashes provide tamper evidence for the Article 11 technical documentation package.*",
    ]

    manifest_path = output_dir / "MANIFEST.md"
    manifest_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  [MANIFEST] {manifest_path}")
    return manifest_path


def _build_summary(manifest: dict[str, str]) -> str:
    by_dir: dict[str, int] = {}
    for p in manifest:
        top = p.split("/")[0] if "/" in p else "root"
        by_dir[top] = by_dir.get(top, 0) + 1
    lines = []
    for d in sorted(by_dir):
        lines.append(f"- `{d}/` — {by_dir[d]} file(s)")
    lines.append(f"\n**Total: {len(manifest)} files**")
    return "\n".join(lines)


def _build_cert_summary(bundle_dir: Path) -> str:
    cert_dir = bundle_dir / "certs"
    if not cert_dir.exists():
        return "No certification artifacts in bundle."
    lines = []
    for f in sorted(cert_dir.glob("*_cert_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            agent   = data.get("agent", f.stem)
            level   = data.get("conformance_level", "?")
            honesty = data.get("steps", {})
            # Try to find honesty_rate from step 6
            h_rate = "?"
            for step in data.get("steps", []):
                if isinstance(step, dict) and step.get("step") == 6:
                    h_rate = step.get("honesty_rate", "?")
                    break
            lines.append(f"| `{agent}` | {level} | honesty_rate: {h_rate} |")
        except Exception:
            lines.append(f"| `{f.name}` | (parse error) | — |")
    if lines:
        return "| Agent | Conformance | Honesty |\n|-------|-------------|---------|" + "\n" + "\n".join(lines)
    return "No cert JSON files found."


def create_zip(bundle_dir: Path) -> Path:
    zip_path = bundle_dir.parent / f"{bundle_dir.name}.zip"
    print(f"\n  Creating {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(bundle_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(bundle_dir.parent))
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  [{zip_path.name}] {size_mb:.1f} MB")
    return zip_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Build AXIOM v1.8.0 export bundle")
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / BUNDLE_NAME),
        help=f"Bundle output directory (default: ./{BUNDLE_NAME})"
    )
    parser.add_argument(
        "--no-zip", action="store_true",
        help="Skip creating the .zip archive"
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    print("=" * 60)
    print(f"  AXIOM v{VERSION} Export Bundle")
    print(f"  EU AI Act Article 11 — Technical Documentation Package")
    print("=" * 60)
    print()

    # Clean previous build
    if output_dir.exists():
        print(f"  Removing previous bundle: {output_dir.name}/")
        shutil.rmtree(output_dir)

    manifest = build_bundle(output_dir)
    manifest_path = write_manifest(manifest, output_dir)

    # Add manifest itself to the hash record
    manifest_hash = sha256_file(manifest_path)
    print(f"  MANIFEST.md sha256: {manifest_hash}")

    if not args.no_zip:
        zip_path = create_zip(output_dir)

    print()
    print("=" * 60)
    print(f"  Bundle complete: {len(manifest)} files")
    print(f"  Output: {output_dir}")
    if not args.no_zip:
        print(f"  Zip:    {zip_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
