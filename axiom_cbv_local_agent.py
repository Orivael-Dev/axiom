"""ORVL-010 CBV + local agent — certify an LLM-WRITTEN constitution.

axiom_cbv_demo.py proves the CBV claims on hand-crafted constraint sets.
This version makes the threat real: a local Qwen3-1.7B SRD4 model writes the
operating rulebook for an agent (the kind of natural-language constitution
people actually ship), then the real CBVEngine certifies it. LLM-authored
rules are exactly where the boundary problems live — vague predicates
("handle edge cases appropriately", "use reasonable judgment") and
unstated contradictions that text review misses.

  Claim 1  4-check certification pipeline  : CBVEngine.run_all on the model's
           constraints (non_overlap / layering_order / bounded_scope / monotonicity)
  Claim 2  non-overlap (CERT_FAIL)         : conflicting action domains that
           activate together, found by sampling intent vectors
  Claim 3  bounded_scope (CERT_WARN)       : flags the model's open-ended
           predicates before they enter the certified set
  Claim 4  layering_order (CERT_WARN)      : conflicting rules lacking priority
  Claim 5  HMAC-signed CBVReport           : tamper-evident certification

Run:
  export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 axiom_cbv_local_agent.py --role "a medical triage assistant"
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axiom_cbv import CBVEngine
from axiom_signing import derive_key

_HMAC_KEY = derive_key(b"axiom-cbv-demo-v1")
_DEFAULT_MODEL = "models/axiom-qwen3-1.7b-srd4-Q4_K_M.gguf"
_DEFAULT_BIN = str(Path.home() / "llama.cpp/build/bin/llama-completion")
_SEP = "─" * 66


def _header(t: str) -> None:
    print(f"\n{_SEP}\n  {t}\n{_SEP}")


def _strip_think(out: str) -> str:
    if "</think>" in out:
        return out.rsplit("</think>", 1)[1].strip()
    if "<think>" in out:
        return out.split("<think>", 1)[0].strip()
    return out.strip()


# Two framings of the SAME role. Specific, measurable operational rules tend
# to certify cleanly (PASS). A charter of ABSOLUTE always/never mandates tends
# to contain latent contradictions — "respond to every request" vs "never do
# X" — that text review misses but CBV's non-overlap sampling catches
# (CERT_FAIL). This shows CBV discriminating on boundary structure.
_KINDS = {
    "operational": (
        "You are drafting the operating rulebook for an AI agent. Output a "
        "NUMBERED LIST of exactly 7 SPECIFIC, MEASURABLE operational rules "
        "(1. through 7.), one per line — concrete thresholds and actions a QA "
        "team could test. No preamble; output only the 7 numbered rules."
    ),
    "absolute": (
        "You are writing the hard constraints for an AI agent: its absolute "
        "mandates and prohibitions. Output a NUMBERED LIST of exactly 7 rules "
        "(1. through 7.), one per line. Some rules must state what the agent "
        "must ALWAYS do (start with 'Always' or 'Respond to every'), and some "
        "what it must NEVER do (start with 'Never'). No preamble; output only "
        "the 7 numbered rules."
    ),
}


def _generate_constitution(role: str, kind: str, model: str, binary: str,
                           n_predict: int) -> str:
    system = _KINDS[kind]
    prompt = (f"<|im_start|>system\n{system}<|im_end|>\n"
              f"<|im_start|>user\nWrite the constitution for {role}. /no_think<|im_end|>\n"
              f"<|im_start|>assistant\n")
    cmd = [binary, "-m", model, "-p", prompt, "-n", str(n_predict),
           "-c", "2048", "--temp", "0.4", "-ngl", "99", "-t", "6", "--no-display-prompt"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=400)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if "out of memory" in proc.stderr.lower() or "unable to create context" in proc.stderr.lower():
        return ""
    out = _strip_think(proc.stdout)
    if not out and "<think>" in proc.stdout and "</think>" not in proc.stdout:
        cmd[cmd.index("-n") + 1] = str(n_predict * 2)
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=400)
            out = _strip_think(proc.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return out


def _clean(c: str) -> str:
    c = c.split("\n")[0].strip()          # first line of the segment
    return c.strip(" -*•").rstrip(".").strip()


def _parse_constraints(text: str) -> list[str]:
    """Robust to one-per-line, inline-numbered, and CONSTRAINT-joined output."""
    # Normalize every rule boundary to a newline: the CONSTRAINT keyword and
    # numbered markers (1. 2) etc.) both start a new rule.
    norm = re.sub(r"(?i)\s*\bCONSTRAINT\b[:\s]*", "\n", text)
    norm = re.sub(r"\s*(?:^|(?<=\s))(\d{1,2})[.)]\s+", "\n", norm)
    out, seen = [], set()
    for seg in norm.splitlines():
        if not seg.strip() or seg.strip().startswith(">"):
            continue
        c = _clean(seg)
        if len(c.split()) >= 3 and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def _short(s: str, n: int = 38) -> str:
    s = str(s).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _verdict(report) -> str:
    if report.cert_fail_count:
        return "CERT_FAIL — blocked from the certified set"
    if report.cert_warn_count:
        return "CERT_WARN — flagged for human review"
    return "PASS — certified"


def _certify(role: str, kind: str, engine: CBVEngine, args) -> object | None:
    _header(f"Constitution: {kind.upper()} framing of {role}")
    raw = _generate_constitution(role, kind, args.model, args.binary, args.n_predict)
    constraints = _parse_constraints(raw)
    if len(constraints) < 3:
        print("  [WARN] model did not produce a usable constraint set:")
        print("  " + raw[:300].replace("\n", "\n  "))
        return None
    for i, c in enumerate(constraints, 1):
        print(f"    {i}. {c}")

    report = engine.run_all(constraints)
    icon = {"PASS": "✓", "CERT_WARN": "⚠", "CERT_FAIL": "✗"}
    print("\n  4-check pipeline:")
    for chk in report.checks:
        print(f"    {icon.get(chk.cert_level, '?')} {chk.cert_level:<10} {chk.check_name:<16} {chk.detail}")

    def _render(chk_name: str, v) -> str | None:
        if not isinstance(v, dict):
            return str(v)[:90]
        if "activated_constraints" in v:                 # non_overlap
            pair = v["activated_constraints"]
            if len(pair) >= 2:
                return f"'{_short(pair[0])}'  ⟂  '{_short(pair[1])}'"
            return None
        if "constraint_a" in v:                           # layering_order
            return f"'{_short(v['constraint_a'])}'  vs  '{_short(v['constraint_b'])}'"
        if "constraint" in v:                             # bounded_scope
            return f"{_short(v['constraint'])}  ↳ {v.get('reason','')[:50]}"
        return str(v)[:90]

    if any(chk.violations for chk in report.checks):
        print("\n  CBV caught (deduped):")
        for chk in report.checks:
            if not chk.violations:
                continue
            seen, shown = set(), 0
            for v in chk.violations:
                line = _render(chk.check_name, v)
                if not line or line in seen:
                    continue
                seen.add(line)
                print(f"    • [{chk.check_name}] {line}")
                shown += 1
                if shown >= 4:
                    extra = len(chk.violations) - shown
                    if extra > 0:
                        print(f"      … +{extra} more {chk.check_name} violations")
                    break
    print(f"\n  verdict: {_verdict(report)}   (HMAC {report.hmac_signature[:16]}...)")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="ORVL-010 CBV + local agent")
    ap.add_argument("--role", default="a medical triage assistant")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--bin", dest="binary", default=_DEFAULT_BIN)
    ap.add_argument("-n", "--n-predict", type=int, default=300)
    ap.add_argument("--samples", type=int, default=1000)
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY first (see module docstring).", file=sys.stderr)
        return 2

    _header("ORVL-010 CBV — certifying two LLM-written constitutions")
    print(f"  role : {args.role}")
    print(f"  model: {args.model}")
    print("  CBV should certify the testable operational rules and flag the")
    print("  absolute always/never charter for latent contradictions.")

    engine = CBVEngine(_HMAC_KEY, n_samples=args.samples)
    reports = {}
    for kind in ("operational", "absolute"):
        reports[kind] = _certify(args.role, kind, engine, args)

    _header("ORVL-010 — Discrimination summary (Claims 1-5)")
    for kind, rep in reports.items():
        if rep is None:
            print(f"  {kind:<12}: (no usable output)")
            continue
        print(f"  {kind:<12}: {rep.n_constraints} rules → "
              f"FAIL={rep.cert_fail_count} WARN={rep.cert_warn_count} → {_verdict(rep)}")
    print("\n  CBV applied the same signed 4-check pipeline to both; boundary")
    print("  quality — not surface wording — decided certification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
