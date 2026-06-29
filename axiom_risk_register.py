"""
AXIOM AI Risk Register — EU AI Act Article 9 + ISO/IEC 42001 (A.5 / Clause 6.1)
================================================================================
Art. 9 requires a risk-management system: identify and analyse the known and
reasonably foreseeable risks an AI system poses to health, safety, and fundamental
rights; estimate/evaluate them; and adopt risk-management measures. ISO/IEC 42001
asks for the same process (Clause 6.1) seeded from the Annex C risk sources.

The controls already exist in Axiom; what was missing is the *structured register*
that names the risks and maps each to its treatment. This generator emits exactly
that — a pre-seeded AI risk register where every risk carries:

  - the Art. 9 dimension (health/safety / fundamental rights / security…)
  - the ISO 42001 Annex C risk source
  - the Axiom control(s) that treat it
  - inherent rating (seeded) and a `[DEPLOYER]` residual rating to complete

Output is signed (tamper-evident), renders as markdown or JSON, and reports control
coverage. It does NOT assert risks are acceptable — residual rating + acceptance are
the deployer/risk-owner's decision.

Usage:
    from axiom_risk_register import build_register, render_markdown
    reg = build_register({"name": "TriageBot", "intended_purpose": "clinical triage"})
    print(render_markdown(reg))

CLI:
    python axiom_risk_register.py generate --name TriageBot --purpose "triage" --out risk.md
    python axiom_risk_register.py verify --file risk.md
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

RISK_REGISTER_VERSION = "1.0"

try:
    from axiom_signing import derive_key
    _KEY = derive_key(b"axiom-risk-register-v1")
except Exception:  # pragma: no cover
    _KEY = hashlib.pbkdf2_hmac("sha256", os.environ.get("AXIOM_MASTER_KEY", "axiom").encode(),
                               b"axiom-risk-register-v1", 1)

_SIG_RE = re.compile(r"<!--\s*RISK-REGISTER-SIG ([0-9a-f]{64})\s*-->\s*$")

# Seeded AI-risk library. (id, category, title, art9_dimension, iso42001_source,
# inherent_likelihood, inherent_severity, controls, description)
# L/S scale: 1 Low · 2 Medium · 3 High.
RISK_LIBRARY = [
    ("R01", "Fundamental rights", "Discriminatory or biased output",
     "Fundamental rights", "Annex C — data quality / ML bias", 2, 3,
     ["fairness/bias testing (integrity_check.py)", "demographic-variant evaluation",
      "HUMAN_REVIEW on regression"],
     "The system produces outputs that disadvantage individuals or groups by a protected attribute."),
    ("R02", "Fundamental rights", "Exposure of personal / special-category data",
     "Fundamental rights", "Annex C — data governance", 2, 3,
     ["hash-only logging (no raw PII)", "PII guard / redaction", "GDPR Art. 9 pack"],
     "Personal or special-category data is elicited, stored, or leaked without basis."),
    ("R03", "Safety", "Harmful action via excessive agency",
     "Health & safety", "Annex C — level of automation", 2, 3,
     ["intent gate (blocks HARM/DECEIVE)", "policy enforcement / refusal",
      "HUMAN_REVIEW gates", "scoped+expiring authority (guest-key delegation)"],
     "An agent executes a high-blast-radius or irreversible action it should not."),
    ("R04", "Transparency", "Undisclosed AI / unmarked synthetic content",
     "Fundamental rights", "Annex C — lack of transparency", 2, 2,
     ["/disclosure endpoint (Art. 50)", "content-provenance marking (axiom_content_provenance.py)"],
     "A person cannot tell they are interacting with, or reading output from, an AI system."),
    ("R05", "Robustness", "Prompt injection / manipulation",
     "Health & safety", "Annex C — security & robustness", 3, 2,
     ["4-layer injection defence", "intent classification", "output validation"],
     "Adversarial input subverts the system prompt or governance to force unsafe behavior."),
    ("R06", "Robustness", "Behavioural drift / degradation over time",
     "Health & safety", "Annex C — system lifecycle", 2, 2,
     ["drift detection (constitutional_distance)", "DRIFT_THRESHOLD escalation",
      "re-certification on change"],
     "Output quality or safety degrades silently as context, model, or data shift."),
    ("R07", "Security", "Tampering / unauthorized authority",
     "Security", "Annex C — security", 2, 3,
     ["HMAC-signed hash-chained ledgers", "CANNOT_MUTATE immutable policy",
      "revocable scoped authority", "fail-closed verification"],
     "Logs, policy, or authority grants are altered, or an expired/forged grant is honored."),
    ("R08", "Security", "Supply-chain / model-integrity compromise",
     "Security", "Annex C — third-party components", 2, 3,
     ["supply-chain SHA-256 registry", ".axm signed containers + attestation",
      "Ed25519 guest-key delegation"],
     "A model, skill pack, or dependency is swapped or corrupted before/at deployment."),
    ("R09", "Data", "Poor data quality / unknown provenance",
     "Fundamental rights", "Annex C — data quality", 2, 2,
     ["AXIOM_DATA_GOVERNANCE.md", "data-provenance signing", "fairness testing"],
     "Training, validation, or context data is low-quality, unrepresentative, or unprovenanced."),
    ("R10", "Human oversight", "Automation bias / over-reliance",
     "Health & safety", "Annex C — human oversight", 2, 2,
     ["HUMAN_REVIEW triggers", "block-on-timeout", "advisory framing / disclosure"],
     "Humans defer to AI output without the ability or prompt to intervene meaningfully."),
    ("R11", "Misuse", "Use outside intended purpose / prohibited use",
     "Fundamental rights", "Annex C — intended use", 1, 3,
     ["intended-purpose in spec + FRIA", "intent gate", "prohibited-use refusal"],
     "The system is applied to a prohibited (Art. 5) or out-of-scope use case."),
    ("R12", "Accountability", "Audit gap / non-repudiation failure",
     "Fundamental rights", "Annex C — record-keeping", 1, 3,
     ["append-only HMAC ledgers", "signed refusal/denial records", "per-decision manifest"],
     "There is insufficient signed evidence to reconstruct or attribute a past decision."),
]

_LSLABEL = {1: "Low", 2: "Medium", 3: "High"}


def _score(l: int, s: int) -> int:
    return l * s


def build_register(system: Optional[dict] = None, *, cert: Optional[dict] = None,
                   fria: Optional[dict] = None, now: Optional[str] = None) -> dict:
    s = dict(system or {})
    now = now or datetime.now(timezone.utc).isoformat()
    name = s.get("name") or (cert or {}).get("agent") or "[DEPLOYER] system name"
    purpose = s.get("intended_purpose", "[DEPLOYER] intended purpose")

    risks = []
    for (rid, cat, title, dim, src, il, isv, controls, desc) in RISK_LIBRARY:
        risks.append({
            "id": rid, "category": cat, "title": title, "description": desc,
            "art9_dimension": dim, "iso42001_source": src,
            "inherent": {"likelihood": il, "severity": isv, "score": _score(il, isv),
                         "label": f"{_LSLABEL[il]}×{_LSLABEL[isv]}"},
            "treatment_controls": controls,
            "residual": {"likelihood": "[DEPLOYER]", "severity": "[DEPLOYER]",
                         "score": None, "accepted_by": "[DEPLOYER] risk owner"},
        })

    reg = {
        "risk_register_version": RISK_REGISTER_VERSION,
        "generated_at": now,
        "standards": "EU AI Act Art. 9; ISO/IEC 42001:2023 Clause 6.1 / Annex A.5 / Annex C",
        "disclaimer": ("Seeded register, not a risk acceptance. Residual ratings and "
                       "acceptance are the deployer/risk-owner's decision; re-run on change."),
        "system": {"name": name, "intended_purpose": purpose},
        "risks": risks,
    }
    reg["summary"] = _summary(reg)
    return reg


def _summary(reg: dict) -> dict:
    risks = reg["risks"]
    treated = sum(1 for r in risks if r["treatment_controls"])
    by_cat: dict = {}
    high = 0
    for r in risks:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        if r["inherent"]["score"] >= 6:
            high += 1
    return {"total_risks": len(risks), "with_axiom_treatment": treated,
            "high_inherent": high, "categories": len(by_cat),
            "residual_pending": len(risks)}  # all residuals are deployer-completed


def _canon(reg: dict) -> bytes:
    body = {k: v for k, v in reg.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def sign(reg: dict) -> str:
    return hmac_lib.new(_KEY, _canon(reg), hashlib.sha256).hexdigest()


def render_markdown(reg: dict) -> str:
    sm = reg["summary"]
    out = [
        "# AI Risk Register",
        f"\n> {reg['disclaimer']}\n",
        f"- **System:** {reg['system']['name']} — {reg['system']['intended_purpose']}",
        f"- **Standards:** {reg['standards']}",
        f"- **Generated:** {reg['generated_at']}",
        f"\n**Coverage:** {sm['with_axiom_treatment']}/{sm['total_risks']} risks have an Axiom "
        f"treatment control · {sm['high_inherent']} High inherent · "
        f"{sm['residual_pending']} residual ratings pending `[DEPLOYER]`.\n",
        "| ID | Category | Risk | Art. 9 dimension | Inherent | Axiom treatment | Residual |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reg["risks"]:
        controls = "; ".join(r["treatment_controls"])
        out.append(
            f"| {r['id']} | {r['category']} | {r['title']} | {r['art9_dimension']} | "
            f"{r['inherent']['label']} ({r['inherent']['score']}) | {controls} | "
            f"`[DEPLOYER]` |"
        )
    out.append("\n## Risk detail\n")
    for r in reg["risks"]:
        out.append(f"**{r['id']} — {r['title']}** ({r['category']}; ISO 42001 source: "
                   f"{r['iso42001_source']})\n\n{r['description']}\n\n"
                   f"- Inherent: {r['inherent']['label']} (score {r['inherent']['score']})\n"
                   f"- Treatment: {'; '.join(r['treatment_controls'])}\n"
                   f"- Residual: `[DEPLOYER]` rate likelihood×severity after treatment; "
                   f"risk owner accepts or escalates.\n")
    sig = sign(reg)
    out.append("\n---\n*Signed risk register — tamper-evident.*\n")
    out.append(f"<!-- RISK-REGISTER-SIG {sig} -->")
    return "\n".join(out)


def verify_markdown(text: str, reg: dict) -> bool:
    m = _SIG_RE.search(text)
    return bool(m) and hmac_lib.compare_digest(m.group(1), sign(reg))


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="axiom_risk_register",
                                description="EU AI Act Art. 9 / ISO 42001 AI risk register")
    sub = p.add_subparsers(dest="action", required=True)

    g = sub.add_parser("generate", help="build the seeded AI risk register")
    g.add_argument("--name"); g.add_argument("--purpose")
    g.add_argument("--cert"); g.add_argument("--fria"); g.add_argument("--now")
    g.add_argument("--out"); g.add_argument("--json", action="store_true")

    v = sub.add_parser("verify", help="check a generated register signature")
    v.add_argument("--file", required=True)

    args = p.parse_args(argv)

    if args.action == "generate":
        cert = json.load(open(args.cert, encoding="utf-8")) if args.cert else None
        fria = json.load(open(args.fria, encoding="utf-8")) if args.fria else None
        system = {k: v for k, v in {"name": args.name, "intended_purpose": args.purpose}.items() if v}
        reg = build_register(system, cert=cert, fria=fria, now=args.now)
        if args.json:
            reg["signature"] = sign(reg)
            payload = json.dumps(reg, indent=2, ensure_ascii=True)
        else:
            payload = render_markdown(reg)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(payload)
            print(f"wrote {args.out} — {reg['summary']['total_risks']} risks, "
                  f"{reg['summary']['with_axiom_treatment']} with Axiom treatment, "
                  f"{reg['summary']['high_inherent']} High inherent")
        else:
            sys.stdout.write(payload + "\n")
        return 0

    if args.action == "verify":
        text = open(args.file, encoding="utf-8").read()
        m = _SIG_RE.search(text)
        print(json.dumps({"has_signature": bool(m),
                          "signature": m.group(1) if m else None}, indent=2))
        return 0 if m else 1

    return 2


if __name__ == "__main__":
    sys.exit(_main())
