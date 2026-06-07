"""Add Axiom MET slot metadata to a SmolLM2-135M Q4_K_M GGUF file.

Two outputs:
  1. Sidecar JSON   — <gguf_path>.axiom_meta.json  (always written)
                      Readable by any app (PocketPal, llamafile, etc.)
  2. Annotated GGUF — <gguf_path>.axiom.gguf       (--annotate flag)
                      New GGUF copy with axiom.* KV metadata keys added;
                      for llama.cpp builds that support custom KV reads.

Metadata written (axiom.* namespace):

  axiom.embedding_mb        : "54"        always-pinned EventToken slot
  axiom.embedding_precision : "F16"
  axiom.slot.early          : "0-5"       transformer chunk layer range
  axiom.slot.factual        : "6-11"
  axiom.slot.reasoning      : "12-22"
  axiom.slot.governance     : "23-29"
  axiom.chunk_map           : JSON {layer_idx: slot_name}
  axiom.hydration_policy    : JSON {intent: [chunk_keys]}
  axiom.fingerprint         : from --fingerprint arg or ""
  axiom.storage_speed_mbs   : "1500"      UFS 3.1 phone default
  axiom.version             : "1.4"

Usage
-----
  # Sidecar JSON only (no GPU, no GGUF rewrite):
  python3 research/quant/add_axiom_gguf_meta.py \\
      --gguf /path/to/smollm2_135m_instruct_q4km.gguf

  # Sidecar + annotated GGUF copy:
  python3 research/quant/add_axiom_gguf_meta.py \\
      --gguf /path/to/smollm2_135m_instruct_q4km.gguf \\
      --annotate \\
      --fingerprint abcdef1234567890

  # Specify a custom output path for the sidecar:
  python3 research/quant/add_axiom_gguf_meta.py \\
      --gguf /path/to/model.gguf \\
      --sidecar /tmp/axiom_meta.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# ── Axiom slot definitions (matches CHUNK_CATALOG in hydration_sim.py) ────────

EMBEDDING_MB        = 54.0
EMBEDDING_PRECISION = "F16"
AXIOM_VERSION       = "1.4"
DEFAULT_STORAGE_MBS = 1500    # UFS 3.1 (Android phone)

SLOT_RANGES: dict[str, tuple[int, int]] = {
    "early":      (0, 5),
    "factual":    (6, 11),
    "reasoning":  (12, 22),
    "governance": (23, 29),
}

HYDRATION_POLICY: dict[str, list[str]] = {
    "INFORM":    ["early"],
    "CLARIFY":   ["early", "governance"],
    "REFUSE":    ["early", "governance"],
    "UNCERTAIN": ["early", "governance"],
    "HARM":      ["early", "factual", "reasoning", "governance"],
    "DECEIVE":   ["early", "factual", "reasoning", "governance"],
}

SLOT_MB: dict[str, float] = {
    "early": 11.0, "factual": 11.0, "reasoning": 22.0, "governance": 13.0,
}


def _build_chunk_map() -> dict[str, str]:
    """Return {str(layer_idx): slot_name} for all 30 transformer layers."""
    cm: dict[str, str] = {}
    for slot, (lo, hi) in SLOT_RANGES.items():
        for idx in range(lo, hi + 1):
            cm[str(idx)] = slot
    return cm


def _read_gguf_info(gguf_path: Path) -> dict:
    """Read GGUF header metadata for validation and sidecar enrichment."""
    try:
        from gguf import GGUFReader
        reader = GGUFReader(str(gguf_path))
        info: dict = {}
        for name, field in reader.fields.items():
            if name.startswith("GGUF."):
                continue
            try:
                val = field.parts[field.data[0]]
                if hasattr(val, "tolist"):
                    info[name] = val.tolist()
                    if isinstance(info[name], list) and len(info[name]) == 1:
                        info[name] = info[name][0]
                else:
                    info[name] = str(val)
            except Exception:
                pass
        n_tensors = len(reader.tensors)
        total_bytes = sum(t.n_bytes for t in reader.tensors)
        return {
            "kv": info,
            "n_tensors": n_tensors,
            "tensor_bytes": total_bytes,
            "arch": info.get("general.architecture", "unknown"),
            "block_count": info.get("llama.block_count", 30),
            "quant_hint": info.get("general.file_type", "unknown"),
        }
    except ImportError:
        return {"error": "gguf library not installed — install with: pip install gguf"}
    except Exception as e:
        return {"error": str(e)}


def build_sidecar(gguf_path: Path, fingerprint: str = "",
                  storage_mbs: int = DEFAULT_STORAGE_MBS,
                  gguf_info: Optional[dict] = None) -> dict:
    """Construct the full axiom_meta sidecar dict."""
    chunk_map = _build_chunk_map()
    file_mb   = round(gguf_path.stat().st_size / (1024 ** 2), 1) if gguf_path.exists() else None

    # Per-intent memory budget (embedding + transformer chunks)
    intent_ram: dict[str, dict] = {}
    for intent, chunks in HYDRATION_POLICY.items():
        xfmr_mb   = sum(SLOT_MB[c] for c in chunks)
        total_mb  = EMBEDDING_MB + xfmr_mb
        load_ms   = round((xfmr_mb / storage_mbs) * 1000, 1)
        intent_ram[intent] = {
            "chunks": chunks,
            "transformer_mb": xfmr_mb,
            "total_mb": total_mb,
            "ufs_load_ms": load_ms,
        }

    meta = {
        "axiom_version":    AXIOM_VERSION,
        "generated_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gguf_path":        str(gguf_path),
        "gguf_mb":          file_mb,
        "fingerprint":      fingerprint,
        "embedding_slot": {
            "chunk":        "embedding",
            "mb":           EMBEDDING_MB,
            "precision":    EMBEDDING_PRECISION,
            "always_pinned": True,
            "purpose":      "tok_embeddings + lm_head (weight-tied)",
            "description":  "Always resident in EventToken slot; zero I/O cost per token after init",
        },
        "transformer_chunks": {
            slot: {
                "layers":    f"{lo}-{hi}",
                "mb":        SLOT_MB[slot],
                "precision": "Q4_K_M",
                "first_layer": lo,
                "last_layer":  hi,
            }
            for slot, (lo, hi) in SLOT_RANGES.items()
        },
        "chunk_map":         chunk_map,
        "hydration_policy":  HYDRATION_POLICY,
        "intent_ram_budget": intent_ram,
        "storage_speed_mbs": storage_mbs,
        "between_met_floor_mb": EMBEDDING_MB,
        "peak_harm_mb":      EMBEDDING_MB + sum(SLOT_MB.values()),
    }

    if gguf_info and "error" not in gguf_info:
        meta["gguf_metadata"] = {
            "architecture": gguf_info.get("arch"),
            "block_count":  gguf_info.get("block_count"),
            "n_tensors":    gguf_info.get("n_tensors"),
        }

    return meta


def write_sidecar(meta: dict, sidecar_path: Path) -> None:
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# ── Annotated GGUF writer ─────────────────────────────────────────────────────

def write_annotated_gguf(gguf_path: Path, out_path: Path,
                         meta: dict, fingerprint: str = "") -> None:
    """Create a new GGUF copy with axiom.* KV metadata keys prepended."""
    try:
        import numpy as np
        from gguf import GGUFReader, GGUFWriter, GGMLQuantizationType
    except ImportError:
        print("  [annotate] gguf and numpy required: pip install gguf numpy")
        return

    print(f"  [annotate] reading {gguf_path.name} ...")
    reader = GGUFReader(str(gguf_path))
    arch   = meta.get("gguf_metadata", {}).get("architecture") or "llama"

    print(f"  [annotate] writing annotated copy → {out_path.name}")
    writer = GGUFWriter(str(out_path), arch=arch)

    # Copy all existing KV metadata (skip internal GGUF.* fields)
    for name, field in reader.fields.items():
        if name.startswith("GGUF."):
            continue
        if name.startswith("axiom."):
            continue    # will be overwritten below
        try:
            from gguf import GGUFValueType
            vtype = field.types[0]
            val   = field.parts[field.data[0]]
            if vtype == GGUFValueType.STRING:
                raw = bytes(val.tolist())
                writer.add_string(name, raw.decode("utf-8", errors="replace"))
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
            pass    # skip fields we can't copy cleanly

    # Add axiom.* KV metadata
    writer.add_string("axiom.version",             AXIOM_VERSION)
    writer.add_string("axiom.embedding_mb",        str(EMBEDDING_MB))
    writer.add_string("axiom.embedding_precision", EMBEDDING_PRECISION)
    writer.add_string("axiom.storage_speed_mbs",   str(DEFAULT_STORAGE_MBS))
    writer.add_string("axiom.fingerprint",         fingerprint)
    for slot, (lo, hi) in SLOT_RANGES.items():
        writer.add_string(f"axiom.slot.{slot}", f"{lo}-{hi}")
    writer.add_string("axiom.chunk_map",        json.dumps(meta["chunk_map"]))
    writer.add_string("axiom.hydration_policy", json.dumps(HYDRATION_POLICY))

    # Copy tensors (raw bytes passthrough — preserves Q4_K_M quantization)
    print(f"  [annotate] copying {len(reader.tensors)} tensors ...")
    for t in reader.tensors:
        raw_data = np.frombuffer(bytes(t.data), dtype=np.uint8)
        writer.add_tensor(
            t.name,
            raw_data,
            raw_shape=list(t.shape),
            raw_dtype=t.tensor_type,
        )

    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    out_mb = round(out_path.stat().st_size / (1024 ** 2), 1)
    print(f"  [annotate] done → {out_path}  ({out_mb} MB)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Add Axiom MET slot metadata to a GGUF file")
    p.add_argument("--gguf", required=True,
                   help="path to the Q4_K_M GGUF file")
    p.add_argument("--sidecar", default=None,
                   help="sidecar JSON output path (default: <gguf>.axiom_meta.json)")
    p.add_argument("--annotate", action="store_true",
                   help="also write an annotated GGUF copy with axiom.* KV keys")
    p.add_argument("--annotated-out", default=None,
                   help="annotated GGUF output path (default: <gguf>.axiom.gguf)")
    p.add_argument("--fingerprint", default="",
                   help="AXM container fingerprint to embed (from axm_cli.py verify)")
    p.add_argument("--storage-mbs", type=int, default=DEFAULT_STORAGE_MBS,
                   help=f"storage read speed in MB/s (default: {DEFAULT_STORAGE_MBS} = UFS 3.1)")
    return p.parse_args()


def main() -> int:
    args   = _parse_args()
    gguf_p = Path(args.gguf)

    if not gguf_p.exists():
        print(f"[error] GGUF not found: {gguf_p}")
        print("        Pass a local path; for phone files, copy first with adb pull:")
        print("        adb pull /storage/emulated/0/models/smollm2_135m_instruct_q4km.gguf .")
        return 1

    print()
    print("═" * 68)
    print("  Axiom GGUF Metadata Writer")
    print("─" * 68)
    print(f"  Input   : {gguf_p}  ({gguf_p.stat().st_size / 1024**2:.0f} MB)")

    # Read existing GGUF info
    print("  Reading GGUF header ...")
    gguf_info = _read_gguf_info(gguf_p)
    if "error" in gguf_info:
        print(f"  [warn] GGUF read error: {gguf_info['error']}")
        print("         Sidecar will be written with SmolLM2-135M defaults")
        gguf_info = {}
    else:
        print(f"  Architecture : {gguf_info.get('arch', '?')}")
        print(f"  Block count  : {gguf_info.get('block_count', '?')} transformer layers")
        print(f"  Tensors      : {gguf_info.get('n_tensors', '?')}")
        print(f"  Tensor data  : {gguf_info.get('tensor_bytes', 0) / 1024**2:.0f} MB")
        if gguf_info.get("block_count") not in (None, 30):
            print(f"  [warn] block_count={gguf_info['block_count']} — "
                  "chunk boundaries tuned for 30-layer SmolLM2-135M; review SLOT_RANGES if different")

    # Build sidecar
    meta = build_sidecar(
        gguf_p, fingerprint=args.fingerprint,
        storage_mbs=args.storage_mbs, gguf_info=gguf_info,
    )

    # Write sidecar JSON
    sidecar_p = Path(args.sidecar) if args.sidecar else gguf_p.with_suffix(".axiom_meta.json")
    write_sidecar(meta, sidecar_p)
    print()
    print(f"  Sidecar JSON → {sidecar_p}")

    # Print summary table
    print()
    print("  MET SLOT SUMMARY")
    print(f"  {'Slot':<14}  {'Layers':<8}  {'MB':>5}  {'Precision':>9}  Always?")
    print("  " + "─" * 52)
    print(f"  {'embedding':<14}  {'—embed—':<8}  {EMBEDDING_MB:>5.0f}  {'F16':>9}  ✓ pinned")
    for slot, (lo, hi) in SLOT_RANGES.items():
        print(f"  {slot:<14}  {lo}-{hi:<6}  {SLOT_MB[slot]:>5.0f}  {'Q4_K_M':>9}  on demand")

    print()
    print("  HYDRATION POLICY  (intent → chunks loaded from storage)")
    print(f"  {'Intent':<10}  {'Chunks':<34}  {'MB':>5}  {'UFS ms':>7}")
    print("  " + "─" * 64)
    for intent, chunks in HYDRATION_POLICY.items():
        xfmr_mb = sum(SLOT_MB[c] for c in chunks)
        total_mb = EMBEDDING_MB + xfmr_mb
        load_ms  = round((xfmr_mb / args.storage_mbs) * 1000, 1)
        print(f"  {intent:<10}  {'+'.join(chunks):<34}  {total_mb:>5.0f}  {load_ms:>6.1f}ms")

    print()
    if args.fingerprint:
        print(f"  Fingerprint  : {args.fingerprint}")
    else:
        print("  Fingerprint  : (not set — run axm_cli.py verify and pass --fingerprint)")

    print()
    print("  PHONE DEPLOY  (transfer sidecar alongside GGUF):")
    print(f"    adb push {sidecar_p.name} /storage/emulated/0/models/")
    print(f"    adb push {gguf_p.name} /storage/emulated/0/models/")
    print()
    print("  Apps implementing AxiomSlotLoader read <model>.axiom_meta.json")
    print("  on startup and pin the embedding slice before accepting requests.")

    # Annotated GGUF (optional)
    if args.annotate:
        annot_p = Path(args.annotated_out) if args.annotated_out \
                  else gguf_p.with_name(gguf_p.stem + ".axiom.gguf")
        print()
        print(f"  Annotated GGUF → {annot_p}")
        write_annotated_gguf(gguf_p, annot_p, meta, fingerprint=args.fingerprint)
    else:
        stem = gguf_p.stem + ".axiom.gguf"
        print(f"  (Run with --annotate to also create {stem} with axiom.* KV keys)")

    print()
    print("═" * 68)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
