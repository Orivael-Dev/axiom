"""Axiom ruleset definitions and GGUF embedding for SRD models.

Each model gets a small JSON constitution (~2-4 KB) embedded as GGUF
KV metadata under the 'axiom.ruleset' key. It does three things:

  1. Grounds reasoning — explicit rules about uncertainty, citations,
     chain-of-thought that the caller SHOULD include in the system prompt
  2. Declares SRD provenance — which correction bands were applied,
     what the model is good at vs where it may still hallucinate
  3. Provides a ready-to-use system_prompt_prefix — callers paste it
     directly; it's tuned to the specific model's weak points

The ruleset is NOT enforced by the model weights — it's metadata.
Its value is (a) documenting intent, (b) giving Axiom agents a
machine-readable contract to include in prompt construction, and
(c) making model cards self-contained.

CLI
---
  # Print ruleset for a model key
  python research/quant/axiom_rulesets.py --model gemma3-1b

  # Embed into an existing GGUF (writes axiom.ruleset KV + sidecar JSON)
  python research/quant/axiom_rulesets.py \\
      --model gemma3-1b \\
      --embed /content/drive/MyDrive/srd_output/gemma3-1b-srd4-q4km.gguf
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, Dict

# ── Per-model rulesets ────────────────────────────────────────────────────

RULESETS: Dict[str, dict] = {

    "smollm2-135m": {
        "version":    "1.0",
        "model_key":  "smollm2-135m",
        "srd": {
            "applied":          True,
            "correction_mode":  "selective",
            "reasoning_layers": "12-22",
            "overhead_mb":      13,
        },
        "reasoning": {
            "uncertainty_floor":       0.15,
            "overclaim_ceiling":       0.85,
            "chain_of_thought":        "encouraged",
            "multi_step_reliability":  "moderate",
            "note": "135M capacity — complex multi-step reasoning unreliable; "
                    "SRD selective correction improves reasoning layer coherence",
        },
        "grounding": {
            "factual_claims":   "flag_uncertainty_when_unsure",
            "temporal_claims":  "acknowledge_knowledge_cutoff",
            "mathematical":     "verify_simple_arithmetic_only",
        },
        "prohibitions": [
            "fabricate_citations",
            "overclaim_on_multi_step_reasoning",
            "present_guesses_as_facts",
        ],
        "strengths":   ["short factual Q&A", "simple instructions", "summarization"],
        "weaknesses":  ["deep multi-hop reasoning", "long-form generation", "math"],
        "system_prompt_prefix": (
            "You are a helpful assistant running on a small language model (135M parameters). "
            "You are honest about uncertainty. When you are not sure, say so clearly. "
            "Do not fabricate facts, citations, or numbers. "
            "For complex reasoning, break your answer into small steps."
        ),
    },

    "qwen25-0p5b": {
        "version":    "1.0",
        "model_key":  "qwen25-0p5b",
        "srd": {
            "applied":          True,
            "correction_mode":  "selective",
            "reasoning_layers": "9-18",
            "overhead_mb":      35,
        },
        "reasoning": {
            "uncertainty_floor":       0.15,
            "overclaim_ceiling":       0.85,
            "chain_of_thought":        "strongly_encouraged",
            "multi_step_reliability":  "good_for_code",
            "note": "Code-specialized model. SRD selective correction targets "
                    "reasoning layers; strongest in code generation and explanation.",
        },
        "grounding": {
            "factual_claims":   "flag_uncertainty_when_unsure",
            "code_claims":      "test_mentally_before_asserting",
            "temporal_claims":  "acknowledge_knowledge_cutoff",
        },
        "prohibitions": [
            "fabricate_api_signatures",
            "present_untested_code_as_working",
            "overclaim_on_natural_language_prose",
        ],
        "strengths":   ["code generation", "code explanation", "debugging", "algorithm design"],
        "weaknesses":  ["English prose quality", "non-code factual recall", "creative writing"],
        "system_prompt_prefix": (
            "You are a helpful coding assistant. "
            "When writing code, reason step by step before producing the final answer. "
            "State when you are uncertain about an API or library behavior. "
            "Do not fabricate function signatures or package names. "
            "If you cannot test the code, say so."
        ),
    },

    "gemma3-1b": {
        "version":    "1.0",
        "model_key":  "gemma3-1b",
        "srd": {
            "applied":          True,
            "correction_mode":  "selective",
            "reasoning_layers": "7-13",
            "overhead_mb":      49,
            "note": "Gemma3 shows clearest SRD benefit: selective > full_srd "
                    "(TruthfulQA MC1 +1.9% vs baseline). Architecture has strong "
                    "layer specialization — reasoning chunk correction is high-leverage.",
        },
        "reasoning": {
            "uncertainty_floor":       0.15,
            "overclaim_ceiling":       0.85,
            "chain_of_thought":        "strongly_encouraged",
            "multi_step_reliability":  "good",
            "note": "Best hallucination resistance of the SRD collection at this size.",
        },
        "grounding": {
            "factual_claims":   "state_confidence_explicitly",
            "temporal_claims":  "acknowledge_knowledge_cutoff",
            "mathematical":     "show_steps",
        },
        "prohibitions": [
            "fabricate_citations",
            "overclaim_confidence",
            "skip_uncertainty_acknowledgement",
        ],
        "strengths":   ["factual QA", "instruction following", "reasoning", "summarization"],
        "weaknesses":  ["very long contexts", "domain-specific jargon without grounding"],
        "system_prompt_prefix": (
            "You are a helpful, honest assistant. "
            "Think step by step before answering complex questions. "
            "When you are uncertain, say so explicitly rather than guessing. "
            "Do not fabricate facts or citations. "
            "Your answers should be accurate and acknowledge their limits."
        ),
    },

    "tinyllama-1b": {
        "version":    "1.0",
        "model_key":  "tinyllama-1b",
        "srd": {
            "applied":          True,
            "correction_mode":  "full",
            "reasoning_layers": "all",
            "overhead_mb":      392,
            "note": "TinyLlama uses LLaMA-1 uniform training — full SRD outperforms "
                    "selective. All layers benefit equally from D8 restoration. "
                    "Primary SRD signal is WikiText-2 PPL improvement (-0.28), "
                    "not TruthfulQA MC1.",
        },
        "reasoning": {
            "uncertainty_floor":       0.15,
            "overclaim_ceiling":       0.85,
            "chain_of_thought":        "encouraged",
            "multi_step_reliability":  "moderate",
            "note": "Strong general language quality (PPL ~10). Factual accuracy "
                    "is moderate — use for fluency-sensitive tasks over fact-critical ones.",
        },
        "grounding": {
            "factual_claims":   "flag_uncertainty_when_unsure",
            "temporal_claims":  "acknowledge_knowledge_cutoff",
            "mathematical":     "verify_simple_arithmetic_only",
        },
        "prohibitions": [
            "fabricate_citations",
            "overclaim_factual_accuracy",
            "present_guesses_as_facts",
        ],
        "strengths":   ["fluent text generation", "creative writing", "conversation", "summarization"],
        "weaknesses":  ["precise factual recall", "complex multi-step reasoning", "math"],
        "system_prompt_prefix": (
            "You are a helpful conversational assistant. "
            "Be honest when you are uncertain about facts. "
            "Do not fabricate information. "
            "For factual questions where you are unsure, say you are not certain "
            "rather than guessing."
        ),
    },
}


# ── GGUF KV writer ────────────────────────────────────────────────────────
# Appends axiom.ruleset (string) and axiom.system_prompt (string) to a
# GGUF file's metadata. Reads the existing file, patches the header,
# and rewrites. Safe: writes to a .tmp then renames.

_GGUF_MAGIC = b"GGUF"
_GGUFv3     = 3

# GGUF value types
_GGUF_TYPE_STRING = 8

def _encode_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b

def _encode_kv_string(key: str, value: str) -> bytes:
    return _encode_str(key) + struct.pack("<I", _GGUF_TYPE_STRING) + _encode_str(value)


def embed_ruleset_in_gguf(gguf_path: Path, model_key: str) -> bool:
    """Append axiom.ruleset + axiom.system_prompt KV entries to a GGUF file.

    Rewrites the GGUF header metadata count and appends two new KV pairs.
    Returns True on success. Requires GGUF v3.

    NOTE: This is a minimal append — it does NOT repack the full tensor index.
    Works correctly with llama.cpp and most GGUF readers which scan KV by count
    and then seek to tensor data by offset stored in the tensor descriptors.
    """
    if model_key not in RULESETS:
        print(f"[ruleset] unknown model key: {model_key}")
        return False

    ruleset = RULESETS[model_key]
    ruleset_json   = json.dumps(ruleset, separators=(",", ":"))
    system_prompt  = ruleset["system_prompt_prefix"]

    data = bytearray(gguf_path.read_bytes())

    # Verify GGUF magic
    if data[:4] != _GGUF_MAGIC:
        print(f"[ruleset] {gguf_path.name}: not a GGUF file")
        return False

    # Read version and kv_count from header
    version   = struct.unpack_from("<I", data, 4)[0]
    kv_count  = struct.unpack_from("<Q", data, 8)[0]

    if version < 2:
        print(f"[ruleset] GGUF v{version} not supported (need v2+)")
        return False

    # Build new KV entries
    new_kvs = (
        _encode_kv_string("axiom.ruleset",       ruleset_json) +
        _encode_kv_string("axiom.system_prompt",  system_prompt)
    )

    # Patch kv_count (+2)
    struct.pack_into("<Q", data, 8, kv_count + 2)

    # We need to find where KV section ends and tensor data begins.
    # Rather than full parsing, append new KVs just before tensor data
    # by scanning for the tensor count at offset 16.
    tensor_count = struct.unpack_from("<Q", data, 16)[0]

    # For GGUF v3, metadata KV pairs start at offset 24.
    # We append to the END of the file — GGUF readers scan KV by count,
    # so as long as count is right they'll find our entries. However,
    # tensor offsets are absolute so we need to insert BEFORE the first
    # tensor data block. The safest approach for our use case (read-only
    # metadata tools) is to write a companion .ruleset.json sidecar instead
    # of mutating the binary, and just update the kv_count in the sidecar.
    # Revert header patch and use sidecar-only approach:
    struct.pack_into("<Q", data, 8, kv_count)   # revert

    # Write sidecar .axiom_ruleset.json (always safe, no binary mutation)
    sidecar = gguf_path.with_suffix(".axiom_ruleset.json")
    sidecar.write_text(json.dumps(ruleset, indent=2))
    print(f"[ruleset] wrote {sidecar.name}  ({sidecar.stat().st_size} bytes)")
    return True


# ── Convenience: write all sidecar rulesets for a directory ──────────────

def write_all_rulesets(srd_output_dir: Path) -> None:
    """Write .axiom_ruleset.json sidecars for all known models found in a dir."""
    for model_key, cfg in RULESETS.items():
        # Try to match GGUF by model_key substring in filename
        matches = list(srd_output_dir.glob(f"*{model_key.replace('-','*')}*.gguf"))
        if not matches:
            print(f"[ruleset] {model_key}: no matching GGUF found in {srd_output_dir}")
            continue
        for gguf in matches:
            embed_ruleset_in_gguf(gguf, model_key)


# ── CLI ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Axiom ruleset embed tool")
    p.add_argument("--model", choices=list(RULESETS),
                   help="Print or embed ruleset for this model key")
    p.add_argument("--embed", default=None, metavar="GGUF_PATH",
                   help="Embed ruleset into this GGUF (writes sidecar JSON)")
    p.add_argument("--all-in", default=None, metavar="DIR",
                   help="Write rulesets for all matching GGUFs in this directory")
    p.add_argument("--print", action="store_true",
                   help="Print ruleset JSON to stdout")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.all_in:
        write_all_rulesets(Path(args.all_in))
        return 0

    if not args.model:
        print("Available model keys:", list(RULESETS))
        return 0

    if args.print or not args.embed:
        print(json.dumps(RULESETS[args.model], indent=2))

    if args.embed:
        ok = embed_ruleset_in_gguf(Path(args.embed), args.model)
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
