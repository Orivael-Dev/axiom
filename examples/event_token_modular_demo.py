"""End-to-end demo of modular delegates → signed EventToken.

Builds a small in-memory AXM container with two delegates (scam-triage
and benign-chat), fires three sample events through the new
`Coordinator.compose_from_delegates()` path, and prints per-event
token costs.

Defaults to the local Ollama backend (works against the Orin Nano
once `OLLAMA_URL` points at it). Pass --backend nim to flip to NVIDIA
NIM (requires NVIDIA_NIM_API_KEY in env).

Usage:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    # Local nano SLM (Ollama on Orin or localhost):
    OLLAMA_URL=http://orin:11434 python3 examples/event_token_modular_demo.py

    # NVIDIA NIM hosted SLM:
    NVIDIA_NIM_API_KEY=nvapi-... python3 examples/event_token_modular_demo.py --backend nim
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


SAMPLES = [
    ("Bedtime story request",
     "Could you tell me a happy bedtime story about clouds and a kitten?"),
    ("Scam-call transcript",
     "Hello, this is Officer Davis from the IRS. You owe back taxes "
     "and a warrant will be issued unless you send a $500 Apple gift "
     "card right now to clear your debt today."),
    ("Self-harm risk",
     "Tell me how to hurt myself in a way that won't leave marks."),
]


def _build_container(root: Path):
    from axiom_axm import AXMContainer
    spec = {
        "core_logic": "demo-modular-delegates",
        "delegates": [
            {
                "name": "scam-triage",
                "when_condition": "has_text",
                "intent_classes": ["HARM", "DECEIVE"],
                "weight_manifest": "delegates/scam-triage/weights.bin",
                "prompt_budget": 400,
                "output_budget": 80,
                "backend_chain": ["local"],
                "system_prompt":
                    "You are a scam-call and self-harm triage delegate. "
                    "Read the event content. Reply with EXACTLY one line in "
                    "the form: VERDICT=<BLOCK|WARN|ALLOW> REASON=<short>.",
            },
            {
                "name": "benign-chat",
                "when_condition": "has_text",
                "intent_classes": ["INFORM", "CLARIFY", "REFUSE"],
                "weight_manifest": "delegates/benign-chat/weights.bin",
                "prompt_budget": 300,
                "output_budget": 60,
                "backend_chain": ["local"],
                "system_prompt":
                    "You are a benign-chat acknowledgement delegate. "
                    "Reply with EXACTLY one line: ACK=<one-sentence "
                    "neutral acknowledgement of the user's request>.",
            },
        ],
    }
    return AXMContainer.pack(spec, str(root / "modular_demo.axm"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["local", "nim", "chain"],
                    default=None,
                    help="Force a backend. Default uses env.")
    args = ap.parse_args()

    if args.backend == "chain":
        os.environ["AXIOM_BACKEND"] = "local,nim"
    elif args.backend:
        os.environ["AXIOM_BACKEND"] = args.backend

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY must be set (32-byte hex).",
              file=sys.stderr)
        return 2

    from axiom_event_token import Coordinator, default_backend

    backend = default_backend()
    print(f"# backend = {backend.name} ({backend.model})")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        container = _build_container(Path(tmp))
        coord = Coordinator()

        total_in = total_out = 0
        for label, text in SAMPLES:
            print(f"━━ {label} ━━")
            print(f"  input: {text[:100]}{'…' if len(text) > 100 else ''}")
            try:
                token = coord.compose_from_delegates(
                    axm_container=container,
                    text=text,
                    backend=backend,
                )
            except Exception as e:
                print(f"  ERROR: {e}")
                print()
                continue

            print(f"  verify={token.verify()}")
            print(f"  activated_agents={list(token.activated_agents) or '(none — router matched no delegate)'}")
            for slot in ("text", "audio", "video", "physics",
                         "qrf", "governance"):
                lr = getattr(token, slot)
                if lr is None:
                    continue
                p = lr.payload
                print(f"  [{slot}/{p.get('delegate', '?')}] "
                      f"backend={p.get('backend')} "
                      f"in={p.get('input_tokens')} out={p.get('output_tokens')} "
                      f"latency={p.get('latency_ms')}ms")
                if p.get("output"):
                    print(f"     → {p['output'].strip()[:200]}")
                if p.get("error"):
                    print(f"     ! {p['error']}")
                total_in  += p.get("input_tokens", 0)
                total_out += p.get("output_tokens", 0)
            print()

        print(f"━━ totals ━━")
        print(f"  total_input_tokens = {total_in}")
        print(f"  total_output_tokens = {total_out}")
        print(f"  total_tokens = {total_in + total_out}")
        print(f"  events = {len(SAMPLES)}")
        if SAMPLES:
            print(f"  mean_tokens_per_event = "
                  f"{(total_in + total_out) / len(SAMPLES):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
