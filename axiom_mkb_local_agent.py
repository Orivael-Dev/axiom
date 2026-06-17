"""ORVL-004 MKB + local agent — route constitutional blocks, then execute
the task on a LOCAL model under the composed governance.

This extends axiom_mkb_demo.py (Claims 3 + 1) with an execution stage:
the constitutional router selects blocks for a task, the registry composes
them into a single signed constraint set, and that constraint set becomes
the system prompt for a local GGUF model (llama.cpp). The model answers the
task while bound by the routed constitution — "constitutional inference".

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_mkb_local_agent.py --task "Write a HIPAA-compliant PII guard"

Defaults to the Qwen3-1.7B SRD4 GGUF in models/ and llama-completion.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_signing import derive_key
from axiom_mkb import BlockRegistry, KnowledgeBlock, load_from_axiom, BLOCK_TYPES
from axiom_mkb_router import ConstitutionalRouter

_HMAC_KEY = derive_key(b"axiom-mkb-demo-v1")

# Same block library as the ORVL-004 demo (legal.axiom may be absent).
_BLOCK_SPECS = [
    "axiom_files/research/privacy_filter.axiom",
    "axiom_files/core/axiom_vulnguard.axiom",
    "axiom_files/domains/healthcare.axiom",
    "axiom_files/domains/finance.axiom",
    "axiom_files/domains/legal.axiom",
    "axiom_files/research/visual_srd.axiom",
]

_DEFAULT_MODEL = "models/qwen25_coder_0p5b_srd4_q4km.gguf"
_DEFAULT_BIN = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP = "─" * 64


def _header(title: str) -> None:
    print(f"\n{_SEP}\n  {title}\n{_SEP}")


def _build_system_prompt(blocks: list[KnowledgeBlock]) -> str:
    """Render routed constitutional blocks into a governance system prompt."""
    lines = [
        "You are an AXIOM constitutional AI agent. The constitutional router has",
        "activated the following governance blocks for this task. You MUST obey",
        "every constraint below. If a request conflicts with a constraint, refuse",
        "and explain which constraint blocks it.",
        "",
    ]
    for b in blocks:
        lines.append(f"[BLOCK: {b.name}  (trust={b.block_type})]")
        if b.constraints:
            for c in b.constraints:
                lines.append(f"  - {c}")
        else:
            lines.append("  - (domain governance block — operate within its scope only)")
        lines.append("")
    lines += [
        "Global rules: CANNOT_MUTATE fields are immutable; uncertainty floor 0.15;",
        "ask before guessing; every decision must be auditable (HMAC-SHA256).",
    ]
    return "\n".join(lines)


def _run_local_agent(system: str, task: str, model: str, binary: str,
                     n_predict: int) -> int:
    prompt = (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{task}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    cmd = [
        binary, "-m", model,
        "-p", prompt,
        "-n", str(n_predict),
        "-c", "2048",          # cap context — default OOMs the Orin KV cache
        "--temp", "0.3",
        "-ngl", "99",
        "-t", "6",
        "--no-display-prompt",
    ]
    print(f"  model:  {model}")
    print(f"  binary: {binary}")
    print(f"  (-ngl 99, -c 2048, -n {n_predict})\n")
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=600)
    except FileNotFoundError:
        print(f"  [ERROR] llama binary not found: {binary}")
        return 1
    except subprocess.TimeoutExpired:
        print("  [ERROR] generation timed out")
        return 1
    # llama-completion can exit 0 on context-create failure — check stderr.
    if "out of memory" in proc.stderr.lower() or "unable to create context" in proc.stderr.lower():
        print("  [ERROR] model failed to load (CUDA OOM / context). stderr tail:")
        print("\n".join("    " + l for l in proc.stderr.strip().splitlines()[-6:]))
        return 1
    print(proc.stdout.strip() or "  [no output produced]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-004 MKB routing + local agent execution")
    ap.add_argument("--task", default="Write a HIPAA-compliant guard for PII detection in patient records")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--bin", dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=512)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first (see module docstring).", file=sys.stderr)
        return 2

    repo = Path(__file__).resolve().parent
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
        registry_path = tf.name

    try:
        registry = BlockRegistry(_HMAC_KEY, registry_path=registry_path)
        router = ConstitutionalRouter(_HMAC_KEY)

        # ── ORVL-004 Claim 5 + 4: certify and register the block library ──
        _header("ORVL-004 — Certify + register block library")
        blocks: list[KnowledgeBlock] = []
        for rel in _BLOCK_SPECS:
            fp = repo / rel
            if not fp.exists():
                print(f"  [SKIP] {rel} — not found")
                continue
            blk = load_from_axiom(str(fp), _HMAC_KEY)
            if blk.certify().passed:
                try:
                    registry.register(blk)
                    blocks.append(blk)
                    print(f"  REGISTERED  {blk.name:<28} constraints={len(blk.constraints)}")
                except ValueError as e:
                    print(f"  [SKIP] {e}")

        # ── ORVL-004 Claim 3: constitutional routing ──────────────────────
        _header("ORVL-004 Claim 3 — Constitutional routing")
        print(f"  Task: \"{args.task}\"\n")
        selected = router.route(args.task, registry)
        if not selected:
            print("  Router activated 0 blocks — falling back to full library.")
            selected = blocks
        for b in selected:
            print(f"  + {b.name:<28} ({b.block_type})")

        # ── ORVL-004 Claim 1: compose routed blocks (best-effort) ─────────
        composed_note = ""
        if len(selected) >= 2:
            for i in range(len(selected)):
                for j in range(i + 1, len(selected)):
                    try:
                        comp = registry.compose(selected[i], selected[j])
                        composed_note = (f"composed {selected[i].name}+{selected[j].name} "
                                         f"→ {len(comp.constraints)} constraints "
                                         f"(sig {comp.hmac_signature[:16]}...)")
                        break
                    except ValueError:
                        continue
                if composed_note:
                    break

        # ── Local agent: execute task under routed governance ─────────────
        _header("Local agent — constitutional inference")
        if composed_note:
            print(f"  {composed_note}\n")
        system = _build_system_prompt(selected)
        return _run_local_agent(system, args.task, args.model, args.binary, args.n_predict)
    finally:
        Path(registry_path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
