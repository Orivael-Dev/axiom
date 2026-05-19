"""Run all 9 exoskeleton delegates against a real backend and dump
per-delegate latency + token-cost numbers to a JSON report.

Default: NVIDIA NIM via `NVIDIA_NIM_API_KEY`. Pass `--backend local`
to point at Ollama instead (e.g. on the Orin Nano — set OLLAMA_URL).

The script runs each delegate ONCE with a realistic sample input —
short enough to keep the bill small on free-tier NIM, long enough to
exercise the prompt-budget path.

Usage:
    export AXIOM_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    NVIDIA_NIM_API_KEY=nvapi-... \
      python3 scripts/exoskeleton_nim_smoketest.py

    # Or local fallback:
    OLLAMA_URL=http://orin:11434 \
      python3 scripts/exoskeleton_nim_smoketest.py --backend local

Output:
    benchmarks/results/exoskeleton_smoketest_<backend>_<ts>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path


# Realistic short inputs — one per delegate. Each ~200-400 chars so
# the prompt_budget gets exercised but the bill stays bounded.
SAMPLE_INPUTS: dict[str, str] = {
    "investor_research":
        "Investment thesis: AI governance for regulated enterprises "
        "(banking, healthcare, defense). Stage focus: pre-seed and "
        "seed. Geography: US, Canada, UK. Lead-check appetite: "
        "$250k - $1M. Looking for thesis-driven funds.",

    "enterprise_targeting":
        "Role pattern: 'AI Governance Lead' or 'Head of Trust & "
        "Safety' or 'Responsible AI'. Target industries: financial "
        "services, healthcare, defense contractors. Company size: "
        "500-5000 employees. North America.",

    "outreach_personalization":
        "Buyer: CISO at a 1500-person fintech (commercial lending, "
        "regulated by OCC + FFIEC). Observed signal: posted a job "
        "for AI Governance Lead three days ago. Pain hypothesis: "
        "model-risk-management gap for LLM agents.",

    "demo_scripts":
        "Feature to demo: AXIOM signed-event-token kid-guard flow — "
        "phone receives a prompt with embedded PII, AXIOM Coordinator "
        "fires intent + governance agents, the kid-safe Skill Pack "
        "redacts PII before the cloud call, the resulting EventToken "
        "is signed and shows the redaction in the audit trail.",

    "sales_objection_handling":
        "Objection from prospect (mid-market healthcare CTO): "
        "\"We're not ready for an AI firewall — our LLM use is still "
        "experimental and adding another gate slows the team down. "
        "Maybe in 12-18 months when we have more production traffic.\"",

    "competitive_analysis":
        "Competitor: Guardrails AI (open-source LLM output validation "
        "library, paid cloud product, Y Combinator W23). Comparing "
        "against AXIOM on enterprise audit trail, multi-modal coverage, "
        "patent moat, and deploy footprint.",

    "grant_application":
        "Grant type: SBIR Phase I (DoD / Defense Innovation Unit, "
        "AI assurance topic). Product: AXIOM — constitutional AI "
        "control plane that produces signed event tokens for every "
        "agent decision, with multi-modal (text + audio + video + "
        "physics) governance and an open patent stack (ORVL-001 - 024).",

    "patent_counsel_packet":
        "Invention family: Event Token + Coordinator (ORVL-016 + "
        "ORVL-017). Selective-activation orchestrator that fires only "
        "the agents a query needs, signs each layer report, then signs "
        "the composition. Implementation files: axiom_event_token/. "
        "Filed provisional 2026-02. Continuation candidate: modular "
        "SLM delegate runtime (this branch).",

    "customer_discovery":
        "Call notes — VP Engineering at a 200-person identity "
        "verification SaaS. \"We're scared of running an LLM in "
        "production for KYC fraud screening. Compliance asked for "
        "a signed audit trail before we ship. Currently using a "
        "regex pipeline + human review, but volume is growing 3x.\" "
        "Next step: send a one-page demo.",
}


def _build_backend(name: str):
    from axiom_event_token.backends import (
        NIMBackend, LocalNanoBackend, BackendError,
    )
    if name == "nim":
        return NIMBackend()
    if name == "local":
        return LocalNanoBackend()
    raise BackendError(f"unknown backend: {name}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["nim", "local"], default="nim")
    ap.add_argument("--out-dir", default="benchmarks/results")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="per-call timeout (seconds)")
    args = ap.parse_args(argv)

    if "AXIOM_MASTER_KEY" not in os.environ:
        print("error: AXIOM_MASTER_KEY required", file=sys.stderr)
        return 2

    if args.backend == "nim" and not os.environ.get("NVIDIA_NIM_API_KEY"):
        print("error: NVIDIA_NIM_API_KEY not set. Either set it (free tier "
              "from build.nvidia.com) or rerun with --backend local "
              "(OLLAMA_URL pointing at your nano).", file=sys.stderr)
        return 2

    from examples.exoskeleton_pack import build_exoskeleton_pack
    from axiom_exoskeleton import ExoskeletonAgent
    from axiom_event_token.backends import BackendError

    backend = _build_backend(args.backend)
    print(f"# backend = {backend.name} ({backend.model})")
    print(f"# delegates to test = {len(SAMPLE_INPUTS)}")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        container = build_exoskeleton_pack(Path(tmp) / "exo.axm")
        exo = ExoskeletonAgent(container, backend=backend)

        per_delegate: list[dict] = []
        for name in exo.use_cases():
            sample = SAMPLE_INPUTS.get(name, f"Test input for {name}.")
            print(f"[ start ] {name}")
            t0 = dt.datetime.now(dt.timezone.utc)
            try:
                token = exo.invoke(name, sample)
                err = None
            except Exception as e:
                token = None
                err = f"{type(e).__name__}: {e}"
            t1 = dt.datetime.now(dt.timezone.utc)

            record = {
                "delegate":       name,
                "sample_chars":   len(sample),
                "started_utc":    t0.isoformat().replace("+00:00", "Z"),
                "ended_utc":      t1.isoformat().replace("+00:00", "Z"),
                "wall_clock_ms":  int((t1 - t0).total_seconds() * 1000),
                "error":          err,
                "ok":             err is None,
            }
            if token is not None and token.text is not None:
                p = token.text.payload
                record.update({
                    "backend":        p.get("backend"),
                    "model":          p.get("model"),
                    "input_tokens":   p.get("input_tokens"),
                    "output_tokens":  p.get("output_tokens"),
                    "latency_ms":     p.get("latency_ms"),
                    "output_excerpt": (p.get("output") or "")[:400],
                    "token_id":       token.id,
                    "verified":       token.verify(),
                })
            per_delegate.append(record)
            tag = "OK " if record["ok"] else "ERR"
            tok_str = (f"in/out={record.get('input_tokens')}/{record.get('output_tokens')}"
                       if record["ok"] else record["error"])
            print(f"[ {tag}  ] {name:32s} {tok_str:36s} "
                  f"{record.get('latency_ms', '?')}ms")

        successes = [r for r in per_delegate if r["ok"]]
        in_tokens  = [r["input_tokens"] or 0 for r in successes]
        out_tokens = [r["output_tokens"] or 0 for r in successes]
        latencies  = [r["latency_ms"] or 0 for r in successes]

        report = {
            "backend":              args.backend,
            "model":                backend.model,
            "started_utc":          per_delegate[0]["started_utc"]
                                     if per_delegate else None,
            "delegates_tested":     len(per_delegate),
            "delegates_ok":         len(successes),
            "delegates_failed":     len(per_delegate) - len(successes),
            "total_input_tokens":   sum(in_tokens),
            "total_output_tokens":  sum(out_tokens),
            "mean_input_tokens":    statistics.mean(in_tokens) if in_tokens else 0,
            "mean_output_tokens":   statistics.mean(out_tokens) if out_tokens else 0,
            "mean_latency_ms":      statistics.mean(latencies) if latencies else 0,
            "max_latency_ms":       max(latencies) if latencies else 0,
            "per_delegate":         per_delegate,
        }

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"exoskeleton_smoketest_{args.backend}_{ts}.json"
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print()
        print(f"━━ summary ━━")
        print(f"  delegates_ok       = {report['delegates_ok']} / "
              f"{report['delegates_tested']}")
        print(f"  total_input_tokens = {report['total_input_tokens']}")
        print(f"  total_output_tokens= {report['total_output_tokens']}")
        print(f"  mean_latency_ms    = {report['mean_latency_ms']:.0f}")
        print(f"  max_latency_ms     = {report['max_latency_ms']}")
        print(f"  report → {out_path}")
        return 0 if report["delegates_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
