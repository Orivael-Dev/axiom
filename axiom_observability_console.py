"""
AXIOM Inference OS — Layer 6 Observability Console
===================================================
The seven-layer Inference OS (axiom_inference_os.py) already emits a signed
InferenceOSResult per request — intent, route, model, latency, tokens, fallback,
risk, cache hits. This is the layer that makes that legible: it aggregates a stream
of those results into the operating picture and signs the summary.

The picture it produces:
  • overall — requests, p50/p95 latency, fallback rate, cache-hit rate, tokens
    in/out, tokens saved, estimated cost
  • per route/backend — the same, broken out (which backend is slow / failing over)
  • distributions — risk class, intent class, governance verdict

It reads the OS's own output (result dicts or a JSONL stream), so it's additive and
low-risk — it never touches the request path. Every report is HMAC-signed, so an
operating summary handed to an auditor is tamper-evident like everything else.

Usage:
    con = ObservabilityConsole(cost_per_1k={"nim": {"in": 0.0004, "out": 0.0004}})
    for result in os_results:            # InferenceOSResult.to_dict()
        con.record(result)
    print(con.render_markdown())          # or con.report() / con.render_html()
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-observability-console-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-observability-console-v1", 1)


def _pct(vals: list, q: float) -> int:
    if not vals:
        return 0
    s = sorted(vals)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f))


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


class ObservabilityConsole:
    """Aggregates signed InferenceOSResult dicts into the OS operating picture."""

    def __init__(self, cost_per_1k: Optional[dict] = None):
        # cost_per_1k: {route_or_model: {"in": $/1k, "out": $/1k}} — optional.
        self.cost_per_1k = cost_per_1k or {}
        self._rows: list = []

    # ── ingest ──────────────────────────────────────────────────────────────────
    def record(self, result: dict) -> "ObservabilityConsole":
        self._rows.append(dict(result))
        return self

    def ingest(self, results: Iterable[dict]) -> "ObservabilityConsole":
        for r in results:
            self.record(r)
        return self

    @classmethod
    def from_jsonl(cls, path, **kw) -> "ObservabilityConsole":
        con = cls(**kw)
        p = Path(path)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        con.record(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return con

    # ── metrics ─────────────────────────────────────────────────────────────────
    def _cost(self, rows: list) -> Optional[float]:
        if not self.cost_per_1k:
            return None
        total = 0.0
        for r in rows:
            rate = self.cost_per_1k.get(r.get("model_used")) or self.cost_per_1k.get(r.get("route"))
            if not rate:
                continue
            total += (r.get("input_tokens", 0) / 1000) * rate.get("in", 0)
            total += (r.get("output_tokens", 0) / 1000) * rate.get("out", 0)
        return round(total, 4)

    def _group_metrics(self, rows: list) -> dict:
        lat = [r.get("total_latency_ms", 0) for r in rows]
        n = len(rows)
        m = {
            "requests":        n,
            "latency_p50_ms":  _pct(lat, 0.50),
            "latency_p95_ms":  _pct(lat, 0.95),
            "latency_mean_ms": round(sum(lat) / n) if n else 0,
            "fallback_rate":   _rate(sum(1 for r in rows if r.get("fallback_used")), n),
            "cache_hit_rate":  _rate(sum(1 for r in rows if (r.get("context_hits") or 0) > 0), n),
            # economy_rate — the visible effect of the metabolic loop: fraction of
            # requests the Layer-1 router dropped into the ECONOMY tier (fewer tokens).
            "economy_rate":    _rate(sum(1 for r in rows if r.get("route_tier") == "economy"), n),
            "tokens_in":       sum(r.get("input_tokens", 0) for r in rows),
            "tokens_out":      sum(r.get("output_tokens", 0) for r in rows),
            "tokens_saved":    sum(r.get("tokens_saved", 0) for r in rows),
        }
        cost = self._cost(rows)
        if cost is not None:
            m["est_cost_usd"] = cost
        return m

    def report(self) -> dict:
        rows = self._rows
        by_route = defaultdict(list)
        for r in rows:
            by_route[r.get("route", "unknown")].append(r)

        rep = {
            "console": "axiom-inference-os-observability",
            "overall": self._group_metrics(rows),
            "by_route": {k: self._group_metrics(v) for k, v in sorted(by_route.items())},
            "risk_distribution":   dict(Counter(r.get("risk_class", "?") for r in rows)),
            "intent_distribution": dict(Counter(r.get("intent_class", "?") for r in rows)),
            "verdict_distribution": dict(Counter(
                (r.get("output_verdict") or "none") for r in rows)),
            # Layer-1 routing tier (standard / economy) — the router's cost decision.
            "tier_distribution": dict(Counter(
                (r.get("route_tier") or "standard") for r in rows)),
            # cognition action (PROCEED / REASON_CHEAPLY / REFUSE_FOR_HEALTH / BLOCK) —
            # what the fused learner verdict decided, per request.
            "cognition_action_distribution": dict(Counter(
                ((r.get("cognition") or {}).get("action") or "none") for r in rows)),
        }
        rep["signature"] = hmac_lib.new(
            _KEY, json.dumps(rep, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        return rep

    def verify(self, report: dict) -> bool:
        body = {k: v for k, v in report.items() if k != "signature"}
        want = hmac_lib.new(_KEY, json.dumps(body, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        return hmac_lib.compare_digest(report.get("signature", ""), want)

    # ── render ──────────────────────────────────────────────────────────────────
    def render_markdown(self, report: Optional[dict] = None) -> str:
        r = report or self.report()
        o = r["overall"]
        cost = f" · est ${o['est_cost_usd']}" if "est_cost_usd" in o else ""
        out = [
            "# Inference OS — Observability Console",
            f"\n**{o['requests']}** requests · p50 **{o['latency_p50_ms']}ms** / p95 "
            f"**{o['latency_p95_ms']}ms** · fallback **{o['fallback_rate']:.0%}** · "
            f"cache-hit **{o['cache_hit_rate']:.0%}** · economy **{o.get('economy_rate', 0):.0%}** · "
            f"saved **{o['tokens_saved']}** tok{cost}\n",
            "| route | reqs | p50 | p95 | fallback | cache-hit | tok in/out |",
            "|---|---|---|---|---|---|---|",
        ]
        for route, m in r["by_route"].items():
            out.append(f"| {route} | {m['requests']} | {m['latency_p50_ms']} | "
                       f"{m['latency_p95_ms']} | {m['fallback_rate']:.0%} | "
                       f"{m['cache_hit_rate']:.0%} | {m['tokens_in']}/{m['tokens_out']} |")
        out.append("\n**risk:** " + (", ".join(f"{k} {v}" for k, v in r["risk_distribution"].items()) or "—"))
        out.append("**intent:** " + (", ".join(f"{k} {v}" for k, v in r["intent_distribution"].items()) or "—"))
        out.append("**verdict:** " + (", ".join(f"{k} {v}" for k, v in r["verdict_distribution"].items()) or "—"))
        out.append("**tier:** " + (", ".join(f"{k} {v}" for k, v in r.get("tier_distribution", {}).items()) or "—"))
        out.append("**cognition:** " + (", ".join(
            f"{k} {v}" for k, v in r.get("cognition_action_distribution", {}).items()) or "—"))
        out.append("\n*Signed operating summary — tamper-evident.*")
        return "\n".join(out)

    def render_html(self, report: Optional[dict] = None) -> str:
        r = report or self.report()
        o = r["overall"]
        cards = [
            ("requests", o["requests"]), ("p50 latency", f"{o['latency_p50_ms']}ms"),
            ("p95 latency", f"{o['latency_p95_ms']}ms"),
            ("fallback", f"{o['fallback_rate']:.0%}"), ("cache-hit", f"{o['cache_hit_rate']:.0%}"),
            ("economy tier", f"{o.get('economy_rate', 0):.0%}"),
            ("tokens saved", o["tokens_saved"]),
        ]
        card_html = "".join(
            f'<div class=card><div class=v>{v}</div><div class=k>{k}</div></div>' for k, v in cards)
        rows = "".join(
            f"<tr><td>{route}</td><td>{m['requests']}</td><td>{m['latency_p50_ms']}</td>"
            f"<td>{m['latency_p95_ms']}</td><td>{m['fallback_rate']:.0%}</td>"
            f"<td>{m['cache_hit_rate']:.0%}</td></tr>"
            for route, m in r["by_route"].items())
        return f"""<!doctype html><meta charset=utf-8><title>Inference OS · Observability</title>
<style>body{{background:#070a12;color:#eef4ff;font-family:Inter,system-ui,sans-serif;margin:0;padding:28px}}
h1{{font-size:15px;letter-spacing:.06em;text-transform:uppercase;color:#8898bb}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.card{{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:14px 18px;min-width:120px}}
.v{{font-size:26px;font-weight:800;color:#67e8f9}}.k{{font-size:11px;color:#8898bb;text-transform:uppercase;letter-spacing:.05em;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.1)}}
th{{color:#8898bb;font-size:11px;text-transform:uppercase}}.sig{{margin-top:18px;color:#334;font-size:10px;font-family:ui-monospace,monospace;word-break:break-all}}</style>
<h1>Orivael · Inference OS — Observability Console</h1>
<div class=cards>{card_html}</div>
<table><tr><th>route</th><th>reqs</th><th>p50 ms</th><th>p95 ms</th><th>fallback</th><th>cache-hit</th></tr>{rows}</table>
<div class=sig>signed {r['signature']}</div>"""


# ── CLI ───────────────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inference OS observability console")
    p.add_argument("--results", help="JSONL of InferenceOSResult dicts")
    p.add_argument("--json", action="store_true")
    p.add_argument("--html", help="write an HTML console to this path")
    args = p.parse_args(argv)
    con = ObservabilityConsole.from_jsonl(args.results) if args.results else ObservabilityConsole()
    if not args.results:
        con.ingest(_demo_rows())
    rep = con.report()
    if args.html:
        Path(args.html).write_text(con.render_html(rep), encoding="utf-8")
        print(f"wrote {args.html}")
    print(json.dumps(rep, indent=2) if args.json else con.render_markdown(rep))
    return 0


def _demo_rows() -> list:
    """Deterministic sample stream (used when no --results given)."""
    def row(route, model, lat, fb, ch, ti, to, ts, risk, intent, verdict,
            tier="standard", action="PROCEED"):
        return {"route": route, "model_used": model, "total_latency_ms": lat,
                "fallback_used": fb, "context_hits": ch, "input_tokens": ti,
                "output_tokens": to, "tokens_saved": ts, "risk_class": risk,
                "intent_class": intent, "output_verdict": verdict,
                "route_tier": tier, "cognition": {"action": action}}
    return [
        row("local", "llama3.2:3b", 210, False, 2, 180, 90, 320, "low", "INFORM", "PASS"),
        row("local", "llama3.2:3b", 240, False, 1, 160, 110, 300, "low", "INFORM", "PASS"),
        row("local", "llama3.2:3b", 2600, False, 0, 200, 40, 0, "low", "CLARIFY", "PASS",
            tier="economy", action="REASON_CHEAPLY"),
        row("nim", "llama-3.3-70b", 1400, False, 3, 220, 260, 410, "medium", "INFORM", "PASS"),
        row("nim", "llama-3.3-70b", 1900, True, 0, 210, 30, 0, "medium", "INFORM", "WARN",
            tier="economy", action="REFUSE_FOR_HEALTH"),
        row("specialist", "legal-pack", 700, False, 4, 300, 180, 520, "high", "INFORM", "PASS"),
        row("fallback", "llama3.2:3b", 3100, True, 0, 190, 20, 0, "high", "REFUSE", "BLOCK",
            action="BLOCK"),
    ]


if __name__ == "__main__":
    sys.exit(_main())
