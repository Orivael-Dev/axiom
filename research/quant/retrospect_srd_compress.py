"""
SRD compression for ConstitutionalRetrospect manifest JSONL files.

Compresses the three float embedding vectors per manifest entry
(preflight_vec, mid_chain_vec, final_synthesis_vec) using SRD W4
quantization (top_k_pct=0, ~4.5 bpw effective).  All other fields
are passed through unchanged.  Each compressed entry is re-signed
with the same HMAC key namespace as axiom_retrospect.py.

Storage impact for a 200-turn session (vectors of length 256):
  Uncompressed : 256 × 3 × 4 bytes × 200 entries ≈ 614 KB
  SRD W4 G=64 : 256 × 3 × 0.5625 bytes × 200 entries ≈ 86 KB  (~7×)

Use cases:
  - Edge/offline deployment: compress before copying to Pi 4 / service tablet
  - Long sessions: auto-triggered when manifest exceeds SIZE_THRESHOLD_BYTES
  - CLI: python -m research.quant.retrospect_srd_compress src.jsonl dst.jsonl

No ML training required — pure numeric quantization using the existing
srd_quantize / srd_dequantize kernel in axiom_quant.py.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac as hmac_lib
import json
import sys
from pathlib import Path
from typing import Any

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from axiom_quant import SRDPackedTensor, srd_dequantize, srd_quantize
from axiom_signing import derive_key

# Same namespace as axiom_retrospect.py so entries remain cross-verifiable
_SIGNING_KEY = derive_key(b"axiom-retrospect-v1")

# Vector fields compressed per entry
_VEC_FIELDS = ("preflight_vec", "mid_chain_vec", "final_synthesis_vec")

# Default group size for SRD quantization
DEFAULT_GROUP_SIZE = 64

# Compress manifest when it exceeds this size (bytes)
SIZE_THRESHOLD_BYTES = 512 * 1024   # 500 KB

# Minimum vector length to attempt compression (shorter = not worth the overhead)
MIN_VEC_LEN = 8


# ── HMAC signing (mirrors axiom_retrospect._sign exactly) ──────────────────

def _sign(data: dict) -> str:
    canon = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hmac_lib.new(_SIGNING_KEY, canon, hashlib.sha256).hexdigest()


# ── SRD pack / unpack helpers ───────────────────────────────────────────────

def _pack_vector(vec: list[float], group_size: int) -> dict | None:
    """
    SRD-quantize a 1D float list.  Returns a compact dict suitable for
    JSON serialization, or None if the vector is too short to compress.

    Storage format:
      w4_b64   : base64(W4 int8 bytes)   — nibble-packed half-size on disk
      s4_b64   : base64(S4 float32 bytes) — one scale per group
      group_size : int
      orig_len   : int  (before padding)
      srd        : true  (compression marker)
    """
    n = len(vec)
    if n < MIN_VEC_LEN:
        return None

    # Pad to nearest multiple of group_size
    pad = (-n) % group_size
    padded = vec + [0.0] * pad

    W = torch.tensor(padded, dtype=torch.float32).unsqueeze(0)  # (1, padded_n)
    pack = srd_quantize(W, group_size=group_size, top_k_pct=0.0)

    # Serialize W4 and S4 only (D8=0 for top_k_pct=0, no need to store S8)
    w4_bytes = pack.W4.numpy().tobytes()
    s4_bytes = pack.S4.numpy().tobytes()

    return {
        "w4_b64":     base64.b64encode(w4_bytes).decode("ascii"),
        "s4_b64":     base64.b64encode(s4_bytes).decode("ascii"),
        "group_size": group_size,
        "orig_len":   n,
        "srd":        True,
    }


def _unpack_vector(packed: dict) -> list[float]:
    """Reconstruct a float list from a packed dict produced by _pack_vector."""
    import numpy as np

    w4_bytes = base64.b64decode(packed["w4_b64"])
    s4_bytes = base64.b64decode(packed["s4_b64"])
    g        = packed["group_size"]
    orig_len = packed["orig_len"]

    w4_arr = np.frombuffer(w4_bytes, dtype=np.int8)
    s4_arr = np.frombuffer(s4_bytes, dtype=np.float32)

    padded_n = len(w4_arr)
    n_groups = padded_n // g

    W4 = torch.from_numpy(w4_arr.copy()).view(1, padded_n)
    S4 = torch.from_numpy(s4_arr.copy()).view(1, n_groups)
    S8 = torch.zeros(1, n_groups)
    D8 = torch.zeros(1, padded_n, dtype=torch.int8)

    pack = SRDPackedTensor(
        W4=W4, D8=D8, S4=S4, S8=S8,
        group_size=g, top_k_pct=0.0,
    )
    recon = srd_dequantize(pack, alpha=0.0).squeeze(0).tolist()
    return recon[:orig_len]


# ── Public API ──────────────────────────────────────────────────────────────

def compress_manifest(
    src: Path,
    dst: Path,
    group_size: int = DEFAULT_GROUP_SIZE,
) -> dict:
    """
    Read a ConstitutionalRetrospect JSONL from src, SRD-compress the three
    embedding vectors per entry, re-sign, and write to dst.

    Returns a stats dict:
      entries_processed : int
      entries_compressed : int   (had at least one compressible vector)
      bytes_before : int
      bytes_after : int
      compression_ratio : float
    """
    src, dst = Path(src), Path(dst)
    lines_in  = [l for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    bytes_before = src.stat().st_size

    out_lines: list[str] = []
    entries_compressed = 0

    for line in lines_in:
        entry: dict[str, Any] = json.loads(line)
        compressed_any = False

        for field in _VEC_FIELDS:
            vec = entry.get(field)
            if not isinstance(vec, list):
                continue
            packed = _pack_vector(vec, group_size)
            if packed is not None:
                entry[field] = packed
                compressed_any = True

        if compressed_any:
            entries_compressed += 1

        # Remove old signature so _sign gets a clean dict
        entry.pop("hmac_signature", None)
        entry["hmac_signature"] = _sign(entry)

        out_lines.append(json.dumps(entry, ensure_ascii=False))

    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    bytes_after = dst.stat().st_size

    return {
        "entries_processed":  len(lines_in),
        "entries_compressed": entries_compressed,
        "bytes_before":       bytes_before,
        "bytes_after":        bytes_after,
        "compression_ratio":  round(bytes_before / bytes_after, 2) if bytes_after else 0,
    }


def decompress_manifest(src: Path, dst: Path) -> None:
    """
    Reconstruct float vectors from an SRD-compressed manifest.
    Verifies HMAC on each entry before decompression; raises ValueError
    on signature mismatch.
    """
    src, dst = Path(src), Path(dst)
    lines_in = [l for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]

    out_lines: list[str] = []
    for line in lines_in:
        entry: dict[str, Any] = json.loads(line)

        stored_sig = entry.pop("hmac_signature", "")
        expected   = _sign(entry)
        if stored_sig and stored_sig != expected:
            raise ValueError(
                f"HMAC mismatch on entry — manifest may be tampered. "
                f"stored={stored_sig[:16]}... expected={expected[:16]}..."
            )

        for field in _VEC_FIELDS:
            val = entry.get(field)
            if isinstance(val, dict) and val.get("srd"):
                entry[field] = _unpack_vector(val)

        entry["hmac_signature"] = _sign(entry)
        out_lines.append(json.dumps(entry, ensure_ascii=False))

    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def should_compress(manifest_path: Path) -> bool:
    """Return True if manifest exceeds SIZE_THRESHOLD_BYTES."""
    p = Path(manifest_path)
    return p.exists() and p.stat().st_size > SIZE_THRESHOLD_BYTES


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="SRD-compress or decompress a ConstitutionalRetrospect manifest."
    )
    ap.add_argument("src", type=Path, help="Source JSONL manifest")
    ap.add_argument("dst", type=Path, help="Destination JSONL manifest")
    ap.add_argument("--decompress", action="store_true",
                    help="Decompress SRD-packed manifest back to float vectors")
    ap.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE,
                    help=f"SRD group size (default {DEFAULT_GROUP_SIZE})")
    args = ap.parse_args()

    if args.decompress:
        decompress_manifest(args.src, args.dst)
        print(f"Decompressed {args.src} → {args.dst}")
    else:
        stats = compress_manifest(args.src, args.dst, group_size=args.group_size)
        print(f"Compressed {args.src} → {args.dst}")
        print(f"  entries processed  : {stats['entries_processed']}")
        print(f"  entries compressed : {stats['entries_compressed']}")
        print(f"  bytes before       : {stats['bytes_before']:,}")
        print(f"  bytes after        : {stats['bytes_after']:,}")
        print(f"  ratio              : {stats['compression_ratio']}×")


if __name__ == "__main__":
    _cli()
