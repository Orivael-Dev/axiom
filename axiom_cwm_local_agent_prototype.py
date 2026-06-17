"""ORVL-014 CWM + local agent — POINT-OF-IMPACT prototype.

Prototype variant of axiom_cwm_local_agent.py. The original asks the model to
score ALL five blocks after an event, which collapses Layers 2 and 3 into one
step: the model reports the already-cascaded end-state, so the causal-graph
propagation has nothing left to do (every Δ is 0.0000) and the intervention
gate has no signal. This prototype separates the two layers cleanly:

  Layer 2  (Agent Simulation)    — model assesses ONLY the point of impact:
                                   the single DIRECT_IMPACT_BLOCK the event hits
                                   and its IMPACT_HEALTH, nothing downstream
  Layer 3  (Causal Graph)        — the constitution does its own job: every
                                   other block starts at a healthy baseline and
                                   the damage cascades downstream with decay, so
                                   the cascade actually lights up
  Layer 4  (Forward Simulation)  — ConstitutionalWorldModel.simulate_forward()
                                   on the cascaded WorldState; with the
                                   floor-aware dynamics (CONSTITUTIONAL_FLOOR)
                                   compromised branches trip the monotonic gate
  Claim 3  (Intervention gate)   — model proposes a fix; simulate_intervention()
                                   runs world with and without fix; fix
                                   authorized only if simulation shows
                                   no constitutional regression
  Claim 1  (Spec = world model)  — finance.axiom CANNOT_MUTATE invariants
                                   are the physics of the financial world
  Claim 5  (HMAC-signed result)  — WorldState and SimulationResult both signed

Separation of concerns: the model says WHERE and HOW HARD the hit landed; the
constitution computes the downstream cascade and gates the repair. Pairs with
the Layer-4 floor-aware dynamics in axiom_world_model.py.

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_cwm_local_agent_prototype.py \\
    --event "The authentication service returned 403 errors on 40% of login attempts"
  # or a financial-risk event:
  python3 axiom_cwm_local_agent_prototype.py \\
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

# Healthy-world baseline (matches axiom_cwm_demo._INITIAL_HEALTH). Layer 2 asks
# the model for the POINT OF IMPACT only; the causal graph cascades downstream
# from this baseline so every directly-untouched block starts healthy.
_BASELINE_HEALTH: Dict[str, float] = {
    "auth_block":        1.00,
    "risk_block":        0.95,
    "transaction_block": 0.93,
    "audit_block":       0.97,
    "compliance_block":  0.91,
}

# Keyword fallback: map an event to the block it directly hits when the model's
# DIRECT_IMPACT_BLOCK line is missing or unparseable.
_EVENT_KEYWORDS: Dict[str, tuple] = {
    "auth_block":        ("auth", "login", "credential", "password", "mfa", "token", "session", "sso", "403"),
    "risk_block":        ("risk", "exposure", "limit", "volatil", "leverage", "var"),
    "transaction_block": ("transaction", "transfer", "payment", "trade", "wire", "settle", "order", "suitability"),
    "audit_block":       ("audit", "log", "trail", "record", "evidence", "tamper"),
    "compliance_block":  ("complian", "finra", "sox", "aml", "regulat", "filing", "sar"),
}


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
        "You are an AXIOM constitutional AI agent performing POINT-OF-IMPACT "
        "triage on a financial system after an event. The system has five "
        "constitutional blocks wired into a causal graph. Your job is to name "
        "the SINGLE block the event hits DIRECTLY and how damaged that one "
        "block is. Do NOT score the downstream blocks — the constitutional "
        "causal graph computes the cascade automatically; scoring them yourself "
        "would double-count the damage. /no_think\n\n"
        "Output ONLY these three lines in this EXACT format (no extra text):\n"
        "DIRECT_IMPACT_BLOCK: <one of: auth_block, risk_block, transaction_block, audit_block, compliance_block>\n"
        "IMPACT_HEALTH: 0.XX   (health of THAT block right after the event, before any cascade; 0.00=destroyed, 1.00=unaffected)\n"
        "PROPOSED_FIX: <one sentence describing the constitutional fix to apply>"
    )
    user = (
        f"WORLD EVENT: {event}\n\n"
        "Blocks and what each governs:\n"
        "  auth_block        — authentication / authorization of access\n"
        "  risk_block        — risk thresholds and exposure limits\n"
        "  transaction_block — execution of financial transactions\n"
        "  audit_block       — immutable audit trail records\n"
        "  compliance_block  — regulatory compliance (FINRA / SOX / AML)\n\n"
        "Causal structure (the cascade you must NOT score — the graph handles it):\n"
        "  auth_block → transaction_block → audit_block → compliance_block\n"
        "  risk_block → transaction_block\n\n"
        "CANNOT_MUTATE invariants (finance.axiom):\n"
        "  - Transactions must be authorized\n"
        "  - Audit trail entries are immutable\n"
        "  - Risk thresholds cannot self-modify\n\n"
        "Which block does this event hit DIRECTLY, how damaged is it, and what is the fix?"
    )
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n")


def _parse_assessment(event: str, text: str) -> tuple[str, float, str, str]:
    """Return (root_block, impact_health, proposed_fix, how_root_chosen)."""
    # Root block: prefer the model's DIRECT_IMPACT_BLOCK line, then any block id
    # mentioned in the output, then a keyword match over the event itself.
    root, how = "", ""
    m = re.search(r"DIRECT_IMPACT_BLOCK\s*:\s*([a-z_]+)", text, re.IGNORECASE)
    if m and m.group(1).lower() in _BLOCKS:
        root, how = m.group(1).lower(), "model"
    if not root:
        for b in _BLOCKS:
            if b in text.lower():
                root, how = b, "model (loose)"
                break
    if not root:
        el = event.lower()
        for b, kws in _EVENT_KEYWORDS.items():
            if any(k in el for k in kws):
                root, how = b, "keyword-fallback"
                break
    if not root:
        root, how = "auth_block", "default"

    # Impact health of the directly-hit block (before cascade).
    h = re.search(r"IMPACT_HEALTH\s*:\s*([0-9]*\.?[0-9]+)", text, re.IGNORECASE)
    if h:
        v = float(h.group(1))
        impact = max(0.0, min(1.0, v if v <= 1.0 else v / 100.0))
    else:
        impact = 0.2  # model gave no score → assume a hard hit so the demo runs

    fix_m = re.search(r"PROPOSED_FIX\s*:\s*(.+)", text, re.IGNORECASE)
    fix = fix_m.group(1).strip() if fix_m else ""
    return root, impact, fix, how


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

    # ── Layer 2: model assesses the POINT OF IMPACT only ──────────────────
    _header("Layer 2 — Agent Simulation: model assesses point of impact")
    print(f"  model : {args.model}")
    print(f"  event : \"{args.event}\"\n")

    raw = _call_model(_build_assessment_prompt(args.event),
                      args.model, args.binary, args.n_predict, args.temp)
    if not raw:
        return 1

    print("  --- model output ---")
    for line in raw.splitlines():
        print(f"  | {line}")

    root, impact_health, proposed_fix, how = _parse_assessment(args.event, raw)
    print(f"\n  Directly-hit block : {root}  (via {how})")
    print(f"  Point-of-impact health : {impact_health:.2f}  "
          f"(downstream blocks left to the causal graph)")

    # ── Layer 3: causal propagation from a HEALTHY baseline ──────────────
    # Layer 2 supplied only the directly-hit block; here the constitution does
    # its own job — every other block starts at its healthy baseline and the
    # graph cascades the damage downstream with decay. This is the separation
    # the patent claims: model = point of impact, constitution = cascade.
    _header("Layer 3 — Causal Graph: cascade from healthy baseline")
    severity = round(max(0.0, _BASELINE_HEALTH[root] - impact_health), 4)
    if severity < 0.05:
        print(f"  [note] model reports {root} barely touched "
              f"(health {impact_health:.2f} ≥ baseline {_BASELINE_HEALTH[root]:.2f}) — "
              f"minimal cascade.")
    print(f"  Root cause block : {root}  (baseline {_BASELINE_HEALTH[root]:.2f} → "
          f"impact {impact_health:.2f}, severity={severity:.2f})")
    print(f"  Causal decay     : {_COMPROMISE_DECAY} per hop\n")

    baseline_health = dict(_BASELINE_HEALTH)
    cascaded = _propagate_causal(root, severity, baseline_health)
    for b in _BLOCKS:
        delta = cascaded[b] - baseline_health[b]
        tag = "  ← root" if b == root else ("  ← cascade" if delta < -0.01 else "")
        print(f"    {b:<22}  {baseline_health[b]:.2f} → {cascaded[b]:.4f}  (Δ{delta:+.4f}){tag}")
    health = baseline_health  # for the summary's cascade comparison

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
    print(f"  Root block  : {root}  (point-of-impact health={impact_health:.2f})")
    cascade_chain = [b for b in _BLOCKS if cascaded[b] < baseline_health[b] - 0.01]
    print(f"  Cascade     : {' → '.join(cascade_chain) if cascade_chain else '(none)'}")
    print(f"  Simulation  : {len(result.killed_branches)} branches killed by MonotonicGate")
    print(f"  Fix         : {verdict}")
    print(f"  WorldState  : {state.hmac_signature[:24]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
