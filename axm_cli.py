"""Unified ``axm`` command-line interface for AXM model containers.

A single entry point that wraps the three things you actually do with a
``.axm`` file:

    axm verify  FILE                 — check every signature, print fingerprint
    axm info    FILE                 — header, quant_map, bpw, sizes, manifest
    axm run     FILE --prompt "..."  — load weights, verify, generate
    axm pack    --model M --out F    — quantize + pack (thin wrapper)

Installed as a console script (``axm``) via pyproject [project.scripts];
also runnable as ``python -m axm_cli`` or ``python axm_cli.py``.

``info`` is the honest-size surface: it reports both the actual on-disk
archive size and, for SRD containers, what a real E3-packed archive would
weigh (``srd_packed_bytes``-style estimate from the quant_map). That gap
is the fake-quant-vs-real-pack story in one view.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axiom_axm import AXMContainer, AXMError    # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

def _human_mb(num_bytes: int) -> str:
    mb = num_bytes / (1024 ** 2)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def _archive_bytes(container_path: str) -> Optional[int]:
    p = Path(container_path)
    return p.stat().st_size if p.is_file() else None


def _quant_summary(qmap) -> dict:
    """Normalize a quant_map (dict or legacy string) into a flat summary."""
    if isinstance(qmap, dict):
        return {
            "scheme":     qmap.get("scheme", "unknown"),
            "bpw":        qmap.get("bpw"),
            "group_size": qmap.get("group_size"),
            "top_k_pct":  qmap.get("top_k_pct"),
            "packed":     qmap.get("packed", False),
        }
    return {"scheme": str(qmap), "bpw": None, "group_size": None,
            "top_k_pct": None, "packed": False}


def _estimate_real_packed_mb(qmap: dict, archive_bytes: int) -> Optional[float]:
    """For an SRD fake-quant archive, estimate the real E3-packed size.

    The fake-quant archive stores FP16 weights (~16 bpw equivalent of the
    weight bytes). Scale the *weight* portion down by bpw/16. This is a
    coarse estimate — info prints it as 'approx'.
    """
    if not isinstance(qmap, dict):
        return None
    if qmap.get("scheme") != "srd":
        return None
    bpw = qmap.get("bpw")
    if not bpw:
        return None
    # Treat the whole archive as dominated by weights (true for these models).
    return (archive_bytes / (1024 ** 2)) * (bpw / 16.0)


# ── subcommands ──────────────────────────────────────────────────────────────

def cmd_verify(args: argparse.Namespace) -> int:
    try:
        c = AXMContainer.from_path(args.container)
        ok = c.verify_proofs()
    except AXMError as e:
        print(json.dumps({"verified": False, "error": str(e)}, indent=2))
        return 1
    result = {
        "verified":       ok,
        "proofs_checked": len(c.proofs),
        "fingerprint":    c.fingerprint(),
    }
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if ok else 1


def cmd_info(args: argparse.Namespace) -> int:
    c = AXMContainer.from_path(args.container)
    base = c.inspect()
    qmap = c.header.quant_map
    quant = _quant_summary(qmap)

    arch_bytes = _archive_bytes(args.container)
    info = {
        "fingerprint":     base["fingerprint"],
        "format_version":  base["header"]["format_version"],
        "core_logic":      base["header"]["core_logic"],
        "hardware_map":    base["header"]["hardware_map"],
        "quant":           quant,
        "counts": {
            "delegates":    base["delegate_count"],
            "trajectories": base["trajectory_count"],
            "vertices":     base["vertex_count"],
            "proofs":       base["proof_count"],
        },
        "has_weights":     c.weights_path is not None,
    }

    if arch_bytes is not None:
        info["size"] = {"archive": _human_mb(arch_bytes)}
        if not quant.get("packed"):
            est = _estimate_real_packed_mb(qmap, arch_bytes)
            if est is not None:
                info["size"]["real_packed_approx"] = f"{est:.0f} MB"
                info["size"]["note"] = (
                    "archive is fake-quant (FP16 weights on the SRD grid); "
                    "real_packed_approx ≈ what E3 W4+sparse-D8 packing would "
                    "produce. Use `axm pack --real-pack` to realize it."
                )
        else:
            info["size"]["note"] = "weights are E3-packed (real on-disk savings)"

    print(json.dumps(info, indent=2, ensure_ascii=True))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # Imported lazily — pulls torch + transformers only when running.
    from research.quant.load_from_axm import load_and_measure
    stats = load_and_measure(
        args.container,
        prompt=args.prompt,
        n_tokens=args.tokens,
        n_runs=args.n_runs,
        device=args.device,
        clean=args.clean,
        drop_uncertain=args.drop_uncertain,
        kv_cache_path=getattr(args, "kv_cache", None),
        save_kv_cache=getattr(args, "save_kv_cache", None),
        kv_token_id=getattr(args, "kv_token_id", None),
    )
    if args.stats_json:
        Path(args.stats_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_json).write_text(json.dumps(stats, indent=2) + "\n")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from research.quant.axm_to_gguf import convert_axm_to_gguf
    stats = convert_axm_to_gguf(
        args.container,
        args.gguf_out,
        llamacpp_dir=args.llamacpp,
        quant_type=args.quant,
        device=args.device,
    )
    if args.stats_json:
        Path(args.stats_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_json).write_text(json.dumps(stats, indent=2) + "\n")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    from research.quant.pack_to_axm import pack_model
    top_k = args.srd_top_k_pct
    if getattr(args, "srd4", False):
        top_k = 0.0
    stats = pack_model(
        model_name=args.model,
        output_path=args.output,
        srd_top_k_pct=top_k,
        group_size=args.group_size,
        model_revision=args.revision,
        hardware_map=args.hardware_map,
        compresslevel=args.compresslevel,
        real_pack=getattr(args, "real_pack", False),
    )
    if args.stats_json:
        Path(args.stats_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_json).write_text(json.dumps(stats, indent=2) + "\n")
    return 0


# ── parser ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axm",
        description="AXM model container CLI — verify, inspect, and run .axm files",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pv = sub.add_parser("verify", help="verify every signature in the container")
    pv.add_argument("container")
    pv.set_defaults(func=cmd_verify)

    pi = sub.add_parser("info", help="print header, quant_map, bpw, sizes")
    pi.add_argument("container")
    pi.set_defaults(func=cmd_info)

    pr = sub.add_parser("run",
                        help="load weights, verify, generate (prints peak RSS)")
    pr.add_argument("container")
    pr.add_argument("--prompt", default="Once upon a time,")
    pr.add_argument("--tokens", type=int, default=80)
    pr.add_argument("--n-runs", type=int, default=1)
    pr.add_argument("--device", default=None)
    pr.add_argument("--clean", action="store_true",
                    help="filter the generation through the ORVL-016 intent "
                         "gate (drops repeats, blocked, and filler steps)")
    pr.add_argument("--drop-uncertain", action="store_true",
                    help="with --clean, also drop UNCERTAIN filler steps")
    pr.add_argument("--kv-cache", default=None,
                    help="path to a signed .kvcache.pt file — skips prompt prefill")
    pr.add_argument("--save-kv-cache", default=None,
                    help="sign and save the prompt KV cache to this path")
    pr.add_argument("--kv-token-id", default=None,
                    help="EventToken.id to embed in the KV cache signature")
    pr.add_argument("--stats-json", default=None)
    pr.set_defaults(func=cmd_run)

    pe = sub.add_parser("extract",
                        help="verify .axm, reconstruct weights, export to GGUF "
                             "for llama.cpp inference")
    pe.add_argument("container")
    pe.add_argument("--gguf-out",  required=True, help="output .gguf path")
    pe.add_argument("--llamacpp",  required=True,
                    help="root of a llama.cpp checkout (needs build/bin/ + "
                         "convert_hf_to_gguf.py)")
    pe.add_argument("--quant", default="Q4_K_M",
                    help="GGUF quant type: Q4_K_M / Q5_K_M / F16 / none "
                         "(default: Q4_K_M)")
    pe.add_argument("--device", default="cpu",
                    help="device for weight reconstruction (cpu is safe on Orin)")
    pe.add_argument("--stats-json", default=None)
    pe.set_defaults(func=cmd_extract)

    pp = sub.add_parser("pack", help="quantize and pack a model into .axm")
    pp.add_argument("--model", required=True)
    pp.add_argument("--output", required=True)
    pp.add_argument("--srd-top-k-pct", type=float, default=None,
                    help="SRD sparsity (0.25 = ~7 bpw, 0 = W4-only ~4.5 bpw); omit for FP16")
    pp.add_argument("--srd4", action="store_true",
                    help="shorthand for --srd-top-k-pct 0 (pure W4, no residual, ~4.5 bpw)")
    pp.add_argument("--real-pack", action="store_true",
                    help="bit-pack W4+D8 into E3 format for real storage savings "
                         "(requires --srd4 or --srd-top-k-pct); without this flag "
                         "weights are stored as FP16 fake-quant on the SRD grid")
    pp.add_argument("--group-size", type=int, default=64)
    pp.add_argument("--revision", default=None)
    pp.add_argument("--hardware-map", default="gpu",
                    choices=["cpu", "gpu", "npu", "fpga", "compile_on_load"])
    pp.add_argument("--compresslevel", type=int, default=1,
                    choices=range(0, 10), metavar="[0-9]")
    pp.add_argument("--stats-json", default=None)
    pp.set_defaults(func=cmd_pack)

    # ── index-pack / index-verify / index-unpack ─────────────────────────────
    pip = sub.add_parser(
        "index-pack",
        help="pack FTS5 shards + caches into a signed .rag.axm bundle",
    )
    pip.add_argument(
        "--shard", action="append", required=True, metavar="DOMAIN:PATH",
        help="e.g. --shard cve:/data/cve.db  (repeatable for multiple shards)",
    )
    pip.add_argument("--output", "-o", required=True,
                     help="output .rag.axm path")
    pip.add_argument("--compresslevel", type=int, default=6,
                     choices=range(0, 10), metavar="[0-9]")
    pip.set_defaults(func=cmd_index_pack)

    piv = sub.add_parser(
        "index-verify",
        help="verify HMAC + per-shard SHA-256 of a .rag.axm bundle",
    )
    piv.add_argument("bundle")
    piv.set_defaults(func=cmd_index_verify)

    piu = sub.add_parser(
        "index-unpack",
        help="verify and extract a .rag.axm bundle to a directory",
    )
    piu.add_argument("bundle")
    piu.add_argument("--dest", "-d", required=True, help="output directory")
    piu.add_argument("--no-verify", action="store_true",
                     help="skip HMAC verification (not recommended)")
    piu.set_defaults(func=cmd_index_unpack)

    return p


def cmd_index_pack(args: argparse.Namespace) -> int:
    from axiom_shard_router import RAGBundle
    shards = []
    for spec in args.shard:
        if ":" not in spec:
            print(f"error: --shard must be domain:path (got {spec!r})", file=sys.stderr)
            return 1
        domain, db_path = spec.split(":", 1)
        cache_key  = f"{domain.upper()}_CACHE"
        cache_path = getattr(args, "cache", {}).get(domain)
        shards.append((domain, Path(db_path), Path(cache_path) if cache_path else None))

    try:
        result = RAGBundle.pack(shards, Path(args.output))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


def cmd_index_verify(args: argparse.Namespace) -> int:
    from axiom_shard_router import RAGBundle
    ok, info = RAGBundle.verify(Path(args.bundle))
    print(json.dumps({**info, "verified": ok}, indent=2, ensure_ascii=True))
    return 0 if ok else 1


def cmd_index_unpack(args: argparse.Namespace) -> int:
    from axiom_shard_router import RAGBundle
    try:
        dest = RAGBundle.unpack(
            Path(args.bundle),
            Path(args.dest),
            verify=not args.no_verify,
        )
        print(json.dumps({"unpacked_to": str(dest)}, indent=2, ensure_ascii=True))
        return 0
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
