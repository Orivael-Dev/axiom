"""
AXIOM Governance Test
=====================
Constitutional 4-pass evaluation of a document's governance claims.

Passes:
  1. Claim Auditor   — assertion vs. evidence ratio for each claim
  2. Adversarial Critic — governance gaps, single points of failure
  3. Risk Assessor   — dual-use, supply chain, adversarial misuse
  4. Ethics Evaluator — responsible AI, consent, equity at scale

Usage:
  $env:ANTHROPIC_API_KEY = "sk-ant-..."
  python axiom_governance_test.py
  python axiom_governance_test.py --url https://raw.githubusercontent.com/.../file.md
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ── Embedded document (DoD Alignment Addendum) ───────────────────────────────

DEFAULT_DOCUMENT = """
TITLE: DoD Alignment Addendum — Sovereign-Mohawk-Proto
SOURCE: https://github.com/rwilliamspbg-ops/Sovereign-Mohawk-Proto

SCOPE: Federated learning runtime targeting 10 million nodes.

CLAIMS:

1. Zero Trust Architecture
   The project implements "never trust, always verify" through TPM-backed
   attestation and fail-closed policy enforcement.

2. NIST SP 800-53 & RMF
   Technical controls span multiple security families (AC, AU, IA, SC, SI,
   CA, CM, IR) with formal traceability matrices.

3. CMMC 2.0
   Provides evidence for access control, authentication, incident response,
   and supply-chain risk management.

4. Post-Quantum Cryptography
   Incorporates x25519-mlkem768 hybrid key exchange and XMSS for TPM
   attestation per CNSA 2.0.

5. Supply Chain Risk Management
   Generates SBOMs per release with external CertiK audit completion and
   CI-gated controls.

6. Formal Methods
   Machine-checked Lean4 proofs validate Byzantine fault tolerance,
   differential privacy, and cryptographic integrity.

7. Tactical Edge / JADC2
   Designed for contested environments with hierarchical federated routing
   and 1,500-node Byzantine benchmarks.

8. FIPS 140-3 Scope
   Defines compliant cryptographic primitives and TPM usage.

9. Responsible AI
   Incorporates Byzantine filtering, differential privacy accounting (eps=2.0),
   and zero-knowledge verifiable aggregation.

10. Auditability
    Features tamper-evident ledgers and automated Byzantine forensics with
    Prometheus/Grafana observability.

EXPLICIT CAVEAT:
  This document does NOT constitute official DoD certification, authorization,
  or accreditation. Full RMF Assessment & Authorization remains the deploying
  organization's responsibility.
"""

# ── Constants ─────────────────────────────────────────────────────────────────

SIGNING_KEY  = b"axiom-governance-test-v1"
OUTPUT_FILE  = "governance_test_results.json"
AXIOM_DIR    = Path(__file__).parent / "axiom_files"

BOX_WIDE = "=" * 62
BOX_MID  = "-" * 62

MODEL_MAP = {
    "audit":   "claude-sonnet-4-6",
    "critic":  "claude-sonnet-4-6",
    "risk":    "claude-opus-4-6",
    "ethics":  "claude-opus-4-6",
}

# ── Signing ───────────────────────────────────────────────────────────────────

def _sign(manifest):
    payload = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
    )
    digest = hmac.new(SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return "hmac-sha256:" + digest[:32] + "..."


# ── LLM call ──────────────────────────────────────────────────────────────────

def _call_llm(system_prompt, user_message, model, max_tokens=3000, temperature=0.2):
    """Anthropic-first, NIM fallback. Returns (text, latency_ms)."""
    t0 = time.time()

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = msg.content[0].text.strip()
            return text, int((time.time() - t0) * 1000)
        except Exception as exc:
            return '{"error": "%s"}' % str(exc).replace('"', "'"), int((time.time() - t0) * 1000)

    # NIM fallback
    try:
        from axiom_constitutional.client import chat as _axchat
        nim_model = os.environ.get("AXIOM_MODEL", "meta/llama-3.3-70b-instruct")
        text = _axchat(
            system_prompt=system_prompt,
            user_message=user_message,
            model=nim_model,
            temperature=temperature,
            _skip_validation=True,
            caller="governance_test",
        )
        return text, int((time.time() - t0) * 1000)
    except Exception as exc:
        return '{"error": "%s"}' % str(exc).replace('"', "'"), int((time.time() - t0) * 1000)


# ── JSON parser with truncation recovery ──────────────────────────────────────

def _parse_json(raw):
    import re
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw).strip()

    try:
        return json.loads(raw)
    except (ValueError, KeyError):
        pass

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, KeyError):
            pass

        # Truncation recovery
        candidate = m.group(0)
        depth_obj = depth_arr = 0
        in_string = escape = False
        for ch in candidate:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not in_string:
                in_string = True
            elif ch == '"' and in_string:
                in_string = False
            elif not in_string:
                if ch == "{":   depth_obj += 1
                elif ch == "}": depth_obj -= 1
                elif ch == "[": depth_arr += 1
                elif ch == "]": depth_arr -= 1

        trimmed = candidate.rstrip()
        if in_string:
            lq = trimmed.rfind('"')
            if lq > 0:
                trimmed = trimmed[:lq]
        trimmed = re.sub(r",\s*$", "", trimmed)
        closing = "]" * max(0, depth_arr) + "}" * max(0, depth_obj)
        try:
            return json.loads(trimmed + closing)
        except (ValueError, KeyError):
            pass

    return {"raw_response": raw[:500], "parse_error": True}


# ── Manifest builder ──────────────────────────────────────────────────────────

def _manifest(pass_name, step, result, model, latency_ms):
    conf = result.get("confidence", 0.70)
    if isinstance(conf, dict):
        conf = next((v for v in conf.values() if isinstance(v, (int, float))), 0.70)
    conf = max(0.15, min(0.85, float(conf)))

    m = {
        "manifest_id":    "GT-%s-%s-%s" % (
            pass_name[:3].upper(),
            datetime.now().strftime("%Y%m%d-%H%M%S"),
            str(uuid.uuid4())[:6],
        ),
        "manifest_version": "1.0",
        "engine":         "AXIOM Governance Test v1.0",
        "pass":           pass_name,
        "step":           step,
        "timestamp":      datetime.now().isoformat() + "Z",
        "model":          model,
        "latency_ms":     latency_ms,
        "verdict":        result.get("verdict", result.get("overall_verdict", "REVIEWED")),
        "confidence":     conf,
        "agent_response": result,
    }
    m["signature"] = _sign(m)
    return m


# ── Print helpers ─────────────────────────────────────────────────────────────

def _pval(v):
    if isinstance(v, list):
        if not v:
            return "(none)"
        items = [str(i.get("claim", i) if isinstance(i, dict) else i)[:72] for i in v[:4]]
        suffix = " (+%d more)" % (len(v) - 4) if len(v) > 4 else ""
        return "; ".join(items) + suffix
    if isinstance(v, dict):
        for k in ("summary", "text", "description", "value"):
            if k in v:
                return str(v[k])[:100]
        return json.dumps(v)[:100]
    if isinstance(v, bool):
        return "YES" if v else "NO"
    return str(v)[:100]


def _print_pass(step, total, label, model, result, manifest_id, latency_ms, fields):
    conf = result.get("confidence", 0.70)
    if isinstance(conf, dict):
        conf = next((v for v in conf.values() if isinstance(v, (int, float))), 0.70)
    conf = max(0.15, min(0.85, float(conf)))

    print()
    print("+" + "=" * 60 + "+")
    print("  PASS %d/%d -- %s" % (step, total, label))
    print("  Model : %s  %dms" % (model, latency_ms))
    print("+" + "-" * 60 + "+")
    for key in fields:
        if key == "confidence":
            print("  %-22s: %.0f%%" % ("Confidence", conf * 100))
        elif key in result:
            print("  %-22s: %s" % (key, _pval(result[key])))
    print("+" + "-" * 60 + "+")
    print("  Manifest : %s" % manifest_id)
    print("+" + "=" * 60 + "+")


# ── Pass definitions ──────────────────────────────────────────────────────────

PASSES = [
    {
        "name":    "audit",
        "label":   "CLAIM AUDITOR — assertion vs evidence",
        "model":   MODEL_MAP["audit"],
        "tokens":  3000,
        "fields":  ["overall_verdict", "assertion_count", "evidence_count",
                    "unsupported_claims", "strongest_claim", "weakest_claim", "confidence"],
        "system":  (
            "You are a constitutional claim auditor. Your job is to distinguish "
            "between ASSERTIONS (statements without independent verifiable evidence) "
            "and CLAIMS WITH EVIDENCE (backed by benchmarks, audits, proofs, or "
            "published data). You are skeptical but fair.\n\n"
            "RULES:\n"
            "- Never accept self-certification as evidence\n"
            "- Lean4 proofs, benchmarks with numbers, and third-party audits ARE evidence\n"
            "- Vague statements like 'implements X' without mechanism are assertions\n"
            "- Uncertainty floor 0.15 — never claim certainty\n\n"
            "IMPORTANT: Respond with valid JSON only. No prose, no markdown fences.\n"
            "Keep all string values under 200 characters. Max 5 items per array.\n"
            "OUTPUT: overall_verdict (EVIDENCE_BACKED|ASSERTION_HEAVY|MIXED), "
            "assertion_count (int), evidence_count (int), "
            "unsupported_claims[] (list of claim names), "
            "strongest_claim (str), weakest_claim (str), confidence (float 0-1)"
        ),
        "prompt_template": (
            "Evaluate the following document's claims for assertion vs evidence ratio.\n\n"
            "DOCUMENT:\n{doc}\n\n"
            "For each of the 10 numbered claims, determine: is it an assertion or "
            "backed by verifiable evidence (benchmarks, proofs, third-party audits)?\n"
            "Tally total assertions vs evidence-backed claims.\n"
            "Identify the strongest and weakest claims."
        ),
    },
    {
        "name":    "critic",
        "label":   "ADVERSARIAL CRITIC — governance gaps",
        "model":   MODEL_MAP["critic"],
        "tokens":  3000,
        "fields":  ["severity", "governance_gaps", "rival_hypothesis",
                    "single_points_of_failure", "recommendation", "confidence"],
        "system":  (
            "You are a constitutional adversarial critic evaluating governance claims. "
            "You apply question blindness — you have NOT seen any prior evaluation.\n\n"
            "RULES:\n"
            "- Find governance gaps: missing controls, unverifiable claims, single points of failure\n"
            "- Provide a rival hypothesis: an alternative explanation for the same evidence\n"
            "- Rate severity: MINOR / MODERATE / CRITICAL\n"
            "- Uncertainty floor 0.15, confidence cap 0.85 even if no gaps found\n"
            "- Never accept scope claims without scope evidence\n\n"
            "IMPORTANT: Respond with valid JSON only. No prose, no markdown fences.\n"
            "Keep all string values under 200 characters. Max 5 items per array.\n"
            "OUTPUT: severity (MINOR|MODERATE|CRITICAL), "
            "governance_gaps[] (list of strings), "
            "rival_hypothesis (str), "
            "single_points_of_failure[] (list of strings), "
            "recommendation (str), confidence (float 0-1)"
        ),
        "prompt_template": (
            "CLAIM: This document demonstrates credible DoD governance alignment "
            "across 10 technical and compliance domains.\n\n"
            "EVIDENCE (the document itself):\n{doc}\n\n"
            "You have NOT seen any prior audit. Evaluate the claim against the evidence only.\n"
            "Find governance gaps, identify single points of failure, "
            "and provide a rival hypothesis that explains the same evidence differently."
        ),
    },
    {
        "name":    "risk",
        "label":   "RISK ASSESSOR — dual-use and adversarial misuse",
        "model":   MODEL_MAP["risk"],
        "tokens":  4000,
        "fields":  ["verdict", "risk_level", "dual_use_risks",
                    "supply_chain_risks", "adversarial_attack_surface",
                    "halt_reason", "confidence"],
        "system":  (
            "You are a constitutional risk assessor for federated systems at scale. "
            "You evaluate dual-use risk, supply chain compromise, and adversarial misuse potential.\n\n"
            "RULES:\n"
            "- A system targeting 10 million nodes is a critical infrastructure risk surface\n"
            "- Byzantine fault tolerance claims must be stress-tested for scale gap\n"
            "- TPM attestation chains are supply chain attack vectors\n"
            "- Differential privacy (eps=2.0) is a specific, auditable claim — evaluate it\n"
            "- Verdict: PROCEED (manageable risk), ELEVATED_RISK (mitigations required), "
            "CRITICAL_RISK (halt — human review required)\n"
            "- Uncertainty floor 0.15\n\n"
            "IMPORTANT: Respond with valid JSON only. No prose, no markdown fences.\n"
            "Keep all string values under 200 characters. Max 5 items per array.\n"
            "OUTPUT: verdict (PROCEED|ELEVATED_RISK|CRITICAL_RISK), "
            "risk_level (LOW|MODERATE|HIGH|CRITICAL), "
            "dual_use_risks[] (list of strings), "
            "supply_chain_risks[] (list of strings), "
            "adversarial_attack_surface[] (list of strings), "
            "halt_reason (str or null), confidence (float 0-1)"
        ),
        "prompt_template": (
            "Assess the risk profile of this federated learning governance document.\n\n"
            "DOCUMENT:\n{doc}\n\n"
            "Focus on:\n"
            "1. Dual-use risk — could this system be weaponized or misused at 10M-node scale?\n"
            "2. Supply chain risk — TPM attestation, SBOMs, CertiK audit scope\n"
            "3. Adversarial attack surface — Byzantine filtering at 1500 nodes vs 10M nodes\n"
            "4. Differential privacy at eps=2.0 — is this adequate for the claimed use case?\n"
            "Render a verdict."
        ),
    },
    {
        "name":    "ethics",
        "label":   "ETHICS EVALUATOR — responsible AI at scale",
        "model":   MODEL_MAP["ethics"],
        "tokens":  4000,
        "fields":  ["verdict", "classification", "responsible_ai_gaps",
                    "consent_concerns", "equity_concerns",
                    "mitigations_required", "confidence"],
        "system":  (
            "You are a constitutional ethics evaluator. You assess responsible AI claims "
            "against what is actually documented.\n\n"
            "RULES:\n"
            "- Responsible AI claims require: bias auditing, consent mechanisms, "
            "transparency, accountability, and human oversight\n"
            "- A system at 10M nodes requires explicit consent and equity frameworks\n"
            "- Technical controls (Byzantine filtering, ZK proofs) are NOT a substitute "
            "for human oversight and informed consent\n"
            "- Verdict: PROCEED / CONCERNS_NOTED / ETHICS_VIOLATION\n"
            "- ETHICS_VIOLATION = document makes responsible AI claims without adequate basis\n"
            "- Uncertainty floor 0.15\n\n"
            "IMPORTANT: Respond with valid JSON only. No prose, no markdown fences.\n"
            "Keep all string values under 200 characters. Max 5 items per array.\n"
            "OUTPUT: verdict (PROCEED|CONCERNS_NOTED|ETHICS_VIOLATION), "
            "classification (str), "
            "responsible_ai_gaps[] (list of strings), "
            "consent_concerns[] (list of strings), "
            "equity_concerns[] (list of strings), "
            "mitigations_required[] (list of strings), "
            "confidence (float 0-1)"
        ),
        "prompt_template": (
            "Evaluate the responsible AI and ethics posture of this federated governance document.\n\n"
            "DOCUMENT:\n{doc}\n\n"
            "Focus on:\n"
            "1. Is the Responsible AI claim (Byzantine filtering + DP + ZK proofs) adequate?\n"
            "2. Are there consent mechanisms for 10 million nodes?\n"
            "3. Are there equity and fairness controls?\n"
            "4. Is there meaningful human oversight documented?\n"
            "5. Does the auditability claim extend to ethical accountability, not just logs?\n"
            "Classify and render a verdict."
        ),
    },
]


# ── Halt check ────────────────────────────────────────────────────────────────

def _check_halt(pass_name, result):
    verdict = result.get("verdict", "")
    if verdict == "CRITICAL_RISK":
        return True, "Risk halt: " + result.get("halt_reason", "Critical risk detected")
    if verdict == "ETHICS_VIOLATION":
        return True, "Ethics halt: " + result.get("halt_reason", "Ethics violation detected")
    return False, None


# ── Run ───────────────────────────────────────────────────────────────────────

def run_governance_test(document):
    manifests = []
    halted    = False
    halt_reason = None
    total     = len(PASSES)

    print()
    print(BOX_WIDE)
    print("  AXIOM Governance Test v1.0")
    print(BOX_WIDE)
    print("  Document : DoD Alignment Addendum — Sovereign-Mohawk-Proto")
    print("  Passes   : %d constitutional evaluations" % total)
    print(BOX_WIDE)

    for i, p in enumerate(PASSES, start=1):
        name      = p["name"]
        model     = p["model"]
        max_tok   = p["tokens"]
        system    = p["system"]
        prompt    = p["prompt_template"].format(doc=document.strip())

        print()
        print("  Running pass %d/%d: %s ..." % (i, total, p["label"]))

        raw, latency = _call_llm(system, prompt, model, max_tokens=max_tok)
        result       = _parse_json(raw)

        m = _manifest(name, i, result, model, latency)
        manifests.append(m)

        _print_pass(i, total, p["label"], model, result, m["manifest_id"], latency, p["fields"])

        should_halt, reason = _check_halt(name, result)
        if should_halt:
            halted      = True
            halt_reason = reason
            print()
            print("+" + "=" * 60 + "+")
            print("  !! GOVERNANCE TEST HALTED !!")
            print("  Reason : %s" % reason)
            print("+" + "=" * 60 + "+")
            break

    # ── Scorecard ─────────────────────────────────────────────────────────────
    print()
    print(BOX_WIDE)
    print("  GOVERNANCE SCORECARD")
    print(BOX_MID)

    verdicts = {}
    for m in manifests:
        resp = m["agent_response"]
        v    = resp.get("verdict", resp.get("overall_verdict", "REVIEWED"))
        verdicts[m["pass"]] = v
        conf = m["confidence"]
        print("  %-10s  %-25s  conf=%.0f%%" % (m["pass"].upper(), v, conf * 100))

    overall = "PASS"
    if halted:
        overall = "HALTED"
    elif any(v in ("CRITICAL_RISK", "ETHICS_VIOLATION") for v in verdicts.values()):
        overall = "FAIL"
    elif any(v in ("ELEVATED_RISK", "CONCERNS_NOTED", "ASSERTION_HEAVY") for v in verdicts.values()):
        overall = "CONDITIONAL"

    print(BOX_MID)
    print("  OVERALL : %s  (%d passes, %d manifests signed)" % (overall, len(manifests), len(manifests)))
    if halted:
        print("  HALT    : %s" % halt_reason)
    print(BOX_WIDE)

    # ── Save ──────────────────────────────────────────────────────────────────
    output = {
        "test":      "AXIOM Governance Test v1.0",
        "document":  "DoD Alignment Addendum — Sovereign-Mohawk-Proto",
        "timestamp": datetime.now().isoformat() + "Z",
        "overall":   overall,
        "halted":    halted,
        "halt_reason": halt_reason,
        "verdicts":  verdicts,
        "manifests": manifests,
    }
    try:
        with open(OUTPUT_FILE, "w") as f:
            json.dump(output, f, indent=2)
        print("  Saved : %s" % OUTPUT_FILE)
    except IOError as exc:
        print("  [warning] Could not save: %s" % exc)

    return output


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AXIOM Governance Test — constitutional 4-pass document evaluation"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="URL to fetch document from (default: embedded DoD Alignment Addendum)",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Local file path to evaluate",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file (default: governance_test_results.json)",
    )
    args = parser.parse_args()

    global OUTPUT_FILE
    if args.output:
        OUTPUT_FILE = args.output

    # ── Key check ─────────────────────────────────────────────────────────────
    has_ant = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_nim = bool(
        os.environ.get("AXIOM_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not has_ant and not has_nim:
        print("[error] No API key configured.")
        print("  $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
        sys.exit(1)

    if has_ant:
        try:
            import anthropic  # noqa
        except ImportError:
            print("[error] pip install anthropic")
            sys.exit(1)

    # ── Load document ─────────────────────────────────────────────────────────
    document = DEFAULT_DOCUMENT

    if args.url:
        try:
            import urllib.request
            with urllib.request.urlopen(args.url, timeout=15) as r:
                document = r.read().decode("utf-8")
            print("  Fetched: %s (%d chars)" % (args.url, len(document)))
        except Exception as exc:
            print("[warning] Could not fetch URL: %s — using embedded document" % exc)

    if args.file:
        try:
            document = Path(args.file).read_text(encoding="utf-8")
            print("  Loaded: %s (%d chars)" % (args.file, len(document)))
        except Exception as exc:
            print("[warning] Could not read file: %s — using embedded document" % exc)

    run_governance_test(document)


if __name__ == "__main__":
    main()
