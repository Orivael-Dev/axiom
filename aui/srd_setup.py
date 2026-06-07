"""Route A: serve an SRD-quantized GGUF through Ollama and point Aria at it.

Prerequisite (run once, from the research tree on `main`/`orvl`):

    python -m research.quant.axm_to_gguf \
        --container artifacts/tinyllama_srd_7bpw_REAL.axm \
        --gguf-out  artifacts/tinyllama_srd.gguf \
        --llamacpp  ~/llama.cpp --quant none

That reconstructs FP16 from the signed .axm and writes a GGUF. SRD's win is
the compact *signed delivery* (~13 bpw on disk); llama.cpp runs the
reconstructed weights, so generation itself is FP16-compute (no packed-forward
kernel exists yet — see docs/SRD_RESULTS.md).

Then this tool does the Aria-facing half:

    python -m aui.srd_setup --gguf artifacts/tinyllama_srd.gguf --name aria-srd

  1. render a Modelfile next to the GGUF
  2. `ollama create <name>` so Ollama serves it at its OpenAI-compatible API
  3. enable Aria's LLM + set the persona's base_model to <name>

After this, Aria speaks through the SRD model on her next turn — no restart,
because base_model is resolved live (companion._persona_model).

For a llama.cpp `llama-server` instead of Ollama, pass --no-ollama and
--base-url http://localhost:8080/v1; this tool then only flips Aria's config.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

OLLAMA_BASE_URL = "http://localhost:11434/v1"  # matches settings._DEFAULT_LLM


def render_modelfile(gguf_path: str, *, temperature: float = 0.7,
                     system: Optional[str] = None,
                     template: Optional[str] = None) -> str:
    """Minimal Modelfile: FROM the GGUF + a temperature default. We deliberately
    do NOT hardcode a chat TEMPLATE/stops — Ollama applies the one embedded in
    the GGUF metadata, which is correct far more often than a guessed format.
    Override with --template only if your GGUF carries no chat template."""
    abs_gguf = str(Path(gguf_path).expanduser().resolve())
    lines = [f"FROM {abs_gguf}", f"PARAMETER temperature {temperature}"]
    if template:
        lines.append(f'TEMPLATE """{template}"""')
    if system:
        lines.append(f'SYSTEM """{system}"""')
    return "\n".join(lines) + "\n"


def ollama_create(name: str, modelfile_path: str) -> None:
    """Register the model with the local Ollama daemon."""
    subprocess.run(["ollama", "create", name, "-f", modelfile_path], check=True)


def configure_aria(name: str, *, base_url: str = OLLAMA_BASE_URL) -> dict:
    """Enable Aria's LLM at base_url and make `name` her persona's base_model.
    Returns a summary {base_url, base_model, persona_token_signature}."""
    from aui.settings import update_llm
    from aui.persona import PersonaStore

    update_llm({"enabled": True, "base_url": base_url})
    tok = PersonaStore().save({"base_model": name})
    return {
        "base_url": base_url,
        "base_model": tok.base_model,
        "persona_token_signature": tok.token_signature,
    }


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Wire an SRD GGUF into Aria via Ollama.")
    p.add_argument("--gguf", help="path to the SRD-reconstructed .gguf "
                   "(from research.quant.axm_to_gguf)")
    p.add_argument("--name", default="aria-srd",
                   help="Ollama model id / Aria base_model (default: aria-srd)")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--system", default=None,
                   help="optional SYSTEM line baked into the Modelfile "
                        "(Aria already injects her persona, so usually skip)")
    p.add_argument("--template", default=None,
                   help="override chat TEMPLATE (only if the GGUF lacks one)")
    p.add_argument("--base-url", default=OLLAMA_BASE_URL,
                   help="OpenAI-compatible base_url Aria should call")
    p.add_argument("--no-ollama", action="store_true",
                   help="skip `ollama create` (e.g. when serving via llama-server); "
                        "only flip Aria's config")
    p.add_argument("--modelfile-out", default=None,
                   help="where to write the Modelfile (default: alongside the GGUF)")
    args = p.parse_args(argv)

    if not args.no_ollama:
        if not args.gguf:
            p.error("--gguf is required unless --no-ollama is set")
        gguf = Path(args.gguf).expanduser()
        if not gguf.exists():
            print(f"error: GGUF not found: {gguf}", file=sys.stderr)
            return 2
        mf_path = Path(args.modelfile_out) if args.modelfile_out \
            else gguf.with_suffix(".Modelfile")
        mf_path.write_text(render_modelfile(
            str(gguf), temperature=args.temperature,
            system=args.system, template=args.template), encoding="utf-8")
        print(f"• wrote Modelfile → {mf_path}")
        try:
            ollama_create(args.name, str(mf_path))
            print(f"• ollama create {args.name} ✓")
        except FileNotFoundError:
            print("error: `ollama` not on PATH — install Ollama or use --no-ollama "
                  "with a llama-server --base-url.", file=sys.stderr)
            return 3
        except subprocess.CalledProcessError as e:
            print(f"error: `ollama create` failed ({e.returncode})", file=sys.stderr)
            return e.returncode

    summary = configure_aria(args.name, base_url=args.base_url)
    print(f"• Aria → enabled @ {summary['base_url']}, "
          f"base_model={summary['base_model']}")
    print(f"  persona token {summary['persona_token_signature'][:16]}… "
          f"(lineage updated)")
    print("Aria speaks through the SRD model on her next turn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
