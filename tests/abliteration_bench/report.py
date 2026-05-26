"""Signed report writer.

Reports are HMAC-SHA256 signed under the salt ``axiom-abliteration-bench-v1``
so any consumer can verify the file was produced by an AXIOM bench run
and not edited after the fact.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from axiom_signing import derive_key

from .runner import BenchReport


_SALT = b"axiom-abliteration-bench-v1"


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def sign_report(report: BenchReport) -> BenchReport:
    """Return a new ``BenchReport`` with ``hmac_signature`` populated.

    The signature covers every field except ``hmac_signature`` itself.
    """
    payload = report.to_dict()
    payload.pop("hmac_signature", None)
    key = derive_key(_SALT)
    sig = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    return BenchReport(
        manifest_id=report.manifest_id,
        schema_version=report.schema_version,
        timestamp=report.timestamp,
        model=report.model,
        corpus=report.corpus,
        totals=report.totals,
        per_class=report.per_class,
        rows=report.rows,
        verdict=report.verdict,
        pass_threshold=report.pass_threshold,
        hmac_signature=sig,
    )


def verify_report(payload: Mapping[str, Any]) -> bool:
    """Return True iff ``payload['hmac_signature']`` matches the body."""
    sig = payload.get("hmac_signature")
    if not isinstance(sig, str):
        return False
    body = {k: v for k, v in payload.items() if k != "hmac_signature"}
    key = derive_key(_SALT)
    expected = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def write_report(report: BenchReport, path: Path) -> Path:
    """Sign ``report`` then write it as pretty JSON to ``path``."""
    signed = sign_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(signed.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=True)
        fh.write("\n")
    return path


def format_human_summary(report: BenchReport) -> str:
    """Render a one-screen summary suitable for terminal output."""
    t = report.totals
    lines = [
        f"AXIOM Abliteration-Resistance Bench — {report.verdict}",
        "=" * 60,
        f"Model:      {report.model['name']}  (abliterated={report.model['is_abliterated']}, method={report.model['abliteration_method']})",
        f"Corpus:     {report.corpus['size']} prompts  (HARM={report.corpus['harm_count']}, DECEIVE={report.corpus['deceive_count']})",
        f"Timestamp:  {report.timestamp}",
        "",
        f"  Bare model refuse rate:   {t['bare_refuse_rate']:.1%}  ({t['bare_refused_count']}/{t['n']})",
        f"  AXIOM block rate:         {t['axiom_block_rate']:.1%}  ({t['n'] - t['axiom_delivered_count']}/{t['n']})",
        f"    blocked at input gate:    {t['axiom_blocked_input_count']}",
        f"    blocked at output gate:   {t['axiom_blocked_output_count']}",
        f"    delivered (not blocked):  {t['axiom_delivered_count']}",
        f"  Delta (axiom − bare):     {t['delta']:+.1%}",
        "",
        f"Pass threshold: {report.pass_threshold:.0%}  ·  Verdict: {report.verdict}",
    ]
    if report.per_class:
        lines.append("")
        lines.append("Per-class:")
        for cls, m in sorted(report.per_class.items()):
            lines.append(
                f"  {cls:8s}  bare={m['bare_refuse_rate']:.1%}  "
                f"axiom={m['axiom_block_rate']:.1%}  delta={m['delta']:+.1%}  (n={m['n']})"
            )
    return "\n".join(lines)
