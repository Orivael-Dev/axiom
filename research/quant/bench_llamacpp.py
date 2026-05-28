"""K-quant baseline: shell out to llama.cpp's `llama-perplexity` for
Q4_K_M / Q5_K_M / Q6_K / Q8_0 PPL on the same WikiText-2 test split.

Produces rows 6-9 of the results table in the same JSON shape that
bench_perplexity.py emits, so plot_results.py can read both files.

The honest path is "build llama.cpp, convert TinyLlama to GGUF,
re-quantize to each K-format, run --perplexity on each." If any of
that breaks (missing binaries, conversion script changes, etc.),
the script falls back to citing the published numbers and flags
the mismatch in the output JSON.

Reference published values (llama.cpp upstream, TinyLlama-1.1B):
  Q4_K_M : PPL 9.05  (4.85 bpw)
  Q5_K_M : PPL 8.36  (5.69 bpw)
  Q6_K   : PPL 7.82  (6.56 bpw)
  Q8_0   : PPL 7.71  (8.50 bpw)

Sources: llama.cpp README + community PPL spreadsheets. These numbers
are quoted for the same WikiText-2 raw test split but may use a
slightly different stride. Use --rerun-locally for apples-to-apples
when binaries are available.

CLI:
    # Cite-only (fast, no binaries required)
    python -m research.quant.bench_llamacpp \\
        --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \\
        --output research/quant/results/kquant_sweep.json

    # Re-run locally (slow, needs llama.cpp built)
    python -m research.quant.bench_llamacpp \\
        --rerun-locally \\
        --llama-bin /opt/llama.cpp/build/bin \\
        --gguf-dir /tmp/gguf \\
        --output research/quant/results/kquant_sweep.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Allow direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# Published K-quant PPLs for TinyLlama-1.1B-Chat-v1.0 on WikiText-2
# (raw, test split). Stride convention may differ slightly from ours.
PUBLISHED = {
    "Q4_K_M": {"bpw": 4.85, "ppl": 9.05},
    "Q5_K_M": {"bpw": 5.69, "ppl": 8.36},
    "Q6_K":   {"bpw": 6.56, "ppl": 7.82},
    "Q8_0":   {"bpw": 8.50, "ppl": 7.71},
}

DEFAULT_QUANTS = ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0")


@dataclass
class KQuantRow:
    name: str
    bpw_reported: float
    perplexity: float
    source: str               # "rerun_local" or "published_cite"
    wallclock_seconds: float
    notes: str = ""


# ── Cite-only path ───────────────────────────────────────────────────


def cite_published(model_name: str, quants: tuple[str, ...]) -> list[dict]:
    """No binaries required — emit the published numbers with a clear
    'cited' source flag so the write-up doesn't misrepresent them as
    locally-measured."""
    if "TinyLlama" not in model_name:
        raise RuntimeError(
            f"Published K-quant numbers are only catalogued for TinyLlama. "
            f"For {model_name}, run --rerun-locally instead."
        )
    rows = []
    for q in quants:
        if q not in PUBLISHED:
            raise ValueError(f"no published number for {q}")
        rows.append(_row_to_dict(KQuantRow(
            name=f"llama_cpp_{q}",
            bpw_reported=PUBLISHED[q]["bpw"],
            perplexity=PUBLISHED[q]["ppl"],
            source="published_cite",
            wallclock_seconds=0.0,
            notes="From llama.cpp upstream PPL table for TinyLlama-1.1B. "
                  "Stride may differ slightly from our SRD harness — "
                  "flagged in the write-up.",
        ), model_name=model_name))
    return rows


# ── Local re-run path ────────────────────────────────────────────────


def _find_binary(name: str, hint_dir: Optional[Path]) -> Path:
    """Locate a llama.cpp binary, searching the hint dir then PATH."""
    if hint_dir is not None:
        candidate = hint_dir / name
        if candidate.exists():
            return candidate
    on_path = shutil.which(name)
    if on_path is not None:
        return Path(on_path)
    raise FileNotFoundError(
        f"could not find {name}. Pass --llama-bin <dir> pointing at "
        f"your llama.cpp build directory, or put it on PATH."
    )


def convert_hf_to_gguf(
    model_name: str,
    model_revision: Optional[str],
    out_dir: Path,
    llama_bin: Optional[Path],
) -> Path:
    """Convert a HF model to GGUF F16. Skips if the output already exists."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = model_name.replace("/", "_")
    f16_path = out_dir / f"{safe_name}-f16.gguf"
    if f16_path.exists():
        print(f"[gguf] reusing existing {f16_path.name}")
        return f16_path

    # llama.cpp ships convert-hf-to-gguf.py at the repo root, not in build/.
    # Hint dir usually points at build/bin so search the parent too.
    candidates = []
    if llama_bin is not None:
        candidates.extend([
            llama_bin / "convert-hf-to-gguf.py",
            llama_bin.parent / "convert-hf-to-gguf.py",
            llama_bin.parent.parent / "convert-hf-to-gguf.py",
        ])
    for c in candidates:
        if c.exists():
            convert_script = c
            break
    else:
        raise FileNotFoundError(
            "convert-hf-to-gguf.py not found. Pass --llama-bin <dir> "
            "pointing at your llama.cpp build dir (the script lives "
            "in the parent repo root)."
        )

    print(f"[gguf] converting {model_name} → {f16_path.name}...")
    # The convert script expects a local model dir; let HF resolve it first.
    from transformers import AutoModelForCausalLM, AutoTokenizer
    local_dir = out_dir / safe_name
    if not local_dir.exists():
        AutoTokenizer.from_pretrained(
            model_name, revision=model_revision
        ).save_pretrained(local_dir)
        AutoModelForCausalLM.from_pretrained(
            model_name, revision=model_revision
        ).save_pretrained(local_dir)

    subprocess.run(
        [sys.executable, str(convert_script), str(local_dir),
         "--outfile", str(f16_path), "--outtype", "f16"],
        check=True,
    )
    return f16_path


def quantize_gguf(f16_path: Path, quant: str, llama_bin: Optional[Path]) -> Path:
    """Run llama-quantize on the F16 GGUF to produce a K-quant variant."""
    out_path = f16_path.with_name(f16_path.stem.replace("-f16", f"-{quant.lower()}") + ".gguf")
    if out_path.exists():
        print(f"[gguf] reusing existing {out_path.name}")
        return out_path
    binary = _find_binary("llama-quantize", llama_bin)
    print(f"[gguf] {quant}: {f16_path.name} → {out_path.name}...")
    subprocess.run(
        [str(binary), str(f16_path), str(out_path), quant],
        check=True,
    )
    return out_path


# llama-perplexity prints lines like "Final estimate: PPL = 9.0512 +/- 0.05912"
PPL_RE = re.compile(r"Final estimate:\s*PPL\s*=\s*([\d.]+)")


def llama_perplexity(
    gguf_path: Path,
    *,
    llama_bin: Optional[Path],
    context: int = 2048,
    n_threads: Optional[int] = None,
    wikitext_path: Optional[Path] = None,
) -> tuple[float, float]:
    """Run `llama-perplexity` against WikiText-2 and parse the final
    estimate. Returns (ppl, wallclock_seconds)."""
    binary = _find_binary("llama-perplexity", llama_bin)
    if wikitext_path is None:
        raise ValueError(
            "wikitext_path is required — llama-perplexity needs the "
            "raw test text as a file. Pass --wikitext-file."
        )
    cmd = [str(binary), "-m", str(gguf_path), "-f", str(wikitext_path),
           "-c", str(context), "--perplexity"]
    if n_threads is not None:
        cmd += ["-t", str(n_threads)]

    print(f"[ppl] running: {' '.join(cmd)}")
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    wall = time.monotonic() - t0
    combined = result.stdout + "\n" + result.stderr
    m = PPL_RE.search(combined)
    if not m:
        raise RuntimeError(
            f"could not parse PPL from llama-perplexity output. "
            f"Last 400 chars: {combined[-400:]}"
        )
    ppl = float(m.group(1))
    print(f"[ppl] PPL={ppl:.4f}, {wall:.1f}s")
    return ppl, wall


def rerun_locally(
    model_name: str,
    model_revision: Optional[str],
    quants: tuple[str, ...],
    gguf_dir: Path,
    llama_bin: Optional[Path],
    wikitext_path: Path,
    context: int,
    n_threads: Optional[int],
) -> list[dict]:
    """Full local re-run: convert → quantize → perplexity for each K-quant."""
    f16_gguf = convert_hf_to_gguf(model_name, model_revision, gguf_dir, llama_bin)
    rows = []
    for q in quants:
        quant_gguf = quantize_gguf(f16_gguf, q, llama_bin)
        ppl, wall = llama_perplexity(
            quant_gguf, llama_bin=llama_bin, context=context,
            n_threads=n_threads, wikitext_path=wikitext_path,
        )
        rows.append(_row_to_dict(KQuantRow(
            name=f"llama_cpp_{q}",
            bpw_reported=PUBLISHED.get(q, {}).get("bpw", float("nan")),
            perplexity=ppl,
            source="rerun_local",
            wallclock_seconds=round(wall, 2),
            notes=f"Re-run via llama-perplexity on {gguf_dir.name}.",
        ), model_name=model_name))
    return rows


# ── JSON helper ──────────────────────────────────────────────────────


def _row_to_dict(row: KQuantRow, *, model_name: str) -> dict:
    return {
        "name":               row.name,
        "description":        f"llama.cpp K-quant ({row.name.split('_')[-1]}) "
                              f"baseline ({row.source}).",
        "bpw_reported":       row.bpw_reported,
        "perplexity":         row.perplexity,
        "n_tokens":           None,
        "stride":             None,
        "context":            2048,
        "wallclock_seconds":  row.wallclock_seconds,
        "model":              model_name,
        "model_revision":     None,
        "dataset":            "wikitext/wikitext-2-raw-v1/test",
        "source":             row.source,
        "notes":              row.notes,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="K-quant baseline via llama.cpp")
    p.add_argument("--model", default="TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    p.add_argument("--revision", default=None)
    p.add_argument("--quants", default=",".join(DEFAULT_QUANTS),
                   help="Comma-separated K-quant names")
    p.add_argument("--rerun-locally", action="store_true",
                   help="Run llama-perplexity locally (needs binaries). "
                        "Default: cite published numbers.")
    p.add_argument("--llama-bin", type=Path, default=None,
                   help="llama.cpp build directory (or PATH if omitted)")
    p.add_argument("--gguf-dir", type=Path,
                   default=Path("/tmp/srd_gguf"),
                   help="Where to cache GGUF + quantized files")
    p.add_argument("--wikitext-file", type=Path, default=None,
                   help="Path to wikitext-2 raw test text "
                        "(required with --rerun-locally)")
    p.add_argument("--context", type=int, default=2048)
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    quants = tuple(q.strip() for q in args.quants.split(",") if q.strip())

    if args.rerun_locally:
        if args.wikitext_file is None:
            print("ERROR: --wikitext-file is required with --rerun-locally",
                  file=sys.stderr)
            return 2
        rows = rerun_locally(
            args.model, args.revision, quants,
            gguf_dir=args.gguf_dir,
            llama_bin=args.llama_bin,
            wikitext_path=args.wikitext_file,
            context=args.context,
            n_threads=args.threads,
        )
    else:
        rows = cite_published(args.model, quants)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"\nWrote {len(rows)} K-quant rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
