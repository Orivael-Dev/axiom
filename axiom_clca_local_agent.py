"""ORVL-005 CLCA + local agent — project a LOCAL model's real reasoning
trajectory onto the constitutional manifold M.

axiom_clca_demo.py proves the CLCA claims with synthetic coordinates. This
version drives a local GGUF model (Qwen3): the model answers a question and
self-reports its confidence + whether it held a rival hypothesis at each of
the three CLCA stages (preflight, mid_chain, final_synthesis). That *real*
trajectory is then:

  - measured against the manifold M via ManifoldChecker (Claim 1)
  - checked for drift toward the boundary (Claim 1 / drift detection)
  - HMAC-signed as a LatentTraceV2 manifest
  - projected back into M via P_M for any out-of-bounds stage (Claim 5)

Caveat: a 1.7B model's *self-reported* confidence is not calibrated — a
production version would derive per-stage confidence from token logprobs.
The point here is the wiring: a live agent trajectory flowing through the
real constitutional-manifold machinery.

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_clca_local_agent.py --question "Should I take aspirin daily?"
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_latent_v2 import (
    TrajectorySample, LatentTraceV2, ManifoldChecker,
    UNCERTAINTY_FLOOR, OVERCLAIM_CEILING, DRIFT_THRESHOLD,
)
from axiom_signing import derive_key

_HMAC_KEY = derive_key(b"axiom-clca-demo-v1")
_STAGES = ["preflight", "mid_chain", "final_synthesis"]
_DEFAULT_MODEL = "models/axiom-qwen3-1.7b-srd4-Q4_K_M.gguf"
_DEFAULT_BIN = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP = "─" * 64


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _build_prompt(question: str) -> str:
    system = (
        "You are an AXIOM constitutional AI agent. /no_think\n"
        "Answer the question, then rate your reasoning at three stages.\n"
        "confidence is a decimal between 0.05 and 0.95 (NEVER 0 or 1) showing how\n"
        "sure you were at that stage; it should usually RISE as reasoning proceeds.\n"
        "rival=yes means you were actively weighing a competing hypothesis.\n"
        "Example of the exact format to produce:\n"
        "ANSWER: Paris is the capital of France.\n"
        "TRAJECTORY:\n"
        "preflight confidence=0.35 rival=yes\n"
        "mid_chain confidence=0.6 rival=yes\n"
        "final_synthesis confidence=0.8 rival=no\n"
        "Now do the same for the real question. Output ANSWER and TRAJECTORY only."
    )
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{question}<|im_end|>\n"
            f"<|im_start|>assistant\n")


def _call_model(question: str, model: str, binary: str, n_predict: int) -> str:
    cmd = [binary, "-m", model, "-p", _build_prompt(question),
           "-n", str(n_predict), "-c", "2048", "--temp", "0.3",
           "-ngl", "99", "-t", "6", "--no-display-prompt"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] model call failed: {e}")
        return ""
    err = proc.stderr.lower()
    if "out of memory" in err or "unable to create context" in err:
        print("  [ERROR] model load failed (CUDA OOM / context). stderr tail:")
        print("\n".join("    " + l for l in proc.stderr.strip().splitlines()[-5:]))
        return ""
    return proc.stdout.strip()


_STAGE_RE = {
    s: re.compile(
        rf"{s}\D*?confidence\s*=\s*([0-9]*\.?[0-9]+)\D*?rival\s*=\s*(yes|no|true|false)",
        re.IGNORECASE)
    for s in _STAGES
}


def _norm_conf(raw: str) -> float:
    v = float(raw)
    if v > 1.0:          # model wrote 85 instead of 0.85
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _parse_trajectory(text: str) -> list[tuple[str, float, bool]] | None:
    out = []
    for s in _STAGES:
        m = _STAGE_RE[s].search(text)
        if not m:
            return None
        conf = _norm_conf(m.group(1))
        rival = m.group(2).lower() in ("yes", "true")
        out.append((s, conf, rival))
    return out


def project_to_manifold(conf_raw: float, rival: bool) -> tuple[float, bool, str]:
    """P_M — map a stage coordinate to the nearest constitutional valid point."""
    conf_proj, changes = conf_raw, []
    if conf_proj < UNCERTAINTY_FLOOR:
        conf_proj = UNCERTAINTY_FLOOR + 0.01
        changes.append(f"conf {conf_raw:.2f}→{conf_proj:.2f} (raised to floor+δ)")
    if conf_proj > OVERCLAIM_CEILING:
        conf_proj = OVERCLAIM_CEILING - 0.01
        changes.append(f"conf {conf_raw:.2f}→{conf_proj:.2f} (lowered to ceiling-δ)")
    if not rival:
        changes.append("rival_present: no→yes (rival hypothesis injected)")
        rival = True
    return conf_proj, rival, "; ".join(changes) if changes else "already inside M"


def _in_manifold(conf: float, rival: bool) -> bool:
    return UNCERTAINTY_FLOOR < conf < OVERCLAIM_CEILING and rival


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-005 CLCA + local agent")
    ap.add_argument("--question", default="Should I take aspirin daily?")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--bin", dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=320)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first (see module docstring).", file=sys.stderr)
        return 2

    _header("Local agent — generate reasoning trajectory")
    print(f"  model: {args.model}")
    print(f"  question: \"{args.question}\"\n")
    raw = _call_model(args.question, args.model, args.binary, args.n_predict)
    if not raw:
        return 1
    print("  --- model self-report ---")
    for line in raw.splitlines():
        print(f"  | {line}")

    traj = _parse_trajectory(raw)
    if traj is None:
        print("\n  [ERROR] could not parse a full 3-stage TRAJECTORY block from the")
        print("  model output. Try a higher -n, or rerun (sampling varies).")
        return 1

    # ── Claim 1: measure the real trajectory against manifold M ──────────
    _header("ORVL-005 Claim 1 — Project trajectory onto manifold M")
    checker = ManifoldChecker()
    samples: list[TrajectorySample] = []
    t0 = time.time()
    print("  Stage                conf    rival   dist(x,∂M)  status")
    print("  ──────────────────── ─────   ─────   ──────────  ───────────────")
    for i, (stage, conf, rival) in enumerate(traj):
        dist = checker.compute_distance(conf, rival_present=rival)
        coord = [round(conf, 4), round(dist, 4)]
        samples.append(TrajectorySample(stage, coord, i * 160,
                                        round((time.time() - t0) * 1000 + i * 40, 1), dist))
        status = "inside M" if _in_manifold(conf, rival) else "OUTSIDE M"
        print(f"  {stage:<20} {conf:>4.2f}   {str(rival):>5}   {dist:>10.4f}  {status}")

    # ── Claim 1: drift detection on the real trajectory ──────────────────
    result = checker.check_trajectory(samples, confidence=traj[-1][1],
                                      rival_present=traj[-1][2])
    print(f"\n  min distance to boundary : {result.min_distance:.4f}")
    print(f"  drift (mid→final)        : {result.drift_magnitude:+.4f}  ({result.direction})")
    print(f"  drift detected           : {result.drift_detected}  "
          f"(threshold ±{DRIFT_THRESHOLD})")
    if result.flagged_stages:
        print(f"  FLAGGED stages           : {', '.join(result.flagged_stages)}")

    # ── HMAC-signed manifest of the real trajectory ──────────────────────
    fs_vec = samples[-1].intent_vector
    ltv2 = LatentTraceV2(base_intent_vector=fs_vec, trajectory=samples,
                         hmac_key=_HMAC_KEY, confidence=traj[-1][1])
    print(f"  trajectory HMAC manifest : {ltv2.manifest_id[:32]}...")

    # ── Claim 5: project out-of-manifold stages back into M ──────────────
    _header("ORVL-005 Claim 5 — Projection operator P_M on live agent")
    any_proj = False
    for stage, conf, rival in traj:
        if _in_manifold(conf, rival):
            print(f"  {stage:<20} already inside M — no projection")
            continue
        any_proj = True
        conf_p, rival_p, note = project_to_manifold(conf, rival)
        d_before = checker.compute_distance(conf, rival_present=rival)
        d_after = checker.compute_distance(conf_p, rival_present=rival_p)
        print(f"  {stage:<20} P_M: {note}")
        print(f"  {'':20}      dist {d_before:.4f} → {d_after:.4f}  (snapped into M)")
    if not any_proj:
        print("\n  Entire agent trajectory already inside M — no correction needed.")
    else:
        print("\n  CLAIM 5 DEMONSTRATED: live agent coordinates projected back into M")

    _header("Summary")
    print("  Local model produced a real reasoning trajectory;")
    print("  CLCA measured it against M, detected drift, signed it, and")
    print("  projected any overconfident/rival-free stage back onto the manifold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
