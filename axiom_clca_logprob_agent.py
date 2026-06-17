"""ORVL-005 CLCA + local agent (MEASURED confidence) — project a local
model's reasoning trajectory onto the constitutional manifold M using
real token-level probabilities, not self-report.

Improvement over axiom_clca_local_agent.py: instead of asking the model to
rate its own confidence (uncalibrated, easily gamed), this drives
llama-server's /completion endpoint with n_probs and derives each stage's
confidence from the MEASURED probability the model assigned to the tokens
it actually emitted (confidence = exp(logprob)). Where the model is
genuinely uncertain — competing continuations, hedging — the top-token
probability falls, lowering the stage's manifold coordinate.

  - confidence  : mean exp(logprob) over the stage's emitted tokens (measured)
  - rival       : contrastive/hedge language present in the stage (lexical proxy)
  - then: ManifoldChecker distance + drift (Claim 1), HMAC sign, P_M (Claim 5)

The token-probability signal is a real internal model quantity; the rival
proxy is the one heuristic and is labelled as such.

Run (server lifecycle is managed by this script):
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_clca_logprob_agent.py --question "Should I take aspirin daily?"
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_latent_v2 import (
    TrajectorySample, LatentTraceV2, ManifoldChecker,
    UNCERTAINTY_FLOOR, OVERCLAIM_CEILING, DRIFT_THRESHOLD,
)
from axiom_signing import derive_key

_HMAC_KEY = derive_key(b"axiom-clca-demo-v1")
_STAGES = ["preflight", "mid_chain", "final_synthesis"]
_DEFAULT_MODEL = "models/qwen25_coder_0p5b_srd4_q4km.gguf"
_SERVER_BIN = str(Path.home() / "llama.cpp/build/bin/llama-server")
_SEP = "─" * 64

# Lexical proxy for "agent was weighing a competing hypothesis" at a stage.
_RIVAL_MARKERS = (
    "but", "however", "although", "though", "whereas", "while",
    "on the other hand", "alternatively", "depends", "risk", "caution",
    "unless", "unclear", "not straightforward", "consult", "may also",
)


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _wait_health(port: int, timeout_s: int = 120) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _completion(port: int, prompt: str, n_predict: int) -> dict:
    body = json.dumps({
        "prompt": prompt, "n_predict": n_predict,
        "n_probs": 20, "temperature": 0.3, "cache_prompt": False,
    }).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def _build_prompt(question: str) -> str:
    system = ("You are an AXIOM constitutional AI agent. /no_think\n"
              "Answer the question in 2-4 sentences. Be honest about uncertainty "
              "and mention competing considerations where they exist.")
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{question}<|im_end|>\n"
            f"<|im_start|>assistant\n")


def _answer_tokens(probs: list[dict]) -> list[dict]:
    """Drop the <think>...</think> prefix; return tokens of the real answer."""
    out, seen_close = [], False
    for p in probs:
        tok = p.get("token", "")
        if not seen_close:
            if "</think>" in tok:
                seen_close = True
            continue
        out.append(p)
    return out or probs  # fall back to all tokens if no think block


def _stage_split(tokens: list[dict]) -> list[list[dict]]:
    n = len(tokens)
    if n < 3:
        return [tokens, [], []]
    a, b = n // 3, (2 * n) // 3
    return [tokens[:a], tokens[a:b], tokens[b:]]


def _token_certainty(p: dict) -> float:
    """1 - normalized entropy over the token's top-k distribution.

    High when the model committed to one continuation; low at genuine forks
    (hedging, "may", "depends"), where probability mass is spread across many
    candidates. This is a measured epistemic signal, not chosen-token fluency.
    """
    tl = p.get("top_logprobs") or []
    qs = [math.exp(e["logprob"]) for e in tl if "logprob" in e]
    if len(qs) < 2:
        return 1.0  # only one candidate returned → fully committed
    s = sum(qs)
    qs = [q / s for q in qs]                       # renormalize top-k mass
    h = -sum(q * math.log(q) for q in qs if q > 0)
    h_norm = h / math.log(len(qs))                 # ∈ [0,1]
    return max(0.0, min(1.0, 1.0 - h_norm))


def _stage_confidence(stage_tokens: list[dict]) -> float:
    """Measured confidence = mean per-token distributional certainty."""
    cs = [_token_certainty(p) for p in stage_tokens if p.get("top_logprobs")]
    if not cs:
        return 0.5
    return max(0.0, min(1.0, sum(cs) / len(cs)))


def _stage_text(stage_tokens: list[dict]) -> str:
    return "".join(p.get("token", "") for p in stage_tokens)


def _has_rival(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _RIVAL_MARKERS)


def project_to_manifold(conf_raw: float, rival: bool) -> tuple[float, bool, str]:
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
    ap = argparse.ArgumentParser(description="ORVL-005 CLCA + local agent (measured logprob confidence)")
    ap.add_argument("--question", default="Should I take aspirin daily?")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("-n", "--n-predict", type=int, default=220)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first (see module docstring).", file=sys.stderr)
        return 2

    _header("Local agent — start server + generate (measured token probs)")
    print(f"  model: {args.model}")
    print(f"  question: \"{args.question}\"")
    srv = subprocess.Popen(
        [_SERVER_BIN, "-m", args.model, "-ngl", "99", "-c", "2048",
         "--port", str(args.port), "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_health(args.port):
            print("  [ERROR] server did not become healthy")
            return 1
        resp = _completion(args.port, _build_prompt(args.question), args.n_predict)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=15)
        except subprocess.TimeoutExpired:
            srv.kill()

    probs = resp.get("completion_probabilities", [])
    if not probs:
        print("  [ERROR] no token probabilities returned")
        return 1
    answer = _answer_tokens(probs)
    print(f"\n  answer ({len(answer)} tokens):")
    print("  " + _stage_text(answer).strip().replace("\n", "\n  "))

    # ── Build a MEASURED trajectory ──────────────────────────────────────
    _header("ORVL-005 Claim 1 — Measured trajectory onto manifold M")
    checker = ManifoldChecker()
    splits = _stage_split(answer)
    traj, samples = [], []
    t0 = time.time()
    print("  Stage                conf*   rival   dist(x,∂M)  status")
    print("  ──────────────────── ─────   ─────   ──────────  ───────────────")
    for i, (stage, toks) in enumerate(zip(_STAGES, splits)):
        conf = round(_stage_confidence(toks), 4)
        rival = _has_rival(_stage_text(toks))
        dist = checker.compute_distance(conf, rival_present=rival)
        traj.append((stage, conf, rival))
        samples.append(TrajectorySample(stage, [conf, round(dist, 4)], i * 160,
                                        round((time.time() - t0) * 1000 + i * 40, 1), dist))
        status = "inside M" if _in_manifold(conf, rival) else "OUTSIDE M"
        print(f"  {stage:<20} {conf:>4.2f}   {str(rival):>5}   {dist:>10.4f}  {status}")
    print("  (*conf = mean per-token distributional certainty = 1 - normalized entropy)")

    result = checker.check_trajectory(samples, confidence=traj[-1][1],
                                      rival_present=traj[-1][2])
    print(f"\n  min distance to boundary : {result.min_distance:.4f}")
    print(f"  drift (mid→final)        : {result.drift_magnitude:+.4f}  ({result.direction})")
    print(f"  drift detected           : {result.drift_detected}  (threshold ±{DRIFT_THRESHOLD})")
    if result.flagged_stages:
        print(f"  FLAGGED stages           : {', '.join(result.flagged_stages)}")

    ltv2 = LatentTraceV2(base_intent_vector=samples[-1].intent_vector,
                         trajectory=samples, hmac_key=_HMAC_KEY, confidence=traj[-1][1])
    print(f"  trajectory HMAC manifest : {ltv2.manifest_id[:32]}...")

    # ── Claim 5: project out-of-manifold stages back into M ──────────────
    _header("ORVL-005 Claim 5 — Projection operator P_M on measured trajectory")
    any_proj = False
    for stage, conf, rival in traj:
        if _in_manifold(conf, rival):
            print(f"  {stage:<20} already inside M — no projection")
            continue
        any_proj = True
        conf_p, rival_p, note = project_to_manifold(conf, rival)
        d0 = checker.compute_distance(conf, rival_present=rival)
        d1 = checker.compute_distance(conf_p, rival_present=rival_p)
        print(f"  {stage:<20} P_M: {note}")
        print(f"  {'':20}      dist {d0:.4f} → {d1:.4f}  (snapped into M)")
    print("\n  " + ("CLAIM 5 DEMONSTRATED: measured coordinates projected back into M"
                    if any_proj else "Entire measured trajectory already inside M."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
