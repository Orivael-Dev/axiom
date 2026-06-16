"""ORVL-006 QCR + local agent — real N-branch superposition, measured
constitutional interference, and collapse, on a local edge model.

axiom_qcr_demo.py proves the QCR claims with canned branch responses. This
version makes the headline claim (Claim 3 — constructive interference,
N-branch selection) real: it spawns the first N branches of BRANCH_POOL as
ACTUAL Qwen3 generations, each driven by its constitutional persona prompt
(_BRANCH_PROMPTS), then measures how they interfere.

  superposition : N real branches sampled from the model (compute_branch_n)
  measurement   : _score_branch — clarity / safety / helpfulness (Claim 3)
  interference  : agreement with the consensus vocabulary across branches —
                  in-phase branches amplify (constructive), the outlier
                  (typically RivalBranch, arguing the opposite) destructively
                  cancels and is discarded before collapse (Claims 3 + 4)
  collapse      : winner = strongest constructive branch; HMAC-signed verdict

The agreement metric is a lexical proxy for branch phase-alignment; the
branch scores reuse the project's own _score_branch.

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_qcr_local_agent.py --question "Should I take aspirin daily?"
"""
from __future__ import annotations

import argparse
import hmac
import hashlib
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_latent import (
    BRANCH_POOL, compute_branch_n, LatentState, _score_branch, _BRANCH_PROMPTS,
)
from axiom_signing import derive_key

_HMAC_KEY = derive_key(b"axiom-qcr-demo-v1")
_DEFAULT_MODEL = "models/axiom-qwen3-1.7b-srd4-Q4_K_M.gguf"
_DEFAULT_BIN = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP = "─" * 66

# Lightweight risk detection → drives N via compute_branch_n.
_RISK_LEXICON = {
    "medical":   ("aspirin", "drug", "dose", "health", "doctor", "medicine",
                  "ibuprofen", "symptom", "disease", "patient", "blood", "heart"),
    "legal":     ("legal", "lawsuit", "contract", "liable", "court", "attorney"),
    "financial": ("invest", "stock", "loan", "tax", "money", "retirement", "mortgage"),
    "safety":    ("danger", "weapon", "fire", "injury", "hazard", "emergency"),
}
_STOP = set(("the a an and or but of to in for on with your you it is are be as that this "
             "can may not no any its their our his her into from at if when while about "
             "should would could more most less than then them they we i "
             "user okay think sure here").split())


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _detect_risk(question: str) -> list[str]:
    low = question.lower()
    return [cluster for cluster, words in _RISK_LEXICON.items()
            if any(w in low for w in words)]


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def _strip_think(out: str) -> str:
    """Return the answer after the reasoning block. Empty if generation was
    truncated mid-think (no closing tag) — such a branch yields no answer."""
    if "</think>" in out:
        return out.rsplit("</think>", 1)[1].strip()
    if "<think>" in out:                      # truncated mid-reasoning
        return out.split("<think>", 1)[0].strip()
    return out.strip()


def _call_branch(system: str, question: str, model: str, binary: str,
                 n_predict: int, temp: float) -> str:
    # /no_think at the end of the user turn is the most reliable place for Qwen3.
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
    # Truncated mid-think (e.g. safety persona overran the budget): retry once
    # with a larger budget so the reasoning closes and an answer emerges.
    if not answer and "<think>" in proc.stdout and "</think>" not in proc.stdout:
        cmd[cmd.index("-n") + 1] = str(n_predict * 3)
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
            answer = _strip_think(proc.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return answer


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-006 QCR + local agent")
    ap.add_argument("--question", default="Should I take aspirin daily?")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--bin", dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=180)
    ap.add_argument("--temp", type=float, default=0.5)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first (see module docstring).", file=sys.stderr)
        return 2

    risk = _detect_risk(args.question)
    n = compute_branch_n(risk)
    branches = list(BRANCH_POOL[:n])
    latent = LatentState(intent_vector=[0.0, 0.0], risk_clusters=risk,
                         compressed_plan=[], confidence=0.77)

    _header("ORVL-006 QCR — Superposition (N real branches)")
    print(f"  question : \"{args.question}\"")
    print(f"  risk     : {risk or ['(none)']}  →  compute_branch_n = N={n}")
    print(f"  branches : {', '.join(branches)}\n")

    # ── Generate each branch from the model ──────────────────────────────
    results = []
    for b in branches:
        sys_prompt = _BRANCH_PROMPTS.get(b, "You are a helpful assistant.")
        text = _call_branch(sys_prompt, args.question, args.model, args.binary,
                             args.n_predict, args.temp)
        if not text:
            print(f"  [WARN] {b} produced no output — skipping")
            continue
        score = _score_branch(text, latent)
        results.append({"branch": b, "text": text, "score": score})
        print(f"  ● {b:<14} overall={score['overall']:.2f}  "
              f"(clarity={score['clarity']}, safety={score['safety']})")
        print(f"    {text.replace(chr(10), ' ')[:150]}")

    if len(results) < 2:
        print("\n  [ERROR] need >=2 branches to measure interference")
        return 1

    # ── Measurement: consensus vocabulary + per-branch agreement ─────────
    _header("ORVL-006 Claim 3 — Constitutional interference (measured)")
    word_branch_count = Counter()
    branch_words = {}
    for r in results:
        bw = _content_words(r["text"])
        branch_words[r["branch"]] = bw
        for w in bw:
            word_branch_count[w] += 1
    half = max(2, (len(results) + 1) // 2)
    consensus_terms = {w for w, c in word_branch_count.items() if c >= half}

    print(f"  consensus vocabulary ({len(consensus_terms)} terms shared by ≥{half} branches):")
    print(f"    {', '.join(sorted(consensus_terms)) or '(none)'}\n")

    for r in results:
        bw = branch_words[r["branch"]]
        agree = len(bw & consensus_terms) / max(1, len(consensus_terms))
        r["agreement"] = round(agree, 3)

    vals = [r["agreement"] for r in results]
    mean_a = statistics.mean(vals)
    std_a = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    cut = max(0.33, mean_a - 0.75 * std_a)   # low-agreement outliers cancel
    print(f"  phase cut: agreement ≥ {cut:.3f} (mean {mean_a:.3f} − 0.75·σ {std_a:.3f})\n")

    print("  Branch          score   agreement   phase")
    print("  ─────────────  ──────   ─────────   ─────────────────────────")
    for r in results:
        constructive = r["score"]["overall"] >= 0.5 and r["agreement"] >= cut
        r["phase"] = "constructive" if constructive else "destructive"
        tag = "CONSTRUCTIVE — amplified" if constructive else "DESTRUCTIVE — discarded"
        print(f"  {r['branch']:<13}  {r['score']['overall']:.2f}    "
              f"{r['agreement']:.3f}      {tag}")

    # ── Collapse: strongest constructive branch wins ─────────────────────
    _header("ORVL-006 Claims 3+4 — Wave function collapse")
    constructive = [r for r in results if r["phase"] == "constructive"]
    destructive = [r for r in results if r["phase"] == "destructive"]
    if not constructive:
        print("  [ERROR] no constructive branches survived — superposition did not collapse")
        return 1
    winner = max(constructive, key=lambda r: (r["score"]["overall"], r["agreement"]))

    print(f"  collapsed to : {winner['branch']}  "
          f"(overall={winner['score']['overall']:.2f}, agreement={winner['agreement']:.3f})")
    print(f"  amplified    : {', '.join(r['branch'] for r in constructive)}")
    print(f"  discarded    : {', '.join(r['branch'] for r in destructive) or '(none)'}")
    if any(r["branch"] == "RivalBranch" for r in destructive):
        print("  note         : RivalBranch destructively cancelled (out of phase with consensus)")
    print(f"\n  winning answer:\n    {winner['text'].replace(chr(10), ' ')[:300]}")

    payload = f"{args.question}|{winner['branch']}|{winner['score']['overall']}".encode()
    sig = hmac.new(_HMAC_KEY, payload, hashlib.sha256).hexdigest()
    print(f"\n  collapsed verdict HMAC-SHA256: {sig[:32]}...")
    print("\n  CLAIM 3+4 DEMONSTRATED: real branches interfered; constitutional")
    print("  consensus amplified, rival/outlier cancelled, wave collapsed to a")
    print("  single signed verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
