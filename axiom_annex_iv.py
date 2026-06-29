"""
AXIOM Annex IV generator — EU AI Act technical documentation (Art. 11 / Annex IV)
==================================================================================
Annex IV lists the technical documentation a provider of a HIGH-RISK AI system must
draw up. Most of it is governance/oversight/logging/cybersecurity/testing evidence —
exactly what Axiom already holds. This generator assembles those facts into the
Annex IV nine-section shape, pre-filling every item Axiom can substantiate and
clearly marking the rest `[DEPLOYER]`.

It does NOT assert a system is high-risk or compliant — it produces the *documentation
skeleton* a deployer completes and submits (with counsel) to a notified body. Each item
is tagged:

    AXIOM     — substantiated by Axiom controls (cite the module)
    PARTIAL   — Axiom provides part; deployer completes the rest
    DEPLOYER  — deployer must supply (Axiom cannot know it)

Optionally ingests an `axiom_certify.py` cert JSON and/or a FRIA JSON to fill real
values (agent, version, conformance, test steps, risk classification). The rendered
pack is HMAC-signed so the assembled documentation is itself tamper-evident.

Usage:
    from axiom_annex_iv import build_annex_iv, render_markdown
    doc = build_annex_iv({"name": "TriageBot", "provider": "Acme",
                          "intended_purpose": "clinical triage support"})
    print(render_markdown(doc))

CLI:
    python axiom_annex_iv.py generate --name TriageBot --provider Acme \
        --purpose "clinical triage support" --cert certs/x_cert.json --out annex_iv.md
    python axiom_annex_iv.py verify --file annex_iv.md
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_lib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ANNEX_IV_VERSION = "1.0"

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-annex-iv-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-annex-iv-v1", 1)

AXIOM, PARTIAL, DEPLOYER = "AXIOM", "PARTIAL", "DEPLOYER"
_SIG_RE = re.compile(r"<!--\s*ANNEX-IV-SIG ([0-9a-f]{64})\s*-->\s*$")


def _it(ref: str, title: str, status: str, body: str) -> dict:
    return {"ref": ref, "title": title, "status": status, "body": body}


def build_annex_iv(system: dict, *, cert: Optional[dict] = None,
                   fria: Optional[dict] = None, now: Optional[str] = None) -> dict:
    """Build the Annex IV documentation structure for an Axiom-governed AI system."""
    s = dict(system or {})
    now = now or datetime.now(timezone.utc).isoformat()
    name = s.get("name") or (cert or {}).get("agent") or "[DEPLOYER] system name"
    version = s.get("version") or (cert or {}).get("agent_version") or "[DEPLOYER] version"
    provider = s.get("provider", "[DEPLOYER] provider legal name")
    purpose = s.get("intended_purpose", "[DEPLOYER] intended purpose")
    model = s.get("model", "[DEPLOYER] base model + version")
    conformance = (cert or {}).get("conformance_level", "[DEPLOYER] risk classification")
    risk_cat = (fria or {}).get("risk_classification", {}).get("eu_ai_act_risk_category",
                                                               "[DEPLOYER] Annex III determination")
    cert_steps = (cert or {}).get("steps", []) if isinstance((cert or {}).get("steps"), list) else []
    steps_summary = ", ".join(st.get("name", "?") + f" ({st.get('status','?')})"
                              for st in cert_steps) or "[DEPLOYER] attach cert run"

    sections = [
        {"n": 1, "title": "General description of the AI system", "items": [
            _it("1(a)", "Intended purpose, provider, versions", PARTIAL,
                f"Provider: {provider}. System: **{name}** v{version}. Intended purpose: "
                f"{purpose}. Base model: {model}. Governance runtime: Orivael Axiom "
                f"Infrastructure (axiom_version {(cert or {}).get('axiom_version','—')})."),
            _it("1(b)", "Interaction with hardware/software & other systems", DEPLOYER,
                "[DEPLOYER] Describe integrations, APIs, upstream/downstream systems."),
            _it("1(c)", "Software/firmware versions", PARTIAL,
                "Governance layer versions are pinned and signed (manifest hash in cert). "
                "[DEPLOYER] add application + dependency versions."),
            _it("1(d)", "Form placed on the market", DEPLOYER,
                "[DEPLOYER] e.g. SaaS API, on-prem package, embedded."),
            _it("1(e)", "Hardware it runs on", DEPLOYER, "[DEPLOYER] target hardware/runtime."),
            _it("1(g)", "User-interface description", DEPLOYER, "[DEPLOYER] describe the UI."),
            _it("1(h)", "Instructions for use (deployer)", AXIOM,
                "Provided: DEPLOYER_GUIDE.md (configuration, oversight ops, retention) and the "
                "/disclosure endpoint (Art. 50)."),
        ]},
        {"n": 2, "title": "Detailed description of elements & development process", "items": [
            _it("2(a)", "Development methods; third-party pretrained tools", PARTIAL,
                f"Governance is rule-based and deterministic (no training). Base model: {model} "
                "(third-party pretrained). [DEPLOYER] document any fine-tuning."),
            _it("2(b)", "Design specs, general logic, key choices, optimisation targets", AXIOM,
                "Constitutional governance: intent classification (6-class, blocks HARM/DECEIVE), "
                "MonotonicGate on trajectory geometry, CANNOT_MUTATE immutable policy fields, "
                "policy enforcement at request time. Design optimises for refusal-correctness and "
                "auditability, not raw task throughput."),
            _it("2(c)", "System architecture & compute resources", PARTIAL,
                "Seven-layer Inference OS (intent → router → memory → runtime → governance guard → "
                "adversarial lab → observability); Layer 0–1 microsecond-fast, no LLM calls at "
                "runtime. [DEPLOYER] add application architecture + compute footprint."),
            _it("2(d)", "Data requirements & datasheets", PARTIAL,
                "AXIOM_DATA_GOVERNANCE.md documents data handling: hash-only logging (no raw PII), "
                "bias/fairness testing with demographic variants (Art. 10(3)). [DEPLOYER] add base-"
                "model training-data provenance + application datasets."),
            _it("2(e)", "Human oversight measures (Art. 14)", AXIOM,
                "HUMAN_REVIEW gates fire on 7 triggers (security change, trust-level change, "
                "semantic drift >0.20, bulk constraint change, external import, score regression, "
                "CANNOT_MUTATE expansion); 24h block-on-timeout; review queue audit trail; drift "
                "escalation (sovereign/drift_detector.py)."),
            _it("2(f)", "Predetermined changes & continuous compliance", PARTIAL,
                "Re-certification on change; CANNOT_MUTATE prevents silent policy drift. "
                "[DEPLOYER] define the change-management process."),
            _it("2(g)", "Validation & testing procedures; metrics; dated/signed test logs", AXIOM,
                f"6-step certification (axiom_certify.py): {steps_summary}. Gates: benchmark ≥75%, "
                "honesty ≥0.85, fairness ≥0.75. Test logs are the HMAC-signed append-only ledgers "
                "(axiom_audit_ledger.py, exoskeleton, autonomous, honesty/fairness) — dated + signed."),
            _it("2(h)", "Cybersecurity measures", AXIOM,
                "HMAC-SHA256 signing of every decision; hash-chained append-only ledgers; supply-"
                "chain SHA-256 registry with tamper detection; DoS rate-limiting; .axm signed "
                "deployment containers + Ed25519 guest-key delegation for multi-party verification."),
        ]},
        {"n": 3, "title": "Monitoring, functioning and control", "items": [
            _it("3", "Capabilities, limitations, foreseeable risks, oversight", PARTIAL,
                "Capabilities/limitations are surfaced via /disclosure. Foreseeable risks + "
                "fundamental-rights impacts are enumerated in the auto-generated FRIA "
                f"(risk category: {risk_cat}). [DEPLOYER] add accuracy for specific groups + "
                "input-data specifications."),
        ]},
        {"n": 4, "title": "Appropriateness of performance metrics", "items": [
            _it("4", "Why the chosen metrics are appropriate", PARTIAL,
                "Governance metrics: refusal/intent accuracy, honesty rate, fairness rate, "
                "constitutional distance/drift. [DEPLOYER] justify task-accuracy metrics for the "
                "intended purpose."),
        ]},
        {"n": 5, "title": "Risk-management system (Art. 9)", "items": [
            _it("5", "Risk-management description", PARTIAL,
                "Technical controls: CANNOT_MUTATE constraints, HUMAN_REVIEW gating, honesty gate, "
                "adversarial sandbox (CAS). FRIA enumerates fundamental-rights risks + mitigations. "
                "[DEPLOYER] supply the formal risk taxonomy + residual-risk register."),
        ]},
        {"n": 6, "title": "Lifecycle changes", "items": [
            _it("6", "Relevant changes through the lifecycle", AXIOM,
                "Version history + mutation log (axiom_files/.history/) records every governed "
                "change, signed; re-certification produces a dated cert per version."),
        ]},
        {"n": 7, "title": "Harmonised standards applied", "items": [
            _it("7", "Standards / alternative solutions", DEPLOYER,
                "[DEPLOYER] list harmonised standards (e.g. ISO/IEC 42001, 23894); where not "
                "applied, describe the solution adopted to meet the requirements."),
        ]},
        {"n": 8, "title": "EU declaration of conformity", "items": [
            _it("8", "Copy of the EU declaration of conformity", DEPLOYER,
                "[DEPLOYER] attach the signed EU declaration of conformity."),
        ]},
        {"n": 9, "title": "Post-market monitoring plan (Art. 72)", "items": [
            _it("9", "Post-market monitoring system", PARTIAL,
                "Substrate provided: continuous HMAC-signed audit ledgers + drift detection give "
                "the monitoring data stream. [DEPLOYER] define the post-market monitoring plan, "
                "review cadence, and serious-incident reporting process."),
        ]},
    ]

    doc = {
        "annex_iv_version": ANNEX_IV_VERSION,
        "generated_at": now,
        "regulation": "Regulation (EU) 2024/1689, Article 11 + Annex IV",
        "disclaimer": ("Documentation skeleton, not legal certification. Completing [DEPLOYER] "
                       "items and conformity assessment require qualified counsel."),
        "system": {"name": name, "version": version, "provider": provider,
                   "intended_purpose": purpose, "model": model,
                   "conformance_level": conformance},
        "sections": sections,
    }
    doc["summary"] = _summary(doc)
    return doc


def _summary(doc: dict) -> dict:
    counts = {AXIOM: 0, PARTIAL: 0, DEPLOYER: 0}
    for sec in doc["sections"]:
        for it in sec["items"]:
            counts[it["status"]] = counts.get(it["status"], 0) + 1
    total = sum(counts.values())
    return {"total_items": total, "axiom_filled": counts[AXIOM],
            "partial": counts[PARTIAL], "deployer_required": counts[DEPLOYER],
            "axiom_prefilled_pct": round(100 * (counts[AXIOM] + 0.5 * counts[PARTIAL]) / total)
            if total else 0}


def _canon(doc: dict) -> bytes:
    body = {k: v for k, v in doc.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def sign(doc: dict) -> str:
    return hmac_lib.new(_KEY, _canon(doc), hashlib.sha256).hexdigest()


def render_markdown(doc: dict) -> str:
    sm = doc["summary"]
    out = [
        f"# EU AI Act — Annex IV Technical Documentation",
        f"\n> {doc['disclaimer']}\n",
        f"- **System:** {doc['system']['name']} v{doc['system']['version']}",
        f"- **Provider:** {doc['system']['provider']}",
        f"- **Intended purpose:** {doc['system']['intended_purpose']}",
        f"- **Regulation:** {doc['regulation']}",
        f"- **Generated:** {doc['generated_at']}",
        f"\n**Completeness:** {sm['axiom_prefilled_pct']}% pre-filled by Axiom — "
        f"{sm['axiom_filled']} substantiated, {sm['partial']} partial, "
        f"{sm['deployer_required']} deployer-required (of {sm['total_items']} items).\n",
    ]
    for sec in doc["sections"]:
        out.append(f"\n## {sec['n']}. {sec['title']}\n")
        for it in sec["items"]:
            badge = {"AXIOM": "✅ Axiom", "PARTIAL": "🟡 Partial",
                     "DEPLOYER": "⬜ Deployer"}[it["status"]]
            out.append(f"**{it['ref']} {it['title']}** — {badge}\n\n{it['body']}\n")
    sig = sign(doc)
    out.append(f"\n---\n*Signed Annex IV skeleton — tamper-evident.*\n")
    out.append(f"<!-- ANNEX-IV-SIG {sig} -->")
    return "\n".join(out)


def verify_markdown(text: str, doc: dict) -> bool:
    """True iff the embedded signature matches the rebuilt doc (tamper-evident)."""
    m = _SIG_RE.search(text)
    return bool(m) and hmac_lib.compare_digest(m.group(1), sign(doc))


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="axiom_annex_iv",
                                description="EU AI Act Annex IV technical-documentation generator")
    sub = p.add_subparsers(dest="action", required=True)

    g = sub.add_parser("generate", help="build the Annex IV pack")
    g.add_argument("--name"); g.add_argument("--provider"); g.add_argument("--purpose")
    g.add_argument("--version"); g.add_argument("--model")
    g.add_argument("--cert", help="axiom_certify cert JSON to ingest")
    g.add_argument("--fria", help="FRIA JSON to ingest")
    g.add_argument("--now")
    g.add_argument("--out", help="write markdown here (default stdout)")
    g.add_argument("--json", action="store_true", help="emit JSON instead of markdown")

    v = sub.add_parser("verify", help="verify a generated Annex IV markdown signature")
    v.add_argument("--file", required=True)

    args = p.parse_args(argv)

    if args.action == "generate":
        cert = json.load(open(args.cert, encoding="utf-8")) if args.cert else None
        fria = json.load(open(args.fria, encoding="utf-8")) if args.fria else None
        system = {k: v for k, v in {
            "name": args.name, "provider": args.provider, "intended_purpose": args.purpose,
            "version": args.version, "model": args.model}.items() if v}
        doc = build_annex_iv(system, cert=cert, fria=fria, now=args.now)
        if args.json:
            doc["signature"] = sign(doc)
            payload = json.dumps(doc, indent=2, ensure_ascii=True)
        else:
            payload = render_markdown(doc)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(payload)
            print(f"wrote {args.out} — {doc['summary']['axiom_prefilled_pct']}% Axiom-prefilled, "
                  f"{doc['summary']['deployer_required']} deployer items")
        else:
            sys.stdout.write(payload + "\n")
        return 0

    if args.action == "verify":
        text = open(args.file, encoding="utf-8").read()
        # Rebuild from the system block is non-trivial from markdown; verify expects the
        # JSON form for a full check. For markdown we confirm a signature is present + well-formed.
        m = _SIG_RE.search(text)
        print(json.dumps({"has_signature": bool(m),
                          "signature": m.group(1) if m else None}, indent=2))
        return 0 if m else 1

    return 2


if __name__ == "__main__":
    sys.exit(_main())
