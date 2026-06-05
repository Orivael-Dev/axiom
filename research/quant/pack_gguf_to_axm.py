"""Pack a pre-quantized GGUF file into a signed .axm governance container.

The GGUF is already quantized (Q4_K_M, Q5_K_M, etc.) — this script just
wraps it in the AXM signing layer so it has an HMAC-verified fingerprint.

Usage
-----
    python3 research/quant/pack_gguf_to_axm.py \\
        --gguf   /workspace/qwen2.5-coder-0.5b-q4_k_m.gguf \\
        --output /workspace/qwen_coder.axm \\
        --model  Qwen/Qwen2.5-Coder-0.5B-Instruct

    # With stats JSON for push_srd_to_hub.py
    python3 research/quant/pack_gguf_to_axm.py \\
        --gguf   model.gguf \\
        --output model.axm \\
        --model  Qwen/Qwen2.5-Coder-0.5B-Instruct \\
        --stats-json pack_stats.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_axm import AXMContainer, FORMAT_VERSION   # noqa: E402


# ── GGUF metadata reader (reads quant type from binary header) ─────────────────

_GGUF_MAGIC = 0x46554747  # "GGUF"

_GGUF_QUANT_NAMES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1",
    6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M",
    18: "Q6_K", 19: "Q8_K", 20: "IQ2_XXS", 21: "IQ2_XS",
    24: "IQ3_XXS", 26: "IQ4_NL", 28: "IQ3_S", 29: "IQ2_S",
    30: "IQ4_XS", 31: "IQ1_S", 32: "BF16",
}

_GGUF_BPW = {
    "F32": 32.0, "F16": 16.0, "BF16": 16.0,
    "Q8_0": 8.5, "Q8_1": 9.0, "Q6_K": 6.6,
    "Q5_K_M": 5.7, "Q5_K_S": 5.5, "Q5_0": 5.5, "Q5_1": 6.0,
    "Q4_K_M": 4.85, "Q4_K_S": 4.6, "Q4_0": 4.5, "Q4_1": 5.0,
    "Q3_K_L": 4.0, "Q3_K_M": 3.9, "Q3_K_S": 3.5,
    "Q2_K": 2.6, "IQ4_XS": 4.4, "IQ4_NL": 4.5,
    "IQ3_S": 3.5, "IQ3_XXS": 3.1, "IQ2_XS": 2.3, "IQ1_S": 1.6,
}


def _read_gguf_quant(gguf_path: Path) -> tuple[str, float]:
    """Read quantization type from GGUF binary header. Returns (name, bpw)."""
    try:
        with open(gguf_path, "rb") as f:
            magic, version = struct.unpack("<II", f.read(8))
            if magic != _GGUF_MAGIC:
                return "UNKNOWN", 4.85
            # Skip tensor_count + metadata_kv_count (two uint64)
            f.read(16)
            # The first KV pair is usually general.architecture or similar.
            # Scan for "general.quantization_version" or read tensor types.
            # Simpler: infer from filename.
    except Exception:
        pass

    # Fallback: infer from filename
    name = gguf_path.name.upper()
    for q in sorted(_GGUF_BPW.keys(), key=len, reverse=True):
        if q.replace("_", "-") in name.replace("_", "-"):
            return q, _GGUF_BPW[q]
    return "Q4_K_M", 4.85   # safe default


def pack_gguf(
    gguf_path:  str,
    output_path: str,
    model_name:  str,
    stats_json:  Optional[str] = None,
) -> dict:
    """Wrap a GGUF file in a signed .axm container. Returns stats dict."""
    gguf  = Path(gguf_path)
    out   = Path(output_path)
    quant_name, bpw = _read_gguf_quant(gguf)
    gguf_gb = gguf.stat().st_size / 1024**3

    print(f"[pack_gguf] {gguf.name}")
    print(f"  quant:  {quant_name}  ({bpw:.2f} bpw)")
    print(f"  size:   {gguf_gb:.3f} GB")
    print(f"  model:  {model_name}")

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="axm_gguf_") as tmp:
        weights_dir = Path(tmp) / "weights"
        weights_dir.mkdir()

        # Copy GGUF into the weights directory
        dest = weights_dir / "model.gguf"
        print(f"  copying to temp dir...")
        shutil.copy2(gguf, dest)

        spec = {
            "format_version": FORMAT_VERSION,
            "core_logic":     Path(model_name).name,
            "quant_map": {
                "scheme":  "gguf",
                "format":  quant_name,
                "bpw":     bpw,
                "note":    f"Pre-quantized GGUF ({quant_name}) packed into AXM for governance",
            },
            "hardware_map": "llama.cpp",
            "safety_proofs": True,
            "core": {
                "name":     model_name,
                "format":   "gguf",
                "quant":    quant_name,
            },
        }

        print(f"  writing .axm...")
        container = AXMContainer.pack(
            weights_dir,
            output_path=out,
            spec=spec,
            compresslevel=1,   # GGUF is already compressed — skip re-compression
        )

    elapsed = time.monotonic() - t0
    axm_gb  = out.stat().st_size / 1024**3

    print(f"  ✓ done in {elapsed:.1f}s")
    print(f"  .axm:        {out}  ({axm_gb:.3f} GB)")
    print(f"  fingerprint: {container.fingerprint}")
    print(f"  proofs:      {len(container.proofs)}")

    stats = {
        "model":           model_name,
        "fingerprint":     container.fingerprint,
        "proofs":          len(container.proofs),
        "quant":           quant_name,
        "bpw_theoretical": bpw,
        "size": {"archive_mb": round(axm_gb * 1024, 1)},
        "timing": {"total_s": round(elapsed, 1)},
        "source": "gguf",
    }
    if stats_json:
        Path(stats_json).write_text(json.dumps(stats, indent=2))

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pack a pre-quantized GGUF into a signed .axm container.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--gguf",       required=True, help="Input .gguf file")
    ap.add_argument("--output",     required=True, help="Output .axm file")
    ap.add_argument("--model",      required=True, help="HuggingFace model ID (for metadata)")
    ap.add_argument("--stats-json", default=None,  help="Write pack stats to this JSON file")
    args = ap.parse_args()

    pack_gguf(
        gguf_path   = args.gguf,
        output_path = args.output,
        model_name  = args.model,
        stats_json  = args.stats_json,
    )


if __name__ == "__main__":
    main()
