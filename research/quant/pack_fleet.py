"""Pack all specialists in a fleet manifest into signed .axm containers.

For each specialist:
  - Text models  → run_srd4_local.py (SRD-4 quant → .axm + .gguf)
  - Vision models → pack_to_axm.py  (FP16 weights → .axm; 4-bit via bitsandbytes at runtime)

After packing, fills the manifest's fingerprint fields and writes the
updated manifest back to disk.

Usage
-----
    # Dry-run: print what would be packed (no downloads or packs)
    python3 research/quant/pack_fleet.py \\
        --fleet examples/fleets/medical_fleet.json \\
        --output-dir /workspace/fleet_out \\
        --dry-run

    # Full pack (needs GPU for text models, CPU-OK for tiny vision models)
    python3 research/quant/pack_fleet.py \\
        --fleet examples/fleets/medical_fleet.json \\
        --output-dir /workspace/fleet_out \\
        --llamacpp  /workspace/llama.cpp

    # Pack only the vision specialist
    python3 research/quant/pack_fleet.py \\
        --fleet examples/fleets/medical_fleet.json \\
        --output-dir /workspace/fleet_out \\
        --role imaging

    # Push each model to HuggingFace after packing
    python3 research/quant/pack_fleet.py \\
        --fleet examples/fleets/medical_fleet.json \\
        --output-dir /workspace/fleet_out \\
        --hf-org orivael \\
        --push
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from axiom_fleet.fleet_manifest import (      # noqa: E402
    FleetManifest, SpecialistConfig,
    load_manifest, save_manifest, validate_manifest,
)

_QUANT_DIR = Path(__file__).parent
_RUN_SRD   = _QUANT_DIR / "run_srd4_local.py"
_PACK_AXM  = _QUANT_DIR / "pack_to_axm.py"
_AXM_CLI   = _QUANT_DIR / "axm_cli.py"
_PUSH_SRD  = _QUANT_DIR / "push_srd_to_hub.py"


# ── Fingerprint extraction ────────────────────────────────────────────────────


def _get_fingerprint(axm_path: Path) -> Optional[str]:
    if not axm_path.exists():
        return None
    try:
        out = subprocess.run(
            [sys.executable, str(_AXM_CLI), "verify", str(axm_path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(out.stdout)
        return data.get("fingerprint")
    except Exception:
        return None


# ── Pack text specialist ──────────────────────────────────────────────────────


def _pack_text(
    spec:       SpecialistConfig,
    out_dir:    Path,
    llamacpp:   Optional[str],
    dry_run:    bool,
) -> tuple[Optional[Path], Optional[Path]]:
    """Returns (axm_path, gguf_path) after packing. Both None on dry-run."""
    slug = spec.role
    print(f"\n[pack_fleet] TEXT specialist: {spec.role} ({spec.base_model})")
    print(f"  output dir : {out_dir}")
    print(f"  axm target : {out_dir}/{slug}.axm")
    if dry_run:
        print("  DRY RUN — skipping")
        return None, None

    cmd = [
        sys.executable, str(_RUN_SRD),
        "--model",      spec.base_model,
        "--output-dir", str(out_dir),
        "--quant",      "Q4_K_M",
    ]
    if llamacpp:
        cmd += ["--llamacpp", llamacpp]
    else:
        cmd += ["--skip-extract"]

    print(f"  running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # Locate outputs (run_srd4_local names them after the model slug)
    axm_candidates  = list(out_dir.glob("*.axm"))
    gguf_candidates = list(out_dir.glob("*.gguf"))
    axm  = axm_candidates[0]  if axm_candidates  else None
    gguf = gguf_candidates[0] if gguf_candidates else None

    # Rename to role slug for clarity
    if axm:
        dest = out_dir / f"{slug}.axm"
        axm.rename(dest)
        axm = dest
    if gguf:
        dest = out_dir / f"{slug}.gguf"
        gguf.rename(dest)
        gguf = dest

    return axm, gguf


# ── Pack vision specialist ────────────────────────────────────────────────────


def _pack_vision(
    spec:    SpecialistConfig,
    out_dir: Path,
    dry_run: bool,
) -> Optional[Path]:
    """Returns axm_path after packing. None on dry-run."""
    slug = spec.role
    axm_out = out_dir / f"{slug}.axm"
    print(f"\n[pack_fleet] VISION specialist: {spec.role} ({spec.base_model})")
    print(f"  output : {axm_out}")
    if dry_run:
        print("  DRY RUN — skipping")
        return None

    cmd = [
        sys.executable, str(_PACK_AXM),
        "--model",  spec.base_model,
        "--output", str(axm_out),
    ]
    print(f"  running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    return axm_out if axm_out.exists() else None


# ── HF push ───────────────────────────────────────────────────────────────────


def _push_to_hub(
    spec:      SpecialistConfig,
    axm_path:  Path,
    gguf_path: Optional[Path],
    out_dir:   Path,
    hf_org:    str,
    dry_run:   bool,
) -> None:
    slug     = spec.role.lower().replace("_", "-")
    model_slug = spec.base_model.split("/")[-1].lower()
    repo_id  = f"{hf_org}/{model_slug}-{slug}-axm"
    stats_p  = out_dir / "pack_stats.json"

    print(f"\n[pack_fleet] PUSH {spec.role} → {repo_id}")
    if dry_run:
        print("  DRY RUN — skipping push")
        return

    cmd = [
        sys.executable, str(_PUSH_SRD),
        "--axm",       str(axm_path),
        "--repo-id",   repo_id,
        "--base-model", spec.base_model,
        "--domain",    spec.domain,
    ]
    if gguf_path and gguf_path.exists():
        cmd += ["--gguf", str(gguf_path)]
    if stats_p.exists():
        cmd += ["--pack-stats", str(stats_p)]

    subprocess.run(cmd, check=True)


# ── Main ──────────────────────────────────────────────────────────────────────


def pack_fleet(
    fleet_path: str,
    output_dir: str,
    llamacpp:   Optional[str] = None,
    role:       Optional[str] = None,
    push:       bool          = False,
    hf_org:     str           = "orivael",
    dry_run:    bool          = False,
) -> None:
    manifest = load_manifest(fleet_path)
    out_dir  = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    errors = validate_manifest(manifest)
    # Filter out the gguf_path warning for vision specialists (expected)
    errors = [e for e in errors if "text specialist needs gguf_path" not in e]
    if errors:
        print("[pack_fleet] Manifest validation errors:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    print(f"[pack_fleet] Fleet: {manifest.fleet_id}  ({len(manifest.specialists)} specialists)")
    print(f"[pack_fleet] Est. total disk: {manifest.estimated_disk_gb():.2f} GB at current bpw")
    print(f"[pack_fleet] Total params: {manifest.total_params_m()} M")

    for spec in manifest.specialists:
        if role and spec.role != role:
            continue

        spec_out = out_dir / spec.role
        spec_out.mkdir(exist_ok=True)

        if spec.modality == "text":
            axm, gguf = _pack_text(spec, spec_out, llamacpp, dry_run)
        elif spec.modality in ("vision", "multimodal"):
            axm  = _pack_vision(spec, spec_out, dry_run)
            gguf = None
        else:
            print(f"[pack_fleet] Unknown modality {spec.modality!r} for {spec.role}, skipping")
            continue

        if not dry_run and axm:
            fp = _get_fingerprint(axm)
            spec.fingerprint = fp
            spec.axm_path    = str(axm)
            if gguf:
                spec.gguf_path = str(gguf)
            print(f"  fingerprint: {fp or '(verify failed)'}")

        if push and axm:
            _push_to_hub(spec, axm, gguf, spec_out, hf_org, dry_run)

    # Write updated manifest (with fingerprints filled in)
    if not dry_run:
        updated_path = Path(fleet_path).with_suffix(".packed.json")
        save_manifest(manifest, updated_path)
        print(f"\n[pack_fleet] Updated manifest saved → {updated_path}")

    print("\n[pack_fleet] Done.")
    for spec in manifest.specialists:
        if role and spec.role != role:
            continue
        status = "PACKED" if (spec.fingerprint or dry_run) else "SKIPPED"
        print(f"  [{status}] {spec.role:15s} {spec.base_model}  fp={spec.fingerprint or '-'}")


def _main() -> None:
    ap = argparse.ArgumentParser(
        description="Pack a fleet of ≤0.5B SRD-quantized specialists into signed .axm containers."
    )
    ap.add_argument("--fleet",      required=True, help="Path to fleet manifest JSON")
    ap.add_argument("--output-dir", required=True, help="Directory to write packed models into")
    ap.add_argument("--llamacpp",   default=None,  help="Path to llama.cpp directory (for text models)")
    ap.add_argument("--role",       default=None,  help="Pack only this specialist role")
    ap.add_argument("--push",       action="store_true", help="Push each model to HuggingFace after packing")
    ap.add_argument("--hf-org",     default="orivael", help="HuggingFace org to push to (default: orivael)")
    ap.add_argument("--dry-run",    action="store_true", help="Print what would happen without doing it")
    args = ap.parse_args()

    pack_fleet(
        fleet_path=args.fleet,
        output_dir=args.output_dir,
        llamacpp=args.llamacpp,
        role=args.role,
        push=args.push,
        hf_org=args.hf_org,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    _main()
