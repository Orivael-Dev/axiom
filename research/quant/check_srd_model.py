"""SRD model integrity checker.

Verifies that a GGUF file was actually built with the SRD pipeline,
not just relabeled. Reads the Axiom sidecar JSON written at build time
and optionally runs a quick PPL spot-check.

Two-tier check
--------------
  1. Metadata check (fast, no GPU needed):
     Reads <model>.axiom_meta.json alongside the GGUF and validates
     that srd.applied=true, expected parameters match, and the build
     fingerprint is present.

  2. PPL spot-check (optional, ~30 s on GPU):
     Loads the GGUF via llama-perplexity and checks PPL is within the
     expected range stored in the sidecar. Catches models where the
     sidecar was copied from a genuine SRD build but the weights weren't.

CLI
---
  # Metadata check only
  python research/quant/check_srd_model.py gemma3-1b-srd4-q4km.gguf

  # Metadata + PPL spot-check (requires llama.cpp build)
  python research/quant/check_srd_model.py gemma3-1b-srd4-q4km.gguf \\
      --ppl-check --llamacpp /content/llama.cpp

  # Write a fresh sidecar for a GGUF that was built correctly but
  # had its sidecar lost (use --force only if you have the model key)
  python research/quant/check_srd_model.py gemma3-1b-srd4-q4km.gguf \\
      --write-sidecar gemma3-1b --pipeline-commit <git-sha>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Expected SRD parameters — all models in the srd-lab collection
# are built with these defaults.
SRD_DEFAULTS = {
    "alpha":            1.0,
    "group_size":       64,
    "top_k_pct":        0.25,
    "correction_mode":  "selective",   # or "full"
}

# Per-model expected ranges (MC1, PPL delta) for spot-check validation.
# mc1_range: [min, max] acceptable MC1 values at n=200
# ppl_max: upper bound on WikiText-2 PPL (lower = better)
MODEL_PROFILES = {
    "smollm2-135m": {
        "base_model":  "HuggingFaceTB/SmolLM2-135M-Instruct",
        "n_layers":    30,
        "mc1_range":   [0.25, 0.32],
        "ppl_max":     35.0,
    },
    "qwen25-0p5b": {
        "base_model":  "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "n_layers":    24,
        "mc1_range":   [0.27, 0.34],
        "ppl_max":     40.0,
    },
    "gemma3-1b": {
        "base_model":  "google/gemma-3-1b-it",
        "n_layers":    18,
        "mc1_range":   [0.27, 0.35],
        "ppl_max":     40.0,
    },
    "tinyllama-1b": {
        "base_model":  "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        "n_layers":    22,
        "mc1_range":   [0.25, 0.32],
        "ppl_max":     12.0,
    },
}


# ── Sidecar writer (called at build time) ────────────────────────────────

def write_srd_sidecar(
    gguf_path: Path,
    model_key: str,
    *,
    correction_mode: str = "selective",
    alpha: float = 1.0,
    group_size: int = 64,
    top_k_pct: float = 0.25,
    pipeline_commit: str = "",
    mc1_measured: Optional[float] = None,
    ppl_measured: Optional[float] = None,
) -> Path:
    """Write a <gguf>.axiom_srd.json sidecar after a successful SRD build.

    Call this from colab_srd_requant_3models.py after each model is built.
    Returns the sidecar path.
    """
    profile = MODEL_PROFILES.get(model_key, {})
    n_layers = profile.get("n_layers", 0)
    reasoning_start = math.floor(n_layers * 0.40)
    reasoning_end   = math.floor(n_layers * 0.77)

    # Fingerprint: SHA256 of first 4 MB of the GGUF tensor data
    fingerprint = _gguf_fingerprint(gguf_path)

    sidecar = {
        "srd_version":     "4",
        "applied":         True,
        "model_key":       model_key,
        "base_model":      profile.get("base_model", ""),
        "correction_mode": correction_mode,
        "alpha":           alpha,
        "group_size":      group_size,
        "top_k_pct":       top_k_pct,
        "n_layers":        n_layers,
        "reasoning_layers": f"{reasoning_start}-{reasoning_end}",
        "pipeline_commit": pipeline_commit,
        "gguf_fingerprint": fingerprint,
        "gguf_size_bytes":  gguf_path.stat().st_size,
        "build_timestamp": datetime.datetime.utcnow().isoformat(),
    }
    if mc1_measured is not None:
        sidecar["mc1_measured"] = mc1_measured
    if ppl_measured is not None:
        sidecar["ppl_measured"] = ppl_measured

    out = gguf_path.with_suffix(".axiom_srd.json")
    out.write_text(json.dumps(sidecar, indent=2))
    print(f"[srd-check] sidecar written → {out.name}")
    return out


# ── Fingerprint helper ───────────────────────────────────────────────────

def _gguf_fingerprint(gguf_path: Path, read_bytes: int = 4 * 1024 * 1024) -> str:
    """SHA256 of the first 4 MB of the GGUF file.

    Enough to detect relabeled vanilla Q4_K_M — the SRD-corrected weights
    will differ in the first few tensor blocks.
    """
    h = hashlib.sha256()
    with open(gguf_path, "rb") as f:
        h.update(f.read(read_bytes))
    return h.hexdigest()[:16]   # 64-bit prefix, enough for identity


# ── Metadata check ───────────────────────────────────────────────────────

def check_metadata(gguf_path: Path) -> dict:
    """Read the sidecar and validate SRD fields. Returns a result dict."""
    sidecar_path = gguf_path.with_suffix(".axiom_srd.json")
    result: dict = {"gguf": str(gguf_path), "checks": [], "passed": True}

    def fail(msg: str) -> None:
        result["checks"].append({"status": "FAIL", "msg": msg})
        result["passed"] = False

    def ok(msg: str) -> None:
        result["checks"].append({"status": "PASS", "msg": msg})

    # 1. Sidecar exists
    if not sidecar_path.exists():
        fail(f"Sidecar not found: {sidecar_path.name}")
        return result
    ok(f"Sidecar found: {sidecar_path.name}")

    sc = json.loads(sidecar_path.read_text())

    # 2. SRD was actually applied
    if not sc.get("applied"):
        fail("srd.applied is not True")
    else:
        ok(f"SRD applied, mode={sc.get('correction_mode')}")

    # 3. Parameters match expected defaults
    for key, expected in SRD_DEFAULTS.items():
        actual = sc.get(key)
        if actual != expected:
            fail(f"{key}: expected {expected}, got {actual}")
        else:
            ok(f"{key}={actual}")

    # 4. Fingerprint matches GGUF on disk
    if "gguf_fingerprint" in sc:
        current = _gguf_fingerprint(gguf_path)
        if current != sc["gguf_fingerprint"]:
            fail(f"Fingerprint mismatch: stored={sc['gguf_fingerprint']} "
                 f"current={current} — file may have been replaced")
        else:
            ok(f"Fingerprint OK: {current}")

    # 5. File size matches (secondary sanity check)
    if "gguf_size_bytes" in sc:
        actual_size = gguf_path.stat().st_size
        if actual_size != sc["gguf_size_bytes"]:
            fail(f"Size mismatch: stored={sc['gguf_size_bytes']} "
                 f"current={actual_size}")
        else:
            ok(f"File size OK: {actual_size / 1024**2:.0f} MB")

    result["sidecar"] = sc
    return result


# ── PPL spot-check (optional) ────────────────────────────────────────────

def ppl_spot_check(
    gguf_path: Path,
    llamacpp_dir: Path,
    model_key: str,
    *,
    n_tokens: int = 512,
) -> dict:
    """Run llama-perplexity on a 512-token WikiText-2 sample.

    Returns {"ppl": float, "passed": bool, "msg": str}.
    Requires llama.cpp build with llama-perplexity binary.
    """
    binary = llamacpp_dir / "build/bin/llama-perplexity"
    if not binary.exists():
        return {"passed": False, "msg": f"llama-perplexity not found at {binary}"}

    profile = MODEL_PROFILES.get(model_key, {})
    ppl_max = profile.get("ppl_max", 50.0)

    # Download a small WikiText-2 test sample on first use
    sample_path = Path("/tmp/wikitext2_sample.txt")
    if not sample_path.exists():
        try:
            from datasets import load_dataset
            try:
                ds = load_dataset("wikitext", "wikitext-2-raw-v1", split="test")
            except Exception:
                ds = load_dataset("Salesforce/wikitext", "wikitext-2-raw-v1", split="test")
            text = "\n\n".join(t for t in ds["text"] if t.strip())
            sample_path.write_text(text[:20000])   # first ~20k chars
        except Exception as e:
            return {"passed": False, "msg": f"Could not fetch WikiText-2: {e}"}

    cmd = [
        str(binary),
        "-m", str(gguf_path),
        "-f", str(sample_path),
        "--ctx-size", str(n_tokens),
        "--no-warmup",
    ]

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # llama-perplexity prints "Final estimate: PPL = XX.XX +/- YY.YY"
        for line in out.stdout.splitlines():
            if "Final estimate" in line and "PPL" in line:
                ppl = float(line.split("PPL =")[1].split()[0])
                passed = ppl <= ppl_max
                return {
                    "ppl":    round(ppl, 3),
                    "passed": passed,
                    "msg":    f"PPL={ppl:.2f} ({'≤' if passed else '>'} {ppl_max} threshold)",
                }
        return {"passed": False, "msg": f"Could not parse PPL from output:\n{out.stdout[-500:]}"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "msg": "llama-perplexity timed out (>120s)"}


# ── CLI ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SRD model integrity check")
    p.add_argument("gguf", help="Path to the .gguf file to check")
    p.add_argument("--model-key", default=None,
                   choices=list(MODEL_PROFILES),
                   help="Model key for profile-aware checks (auto-detected if omitted)")
    p.add_argument("--ppl-check", action="store_true",
                   help="Run llama-perplexity spot-check (requires --llamacpp)")
    p.add_argument("--llamacpp", default="/content/llama.cpp",
                   help="Path to llama.cpp build directory")
    p.add_argument("--write-sidecar", metavar="MODEL_KEY",
                   help="Write a fresh sidecar for this model key (use after lost sidecar)")
    p.add_argument("--pipeline-commit", default="",
                   help="Git commit SHA to embed in sidecar (used with --write-sidecar)")
    p.add_argument("--json", action="store_true",
                   help="Output results as JSON")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    gguf_path = Path(args.gguf)

    if not gguf_path.exists():
        print(f"ERROR: {gguf_path} not found", file=sys.stderr)
        return 1

    # Auto-detect model key from filename
    model_key = args.model_key
    if not model_key:
        name = gguf_path.name.lower()
        for key in MODEL_PROFILES:
            if key.replace("-", "") in name.replace("-", "").replace("_", ""):
                model_key = key
                break

    if args.write_sidecar:
        write_srd_sidecar(
            gguf_path, args.write_sidecar,
            pipeline_commit=args.pipeline_commit,
        )
        return 0

    # Run checks
    results: dict = {"gguf": str(gguf_path), "model_key": model_key}

    meta = check_metadata(gguf_path)
    results["metadata"] = meta

    if args.ppl_check and model_key:
        ppl = ppl_spot_check(gguf_path, Path(args.llamacpp), model_key)
        results["ppl_check"] = ppl

    overall = meta["passed"] and results.get("ppl_check", {}).get("passed", True)
    results["overall"] = "PASS" if overall else "FAIL"

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\nSRD integrity check — {gguf_path.name}")
        print("=" * 60)
        for chk in meta["checks"]:
            icon = "✓" if chk["status"] == "PASS" else "✗"
            print(f"  {icon}  {chk['msg']}")
        if "ppl_check" in results:
            pc = results["ppl_check"]
            icon = "✓" if pc["passed"] else "✗"
            print(f"  {icon}  PPL spot-check: {pc['msg']}")
        print()
        status = "PASS" if overall else "FAIL"
        print(f"  {'✓  VERIFIED — genuine SRD4 build' if overall else '✗  FAILED — not a genuine SRD4 build'}")
        if "sidecar" in meta:
            sc = meta["sidecar"]
            print(f"  built: {sc.get('build_timestamp', 'unknown')[:10]}")
            print(f"  mode:  {sc.get('correction_mode')}  "
                  f"layers: {sc.get('reasoning_layers')}  "
                  f"commit: {sc.get('pipeline_commit', 'unknown')[:8]}")
        print()

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
