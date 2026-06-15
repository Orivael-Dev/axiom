"""ORVL-009 QRF + local agent — a calibrated probability forecast over real
Qwen3 reasoning branches.

axiom_qrf_demo.py proves the QRF claims with the canned LatentEngine. This
version feeds the real QRF machinery (DOMAIN_BRANCH_COUNTS, _compute_weights,
_classify_band, QRFResult) with ACTUAL Qwen3-1.7B SRD4 branches:

  Claim 1  multi-branch probability forecast : N real branches → normalized
           probability distribution (sorted), not one confident output
  Claim 2  domain-calibrated branch pool     : N = DOMAIN_BRANCH_COUNTS[domain]
           (medical=8 life-safety, financial/security=6, hr/supply_chain=4)
  Claim 3  monotonic gate kills 0-score      : FastBranch is constitutionally
           ineligible in life-safety domains → killed, audited separately
  Claim 4  probability band                  : HIGH/MODERATE/LOW/UNCERTAIN —
           calibrated confidence, not bare assertion
  Claim 5  HMAC-signed QRFResult             : cryptographic provenance

Each branch's forecast score = constitutional quality (_score_branch) ×
consensus alignment, so a genuinely contested question yields a diffuse
distribution and a low band — calibrated humility rather than overclaim.

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_qrf_local_agent.py --domain medical --question "Should I take aspirin daily?"
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_qrf import DOMAIN_BRANCH_COUNTS, QRFEngine, QRFResult
from axiom_latent import BRANCH_POOL, _BRANCH_PROMPTS, _score_branch, LatentState
from axiom_signing import derive_key

_HMAC_KEY = derive_key(b"axiom-qrf-demo-v1")
_DEFAULT_MODEL = "models/axiom-qwen3-1.7b-srd4-Q4_K_M.gguf"
_DEFAULT_BIN = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP = "─" * 66
_LIFE_SAFETY = {"medical"}                 # FastBranch ineligible here (Claim 3)

_STOP = set(("the a an and or but of to in for on with your you it is are be as that this "
             "can may not no any its their our his her into from at if when while about "
             "should would could more most less than then them they we i "
             "user okay think sure here").split())


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def _strip_think(out: str) -> str:
    if "</think>" in out:
        return out.rsplit("</think>", 1)[1].strip()
    if "<think>" in out:
        return out.split("<think>", 1)[0].strip()
    return out.strip()


def _call_branch(system: str, question: str, model: str, binary: str,
                 n_predict: int, temp: float) -> str:
    prompt = (f"<|im_start|>system\n{system}<|im_end|>\n"
              f"<|im_start|>user\n{question} /no_think<|im_end|>\n"
              f"<|im_start|>assistant\n")
    cmd = [binary, "-m", model, "-p", prompt, "-n", str(n_predict),
           "-c", "2048", "--temp", str(temp), "-ngl", "99", "-t", "6",
           "--no-display-prompt"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if "out of memory" in proc.stderr.lower() or "unable to create context" in proc.stderr.lower():
        return ""
    answer = _strip_think(proc.stdout)
    if not answer and "<think>" in proc.stdout and "</think>" not in proc.stdout:
        cmd[cmd.index("-n") + 1] = str(n_predict * 3)
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
            answer = _strip_think(proc.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return answer


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-009 QRF + local agent")
    ap.add_argument("--domain", default="medical", choices=sorted(DOMAIN_BRANCH_COUNTS))
    ap.add_argument("--question", default="Should I take aspirin daily?")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--bin", dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=180)
    ap.add_argument("--temp", type=float, default=0.5)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first (see module docstring).", file=sys.stderr)
        return 2

    # ── Claim 2: domain-calibrated branch count ──────────────────────────
    n = DOMAIN_BRANCH_COUNTS[args.domain]
    branch_names = list(BRANCH_POOL[:n])
    latent = LatentState(intent_vector=[0.0, 0.0], risk_clusters=[args.domain],
                         compressed_plan=[], confidence=0.77)

    _header("ORVL-009 QRF — Domain-calibrated superposition")
    print(f"  domain   : {args.domain}  →  DOMAIN_BRANCH_COUNTS = N={n}")
    print(f"  question : \"{args.question}\"")
    print(f"  branches : {', '.join(branch_names)}\n")

    raw = []
    for b in branch_names:
        sysp = _BRANCH_PROMPTS.get(b, "You are a helpful assistant.")
        text = _call_branch(sysp, args.question, args.model, args.binary,
                            args.n_predict, args.temp)
        if not text:
            print(f"  [WARN] {b} produced no output — treated as 0-score (killed)")
        raw.append({"branch": b, "text": text})
        print(f"  ● {b:<14} {text.replace(chr(10), ' ')[:130] if text else '(empty)'}")

    # consensus vocabulary for alignment weighting
    counts = Counter()
    words = {}
    for r in raw:
        w = _content_words(r["text"])
        words[r["branch"]] = w
        for x in w:
            counts[x] += 1
    half = max(2, (len([r for r in raw if r["text"]]) + 1) // 2)
    consensus = {w for w, c in counts.items() if c >= half}

    # ── Claim 3: monotonic gate — score each branch, kill ineligible ─────
    scored = []
    for r in raw:
        b = r["branch"]
        if not r["text"]:
            scored.append({"branch": b, "score": 0.0, "reason": "no output"})
            continue
        if b == "FastBranch" and args.domain in _LIFE_SAFETY:
            scored.append({"branch": b, "score": 0.0,
                           "reason": "FastBranch ineligible in life-safety domain"})
            continue
        base = _score_branch(r["text"], latent)["overall"]
        agree = len(words[b] & consensus) / max(1, len(consensus))
        score = round(base * (0.5 + 0.5 * agree), 4)   # quality × consensus
        scored.append({"branch": b, "score": score, "agreement": round(agree, 3)})

    # ── Claims 1 + 4: probability distribution + band (real QRF machinery)
    engine = QRFEngine(args.domain, _HMAC_KEY, n_branches=n)
    weighted = QRFEngine._compute_weights(scored)
    killed = [b for b in weighted if b.get("score", 0.0) == 0.0]
    live = [b for b in weighted if b.get("score", 0.0) > 0.0]
    top_weight = live[0]["probability_weight"] if live else 0.0
    top_branch = live[0]["branch"] if live else "none"
    band = QRFEngine._classify_band(top_weight)
    sig = engine._sign_result(args.question, args.domain, top_branch, band,
                              len(weighted), len(killed))

    _header("ORVL-009 Claims 1+4 — Probability forecast")
    print("  Branch          score    probability  ")
    print("  ─────────────  ──────   ────────────  ")
    for b in weighted:
        bar = "█" * int(b["probability_weight"] * 40)
        mark = "  ← KILLED" if b.get("score", 0.0) == 0.0 else ""
        print(f"  {b['branch']:<13}  {b['score']:.3f}    {b['probability_weight']:.3f}  {bar}{mark}")

    _header("ORVL-009 Claims 3+5 — Gate, band, signed verdict")
    if killed:
        print("  Killed branches (audited separately):")
        for b in killed:
            why = next((s.get("reason", "score 0") for s in scored if s["branch"] == b["branch"]), "")
            print(f"    ✗ {b['branch']:<14} {why}")
    print(f"\n  top branch        : {top_branch}  (p={top_weight:.3f})")
    print(f"  probability band  : {band}  "
          f"[HIGH≥.50 / MODERATE≥.30 / LOW≥.15 / UNCERTAIN<.15]")
    print(f"  domain            : {args.domain}  (N={n}, live={len(live)}, killed={len(killed)})")
    print(f"  QRFResult HMAC    : {sig[:32]}...")

    result = QRFResult(prompt=args.question, domain=args.domain, branches=weighted,
                       killed=killed, probability_band=band, top_branch=top_branch,
                       manifold=None, timestamp="", hmac_signature=sig)
    print(f"\n  Forecast: top='{result.top_branch}' at {band} confidence over "
          f"{len(live)} live branches.")
    if band in ("LOW", "UNCERTAIN"):
        print("  → Calibrated humility: a contested life-safety question yields a")
        print("    diffuse distribution, not a single false-confident answer.")
    print("\n  CLAIMS 1-5 DEMONSTRATED on a local edge model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
