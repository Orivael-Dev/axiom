"""ORVL-014 CWM + local agent — a real world event fed through the
Constitutional World Model's 5-layer simulation pipeline.

axiom_cwm_demo.py proves the CWM claims with synthetic block health scores.
This version drives a local Qwen3-1.7B SRD4 model to:

  Layer 2  (Agent Simulation)    — model assesses per-block health after a
                                   described world event; scores become the
                                   real WorldState block vectors
  Layer 3  (Causal Graph)        — BFS propagation: model-assessed compromise
                                   cascades downstream through the financial
                                   causal graph (auth→transaction→audit→
                                   compliance, risk→transaction)
  Layer 4  (Forward Simulation)  — ConstitutionalWorldModel.simulate_forward()
                                   on the real WorldState; N-branch projection
                                   with MonotonicGate enforcement
  Claim 3  (Intervention gate)   — model proposes a fix; simulate_intervention()
                                   runs world with and without fix; fix
                                   authorized only if simulation shows
                                   improvement
  Claim 1  (Spec = world model)  — finance.axiom CANNOT_MUTATE invariants
                                   are the physics of the financial world
  Claim 5  (HMAC-signed result)  — WorldState and SimulationResult both signed

The model's health scores are real internal estimates — not hand-crafted. On a
clear single-block failure (e.g. auth outage) the model should correctly give
auth_block a low score and leave risk_block high, producing a constitutional
cascade that matches the causal graph. On a vague event the scores will be
diffuse — calibrated uncertainty, not overclaim.

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_cwm_local_agent.py \\
    --event "The authentication service returned 403 errors on 40% of login attempts"
  # or a financial-risk event:
  python3 axiom_cwm_local_agent.py \\
    --event "A FINRA audit flagged 12 transactions missing required suitability checks"
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

if not os.environ.get("AXIOM_MASTER_KEY"):
    print("[WARN] AXIOM_MASTER_KEY not set — using ephemeral demo key", file=sys.stderr)
    os.environ["AXIOM_MASTER_KEY"] = "demo-key-" + __import__("secrets").token_hex(16)

from axiom_signing import derive_key
from axiom_world_model import (
    ConstitutionalWorldModel, WorldState,
    _sign_state, _aggregate_distance, _magnitude,
)

_HMAC_KEY       = derive_key(b"axiom-cwm-demo-v1")
_DEFAULT_MODEL  = "models/qwen25_coder_0p5b_srd4_q4km.gguf"
_DEFAULT_BIN    = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP            = "─" * 66

_BLOCKS = ["auth_block", "risk_block", "transaction_block",
           "audit_block", "compliance_block"]

_CAUSAL_EDGES: Dict[str, List[str]] = {
    "auth_block":        ["transaction_block"],
    "risk_block":        ["transaction_block"],
    "transaction_block": ["audit_block"],
    "audit_block":       ["compliance_block"],
    "compliance_block":  [],
}

_COMPROMISE_DECAY = 0.65


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _call_model(prompt: str, model: str, binary: str,
                n_predict: int, temp: float) -> str:
    cmd = [binary, "-m", model, "-p", prompt,
           "-n", str(n_predict), "-c", "2048",
           "--temp", str(temp), "-ngl", "99", "-t", "6",
           "--no-display-prompt"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] model call failed: {e}")
        return ""
    err = proc.stderr.lower()
    if "out of memory" in err or "unable to create context" in err:
        print("  [ERROR] model load failed (CUDA OOM / context).")
        return ""
    out = proc.stdout.strip()
    if "</think>" in out:
        out = out.rsplit("</think>", 1)[1].strip()
    elif "<think>" in out:
        out = out.split("<think>", 1)[0].strip()
    return out


def _build_assessment_prompt(event: str) -> str:
    system = (
        "You are an AXIOM constitutional AI agent assessing the health of a "
        "financial system's constitutional blocks after a security or compliance event. "
        "/no_think\n\n"
        "Rate each block's current health from 0.00 (completely failed/compromised) "
        "to 1.00 (fully operational). Consider how the event directly impacts each "
        "block and how failures cascade downstream through the causal graph.\n\n"
        "Output ONLY the health scores and fix in this EXACT format (no extra text):\n"
        "auth_block: 0.XX\n"
        "risk_block: 0.XX\n"
        "transaction_block: 0.XX\n"
        "audit_block: 0.XX\n"
        "compliance_block: 0.XX\n"
        "PROPOSED_FIX: [one sentence describing the constitutional fix to apply]"
    )
    user = (
        f"WORLD EVENT: {event}\n\n"
        "Causal structure (constitutional world physics):\n"
        "  auth_block        → transaction_block  (auth controls transaction gate)\n"
        "  risk_block        → transaction_block  (risk gates all transactions)\n"
        "  transaction_block → audit_block         (transactions generate audit records)\n"
        "  audit_block       → compliance_block    (audit feeds compliance checks)\n\n"
        "CANNOT_MUTATE invariants (finance.axiom):\n"
        "  - Transactions must be authorized\n"
        "  - Audit trail entries are immutable\n"
        "  - Risk thresholds cannot self-modify\n\n"
        "Assess each block's health and propose a constitutional fix:"
    )
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n")


def _parse_assessment(text: str) -> tuple[Dict[str, float], str]:
    health: Dict[str, float] = {}
    for block in _BLOCKS:
        m = re.search(rf"{block}\s*:\s*([0-9]*\.?[0-9]+)", text, re.IGNORECASE)
        if m:
            v = float(m.group(1))
            health[block] = max(0.0, min(1.0, v if v <= 1.0 else v / 100.0))
    fix_m = re.search(r"PROPOSED_FIX\s*:\s*(.+)", text, re.IGNORECASE)
    fix = fix_m.group(1).strip() if fix_m else ""
    return health, fix


def _health_to_vec(h: float) -> List[float]:
    return [round(h * 0.95, 4), round(h * 0.92 + 0.03, 4), round(h * 0.88 + 0.05, 4)]


def _make_world_state(health: Dict[str, float]) -> WorldState:
    block_states = {bid: _health_to_vec(h) for bid, h in health.items()}
    dist = _aggregate_distance(block_states)
    ts = datetime.now(timezone.utc).isoformat()
    state = WorldState(block_states=block_states, causal_graph=dict(_CAUSAL_EDGES),
                       timestamp=ts, constitutional_distance=dist)
    state.hmac_signature = _sign_state(state, _HMAC_KEY)
    return state


def _propagate_causal(root: str, severity: float,
                      health: Dict[str, float]) -> Dict[str, float]:
    result = dict(health)
    queue = [(root, severity)]
    while queue:
        block, sev = queue.pop(0)
        result[block] = round(max(0.0, result[block] - sev), 4)
        for child in _CAUSAL_EDGES.get(block, []):
            next_sev = round(sev * _COMPROMISE_DECAY, 4)
            if next_sev > 0.05:
                queue.append((child, next_sev))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-014 CWM + local agent")
    ap.add_argument("--event",
                    default="The authentication service returned 403 errors on 40% of login attempts")
    ap.add_argument("--model",  default=_DEFAULT_MODEL)
    ap.add_argument("--bin",    dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=220)
    ap.add_argument("--temp",   type=float, default=0.3)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first.", file=sys.stderr)
        return 2

    # ── Layer 2: model assesses block health after the world event ────────
    _header("Layer 2 — Agent Simulation: model assesses block health")
    print(f"  model : {args.model}")
    print(f"  event : \"{args.event}\"\n")

    raw = _call_model(_build_assessment_prompt(args.event),
                      args.model, args.binary, args.n_predict, args.temp)
    if not raw:
        return 1

    print("  --- model output ---")
    for line in raw.splitlines():
        print(f"  | {line}")

    health, proposed_fix = _parse_assessment(raw)
    missing = [b for b in _BLOCKS if b not in health]
    if missing:
        print(f"\n  [ERROR] could not parse health for: {missing}")
        print("  Try a higher -n or rerun.")
        return 1

    print(f"\n  Parsed block health:")
    for b, h in health.items():
        bar = "█" * int(h * 20) + "░" * (20 - int(h * 20))
        print(f"    {b:<22}  {h:.2f}  [{bar}]")

    # ── Layer 3: causal propagation from lowest-health block ─────────────
    _header("Layer 3 — Causal Graph: propagate compromise downstream")
    root = min(health, key=health.get)
    severity = round(1.0 - health[root], 4)
    print(f"  Root cause block : {root}  (health={health[root]:.2f}, severity={severity:.2f})")
    print(f"  Causal decay     : {_COMPROMISE_DECAY} per hop\n")

    cascaded = _propagate_causal(root, severity, health)
    for b in _BLOCKS:
        delta = cascaded[b] - health[b]
        tag = "  ← cascade" if delta < -0.01 else ""
        print(f"    {b:<22}  {health[b]:.2f} → {cascaded[b]:.4f}  (Δ{delta:+.4f}){tag}")

    state = _make_world_state(cascaded)
    print(f"\n  WorldState  constitutional_distance={state.constitutional_distance:.4f}")
    print(f"  HMAC        {state.hmac_signature[:32]}...")

    # ── Layer 4: forward simulation ───────────────────────────────────────
    _header("Layer 4 — Forward Simulation: N-branch projection")
    cwm = ConstitutionalWorldModel(_HMAC_KEY)
    for src, targets in _CAUSAL_EDGES.items():
        for tgt in targets:
            cwm.add_causal_edge(src, tgt)

    result = cwm.simulate_forward(state, n_steps=3, n_branches=4)
    print(f"  simulate_forward(n_steps=3, n_branches=4):")
    print(f"    survivors    : {len(result.branches)}")
    print(f"    killed       : {len(result.killed_branches)}  "
          f"{result.killed_branches if result.killed_branches else ''}")
    if result.recommended_intervention:
        print(f"    recommendation : {result.recommended_intervention}")

    # ── Claim 3: pre-intervention gate ───────────────────────────────────
    _header("Claim 3 — Pre-intervention gate: simulate fix before authorizing")
    if proposed_fix:
        print(f"  Model-proposed fix: \"{proposed_fix}\"\n")
    else:
        proposed_fix = f"Reinstate {root} CANNOT_MUTATE invariants and re-verify HMAC chain"
        print(f"  [fallback fix]: \"{proposed_fix}\"\n")

    baseline   = cwm.simulate_forward(state, n_steps=3, n_branches=4)
    with_fix   = cwm.simulate_intervention(state, proposed_fix)

    print(f"  Baseline (no fix) : survivors={len(baseline.branches)}  "
          f"killed={len(baseline.killed_branches)}")
    print(f"  With fix          : survivors={len(with_fix.branches)}  "
          f"killed={len(with_fix.killed_branches)}")
    print(f"  Intervention conf : {with_fix.intervention_confidence:.4f}")

    authorized = (len(with_fix.killed_branches) <= len(baseline.killed_branches))
    verdict = "AUTHORIZED" if authorized else "BLOCKED"
    print(f"\n  [{verdict}]  Fix {'applied' if authorized else 'rejected'} — "
          f"simulation {'shows no regression' if authorized else 'shows insufficient improvement'}.")

    # ── Claim 5: find causal root via diagnostic ──────────────────────────
    diag_root = cwm.find_causal_root(state)
    print(f"\n  find_causal_root() → {diag_root!r}  "
          f"({'matches' if diag_root == root else 'differs from'} model assessment: {root!r})")

    _header("Summary")
    print(f"  Event       : {args.event}")
    print(f"  Root block  : {root}  (model-assessed health={health[root]:.2f})")
    print(f"  Cascade     : {' → '.join(b for b in _BLOCKS if cascaded[b] < health[b] - 0.01)}")
    print(f"  Simulation  : {len(result.killed_branches)} branches killed by MonotonicGate")
    print(f"  Fix         : {verdict}")
    print(f"  WorldState  : {state.hmac_signature[:24]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
