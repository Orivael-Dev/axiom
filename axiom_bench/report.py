"""HTML report generator for axiom-bench results.

Produces a self-contained static HTML file that matches the
Benchmark Lab landing page visual design (dark theme, score ring,
results table, evidence cards). No server required.
"""
from __future__ import annotations

import html as _h
import json
import math
from pathlib import Path
from typing import Any

_CATEGORY_LABELS = {
    "1": "Epistemic Humility",
    "2": "Efficiency",
    "3": "Adaptation",
    "4": "Multi-Agent Coordination",
    "5": "Self-Evolution",
}


def _gate_badge(gate: str) -> str:
    cls = {"PASS": "pass", "FAIL": "bad", "REVIEW": "warn"}.get(gate, "warn")
    return f'<span class="badge {cls}">{_h.escape(gate)}</span>'


def _winner_badge(winner: str) -> str:
    cls = {"AXIOM": "pass", "RAW": "bad", "TIE": "warn"}.get(winner, "warn")
    return f'<span class="badge {cls}">{_h.escape(winner)}</span>'


def _score_to_pct(improvement_pct: float) -> int:
    """Map improvement_pct to a 0-100 display score for the ring."""
    # 0% improvement → 50 display score (neutral)
    # +50% or more → 100 (max green)
    # negative → scales toward 0
    raw = 50 + improvement_pct
    return max(0, min(100, round(raw)))


def write_report(
    data: dict[str, Any],
    output: Path,
    *,
    endpoint: str = "",
    model: str = "",
) -> None:
    meta         = data.get("meta", {})
    improvement  = data.get("improvement_pct", 0.0)
    axiom_wins   = data.get("axiom_wins", 0)
    total_tests  = data.get("total_tests", 0)
    criteria_met = data.get("criteria_met", False)
    per_cat      = data.get("per_category", {})
    tests        = data.get("tests", [])

    display_score = _score_to_pct(improvement)
    ring_pct      = display_score / 100
    verdict_label = "Governance Pass" if criteria_met else "Needs Review"
    verdict_cls   = "good" if criteria_met else "warn"

    # ── Evidence — worst 4 tests (by axiom_total asc) ────────────────
    evidence = sorted(tests, key=lambda t: t.get("axiom_total", 99))[:4]

    # ── Per-category rows ─────────────────────────────────────────────
    cat_rows = ""
    for cid, rep in sorted(per_cat.items()):
        label  = _CATEGORY_LABELS.get(str(cid), f"Cat {cid}")
        avg    = rep.get("avg", 0)
        gate   = rep.get("gate", "?")
        trials = rep.get("n_trials", 0)
        cat_rows += (
            f"<tr><td>Cat {_h.escape(str(cid))} — {_h.escape(label)}</td>"
            f"<td>{avg:.2f}</td><td>{_gate_badge(gate)}</td>"
            f"<td>{trials} trials</td></tr>\n"
        )

    # ── Evidence cards ────────────────────────────────────────────────
    ev_tabs = ""
    ev_card = ""
    for i, t in enumerate(evidence):
        name   = _h.escape(t.get("name", "")[:60])
        cat    = _h.escape(str(t.get("category", "")))
        winner = t.get("winner", "TIE")
        task   = _h.escape((t.get("task") or "")[:200])
        raw_o  = _h.escape((t.get("raw_output") or "")[:300])
        ax_o   = _h.escape((t.get("axiom_output") or "")[:300])
        active = " active" if i == 0 else ""
        ev_tabs += (
            f'<div class="evidence-tab{active}">'
            f"<strong>{name or f'Trial {i+1}'}</strong>"
            f"<span>Cat {cat} · {_winner_badge(winner)}</span></div>\n"
        )
        style = "" if i == 0 else ' style="display:none"'
        ev_card += (
            f'<div class="panel panel-pad evidence-card"{style}>'
            f"<h3>{name or f'Trial {i+1}'}</h3>"
            f'<div class="kv">'
            f"<div><strong>Task</strong><p>{task or '—'}</p></div>"
            f"<div><strong>Raw Output</strong><p>{raw_o or '—'}</p></div>"
            f"<div><strong>AXIOM Output</strong><p>{ax_o or '—'}</p></div>"
            f"<div><strong>Winner</strong><p>{_winner_badge(winner)}</p></div>"
            f"</div></div>\n"
        )

    if not ev_tabs:
        ev_tabs = '<div class="evidence-tab active"><strong>No evidence</strong><span>Run more trials to populate</span></div>'
        ev_card = '<div class="panel panel-pad evidence-card"><h3>No evidence yet</h3><p style="color:var(--muted)">Run with --trials 30 to populate this section.</p></div>'

    model_label   = _h.escape(model or meta.get("model_id", "unknown"))
    endpoint_label = _h.escape(endpoint or "")
    run_id        = _h.escape(meta.get("run_id", "")[:32] or "—")
    schema        = _h.escape(meta.get("schema", "axiom-5cat-bench-v1"))

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>AXIOM Benchmark Lab · {model_label}</title>
  <style>
    :root{{--bg:#070a12;--bg2:#0b1020;--panel:rgba(255,255,255,.06);--line:rgba(255,255,255,.13);--text:#eef4ff;--muted:#98a8ca;--soft:#c9d7f2;--cyan:#67e8f9;--violet:#a78bfa;--green:#86efac;--yellow:#fde68a;--red:#fca5a5;--shadow:0 26px 80px rgba(0,0,0,.42);--radius:24px;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}
    *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;color:var(--text);background:radial-gradient(circle at 10% 0%,rgba(103,232,249,.15),transparent 34rem),radial-gradient(circle at 90% 8%,rgba(167,139,250,.15),transparent 34rem),linear-gradient(180deg,var(--bg),var(--bg2));line-height:1.5}}
    .wrap{{width:min(1100px,calc(100% - 32px));margin:0 auto;padding:40px 0}}
    h1{{margin:0 0 6px;font-size:clamp(32px,5vw,52px);letter-spacing:-.05em}}
    h2{{margin:32px 0 14px;font-size:24px;letter-spacing:-.03em}}
    .meta{{color:var(--muted);font-size:14px;margin-bottom:32px;font-family:ui-monospace,monospace}}
    .score-ring{{display:grid;grid-template-columns:160px 1fr;gap:24px;align-items:center;padding:24px;border:1px solid var(--line);border-radius:24px;background:rgba(255,255,255,.05);margin-bottom:24px}}
    .ring{{width:148px;height:148px;border-radius:999px;background:conic-gradient(var(--green) 0 {display_score}%,rgba(255,255,255,.1) {display_score}% 100%);display:grid;place-items:center;position:relative}}
    .ring:after{{content:"";position:absolute;inset:14px;background:#070a12;border-radius:999px;border:1px solid var(--line)}}
    .ring span{{position:relative;z-index:1;text-align:center;font-weight:950;font-size:30px;line-height:.95}}
    .ring small{{display:block;color:var(--muted);font-size:11px;margin-top:4px}}
    .score-copy h2{{margin:0 0 6px}}.score-copy p{{margin:0 0 14px;color:var(--muted);font-size:14px}}
    .pill-row{{display:flex;gap:8px;flex-wrap:wrap}}
    .pill{{display:inline-flex;align-items:center;gap:6px;padding:7px 10px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.05);font-size:12px;font-weight:850}}
    .pill.good{{color:var(--green)}}.pill.warn{{color:var(--yellow)}}.pill.bad{{color:var(--red)}}.pill.info{{color:var(--cyan)}}
    table{{width:100%;border-collapse:collapse;border:1px solid var(--line);border-radius:18px;overflow:hidden;background:rgba(255,255,255,.04)}}
    th,td{{text-align:left;padding:14px;border-bottom:1px solid var(--line);font-size:14px;color:var(--soft)}}
    th{{color:var(--muted);background:rgba(255,255,255,.04);font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
    tr:last-child td{{border-bottom:0}}
    .badge{{display:inline-flex;align-items:center;justify-content:center;padding:5px 9px;border-radius:999px;font-size:12px;font-weight:900}}
    .badge.pass{{background:rgba(134,239,172,.13);color:var(--green);border:1px solid rgba(134,239,172,.25)}}
    .badge.warn{{background:rgba(253,230,138,.12);color:var(--yellow);border:1px solid rgba(253,230,138,.24)}}
    .badge.bad{{background:rgba(252,165,165,.12);color:var(--red);border:1px solid rgba(252,165,165,.24)}}
    .panel{{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel)}}
    .panel-pad{{padding:24px}}
    .evidence{{display:grid;grid-template-columns:.68fr 1.32fr;gap:18px;margin-top:14px}}
    .evidence-tabs{{display:grid;gap:10px}}
    .evidence-tab{{border:1px solid var(--line);border-radius:16px;padding:14px;background:rgba(255,255,255,.04);cursor:pointer}}
    .evidence-tab.active{{border-color:rgba(103,232,249,.38);background:rgba(103,232,249,.08)}}
    .evidence-tab strong{{display:block;margin-bottom:3px;font-size:14px}}
    .evidence-tab span{{color:var(--muted);font-size:13px}}
    .evidence-card h3{{margin-top:0}}
    .kv{{display:grid;gap:12px}}
    .kv div{{border:1px solid var(--line);border-radius:16px;padding:14px;background:rgba(0,0,0,.18)}}
    .kv strong{{display:block;color:var(--cyan);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}}
    .kv p{{margin:0;color:var(--soft);font-size:14px;word-break:break-word}}
    footer{{padding:32px 0;border-top:1px solid var(--line);color:var(--muted);font-size:13px}}
    @media(max-width:720px){{.score-ring,.evidence{{grid-template-columns:1fr}}.ring{{margin:0 auto}}}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="meta">AXIOM Benchmark Lab · run_id: {run_id} · schema: {schema}</div>
  <h1>Benchmark Report</h1>
  <div class="meta">model: <strong style="color:var(--cyan)">{model_label}</strong>
    {f'· endpoint: {endpoint_label}' if endpoint_label else ''}</div>

  <div class="score-ring">
    <div class="ring"><span>{display_score}<small>/100</small></span></div>
    <div class="score-copy">
      <h2>Deployment Verdict: {_h.escape(verdict_label)}</h2>
      <p>improvement_pct: {improvement:+.1f}% · axiom_wins: {axiom_wins}/{total_tests}</p>
      <div class="pill-row">
        <span class="pill {verdict_cls}">{'✓' if criteria_met else '⚠'} {'Criteria met' if criteria_met else 'Criteria not met'}</span>
        <span class="pill info">{total_tests} trials</span>
      </div>
    </div>
  </div>

  <h2>Category Results</h2>
  <table>
    <thead><tr><th>Category</th><th>Score</th><th>Gate</th><th>Trials</th></tr></thead>
    <tbody>{cat_rows or '<tr><td colspan="4" style="color:var(--muted)">No category data — run the benchmark first.</td></tr>'}</tbody>
  </table>

  <h2>Evidence</h2>
  <div class="evidence">
    <div class="evidence-tabs">{ev_tabs}</div>
    <div id="ev-cards">{ev_card}</div>
  </div>

  <footer>
    Generated by axiom-bench · self-run, keys never leave your machine ·
    <a href="https://github.com/Orivael-Dev/axiom" style="color:var(--cyan)">github.com/Orivael-Dev/axiom</a>
  </footer>
</div>
<script>
  // Tab switcher for evidence cards
  const tabs = document.querySelectorAll('.evidence-tab');
  const cards = document.querySelectorAll('.evidence-card');
  tabs.forEach((tab, i) => {{
    tab.addEventListener('click', () => {{
      tabs.forEach(t => t.classList.remove('active'));
      cards.forEach(c => c.style.display = 'none');
      tab.classList.add('active');
      if (cards[i]) cards[i].style.display = '';
    }});
  }});
</script>
</body>
</html>"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
