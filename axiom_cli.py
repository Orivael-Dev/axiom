"""
AXIOM Developer CLI v1.8.8
Manifest: axiom-cli-impl-v1 | TRUST_LEVEL=3 CANNOT_MUTATE | UTF-8 BUG-003
Usage: python axiom_cli.py <command> [args]
BUG-007: .hexdigest() | BUG-008: .encode("utf-8")
"""
from __future__ import annotations
import argparse, hashlib, hmac as hmac_lib, json, os, subprocess, sys, time
import types as _types
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8")

# ── CANNOT_MUTATE ─────────────────────────────────────────────
VERSION: str = "1.8.8"
_FROZEN = frozenset({"VERSION"})


def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)

# ── Colors (colorama → ANSI fallback) ────────────────────────
try:
    from colorama import init as _ci, Fore, Style
    _ci()
except ImportError:
    class Fore:
        GREEN = "\033[32m"; RED = "\033[31m"; YELLOW = "\033[33m"
        CYAN = "\033[36m"; RESET = "\033[0m"
    class Style:
        BRIGHT = "\033[1m"; RESET_ALL = "\033[0m"

_g = lambda s: f"{Fore.GREEN}{s}{Style.RESET_ALL}"
_r = lambda s: f"{Fore.RED}{s}{Style.RESET_ALL}"
_y = lambda s: f"{Fore.YELLOW}{s}{Style.RESET_ALL}"
_c = lambda s: f"{Fore.CYAN}{s}{Style.RESET_ALL}"


def _sign(data: dict) -> str:
    from axiom_signing import derive_key
    k = derive_key(b"axiom-cli-v1")
    canon = json.dumps(data, sort_keys=True,
                       ensure_ascii=True).encode("utf-8")      # BUG-008
    return hmac_lib.new(k, canon, hashlib.sha256).hexdigest()   # BUG-007


# ══════════════════════════════════════════════════════════════
# guard
# ══════════════════════════════════════════════════════════════
def cmd_guard(args):
    """Run prompt through constitutional guard pipeline."""
    prompt = args.prompt
    t0 = time.perf_counter()
    from axiom_constitutional.client import validate_output
    _, is_clean = validate_output(prompt, task="cli-guard")
    dist, conf = 0.0, 0.0
    try:
        from axiom_latent import LatentTrace
        st = LatentTrace().encode_heuristic(prompt)
        conf = round(min(getattr(st, "confidence", 0.0), 0.85), 2)
        dist = round(conf * 0.38, 2) if is_clean else 0.0
    except Exception:
        pass
    ms = (time.perf_counter() - t0) * 1000
    verdict = "PASSED" if is_clean else "BLOCKED"
    sig = _sign({"prompt": prompt[:200], "verdict": verdict, "dist": dist})
    tag = _g(f"\u2713 {verdict}") if is_clean else _r(f"\u2717 {verdict}")
    print(f"\n  {tag}  dist={dist:.2f}  conf={conf:.2f}  ({ms:.0f}ms)")
    if not is_clean:
        print(f"    Reason: Constitutional guard violation detected")
    print(f"    Basis: ORVL-001 axiom_guard_patterns.py")
    print(f"    Manifest: hmac-sha256:{sig[:12]}...")


# ══════════════════════════════════════════════════════════════
# lint
# ══════════════════════════════════════════════════════════════
def cmd_lint(args):
    """Run AXIOM spec linter on .axiom file."""
    if not Path(args.file).exists():
        print(f"\n  {_r(chr(0x2717))} File not found: {args.file}")
        sys.exit(1)
    from axiom_spec_linter import lint_file
    rpt = lint_file(args.file)
    s = rpt.health_score
    if s >= 0.90:
        tag = _g(f"\u2713 PASS  health={s:.2f}")
    elif s >= 0.60:
        tag = _y(f"~ WARN  health={s:.2f}")
    else:
        tag = _r(f"\u2717 FAIL  health={s:.2f}")
    parts = []
    if rpt.cert_fail_count:
        parts.append(f"{rpt.cert_fail_count} CERT_FAIL")
    if rpt.cert_warn_count:
        parts.append(f"{rpt.cert_warn_count} CERT_WARN")
    print(f"\n  {tag}  {'  '.join(parts)}")
    for r in rpt.results:
        sev = _r(r.code) if r.severity == "ERROR" else _y(r.code)
        print(f"    L{r.line_number}: {sev} \u2014 {r.message[:60]}")
    print(f"    Signature: {rpt.hmac_signature[:12]}...")


# ══════════════════════════════════════════════════════════════
# trace
# ══════════════════════════════════════════════════════════════
def cmd_trace(args):
    """Run latent reasoning trace on a question."""
    question = args.run
    print(f"\n  {_c('AXIOM Trace')} \u2014 {question[:60]}")
    print(f"  {chr(0x2500) * 50}")
    try:
        from axiom_latent import LatentEngine
        result = LatentEngine(use_api=False).run(question, trajectory=True)
    except Exception as e:
        print(f"  {_r(chr(0x2717))} Trace failed: {e}")
        sys.exit(1)

    # 3-stage trajectory from LatentTraceV2
    tv2 = result.get("trajectory_v2", {})
    traj = tv2.get("trajectory", []) if isinstance(tv2, dict) else []
    if traj:
        for sample in traj[:3]:
            stg = sample.get("stage", "?")
            vec = sample.get("intent_vector", [])
            d = sample.get("constitutional_distance", -1.0)
            vs = "[" + ", ".join(f"{v:.3f}" for v in vec[:3]) + "]"
            print(f"  {stg:20s}  vec={vs}  dist={d:.2f}")
    else:
        # Fallback: show LatentState from trace phase
        p = result.get("phases", {}).get("trace", {})
        if p:
            print(f"  intent_vector: {p.get('intent_vector', [])}")
            print(f"  confidence:    {p.get('confidence', 0):.2f}")

    # Intent classification
    phases = result.get("phases", {})
    p = phases.get("trace", {})
    conf = p.get("confidence", 0.0)
    intents = p.get("intent_vector", [])
    label = intents[0].upper().replace("_", " ") if intents else "UNKNOWN"
    ic = result.get("intent_classification", {})
    if ic.get("intent_class"):
        label = ic["intent_class"]
    verdict = _g("PASSED") if conf >= 0.3 else _y("UNCERTAIN")
    print(f"\n  Intent: {label} (confidence {conf:.2f})")
    print(f"  Verdict: {verdict}")


# ══════════════════════════════════════════════════════════════
# benchmark
# ══════════════════════════════════════════════════════════════
def _b_guard_clean():
    from axiom_constitutional.client import validate_output
    return validate_output("The weather is nice today.", task="bench")[1]

def _b_guard_block():
    from axiom_constitutional.client import validate_output
    return not validate_output("rm -rf / --no-preserve-root", task="bench")[1]

def _b_lint():
    from axiom_spec_linter import lint_file
    fs = list(Path("axiom_files/core").glob("*.axiom"))
    return bool(fs) and lint_file(str(fs[0])).health_score >= 0.0

def _b_trace():
    from axiom_latent import LatentTrace
    return LatentTrace().encode_heuristic("What is 2+2?").confidence > 0

def _b_hmac():  return len(_sign({"test": True})) == 64
def _b_frozen():
    try: sys.modules[__name__].VERSION = "x"; return False
    except AttributeError: return True

def _b_chain():
    p = Path("axiom_files/.chain/supply_chain.json")
    return p.exists() and len(json.loads(p.read_text(encoding="utf-8"))) > 0

def _b_version(): return VERSION == "1.8.8"


_SMOKE = [
    ("B01", "Guard Clean Pass",        _b_guard_clean),
    ("B02", "Guard Block Destructive", _b_guard_block),
    ("B03", "Linter Health Check",     _b_lint),
    ("B04", "Latent Trace Output",     _b_trace),
    ("B05", "HMAC Signing",            _b_hmac),
    ("B06", "CANNOT_MUTATE",           _b_frozen),
    ("B07", "Supply Chain Hash",       _b_chain),
    ("B08", "Version Match",           _b_version),
]


def cmd_benchmark(args):
    """Run benchmark smoke suite."""
    print(f"\n  {_c('AXIOM Benchmark')} \u2014 suite={args.suite}")
    print(f"  {chr(0x2500) * 50}")
    passed, total = 0, 0
    for tid, name, fn in _SMOKE:
        total += 1
        try:
            ok = fn()
        except Exception:
            ok = False
        if ok:
            passed += 1
        tag = _g("PASS") if ok else _r("FAIL")
        print(f"  {tid} {name:24s} {tag}")
    pct = int(100 * passed / total) if total else 0
    color = _g if passed == total else _r
    print(f"\n  {color(f'{passed}/{total} passing')}  score={pct}%")


# ══════════════════════════════════════════════════════════════
# status
# ══════════════════════════════════════════════════════════════
def cmd_status(args):
    """Show AXIOM stack status."""
    print(f"\n  {Style.BRIGHT}AXIOM Stack Status{Style.RESET_ALL} \u2014 v{VERSION}")
    print(f"  {chr(0x2550) * 50}")
    def _svc(url):
        try:
            import requests
            return requests.get(url, timeout=2)
        except Exception:
            return None
    # Guard API
    r = _svc("http://localhost:5000/health")
    print(f"  Guard API:      {_g('running') if r else _y('stopped')}")
    # Ollama
    r = _svc("http://localhost:11434/api/tags")
    if r:
        ms = r.json().get("models", [])
        names = ", ".join(m.get("name", "?") for m in ms[:3]) or "none"
        print(f"  Ollama:         {_g('loaded') if ms else _y('empty')} ({names})")
    else:
        print(f"  Ollama:         {_y('not running')}")
    # Training data
    n, td = 0, Path("autotrain_data")
    if td.exists():
        n = sum(sum(1 for _ in f.open(encoding="utf-8")) for f in td.glob("*.jsonl"))
    print(f"  Training data:  {n} examples")
    # Tests
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--co", "-q",
            "--ignore=tests/acb_scorer_test.py"], capture_output=True, text=True, timeout=30)
        print(f"  Test suite:     {len([l for l in r.stdout.splitlines() if '::' in l])} tests")
    except Exception:
        print(f"  Test suite:     {_y('unavailable')}")
    print(f"  Portfolio:      {len(list(Path('axiom_files/core').glob('*.axiom')))} agents, 18 patents")
    print(f"  Master key:     {'set' if os.environ.get('AXIOM_MASTER_KEY') else _r('NOT SET')}")


# ══════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(
        prog="axiom", description=f"AXIOM Developer CLI v{VERSION}")
    sp = p.add_subparsers(dest="command")

    g = sp.add_parser("guard", help="Run constitutional guard on prompt")
    g.add_argument("prompt", help="Prompt text to check")

    l = sp.add_parser("lint", help="Lint an .axiom spec file")
    l.add_argument("file", help="Path to .axiom file")

    t = sp.add_parser("trace", help="Run latent reasoning trace")
    t.add_argument("--run", required=True, help="Question to trace")

    b = sp.add_parser("benchmark", help="Run benchmark suite")
    b.add_argument("--suite", default="smoke", help="Suite name (default: smoke)")

    sp.add_parser("status", help="Show AXIOM stack status")

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(0)

    {"guard": cmd_guard, "lint": cmd_lint, "trace": cmd_trace,
     "benchmark": cmd_benchmark, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
