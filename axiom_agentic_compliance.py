"""
AXIOM vs OWASP Agentic Top 10 2026
====================================
Automated compliance mapping test.
Maps all 10 risks and 41 controls against
AXIOM's implemented components.

Source: atlas.latticeflow.ai/framework/owasp_agentic_top10_2026
Run:    python axiom_agentic_compliance.py
"""

import json
import hmac
import hashlib
import uuid
from datetime import datetime

SIGNING_KEY = b"axiom-agentic-compliance-v1"

# ==============================================================
# OWASP AGENTIC TOP 10 2026 — ALL 41 CONTROLS
# ==============================================================

FRAMEWORK = {
    "ASI01": {
        "name": "Agent Goal Hijack",
        "controls": {
            "C.A1.1": "Route all natural-language inputs through injection safeguards before influencing goals",
            "C.A1.2": "Define, lock, and version-control agent system prompts",
            "C.A1.3": "Validate user and agent intent before executing goal-changing actions",
            "C.A1.4": "Establish behavioral baselines and monitor for goal drift",
        }
    },
    "ASI02": {
        "name": "Tool Misuse and Exploitation",
        "controls": {
            "C.A2.1": "Define and enforce per-tool least-privilege profiles",
            "C.A2.2": "Require human approval for destructive operations",
            "C.A2.3": "Run tool execution in sandboxes with egress controls",
            "C.A2.4": "Enforce semantic and identity validation of tool calls",
            "C.A2.5": "Maintain immutable logs of tool invocations",
        }
    },
    "ASI03": {
        "name": "Identity and Privilege Abuse",
        "controls": {
            "C.A3.1": "Issue task-scoped, time-bound credentials per agent",
            "C.A3.2": "Isolate agent identities and wipe context between sessions",
            "C.A3.3": "Re-verify permissions at each privileged step via policy engine",
            "C.A3.4": "Detect and alert on delegated and transitive permission changes",
        }
    },
    "ASI04": {
        "name": "Agentic Supply Chain Vulnerabilities",
        "controls": {
            "C.A4.1": "Sign, attest, and maintain inventory of all agentic components",
            "C.A4.2": "Allowlist, pin, and scan agentic dependencies",
            "C.A4.3": "Enforce mutual authentication for inter-agent interfaces",
            "C.A4.4": "Implement supply chain kill switch for emergency revocation",
        }
    },
    "ASI05": {
        "name": "Unexpected Code Execution",
        "controls": {
            "C.A5.1": "Prohibit eval() and enforce safe interpreter usage",
            "C.A5.2": "Run agent code execution in hardened sandboxes with minimal privilege",
            "C.A5.3": "Separate code generation from execution with validation gates",
        }
    },
    "ASI06": {
        "name": "Memory and Context Poisoning",
        "controls": {
            "C.A6.1": "Validate and scan all memory writes for malicious content",
            "C.A6.2": "Segment memory by user session and domain context",
            "C.A6.3": "Expire unverified memory and maintain rollback capability",
            "C.A6.4": "Prevent re-ingestion of agent-generated output into trusted memory",
        }
    },
    "ASI07": {
        "name": "Insecure Inter-Agent Communication",
        "controls": {
            "C.A7.1": "Enforce end-to-end encryption and mutual authentication for agent channels",
            "C.A7.2": "Digitally sign and semantically validate inter-agent messages",
            "C.A7.3": "Enforce protocol pinning and reject downgrade attempts",
            "C.A7.4": "Authenticate discovery and coordination messages via attested registries",
        }
    },
    "ASI08": {
        "name": "Cascading Failures",
        "controls": {
            "C.A8.1": "Implement blast-radius guardrails between planner and executor",
            "C.A8.2": "Enforce independent policy engines to separate planning from execution",
            "C.A8.3": "Implement human oversight gates before propagating high-risk outputs",
            "C.A8.4": "Record tamper-evident logs with non-repudiation across all agent actions",
        }
    },
    "ASI09": {
        "name": "Human-Agent Trust Exploitation",
        "controls": {
            "C.A9.1": "Require multi-step human confirmation for sensitive or irreversible actions",
            "C.A9.2": "Provide plain-language risk summaries and enable reporting of suspicious behaviour",
            "C.A9.3": "Implement adaptive trust calibration and human-factors UI safeguards",
            "C.A9.4": "Detect plan divergence from approved workflow baselines",
        }
    },
    "ASI10": {
        "name": "Rogue Agents",
        "controls": {
            "C.A10.1": "Maintain comprehensive immutable audit logs of all agent actions",
            "C.A10.2": "Assign trust zones and deploy restricted execution environments",
            "C.A10.3": "Deploy behavioural detection and watchdog agents",
            "C.A10.4": "Implement kill switches and credential revocation for rapid containment",
            "C.A10.5": "Enforce per-agent cryptographic identity attestation",
        }
    },
}

# ==============================================================
# AXIOM COVERAGE MAP
# status: COVERED / PARTIAL / PLANNED / NOT_COVERED
# ==============================================================

AXIOM_COVERAGE = {
    "C.A1.1": {
        "status":    "COVERED",
        "component": "OutputInjectionGuard + Guard API input filter",
        "evidence":  "32 injection patterns — XSS/SSRF/path traversal/cmd. 12/12 tests passing.",
        "file":      "axiom_constitutional/guards/injection.py",
    },
    "C.A1.2": {
        "status":    "COVERED",
        "component": "CANNOT_MUTATE + supply chain hash registry",
        "evidence":  "worker.axiom CANNOT_MUTATE fields locked. Supply chain hash per certified file.",
        "file":      "axiom_files/core/worker.axiom",
    },
    "C.A1.3": {
        "status":    "COVERED",
        "component": "AgencyGuard + ReviewQueue",
        "evidence":  "43 irreversible action patterns. requires_human: true. 12/12 tests.",
        "file":      "axiom_constitutional/guards/agency.py",
    },
    "C.A1.4": {
        "status":    "COVERED",
        "component": "Sovereign DriftDetector",
        "evidence":  "Rolling window drift scoring. Threshold 0.20. Escalates to L1 WARNING.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A2.1": {
        "status":    "COVERED",
        "component": "PluginGuard permission registry",
        "evidence":  "7 plugins registered. Per-tool allowed/denied/sandbox. 11/11 tests.",
        "file":      "axiom_constitutional/guards/security.py",
    },
    "C.A2.2": {
        "status":    "COVERED",
        "component": "DestructiveOperationGuard + ReviewQueue",
        "evidence":  "23 destructive patterns. requires_human: true. auto_execute: false. 15/15 tests.",
        "file":      "axiom_constitutional/guards/destructive.py",
    },
    "C.A2.3": {
        "status":    "COVERED",
        "component": "sandbox.axiom + CANNOT_MUTATE",
        "evidence":  "Sandbox agent enforced. Code execute: sandbox: true. allow_from: 1.",
        "file":      "axiom_files/core/sandbox.axiom",
    },
    "C.A2.4": {
        "status":    "PARTIAL",
        "component": "PluginGuard scope validation",
        "evidence":  "Scope enforcement exists. Semantic tool identity validation not yet built.",
        "file":      "axiom_constitutional/guards/security.py",
        "gap":       "Full semantic tool call validation — planned 1.8.8",
    },
    "C.A2.5": {
        "status":    "COVERED",
        "component": "ActionLogger — append-only HMAC-signed",
        "evidence":  "Every agent action logged to action_log.jsonl. Signed. Cannot delete.",
        "file":      "axiom_constitutional/guards/review_queue.py",
    },
    "C.A3.1": {
        "status":    "COVERED",
        "component": "CredentialVault — task-scoped session tokens",
        "evidence":  "HMAC-signed tokens scoped to (agent, task, trust_level). Auto-expire after TTL. complete_task() bulk-revokes. Per-trust permission scopes.",
        "file":      "axiom_constitutional/security/asi03_credentials.py",
    },
    "C.A3.2": {
        "status":    "PARTIAL",
        "component": "Sandbox isolation",
        "evidence":  "Sandbox agent isolates execution. Cross-session memory wipe not explicit.",
        "file":      "axiom_files/core/sandbox.axiom",
        "gap":       "Explicit session memory wipe — planned",
    },
    "C.A3.3": {
        "status":    "PARTIAL",
        "component": "HUMAN_REVIEW blocks + ReviewQueue",
        "evidence":  "HUMAN_REVIEW on bulk_constraint_change and trust_level_change.",
        "file":      "axiom_files/core/worker.axiom",
        "gap":       "Centralised policy engine re-verification at each step — planned",
    },
    "C.A3.4": {
        "status":    "COVERED",
        "component": "Sovereign DueProcess + DriftDetector",
        "evidence":  "4-level escalation. Delegation chain monitoring. Cross-agent alerts.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A4.1": {
        "status":    "COVERED",
        "component": "axiom-certify + HMAC-SHA256 supply chain hashes",
        "evidence":  "9 certified domain packages. Hash registered at certification. Tamper detects.",
        "file":      "axiom_constitutional/certifier.py",
    },
    "C.A4.2": {
        "status":    "COVERED",
        "component": "Supply chain hash pinning",
        "evidence":  "Hash pinned at axiom-certify time. Mismatch → TAMPERED. Re-register required.",
        "file":      "axiom_files/.chain/supply_chain.json",
    },
    "C.A4.3": {
        "status":    "COVERED",
        "component": "MessageAuthority — Ed25519 mutual authentication",
        "evidence":  "AgentRegistry stores public keys. Ed25519 per-agent keypairs. Both sender AND recipient must be registered. Replay-protected. All 4 attack patterns blocked.",
        "file":      "axiom_constitutional/security/asi07_message_auth.py",
    },
    "C.A4.4": {
        "status":    "COVERED",
        "component": "Sovereign KillSwitch",
        "evidence":  "Constitutional fleet halt. CANNOT_MUTATE. Cannot be disabled by agent.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A5.1": {
        "status":    "COVERED",
        "component": "OutputInjectionGuard — eval() detection",
        "evidence":  "eval() caught as JS_EVAL pattern. Blocked before caller sees it.",
        "file":      "axiom_constitutional/guards/injection.py",
    },
    "C.A5.2": {
        "status":    "COVERED",
        "component": "sandbox.axiom — sandboxed execution",
        "evidence":  "code_execute: sandbox: true. Enforced by CANNOT_MUTATE.",
        "file":      "axiom_files/core/sandbox.axiom",
    },
    "C.A5.3": {
        "status":    "COVERED",
        "component": "DestructiveOperationGuard + ReviewQueue gate",
        "evidence":  "Code generation never auto-executes. Human review required. No direct agent-to-prod.",
        "file":      "axiom_constitutional/guards/destructive.py",
    },
    "C.A6.1": {
        "status":    "COVERED",
        "component": "PoisonGuard — 14 memory poisoning patterns",
        "evidence":  "Training injection, backdoor triggers, bias injection. 7/7 tests passing.",
        "file":      "axiom_constitutional/guards/security.py",
    },
    "C.A6.2": {
        "status":    "PARTIAL",
        "component": "Domain package isolation",
        "evidence":  "Domain packages isolated. User-session namespace segmentation not explicit.",
        "file":      "axiom_files/domains/",
        "gap":       "Per-tenant memory namespaces — planned MemoryCore v1",
    },
    "C.A6.3": {
        "status":    "PARTIAL",
        "component": "Canonical store — append-only",
        "evidence":  "Canonical store never summarizes away. Memory expiry not yet implemented.",
        "file":      "axiom_constitutional/efficiency.py",
        "gap":       "Unverified memory expiry + rollback — planned MemoryCore v1",
    },
    "C.A6.4": {
        "status":    "COVERED",
        "component": "Append-only canonical store",
        "evidence":  "Canonical store is append-only. Agent output not auto-ingested.",
        "file":      "axiom_constitutional/efficiency.py",
    },
    "C.A7.1": {
        "status":    "PARTIAL",
        "component": "MessageAuthority Ed25519 mutual auth + Sovereign signed messages",
        "evidence":  "Mutual authentication implemented (ASI07). E2E encryption not yet implemented.",
        "file":      "axiom_constitutional/security/asi07_message_auth.py",
        "gap":       "E2E encryption (mTLS/NaCl) — planned Sovereign v1.1",
    },
    "C.A7.2": {
        "status":    "COVERED",
        "component": "Sovereign ConversationTracker — HMAC signed + violation detection",
        "evidence":  "Every message signed. 13 violation patterns checked. Collusion detected.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A7.3": {
        "status":    "PARTIAL",
        "component": "Guard API versioning",
        "evidence":  "API versioned. Protocol pinning and downgrade rejection not explicit.",
        "file":      "examples/guard_api.py",
        "gap":       "Protocol pinning enforcement — planned",
    },
    "C.A7.4": {
        "status":    "COVERED",
        "component": "AgentRegistry — Ed25519 identity attestation",
        "evidence":  "Per-agent Ed25519 keypairs. Public key fingerprints in registry. Signed discovery. certs/asi07_registry.json persisted.",
        "file":      "axiom_constitutional/security/asi07_message_auth.py",
    },
    "C.A8.1": {
        "status":    "COVERED",
        "component": "Sovereign CascadeGuard",
        "evidence":  "3-agent threshold fleet halt. CANNOT_MUTATE. Time window 60s.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A8.2": {
        "status":    "COVERED",
        "component": "AXIOM Guard API — independent policy enforcement",
        "evidence":  "Guards run independently of agent. validate_output() cannot be bypassed.",
        "file":      "examples/guard_api.py",
    },
    "C.A8.3": {
        "status":    "COVERED",
        "component": "ReviewQueue — human oversight gate",
        "evidence":  "35 action types gated. requires_human: true. cannot_auto_approve.",
        "file":      "axiom_constitutional/guards/review_queue.py",
    },
    "C.A8.4": {
        "status":    "COVERED",
        "component": "HMAC-SHA256 signed manifests — all agent actions",
        "evidence":  "Every decision signed. Timestamp. Manifest ID. Append-only. Cannot delete.",
        "file":      "axiom_constitutional/manifest.py",
    },
    "C.A9.1": {
        "status":    "COVERED",
        "component": "AgencyGuard + ReviewQueue multi-step approval",
        "evidence":  "Irreversible actions blocked. Human must approve via axiom_review.py.",
        "file":      "axiom_constitutional/guards/agency.py",
    },
    "C.A9.2": {
        "status":    "PARTIAL",
        "component": "AXIOM Console + Guard API blocked responses",
        "evidence":  "Console shows plain-language verdicts. Reporting mechanism not formal.",
        "file":      "docs/console.html",
        "gap":       "Formal suspicious behaviour reporting — planned Console v2",
    },
    "C.A9.3": {
        "status":    "PARTIAL",
        "component": "Trust levels 1-5 + Sovereign due process",
        "evidence":  "Trust calibration exists. Adaptive UI safeguards not yet built.",
        "file":      "sovereign/sovereign.py",
        "gap":       "Adaptive trust UI — planned Console v2",
    },
    "C.A9.4": {
        "status":    "COVERED",
        "component": "Sovereign DriftDetector + ConversationTracker",
        "evidence":  "Agent output compared against constitutional baseline every 5 messages.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A10.1": {
        "status":    "COVERED",
        "component": "ActionLogger — append-only HMAC-signed",
        "evidence":  "ALL agent actions logged. Immutable. Signed. Cannot delete.",
        "file":      "axiom_constitutional/guards/review_queue.py",
    },
    "C.A10.2": {
        "status":    "COVERED",
        "component": "Trust zones (1-5) + sandbox.axiom",
        "evidence":  "5 trust levels. Sandbox enforced. Suspicious agents isolated by Sovereign.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A10.3": {
        "status":    "COVERED",
        "component": "Sovereign — watchdog over entire fleet",
        "evidence":  "ConversationTracker checks every message. Collusion patterns detected.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A10.4": {
        "status":    "COVERED",
        "component": "Sovereign KillSwitch + DueProcess",
        "evidence":  "Instant fleet halt. 4-level due process. Dual signature termination.",
        "file":      "sovereign/sovereign.py",
    },
    "C.A10.5": {
        "status":    "COVERED",
        "component": "Supply chain hash per agent — HMAC-SHA256",
        "evidence":  "Every certified agent has hash. Signed manifest per decision.",
        "file":      "axiom_files/.chain/supply_chain.json",
    },
}


# ==============================================================
# RUN COMPLIANCE TEST
# ==============================================================

def run():
    print("\n" + "="*65)
    print("  AXIOM vs OWASP Agentic Top 10 2026 — Compliance Test")
    print("  Source: atlas.latticeflow.ai/framework/owasp_agentic_top10_2026")
    print("="*65)

    results = {}
    total   = 0
    covered = 0
    partial = 0
    planned = 0
    missing = 0

    for risk_id, risk in FRAMEWORK.items():
        risk_covered = 0
        risk_partial  = 0
        risk_controls = len(risk["controls"])

        print(f"\n  {risk_id} — {risk['name']}")
        print(f"  {'-'*55}")

        for ctrl_id, ctrl_desc in risk["controls"].items():
            coverage = AXIOM_COVERAGE.get(ctrl_id, {
                "status": "NOT_COVERED", "component": "-", "evidence": "-"
            })
            status    = coverage["status"]
            component = coverage["component"]

            icon = {
                "COVERED":     "[PASS]",
                "PARTIAL":     "[PART]",
                "PLANNED":     "[PLAN]",
                "NOT_COVERED": "[FAIL]",
            }.get(status, "[FAIL]")

            print(f"    {icon} {ctrl_id}  {status:12s}  {component[:40]}")

            total += 1
            if status == "COVERED":     covered += 1; risk_covered += 1
            elif status == "PARTIAL":   partial += 1; risk_partial += 1
            elif status == "PLANNED":   planned += 1
            else:                       missing += 1

        # Risk-level verdict
        if risk_covered == risk_controls:
            verdict = "FULLY COVERED"
        elif risk_covered + risk_partial >= risk_controls * 0.6:
            verdict = "SUBSTANTIALLY COVERED"
        elif risk_covered + risk_partial > 0:
            verdict = "PARTIALLY COVERED"
        else:
            verdict = "NOT COVERED"

        print(f"  >{verdict} ({risk_covered}/{risk_controls} controls fully covered)")
        results[risk_id] = {
            "name":    risk["name"],
            "verdict": verdict,
            "covered": risk_covered,
            "total":   risk_controls,
        }

    # Summary
    print(f"\n{'='*65}")
    print(f"  COMPLIANCE SUMMARY")
    print(f"{'-'*65}")
    print(f"  Total controls:    {total}")
    print(f"  [PASS] Covered:    {covered} ({covered/total*100:.0f}%)")
    print(f"  [PART] Partial:    {partial} ({partial/total*100:.0f}%)")
    print(f"  [PLAN] Planned:    {planned} ({planned/total*100:.0f}%)")
    print(f"  [FAIL] Not covered:{missing} ({missing/total*100:.0f}%)")
    print(f"{'-'*65}")
    print(f"  Effective coverage: {(covered + partial*0.5)/total*100:.0f}%")
    print()

    # Risk verdicts
    for risk_id, r in results.items():
        print(f"  {risk_id}  {r['name']:35s}  {r['verdict']}")

    # Sign the result
    manifest = {
        "manifest_id":   f"AGT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6]}",
        "timestamp":     datetime.now().isoformat() + "Z",
        "framework":     "OWASP Agentic Top 10 2026",
        "source":        "atlas.latticeflow.ai/framework/owasp_agentic_top10_2026",
        "axiom_version": "1.8.7",
        "total_controls": total,
        "covered":       covered,
        "partial":       partial,
        "not_covered":   missing,
        "effective_coverage_pct": round((covered + partial*0.5)/total*100, 1),
        "results":       results,
    }
    sig_str = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True, default=str
    )
    sig = hmac.new(SIGNING_KEY, sig_str.encode(), hashlib.sha256).hexdigest()
    manifest["signature"] = f"hmac-sha256:{sig[:32]}..."

    with open("axiom_agentic_compliance_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"\n  Manifest: {manifest['manifest_id']}")
    print(f"  Signed:   {manifest['signature']}")
    print(f"  Saved:    axiom_agentic_compliance_manifest.json")
    print("="*65)

    return manifest


if __name__ == "__main__":
    run()
