"""Simulate pack → load → inference across multiple SRD configs.

Builds a tiny synthetic model (no HF downloads), packs it into .axm
archives at FP16 and several SRD top_k_pct values, then loads each
archive and measures TTFT + throughput. Outputs a comparison table
and an optional JSON report.

The latency numbers are representative of the *pipeline overhead*
(pack, proof-verify, load) on the current machine. Because the model is
synthetic and tiny, tok/s reflects Python/PyTorch overhead only — not
real inference throughput. Use pack_to_axm.py + load_from_axm.py with
a real HF checkpoint for production figures.

CLI:
    # quick table (CPU, no downloads)
    python -m research.quant.simulate_axm

    # save results
    python -m research.quant.simulate_axm --output results/simulate_axm.json

    # skip latency measurement (pack + verify only, faster)
    python -m research.quant.simulate_axm --no-latency
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch                                                    # noqa: E402
import torch.nn as nn                                          # noqa: E402

from axiom_axm import AXMContainer, FORMAT_VERSION, AXMError   # noqa: E402
from axiom_quant import DEFAULT_GROUP_SIZE, srd_bits_per_weight # noqa: E402
from research.quant.quantize_model import (                     # noqa: E402
    DEFAULT_SKIP_MODULES,
    quantize_hf_model_inplace,
)


# ── Tiny synthetic transformer (no HF dependency) ──────────────────────────

class _SyntheticAttention(nn.Module):
    def __init__(self, d: int, heads: int) -> None:
        super().__init__()
        self.q_proj = nn.Linear(d, d, bias=False)
        self.k_proj = nn.Linear(d, d, bias=False)
        self.v_proj = nn.Linear(d, d, bias=False)
        self.o_proj = nn.Linear(d, d, bias=False)
        self.heads  = heads
        self.head_d = d // heads

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, d = x.shape
        q = self.q_proj(x).view(B, T, self.heads, self.head_d).transpose(1, 2)
        k = self.k_proj(x).view(B, T, self.heads, self.head_d).transpose(1, 2)
        v = self.v_proj(x).view(B, T, self.heads, self.head_d).transpose(1, 2)
        scale = math.sqrt(self.head_d)
        attn  = torch.softmax((q @ k.transpose(-2, -1)) / scale, dim=-1)
        out   = (attn @ v).transpose(1, 2).reshape(B, T, d)
        return self.o_proj(out)


class _SyntheticMLP(nn.Module):
    def __init__(self, d: int, ffn: int) -> None:
        super().__init__()
        self.gate_proj = nn.Linear(d, ffn, bias=False)
        self.up_proj   = nn.Linear(d, ffn, bias=False)
        self.down_proj = nn.Linear(ffn, d, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down_proj(
            torch.sigmoid(self.gate_proj(x)) * self.up_proj(x)
        )


class _SyntheticLayer(nn.Module):
    def __init__(self, d: int, heads: int, ffn: int) -> None:
        super().__init__()
        self.attn = _SyntheticAttention(d, heads)
        self.mlp  = _SyntheticMLP(d, ffn)
        self.ln1  = nn.LayerNorm(d)
        self.ln2  = nn.LayerNorm(d)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.mlp(self.ln2(x))
        return x


class SyntheticModel(nn.Module):
    """Tiny 2-layer transformer for simulation (no HF required)."""
    def __init__(self, vocab: int = 512, d: int = 256,
                 heads: int = 4, ffn: int = 512, n_layers: int = 2) -> None:
        super().__init__()
        self.embed  = nn.Embedding(vocab, d)
        self.layers = nn.ModuleList(
            [_SyntheticLayer(d, heads, ffn) for _ in range(n_layers)]
        )
        self.ln_f   = nn.LayerNorm(d)
        self.lm_head = nn.Linear(d, vocab, bias=False)
        self.vocab  = vocab

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        x = self.embed(input_ids)
        for layer in self.layers:
            x = layer(x)
        return self.lm_head(self.ln_f(x))

    def generate(self, input_ids: torch.Tensor,
                 max_new_tokens: int = 20) -> torch.Tensor:
        out = input_ids
        for _ in range(max_new_tokens):
            with torch.no_grad():
                logits = self(out)
            next_id = logits[:, -1, :].argmax(dim=-1, keepdim=True)
            out = torch.cat([out, next_id], dim=1)
        return out


# ── Config helpers ──────────────────────────────────────────────────────────

def _fp16_quant_map() -> dict:
    return {"scheme": "fp16", "bpw": 16.0}


def _srd_quant_map(top_k_pct: float, group_size: int, bpw: float) -> dict:
    return {
        "scheme":     "srd",
        "group_size": group_size,
        "top_k_pct":  top_k_pct,
        "bpw":        round(bpw, 4),
        "alpha":      1.0,
    }


SIM_CONFIGS: List[Dict] = [
    {"label": "FP16 baseline",        "top_k_pct": None},
    {"label": "SRD sparse-10  (~4.8 bpw)", "top_k_pct": 0.10},
    {"label": "SRD sparse-25  (~7.0 bpw)", "top_k_pct": 0.25},
    {"label": "SRD sparse-50  (~9.0 bpw)", "top_k_pct": 0.50},
    {"label": "SRD sparse-75  (~11.0 bpw)","top_k_pct": 0.75},
    {"label": "SRD dense      (13.0 bpw)", "top_k_pct": 1.00},
]


# ── Core simulation ─────────────────────────────────────────────────────────

def _param_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def _mse_vs_original(original: nn.Module, modified: nn.Module,
                     device: str) -> float:
    """Mean squared error of all Linear weight parameters."""
    total_mse, n = 0.0, 0
    orig_params  = dict(original.named_parameters())
    for name, p in modified.named_parameters():
        if name in orig_params:
            diff = (p.float() - orig_params[name].float()) ** 2
            total_mse += diff.mean().item()
            n += 1
    return total_mse / n if n else 0.0


def _pack_model(
    model: nn.Module,
    top_k_pct: Optional[float],
    output_path: str,
    group_size: int,
    device: str,
) -> dict:
    """Quantize (optionally) and pack to .axm. Returns timing + size stats."""
    import copy, shutil

    model_copy = copy.deepcopy(model).to(device)

    bpw = 16.0
    quant_s = 0.0
    packed_layers: dict = {}
    if top_k_pct is not None:
        t0 = time.monotonic()
        packed_layers = quantize_hf_model_inplace(
            model_copy, alpha=1.0,
            group_size=group_size, top_k_pct=top_k_pct, progress=False,
        )
        quant_s = time.monotonic() - t0
        if packed_layers:
            bpw = srd_bits_per_weight(next(iter(packed_layers.values())))

    quant_map = (
        _srd_quant_map(top_k_pct, group_size, bpw)
        if top_k_pct is not None
        else _fp16_quant_map()
    )
    short_name = "synthetic_model"

    with tempfile.TemporaryDirectory(prefix="axm_sim_") as tmp:
        weights_dir = Path(tmp) / "weights"
        weights_dir.mkdir()

        # Persist state_dict as safetensors-style (torch.save for simplicity)
        t1 = time.monotonic()
        torch.save(model_copy.state_dict(), weights_dir / "model.pt")
        # Write a minimal config so weights_path is discoverable
        cfg = {"model_type": "synthetic", "vocab_size": model_copy.vocab}
        (weights_dir / "config.json").write_text(
            json.dumps(cfg, indent=2), encoding="utf-8"
        )
        save_s = time.monotonic() - t1

        spec = {
            "format_version": FORMAT_VERSION,
            "core_logic":     f"{short_name}_srd" if top_k_pct else short_name,
            "quant_map":      quant_map,
            "hardware_map":   "cpu",
            "safety_proofs":  True,
            "core": {
                "name":         short_name,
                "revision":     "sim",
                "quant_map":    quant_map,
                "skip_modules": list(DEFAULT_SKIP_MODULES),
            },
        }

        t2 = time.monotonic()
        container = AXMContainer.pack(
            spec, output_path,
            archive=True,
            weights_source_dir=weights_dir,
        )
        pack_s = time.monotonic() - t2

    archive_bytes = Path(output_path).stat().st_size
    archive_mb    = archive_bytes / (1024 ** 2)
    params        = _param_count(model)
    theoretical_mb = params * bpw / 8 / (1024 ** 2)

    return {
        "quant_s":         round(quant_s, 3),
        "save_s":          round(save_s, 3),
        "pack_s":          round(pack_s, 3),
        "total_pack_s":    round(quant_s + save_s + pack_s, 3),
        "bpw":             round(bpw, 2),
        "archive_mb":      round(archive_mb, 3),
        "theoretical_mb":  round(theoretical_mb, 3),
        "fingerprint":     container.fingerprint(),
    }


def _load_and_infer(
    output_path: str,
    original_model: nn.Module,
    device: str,
    n_tokens: int = 20,
    n_runs: int = 1,
    measure_latency: bool = True,
) -> dict:
    """Open .axm, verify, load state_dict, run generation."""
    import copy

    t0 = time.monotonic()
    container = AXMContainer.from_path(output_path)
    open_s = time.monotonic() - t0

    t1 = time.monotonic()
    ok = container.verify_proofs()
    verify_s = time.monotonic() - t1
    if not ok:
        raise AXMError("proof verification failed")

    weights_path = container.weights_path
    if weights_path is None:
        raise AXMError("no weights/ in archive")

    t2 = time.monotonic()
    # Reconstruct model from saved state
    model = copy.deepcopy(original_model)
    state = torch.load(
        weights_path / "model.pt",
        map_location=device,
        weights_only=True,
    )
    model.load_state_dict(state)
    model.to(device).eval()
    load_s = time.monotonic() - t2

    mse = _mse_vs_original(original_model, model, device)

    if not measure_latency:
        return {
            "open_s":    round(open_s, 3),
            "verify_s":  round(verify_s, 3),
            "load_s":    round(load_s, 3),
            "mse":       round(mse, 6),
            "ttft_ms":   None,
            "tok_per_s": None,
            "runs":      [],
        }

    prompt = torch.randint(0, model.vocab, (1, 8), device=device)
    run_results = []
    for _ in range(n_runs):
        # TTFT (1 token)
        t_ttft = time.monotonic()
        with torch.no_grad():
            model.generate(prompt, max_new_tokens=1)
        ttft_s = time.monotonic() - t_ttft

        # Full generation
        t_gen = time.monotonic()
        with torch.no_grad():
            out = model.generate(prompt, max_new_tokens=n_tokens)
        gen_s = time.monotonic() - t_gen

        n_new = out.shape[1] - prompt.shape[1]
        tps   = n_new / gen_s if gen_s > 0 else 0.0
        run_results.append({
            "ttft_ms":   round(ttft_s * 1000, 1),
            "tok_per_s": round(tps, 1),
        })

    avg_ttft = sum(r["ttft_ms"]   for r in run_results) / len(run_results)
    avg_tps  = sum(r["tok_per_s"] for r in run_results) / len(run_results)

    return {
        "open_s":    round(open_s, 3),
        "verify_s":  round(verify_s, 3),
        "load_s":    round(load_s, 3),
        "mse":       round(mse, 6),
        "ttft_ms":   round(avg_ttft, 1),
        "tok_per_s": round(avg_tps, 1),
        "runs":      run_results,
    }


# ── Table printer ───────────────────────────────────────────────────────────

_HDR = (
    f"{'Config':<34} {'bpw':>6} {'pack_s':>7} {'axm_MB':>7} "
    f"{'verify_s':>9} {'load_s':>7} {'TTFT_ms':>8} {'tok/s':>7} "
    f"{'MSE':>10} {'ΔTTFT':>8}"
)


def _print_table(rows: List[dict]) -> None:
    print()
    print(_HDR)
    print("─" * len(_HDR))
    baseline_ttft = next(
        (r["load"]["ttft_ms"] for r in rows
         if r["load"]["ttft_ms"] is not None and r["cfg"]["top_k_pct"] is None),
        None,
    )
    for r in rows:
        cfg  = r["cfg"]
        pack = r["pack"]
        load = r["load"]
        bpw  = pack["bpw"]
        tps_str  = f"{load['tok_per_s']:>7.1f}" if load["tok_per_s"] is not None else "     n/a"
        ttft_str = f"{load['ttft_ms']:>8.1f}" if load["ttft_ms"] is not None else "     n/a"
        if baseline_ttft is not None and load["ttft_ms"] is not None:
            delta = load["ttft_ms"] - baseline_ttft
            delta_str = f"{delta:>+8.1f}"
        else:
            delta_str = "     n/a"
        print(
            f"{cfg['label']:<34} {bpw:>6.2f} "
            f"{pack['total_pack_s']:>7.3f} "
            f"{pack['archive_mb']:>7.3f} "
            f"{load['verify_s']:>9.3f} "
            f"{load['load_s']:>7.3f} "
            f"{ttft_str} "
            f"{tps_str} "
            f"{load['mse']:>10.6f} "
            f"{delta_str}"
        )
    print()


# ── Main ────────────────────────────────────────────────────────────────────

def simulate(
    configs: List[Dict],
    *,
    group_size: int = DEFAULT_GROUP_SIZE,
    n_tokens: int = 20,
    n_runs: int = 2,
    measure_latency: bool = True,
    device: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> List[dict]:
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[simulate] device={device}  group_size={group_size}  "
          f"configs={len(configs)}")

    original_model = SyntheticModel().to(device)
    params = _param_count(original_model)
    print(f"[simulate] synthetic model: {params:,} parameters")

    rows: List[dict] = []
    with tempfile.TemporaryDirectory(prefix="axm_sim_out_") as tmpdir:
        for cfg in configs:
            label       = cfg["label"]
            top_k_pct   = cfg["top_k_pct"]
            archive_path = str(Path(tmpdir) / f"sim_{label[:16].replace(' ','_')}.axm")

            print(f"[simulate] ── {label} ──")
            pack_stats = _pack_model(
                original_model, top_k_pct, archive_path, group_size, device
            )
            print(f"           bpw={pack_stats['bpw']:.2f}  "
                  f"size={pack_stats['archive_mb']:.3f} MB  "
                  f"pack={pack_stats['total_pack_s']:.3f}s")

            load_stats = _load_and_infer(
                archive_path, original_model, device,
                n_tokens=n_tokens, n_runs=n_runs,
                measure_latency=measure_latency,
            )
            if measure_latency:
                print(f"           TTFT={load_stats['ttft_ms']:.1f}ms  "
                      f"tok/s={load_stats['tok_per_s']:.1f}  "
                      f"MSE={load_stats['mse']:.6f}")

            rows.append({"cfg": cfg, "pack": pack_stats, "load": load_stats})

    _print_table(rows)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "simulate_axm.json"
        out_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"[simulate] results written to {out_path}")

    return rows


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Simulate pack→load→inference across SRD configs"
    )
    p.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    p.add_argument("--tokens", type=int, default=20,
                   help="Tokens to generate per run")
    p.add_argument("--n-runs", type=int, default=2,
                   help="Runs per config for averaging latency")
    p.add_argument("--no-latency", action="store_true",
                   help="Skip inference; pack+verify only (faster)")
    p.add_argument("--device", default=None)
    p.add_argument("--output", type=Path, default=None,
                   help="Directory to write simulate_axm.json")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    simulate(
        SIM_CONFIGS,
        group_size=args.group_size,
        n_tokens=args.tokens,
        n_runs=args.n_runs,
        measure_latency=not args.no_latency,
        device=args.device,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
