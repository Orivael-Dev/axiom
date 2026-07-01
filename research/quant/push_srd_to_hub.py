"""Push SRD-4 governance container to HuggingFace Hub.

Uploads .axm (signed governance container) + .gguf (inference artifact) +
verify.py (standalone tamper check) + README.md (model card).

Usage
-----
    # Full push (A100 / RunPod output → HuggingFace)
    python3 research/quant/push_srd_to_hub.py \\
        --axm        /workspace/out/mistral_srd4.axm \\
        --gguf       /workspace/out/mistral_srd4_q4km.gguf \\
        --pack-stats /workspace/out/results/pack_stats.json \\
        --repo-id    orivael/mistral-7b-srd4-axm \\
        --base-model mistralai/Mistral-7B-Instruct-v0.3 \\
        --domain     general

    # Domain-specific container (healthcare, no GGUF public upload)
    python3 research/quant/push_srd_to_hub.py \\
        --axm        /workspace/out/clinical_llm_srd4.axm \\
        --skip-gguf \\
        --pack-stats /workspace/out/results/pack_stats.json \\
        --repo-id    acme-health/clinical-llm-srd4-axm \\
        --base-model acme-health/clinical-llm-v2 \\
        --domain     healthcare --private

    # Dry run — render model card without any HF calls
    python3 research/quant/push_srd_to_hub.py \\
        --dry-run \\
        --pack-stats /workspace/out/results/pack_stats.json \\
        --repo-id    orivael/test \\
        --base-model mistralai/Mistral-7B-Instruct-v0.3 \\
        --domain     finance
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO     = Path(__file__).resolve().parent.parent.parent
_TEMPLATE = Path(__file__).parent / "MODEL_CARD_SRD_TEMPLATE.md"
_REPO_URL    = "https://github.com/orivael-dev/axiom.git"
_REPO_BRANCH = "claude/srd-prototype-benchmark-JRtv1"

DOMAINS = [
    "general", "healthcare", "legal", "finance",
    "defense", "education", "manufacturing", "code",
]

_DOMAIN_EXTRA_TAGS: dict[str, list[str]] = {
    "healthcare":    ["hipaa-compliant-ai", "medical-ai"],
    "legal":         ["legal-ai", "compliance-ai"],
    "finance":       ["finreg-ai", "model-risk"],
    "defense":       ["cmmc-ai", "supply-chain-integrity"],
    "education":     ["ferpa-ai", "curriculum-ai"],
    "manufacturing": ["iso9001-ai", "process-ai"],
    "code":          ["sbom-ai", "secure-software-supply-chain"],
    "general":       [],
}

_DOMAIN_NOTES: dict[str, str] = {
    "healthcare": (
        "This container supports HIPAA audit requirements. The fingerprint provides "
        "a tamper-evident commitment that can be logged alongside model inference records, "
        "establishing which exact model version generated each output."
    ),
    "legal": (
        "Chain of custody for AI-assisted legal work requires proving which model "
        "version generated a draft or summary. The fingerprint serves as a durable "
        "identifier of the exact weights used — suitable for court-admissible audit logs."
    ),
    "finance": (
        "SR 11-7 and model risk management frameworks require documentation of model "
        "lineage and change control. The .axm HMAC chain provides cryptographic proof "
        "of quantization parameters — bpw, scheme, group size — as part of the signed payload."
    ),
    "defense": (
        "CMMC and supply chain integrity requirements demand that AI model components "
        "be traceable to approved sources. The .axm fingerprint supports offline verification "
        "without network access — suitable for air-gapped deployments."
    ),
    "education": (
        "FERPA and curriculum integrity programs benefit from knowing exactly which "
        "model version generated student-facing content. The fingerprint can be embedded "
        "in audit logs alongside session records."
    ),
    "manufacturing": (
        "ISO 9001 process traceability extends to AI systems making quality decisions. "
        "The .axm container documents the exact quantization applied, making the model "
        "a versioned, traceable artifact in the quality management system."
    ),
    "code": (
        "Software supply chain security (SLSA, SBOM) now extends to AI model components. "
        "The .axm container is the model-layer equivalent of a signed software bill of "
        "materials — it proves what went into the model and that nothing changed after signing."
    ),
    "general": (
        "The .axm container provides model provenance for any deployment context. "
        "The fingerprint is a public commitment; the HMAC chain is the private audit trail "
        "that lets the publishing organization verify any deployed copy."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────

def _load_pack_stats(path: str) -> dict:
    """Load and normalize pack_stats.json or summary.json."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    # Detect summary.json format produced by run_srd4_local.py
    if "axm_gb" in data and "size" not in data:
        data["size"] = {"archive_mb": data["axm_gb"] * 1024}
    if "pack_min" in data and "timing" not in data:
        data["timing"] = {"total_s": data["pack_min"] * 60}
    if "bpw" in data and "bpw_theoretical" not in data:
        data["bpw_theoretical"] = data["bpw"]

    required = [
        ("fingerprint", None),
        ("proofs",      None),
        ("bpw_theoretical", None),
        ("size",        "archive_mb"),
        ("timing",      "total_s"),
    ]
    for top, sub in required:
        if top not in data:
            sys.exit(f"ERROR: pack_stats missing '{top}' — is this pack_stats.json or summary.json?")
        if sub and (not isinstance(data[top], dict) or sub not in data[top]):
            sys.exit(f"ERROR: pack_stats missing '{top}.{sub}'")

    return data


def _verify_axm(axm_path: str) -> dict:
    """Run axm_cli.py verify as subprocess. Returns parsed JSON or exits."""
    result = subprocess.run(
        [sys.executable, str(_REPO / "axm_cli.py"), "verify", str(Path(axm_path).resolve())],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    try:
        out = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.exit(
            f"ERROR: axm_cli.py verify returned unexpected output:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    if not out.get("verified"):
        sys.exit(
            f"ERROR: .axm verification FAILED — do not publish a tampered container.\n{out}"
        )
    print(
        f"  ✓ .axm verified  ({out['proofs_checked']} proofs)"
        f"  fingerprint={out['fingerprint']}"
    )
    return out


def _model_slug(base_model: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", base_model.lower().split("/")[-1])


def _generate_verify_py(slug: str, fingerprint: str) -> str:
    """Generate the per-repo verify.py content (injected slug + fingerprint)."""
    return f'''\
"""Standalone tamper-check for this .axm governance container.

Requirements: git, python3, AXIOM_MASTER_KEY environment variable.

Usage
-----
    export AXIOM_MASTER_KEY="<your-org-key>"
    python verify.py
    # → VERIFIED  fingerprint={fingerprint}  proofs=N
"""
import json, os, subprocess, sys
from pathlib import Path

AXIOM_REPO   = "https://github.com/orivael-dev/axiom.git"
AXIOM_BRANCH = "claude/srd-prototype-benchmark-JRtv1"
AXIOM_DIR    = Path("/tmp/axiom")
AXM_FILENAME = "{slug}.axm"
EXPECTED_FP  = "{fingerprint}"


def _clone_toolkit() -> None:
    if not AXIOM_DIR.is_dir():
        print("  Cloning axiom toolkit to /tmp/axiom (one time) ...")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", AXIOM_BRANCH,
             AXIOM_REPO, str(AXIOM_DIR)],
            check=True,
        )


def main() -> None:
    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Error: AXIOM_MASTER_KEY not set.")
        print("  This key is held by the organization that published this container.")
        print("  export AXIOM_MASTER_KEY=\\'<your-org-key>\\'")
        sys.exit(1)

    axm = Path(AXM_FILENAME)
    if not axm.is_file():
        print(f"Error: {{AXM_FILENAME}} not found in current directory.")
        print("  Download the governance container from this HuggingFace repo first.")
        sys.exit(1)

    _clone_toolkit()

    result = subprocess.run(
        [sys.executable, str(AXIOM_DIR / "axm_cli.py"), "verify", str(axm.resolve())],
        capture_output=True, text=True, cwd=str(AXIOM_DIR),
    )
    try:
        out = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("VERIFICATION FAILED (unexpected output from axm_cli.py)")
        print(result.stdout, result.stderr)
        sys.exit(1)

    if not out.get("verified"):
        print("VERIFICATION FAILED — container may have been tampered.")
        sys.exit(1)

    got_fp = out.get("fingerprint", "")
    if got_fp != EXPECTED_FP:
        print("WARNING: proofs OK but fingerprint mismatch.")
        print(f"  expected : {{EXPECTED_FP}}")
        print(f"  got      : {{got_fp}}")
        print("  This may indicate the container was re-packed with a different key.")
        sys.exit(1)

    print(f"VERIFIED  fingerprint={{got_fp}}  proofs={{out['proofs_checked']}}")


if __name__ == "__main__":
    main()
'''


def _render_card(stats: dict, repo_id: str, base_model: str, domain: str,
                 gguf_path: str | None, skip_gguf: bool) -> str:
    template = _TEMPLATE.read_text(encoding="utf-8")

    slug = _model_slug(base_model)
    axm_filename  = f"{slug}.axm"
    gguf_filename = Path(gguf_path).name if gguf_path else f"{slug}_srd4_q4km.gguf"

    gguf_size = "N/A"
    if gguf_path and not skip_gguf and Path(gguf_path).is_file():
        gguf_size = f"{Path(gguf_path).stat().st_size / 1024**3:.2f} GB"

    extra = _DOMAIN_EXTRA_TAGS.get(domain, [])
    domain_tags_block = "".join(f"  - {t}\n" for t in extra)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return (
        template
        .replace("{{REPO_ID}}",          repo_id)
        .replace("{{BASE_MODEL}}",        base_model)
        .replace("{{FINGERPRINT}}",       stats["fingerprint"])
        .replace("{{BPW}}",               str(stats["bpw_theoretical"]))
        .replace("{{PROOFS_COUNT}}",      str(stats["proofs"]))
        .replace("{{AXM_SIZE_GB}}",       f"{stats['size']['archive_mb'] / 1024:.2f}")
        .replace("{{GGUF_SIZE_GB}}",      gguf_size)
        .replace("{{PACK_TIME_MIN}}",     f"{stats['timing']['total_s'] / 60:.1f}")
        .replace("{{DOMAIN}}",            domain)
        .replace("{{DOMAIN_TAGS_BLOCK}}", domain_tags_block)
        .replace("{{TIMESTAMP}}",         ts)
        .replace("{{DOMAIN_NOTES}}",      _DOMAIN_NOTES.get(domain, ""))
        .replace("{{AXM_FILENAME}}",      axm_filename)
        .replace("{{GGUF_FILENAME}}",     gguf_filename)
    )


# ─────────────────────────────────────────────────────────────────────────────

def push_srd(
    axm_path:       str | None,
    gguf_path:      str | None,
    pack_stats_path: str,
    repo_id:        str,
    base_model:     str,
    domain:         str,
    private:        bool,
    token:          str | None,
    dry_run:        bool,
    skip_gguf:      bool,
) -> None:
    stats = _load_pack_stats(pack_stats_path)
    slug  = _model_slug(base_model)

    # Verify .axm integrity before publishing (skip on dry run without a real file)
    is_real_axm = axm_path and Path(axm_path).is_file() and Path(axm_path).stat().st_size > 100
    if is_real_axm:
        _verify_axm(axm_path)
    elif dry_run:
        print("  (dry-run: .axm verification skipped)")
    else:
        sys.exit(f"ERROR: --axm file not found or empty: {axm_path}")

    card = _render_card(stats, repo_id, base_model, domain, gguf_path, skip_gguf)

    if dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN — rendered model card (no HF calls made):")
        print("=" * 70)
        print(card)
        print("=" * 70)
        print(f"\n  repo_id    : {repo_id}")
        print(f"  base_model : {base_model}")
        print(f"  domain     : {domain}")
        print(f"  fingerprint: {stats['fingerprint']}")
        return

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("ERROR: HF_TOKEN not set. Pass --token or set HF_TOKEN env var.")

    from huggingface_hub import HfApi, create_repo  # type: ignore

    api = HfApi(token=token)
    create_repo(repo_id, token=token, private=private, exist_ok=True, repo_type="model")
    print(f"  Repo: https://huggingface.co/{repo_id}")

    # 1. .axm governance container
    axm_name = f"{slug}.axm"
    axm_size  = Path(axm_path).stat().st_size / 1024**3
    print(f"  Uploading {axm_name}  ({axm_size:.2f} GB) ...")
    api.upload_file(
        path_or_fileobj=axm_path,
        path_in_repo=axm_name,
        repo_id=repo_id,
        repo_type="model",
        commit_message="Add SRD-4 governance container (.axm)",
    )

    # 2. GGUF inference artifact (optional)
    if gguf_path and not skip_gguf and Path(gguf_path).is_file():
        gguf_name = Path(gguf_path).name
        gguf_size = Path(gguf_path).stat().st_size / 1024**3
        print(f"  Uploading {gguf_name}  ({gguf_size:.2f} GB) ...")
        api.upload_file(
            path_or_fileobj=gguf_path,
            path_in_repo=gguf_name,
            repo_id=repo_id,
            repo_type="model",
            commit_message="Add GGUF Q4_K_M inference artifact",
        )

    # 3. verify.py (generated with baked-in slug + fingerprint)
    print("  Uploading verify.py ...")
    verify_bytes = _generate_verify_py(slug, stats["fingerprint"]).encode("utf-8")
    api.upload_file(
        path_or_fileobj=verify_bytes,
        path_in_repo="verify.py",
        repo_id=repo_id,
        repo_type="model",
        commit_message="Add standalone tamper-check script",
    )

    # 4. README / model card
    print("  Uploading README.md ...")
    api.upload_file(
        path_or_fileobj=card.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="model",
        commit_message="Add SRD governance model card",
    )

    print(f"\n✓ Published: https://huggingface.co/{repo_id}")


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Publish an SRD-4 governance container to HuggingFace Hub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--axm",
                   help="Path to .axm governance container (required unless --dry-run)")
    p.add_argument("--gguf",
                   help="Path to GGUF inference artifact (optional)")
    p.add_argument("--skip-gguf",  action="store_true",
                   help="Skip GGUF upload even if --gguf is provided")
    p.add_argument("--pack-stats", required=True,
                   help="Path to pack_stats.json or summary.json from the pipeline")
    p.add_argument("--repo-id",    required=True,
                   help="HuggingFace repo ID (e.g. orivael/mistral-7b-srd4-axm)")
    p.add_argument("--base-model", required=True,
                   help="Original HF model ID (e.g. mistralai/Mistral-7B-Instruct-v0.3)")
    p.add_argument("--domain",     default="general", choices=DOMAINS,
                   help="Domain context for governance framing (default: general)")
    p.add_argument("--private",    action="store_true",
                   help="Create a private HuggingFace repo")
    p.add_argument("--token",
                   help="HuggingFace token (falls back to HF_TOKEN env var)")
    p.add_argument("--dry-run",    action="store_true",
                   help="Render and print the model card without making any HF API calls")
    args = p.parse_args()

    if not args.dry_run and not args.axm:
        p.error("--axm is required unless --dry-run is set")

    push_srd(
        axm_path=args.axm,
        gguf_path=args.gguf,
        pack_stats_path=args.pack_stats,
        repo_id=args.repo_id,
        base_model=args.base_model,
        domain=args.domain,
        private=args.private,
        token=args.token,
        dry_run=args.dry_run,
        skip_gguf=args.skip_gguf,
    )


if __name__ == "__main__":
    main()
