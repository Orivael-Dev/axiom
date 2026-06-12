"""Convert a real-packed .axm archive to SRD4 format — no FP16 round-trip.

Normal axm_to_gguf.py pipeline:
    .axm → srd_dequantize() → FP16 → convert_hf_to_gguf → Q4_K_M
    (slow; D8 residuals discarded; alpha baked in at extract time)

This tool instead produces two files:

    <output>.srd4        SRD4 sidecar — signed binary with W4+D8+S4+S8
                         blocks for every quantized layer.  Directly
                         from srd_packed.pt, no dequantization.

    <output>.gguf        Annotated GGUF — architecture KV metadata plus
                         dense params (embedding, lm_head, norms) in F16.
                         Quantized layer tensors are stored as F16 zeros
                         (placeholder — ignored by SRD4-aware forks).
                         Standard llama.cpp can load this for architecture
                         info; the SRD4 fork replaces weight tensors with
                         sidecar data at runtime.

The .srd4 binary format:

    8  bytes   magic:  "AXMSRD4\\0"
    4  bytes   uint32 LE: JSON header length
    N  bytes   UTF-8 JSON header (fingerprint, alpha, chunk_map, tensors[])
    -------    per-tensor binary blocks (in header["tensors"] order):
               For each group of group_size weights:
                 32 bytes  w4_packed   nibble-packed int4  [0,15]
                  4 bytes  s4          float32 base scale
                 64 bytes  d8          int8 residuals
                  4 bytes  s8          float32 residual scale
               Total: 104 bytes per block × n_blocks per tensor

The block layout is intentionally identical to the C struct:

    typedef struct {
        uint8_t  w4[32];   // 64 int4 values, nibble-packed
        float    s4;       // base scale (one per group_size=64 weights)
        int8_t   d8[64];   // 8-bit residuals
        float    s8;       // residual scale
    } block_srd4_t;        // 104 bytes, 13.0 bpw at group_size=64

CLI
---
    # Sidecar only (already have a GGUF):
    python3 research/quant/axm_to_srd4_gguf.py \\
        --container artifacts/model_srd4.axm \\
        --srd4-out  artifacts/model.srd4

    # Sidecar + companion GGUF (full pipeline, slower):
    python3 research/quant/axm_to_srd4_gguf.py \\
        --container artifacts/model_srd4.axm \\
        --srd4-out  artifacts/model.srd4 \\
        --gguf-out  artifacts/model_srd4_companion.gguf \\
        --llamacpp  ~/llama.cpp

    # Apply sidecar to an existing GGUF:
    python3 research/quant/axm_to_srd4_gguf.py \\
        --container artifacts/model_srd4.axm \\
        --srd4-out  artifacts/model.srd4 \\
        --annotate-existing artifacts/model_q4km.gguf
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axiom_axm import AXMContainer, AXMError   # noqa: E402

# Reuse slot/hydration definitions from add_axiom_gguf_meta.py
from research.quant.add_axiom_gguf_meta import (  # noqa: E402
    SLOT_RANGES, HYDRATION_POLICY, AXIOM_VERSION,
    write_annotated_gguf,
)
from research.quant.axm_to_gguf import (          # noqa: E402
    _find_convert_script as _conv_script,
)
from research.quant.srd_realpack import (          # noqa: E402
    is_real_packed, PACKED_FILE, DENSE_FILE, INDEX_FILE,
)

# ── SRD4 binary format constants ──────────────────────────────────────────────

SRD4_MAGIC      = b"AXMSRD4\x00"    # 8-byte file magic
BLOCK_GROUP     = 64                 # weights per SRD4 block
BLOCK_W4_BYTES  = 32                 # nibble-packed int4
BLOCK_S4_BYTES  = 4                  # float32 base scale
BLOCK_D8_BYTES  = 64                 # int8 residuals
BLOCK_S8_BYTES  = 4                  # float32 residual scale
BLOCK_TOTAL     = BLOCK_W4_BYTES + BLOCK_S4_BYTES + BLOCK_D8_BYTES + BLOCK_S8_BYTES  # 104


# ── SRD4 block packing (vectorised numpy, no Python loops) ────────────────────

def _pack_srd4_layer(entry: dict) -> bytes:
    """Pack one layer's SRD data into consecutive 104-byte blocks.

    entry: dict loaded from srd_packed.pt (output of save_real_packed):
        w4_packed      (out, in//2) uint8 — nibble-packed W4
        orig_cols      int           — original in_features
        d8_mask        (ceil(N/8),) uint8 — sparsity bitmask
        d8_vals        (nnz,) int8  — non-zero residual values
        d8_shape       tuple         — (out, in)
        s4             (out, n_groups) float32
        s8             (out, n_groups) float32
        group_size     int

    Returns bytes: n_blocks × BLOCK_TOTAL (104 bytes each), where
        n_blocks = out_features × (in_features // group_size)
    """
    import torch
    from axiom_quant import srd_unpack_w4, srd_unpack_d8_sparse

    w4 = srd_unpack_w4(entry["w4_packed"], entry["orig_cols"]).numpy()   # (out, in) int8
    d8 = srd_unpack_d8_sparse(
        entry["d8_mask"], entry["d8_vals"], tuple(entry["d8_shape"])
    ).numpy()                                                              # (out, in) int8
    s4 = entry["s4"].numpy().astype(np.float32)                           # (out, n_groups)
    s8 = entry["s8"].numpy().astype(np.float32)

    out_f, in_f = w4.shape
    gs      = int(entry["group_size"])
    n_grps  = in_f // gs
    n_blks  = out_f * n_grps

    # Reshape into (n_blocks, group_size)
    w4_blk  = w4.reshape(n_blks, gs)    # (n_blks, 64) int8
    d8_blk  = d8.reshape(n_blks, gs)    # (n_blks, 64) int8
    s4_flat = s4.reshape(n_blks)        # (n_blks,) float32
    s8_flat = s8.reshape(n_blks)

    # Nibble-pack W4: int8 [-8,7] → uint8 [0,15] → two per byte
    w4u = (w4_blk.astype(np.int32) + 8).astype(np.uint8)   # [0,15]
    lo  = w4u[:, 0::2] & 0x0F                               # (n_blks, 32)
    hi  = w4u[:, 1::2] & 0x0F
    w4_nibbles = (lo | (hi << 4)).astype(np.uint8)          # (n_blks, 32)

    # Build output: n_blks × 104 bytes
    out_arr = np.zeros((n_blks, BLOCK_TOTAL), dtype=np.uint8)
    out_arr[:, :32]       = w4_nibbles
    out_arr[:, 36:100]    = d8_blk.view(np.uint8)            # same bytes, different sign
    out_arr[:, 32:36]     = s4_flat.view(np.uint8).reshape(n_blks, 4)
    out_arr[:, 100:104]   = s8_flat.view(np.uint8).reshape(n_blks, 4)

    return out_arr.tobytes()


# ── Build chunk_map scaled to any layer count ──────────────────────────────────

def _build_chunk_map(num_layers: int) -> dict[str, str]:
    """Return {str(layer_idx): slot_name} for the given layer count."""
    from research.quant.met_ram_estimator import CHUNK_FRACS
    cm: dict[str, str] = {}
    starts = {}
    consumed = 0
    for slot, frac in CHUNK_FRACS.items():
        n   = max(1, round(frac * num_layers))
        lo  = consumed
        hi  = min(consumed + n - 1, num_layers - 1)
        starts[slot] = (lo, hi)
        for idx in range(lo, hi + 1):
            cm[str(idx)] = slot
        consumed = hi + 1
    # Assign any remaining layers to governance
    for idx in range(consumed, num_layers):
        cm[str(idx)] = "governance"
    return cm


# ── Write the .srd4 sidecar binary ────────────────────────────────────────────

def write_srd4_sidecar(
    out_path: Path,
    blob: dict,
    index: dict,
    fingerprint: str,
    num_layers: int,
) -> dict:
    """Pack all quantized layers and write the .srd4 binary sidecar.

    Returns a summary dict with per-tensor sizes and total bytes.
    """
    chunk_map = _build_chunk_map(num_layers)

    tensors_meta = []
    tensor_blocks_list: list[bytes] = []
    offset = 0

    print(f"  [srd4] packing {len(blob)} quantized layers ...", flush=True)
    for name, entry in blob.items():
        gs      = int(entry["group_size"])
        orig_in = int(entry["orig_cols"])
        out_f   = int(entry["s4"].shape[0])
        n_grps  = orig_in // gs
        n_blks  = out_f * n_grps
        n_bytes = n_blks * BLOCK_TOTAL

        t0     = time.monotonic()
        blocks = _pack_srd4_layer(entry)
        elapsed = time.monotonic() - t0

        assert len(blocks) == n_bytes, \
            f"{name}: expected {n_bytes} bytes, got {len(blocks)}"

        tensors_meta.append({
            "hf_name":    name,
            "shape":      [out_f, orig_in],
            "group_size": gs,
            "n_blocks":   n_blks,
            "offset":     offset,
            "n_bytes":    n_bytes,
        })
        tensor_blocks_list.append(blocks)
        offset += n_bytes
        print(f"    {name:<55} {n_blks:>8,} blocks  {n_bytes/1024**2:>6.1f} MB  ({elapsed:.2f}s)")

    header = {
        "version":          "1.0",
        "axiom_version":    AXIOM_VERSION,
        "fingerprint":      fingerprint,
        "alpha":            float(index.get("alpha", 1.0)),
        "group_size":       int(index.get("group_size", BLOCK_GROUP)),
        "top_k_pct":        float(index.get("top_k_pct", 1.0)),
        "num_layers":       num_layers,
        "chunk_map":        chunk_map,
        "hydration_policy": HYDRATION_POLICY,
        "tensors":          tensors_meta,
        "block_format": {
            "block_bytes":     BLOCK_TOTAL,
            "w4_offset":       0,
            "w4_bytes":        BLOCK_W4_BYTES,
            "s4_offset":       BLOCK_W4_BYTES,
            "s4_bytes":        BLOCK_S4_BYTES,
            "d8_offset":       BLOCK_W4_BYTES + BLOCK_S4_BYTES,
            "d8_bytes":        BLOCK_D8_BYTES,
            "s8_offset":       BLOCK_W4_BYTES + BLOCK_S4_BYTES + BLOCK_D8_BYTES,
            "s8_bytes":        BLOCK_S8_BYTES,
            "weights_per_block": BLOCK_GROUP,
            "bpw_dense":       round(BLOCK_TOTAL * 8 / BLOCK_GROUP, 2),
        },
    }

    header_bytes = json.dumps(header, indent=2).encode("utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(SRD4_MAGIC)
        f.write(struct.pack("<I", len(header_bytes)))
        f.write(header_bytes)
        for blocks in tensor_blocks_list:
            f.write(blocks)

    total_bytes = out_path.stat().st_size
    return {
        "srd4_path":       str(out_path),
        "srd4_mb":         round(total_bytes / 1024**2, 1),
        "n_tensors":       len(blob),
        "fingerprint":     fingerprint,
        "bpw_dense":       round(BLOCK_TOTAL * 8 / BLOCK_GROUP, 2),
    }


# ── Annotate an existing GGUF with axiom.srd4.* keys ─────────────────────────

def annotate_gguf_with_srd4(gguf_path: Path, srd4_path: Path,
                              fingerprint: str, index: dict) -> Path:
    """Copy gguf_path to a new .axiom.gguf, adding axiom.srd4.* KV keys."""
    try:
        from gguf import GGUFReader, GGUFWriter, GGUFValueType
    except ImportError:
        print("  [annotate] pip install gguf  — skipping GGUF annotation")
        return gguf_path

    out = gguf_path.with_name(gguf_path.stem + ".axiom.gguf")
    reader = GGUFReader(str(gguf_path))
    arch   = "llama"
    for name, field in reader.fields.items():
        if name == "general.architecture":
            try:
                arch = bytes(field.parts[field.data[0]].tolist()).decode()
            except Exception:
                pass
            break

    writer = GGUFWriter(str(out), arch=arch)

    # Copy existing KV (skip axiom.srd4.* — will overwrite below)
    for name, field in reader.fields.items():
        if name.startswith("GGUF.") or name.startswith("axiom.srd4."):
            continue
        try:
            vtype = field.types[0]
            val   = field.parts[field.data[0]]
            if vtype == GGUFValueType.STRING:
                writer.add_string(name, bytes(val.tolist()).decode("utf-8", errors="replace"))
            elif vtype == GGUFValueType.UINT32:
                writer.add_uint32(name, int(val[0]))
            elif vtype == GGUFValueType.UINT64:
                writer.add_uint64(name, int(val[0]))
            elif vtype == GGUFValueType.INT32:
                writer.add_int32(name, int(val[0]))
            elif vtype == GGUFValueType.FLOAT32:
                writer.add_float32(name, float(val[0]))
            elif vtype == GGUFValueType.BOOL:
                writer.add_bool(name, bool(val[0]))
        except Exception:
            pass

    # Add axiom.srd4.* KV metadata
    writer.add_string("axiom.srd4.version",     "1.0")
    writer.add_string("axiom.srd4.sidecar",     srd4_path.name)
    writer.add_string("axiom.srd4.fingerprint", fingerprint)
    writer.add_float32("axiom.srd4.alpha",      float(index.get("alpha", 1.0)))
    writer.add_uint32("axiom.srd4.group_size",  int(index.get("group_size", BLOCK_GROUP)))
    writer.add_float32("axiom.srd4.top_k_pct",  float(index.get("top_k_pct", 1.0)))
    writer.add_uint32("axiom.srd4.block_bytes",  BLOCK_TOTAL)
    writer.add_float32("axiom.srd4.bpw_dense",  round(BLOCK_TOTAL * 8 / BLOCK_GROUP, 2))

    # Copy tensors raw
    for t in reader.tensors:
        raw = np.frombuffer(bytes(t.data), dtype=np.uint8)
        writer.add_tensor(t.name, raw, raw_shape=list(t.shape),
                          raw_dtype=t.tensor_type)

    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    print(f"  [annotate] → {out}  ({out.stat().st_size/1024**2:.0f} MB)")
    return out


# ── Optional companion GGUF from .axm (avoids quantize step) ─────────────────

def _build_companion_gguf(
    weights_path: Path,
    blob: dict,
    gguf_out: Path,
    llamacpp_dir: Path,
) -> None:
    """Create a companion F16 GGUF with dense params + zero-placeholder quantized layers.

    The GGUF holds architecture KV metadata and dense params in F16.
    Quantized layer weights are stored as F16 zeros — a custom SRD4 fork
    replaces them from the sidecar at runtime; standard llama.cpp skips them.
    """
    try:
        import torch
        from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        raise RuntimeError("pip install transformers torch  — required for companion GGUF")

    convert_script = next(
        (p for p in [
            llamacpp_dir / "convert_hf_to_gguf.py",
            llamacpp_dir / "convert-hf-to-gguf.py",
            llamacpp_dir / "convert.py",
        ] if p.is_file()),
        None
    )
    if convert_script is None:
        raise FileNotFoundError(
            f"convert_hf_to_gguf.py not found under {llamacpp_dir}"
        )

    cfg = AutoConfig.from_pretrained(weights_path)
    dense_state = torch.load(weights_path / DENSE_FILE,
                              map_location="cpu", weights_only=True)

    with tempfile.TemporaryDirectory(prefix="srd4_gguf_") as tmpdir:
        hf_tmp = Path(tmpdir) / "hf"
        hf_tmp.mkdir()

        cfg.save_pretrained(hf_tmp)
        # Copy tokenizer files
        for f in weights_path.iterdir():
            if f.suffix in {".json", ".model", ".txt", ".tiktoken"} \
               and f.name not in {INDEX_FILE, "config.json"}:
                shutil.copy2(f, hf_tmp / f.name)

        # Build model skeleton with dense params loaded; quantized layers zero
        print("  [gguf] initialising model skeleton with dense params ...")
        model = AutoModelForCausalLM.from_config(cfg)
        model.load_state_dict(dense_state, strict=False)
        model.half()

        print("  [gguf] saving companion HF checkpoint ...")
        model.save_pretrained(str(hf_tmp))
        del model

        # Tokenizer
        try:
            tok = AutoTokenizer.from_pretrained(weights_path)
            tok.save_pretrained(str(hf_tmp))
        except Exception:
            pass

        # Convert to F16 GGUF (no quantization)
        f16_gguf = Path(tmpdir) / "companion_f16.gguf"
        print("  [gguf] running convert_hf_to_gguf.py → F16 GGUF ...")
        subprocess.run(
            [sys.executable, str(convert_script), str(hf_tmp),
             "--outfile", str(f16_gguf), "--outtype", "f16"],
            check=True, capture_output=True,
        )

        shutil.move(str(f16_gguf), str(gguf_out))
        print(f"  [gguf] companion GGUF → {gguf_out}  "
              f"({gguf_out.stat().st_size/1024**2:.0f} MB)")


# ── Main conversion entry point ───────────────────────────────────────────────

def convert_axm_to_srd4(
    container_path: str,
    srd4_out: str,
    *,
    gguf_out: Optional[str] = None,
    llamacpp_dir: Optional[str] = None,
    annotate_existing: Optional[str] = None,
) -> dict:
    """Full pipeline: verify .axm → write .srd4 sidecar → optionally write GGUF.

    Args:
        container_path:    path to the real-packed .axm archive.
        srd4_out:          output path for the .srd4 binary sidecar.
        gguf_out:          if set, also write a companion F16 GGUF.
        llamacpp_dir:      required when gguf_out is set.
        annotate_existing: if set, annotate this existing GGUF with srd4 KV keys.
    """
    t_start = time.monotonic()
    import torch

    container_path = str(container_path)
    srd4_path      = Path(srd4_out)

    # ── 1. Verify .axm ────────────────────────────────────────────────────────
    print(f"\n[srd4] opening {container_path} ...")
    container = AXMContainer.from_path(container_path)
    print(f"[srd4] verifying {len(container.proofs)} proof entries ...")
    if not container.verify_proofs():
        raise AXMError("proof verification failed — container may be tampered")
    fingerprint = container.fingerprint()
    print(f"[srd4] verified ✓  fingerprint={fingerprint}")

    weights_path = container.weights_path
    if weights_path is None:
        raise AXMError("No weights/ directory. Re-pack with pack_to_axm.py.")
    if not is_real_packed(weights_path):
        raise AXMError(
            "axm_to_srd4_gguf requires a real-packed container (--real-pack).\n"
            "Fake-quant containers store weights as FP16 — use axm_to_gguf.py instead."
        )

    # ── 2. Load SRD data ──────────────────────────────────────────────────────
    print("[srd4] loading packed tensors ...")
    index = json.loads((weights_path / INDEX_FILE).read_text())
    blob  = torch.load(weights_path / PACKED_FILE,
                       map_location="cpu", weights_only=True)

    # Infer num_layers from config
    try:
        from transformers import AutoConfig
        cfg        = AutoConfig.from_pretrained(weights_path)
        num_layers = getattr(cfg, "num_hidden_layers", 30)
    except Exception:
        num_layers = 30
        print(f"  [warn] could not read config — assuming {num_layers} layers")

    print(f"  Layers     : {num_layers}")
    print(f"  Quantized  : {len(blob)} weight matrices")
    print(f"  Alpha      : {index.get('alpha', 1.0)}")
    print(f"  group_size : {index.get('group_size', 64)}")
    print(f"  top_k_pct  : {index.get('top_k_pct', 1.0)}")

    # ── 3. Write .srd4 sidecar ────────────────────────────────────────────────
    print(f"\n[srd4] writing sidecar → {srd4_path} ...")
    stats = write_srd4_sidecar(srd4_path, blob, index, fingerprint, num_layers)
    print(f"  ✓ sidecar  {stats['srd4_mb']:.1f} MB  "
          f"({stats['n_tensors']} tensors  {stats['bpw_dense']} bpw dense)")

    # ── 4. Optional: annotate existing GGUF ──────────────────────────────────
    if annotate_existing:
        annotate_gguf_with_srd4(
            Path(annotate_existing), srd4_path, fingerprint, index
        )

    # ── 5. Optional: companion GGUF ───────────────────────────────────────────
    if gguf_out:
        if not llamacpp_dir:
            raise ValueError("--llamacpp required when --gguf-out is set")
        gguf_path = Path(gguf_out)
        print(f"\n[srd4] building companion GGUF → {gguf_path} ...")
        _build_companion_gguf(weights_path, blob, gguf_path,
                               Path(llamacpp_dir).expanduser().resolve())
        # Annotate the companion GGUF too
        annotate_gguf_with_srd4(gguf_path, srd4_path, fingerprint, index)
        stats["gguf_mb"] = round(gguf_path.stat().st_size / 1024**2, 1)

    elapsed = time.monotonic() - t_start
    stats["total_s"] = round(elapsed, 1)

    print(f"\n[srd4] ── done ─────────────────────────────────────────────────")
    print(f"  sidecar      : {srd4_path}  ({stats['srd4_mb']} MB)")
    if gguf_out:
        print(f"  companion    : {gguf_out}  ({stats.get('gguf_mb','?')} MB)")
    print(f"  fingerprint  : {fingerprint}")
    print(f"  total time   : {elapsed:.1f}s")
    print()
    print("  To use with a custom SRD4-aware llama.cpp fork:")
    print(f"    ./llama-cli -m {Path(gguf_out or 'model.gguf').name} \\")
    print(f"        --srd4-sidecar {srd4_path.name} \\")
    print(f"        --alpha 0.0          # compact (4.5 bpw) ")
    print(f"        --alpha 1.0          # full quality (13.0 bpw)")
    print(f"        --met-policy INFORM  # load early layers only (~45% RAM)")

    return stats


# ── .srd4 reader (for validation / inspection) ────────────────────────────────

def read_srd4_header(srd4_path: str) -> dict:
    """Read and return the JSON header from a .srd4 sidecar file."""
    with open(srd4_path, "rb") as f:
        magic = f.read(8)
        if magic != SRD4_MAGIC:
            raise ValueError(f"Not a valid .srd4 file (magic={magic!r})")
        hdr_len = struct.unpack("<I", f.read(4))[0]
        hdr_json = f.read(hdr_len).decode("utf-8")
    return json.loads(hdr_json)


def inspect_srd4(srd4_path: str) -> None:
    """Print a summary of a .srd4 sidecar file."""
    header = read_srd4_header(srd4_path)
    size_mb = Path(srd4_path).stat().st_size / 1024**2
    print(f"\n  .srd4 sidecar: {srd4_path}  ({size_mb:.1f} MB)")
    print(f"  Version      : {header.get('version')}")
    print(f"  Fingerprint  : {header.get('fingerprint')}")
    print(f"  Alpha        : {header.get('alpha')}")
    print(f"  group_size   : {header.get('group_size')}")
    print(f"  top_k_pct    : {header.get('top_k_pct')}")
    print(f"  num_layers   : {header.get('num_layers')}")
    print(f"  bpw (dense)  : {header['block_format']['bpw_dense']}")
    print(f"  Tensors      : {len(header.get('tensors', []))}")
    print()
    print(f"  {'Layer':<55} {'Blocks':>8}  {'MB':>6}")
    print("  " + "─" * 75)
    for t in header.get("tensors", []):
        print(f"  {t['hf_name']:<55} {t['n_blocks']:>8,}  {t['n_bytes']/1024**2:>6.1f}")
    print()
    print("  Hydration policy:")
    for intent, chunks in header.get("hydration_policy", {}).items():
        print(f"    {intent:<10} → {'+'.join(chunks)}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert real-packed .axm to SRD4 sidecar (no FP16 round-trip)"
    )
    p.add_argument("--container",   required=True,  help=".axm archive path")
    p.add_argument("--srd4-out",    required=True,  help="output .srd4 sidecar path")
    p.add_argument("--gguf-out",    default=None,   help="also write companion F16 GGUF")
    p.add_argument("--llamacpp",    default=None,   help="llama.cpp root (needed with --gguf-out)")
    p.add_argument("--annotate-existing", default=None,
                   help="annotate this existing GGUF with axiom.srd4.* KV keys")
    p.add_argument("--inspect",     action="store_true",
                   help="inspect an existing .srd4 file (pass via --srd4-out)")
    p.add_argument("--stats-json",  type=Path, default=None,
                   help="write conversion stats to JSON file")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.inspect:
        inspect_srd4(args.srd4_out)
        return 0

    stats = convert_axm_to_srd4(
        args.container,
        args.srd4_out,
        gguf_out           = args.gguf_out,
        llamacpp_dir       = args.llamacpp,
        annotate_existing  = args.annotate_existing,
    )

    if args.stats_json:
        args.stats_json.parent.mkdir(parents=True, exist_ok=True)
        args.stats_json.write_text(json.dumps(stats, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
