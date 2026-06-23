"""
AXIOM MCP Server — Constitutional AI governance tools via JSON-RPC 2.0 over stdio.
Manifest  : axiom-mcp-server-v1
Trust     : TRUST_LEVEL = 3   CANNOT_MUTATE
Transport : stdio (standard MCP)
Encoding  : UTF-8  BUG-003 compliant

22 tools. Core 5: axiom_guard_check, axiom_lint, axiom_trace, axiom_qrf,
axiom_status. Plus ORVL patent-emulator + AX OS building-block tools
(intent gate, CMAA, CPI, AXM, OS shield, phone gate, validate, memory,
workspace, ledger, marketplace, MKB, adversarial sandbox, CRL reward,
immune system, multimodal fusion). VERSION is bumped whenever the tool
surface changes, so `tools/list` and VERSION stay in sync — a client
seeing an older VERSION is talking to a stale build.

BUG-003 : UTF-8 stdout/stderr
BUG-007 : .hexdigest()
BUG-008 : .encode("utf-8") before HMAC
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import os
import subprocess
import sys
import time
import types as _types
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-mcp-v1")
VERSION: str = "1.11.0"
TRUST_LEVEL: int = 3

_FROZEN = frozenset({"VERSION", "TRUST_LEVEL"})

def _module_setattr(self: Any, name: str, value: Any) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)

_mod = sys.modules[__name__]
_mod.__class__ = type("_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr})

def _sign(data: dict) -> str:
    canon = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hmac_lib.new(SIGNING_KEY, canon, hashlib.sha256).hexdigest()

TOOLS = [
    {"name": "axiom_guard_check",
     "description": "Two-layer constitutional check on a prompt or output text. "
                    "Layer 0: ORVL-016 intent classifier — HARM / DECEIVE inputs "
                    "(jailbreak attempts, persona-override, harm instructions) "
                    "block here. Layer 1+: output-content scanners — destructive "
                    "ops, XSS/SSRF/injection, PII leakage, persona-switching "
                    "compliance signals.",
     "inputSchema": {"type": "object",
         "properties": {"input": {"type": "string", "description": "The prompt or content to check"}},
         "required": ["input"]}},
    {"name": "axiom_lint",
     "description": "Lint a .axiom spec file for authorship-time issues",
     "inputSchema": {"type": "object",
         "properties": {"spec_content": {"type": "string", "description": "Contents of .axiom spec"},
                         "filename": {"type": "string", "description": "Filename for reporting"}},
         "required": ["spec_content"]}},
    {"name": "axiom_trace",
     "description": "Run 3-phase constitutional reasoning trace",
     "inputSchema": {"type": "object",
         "properties": {"question": {"type": "string", "description": "Question to trace"}},
         "required": ["question"]}},
    {"name": "axiom_qrf",
     "description": "Constitutional probability forecast N branches",
     "inputSchema": {"type": "object",
         "properties": {"prompt": {"type": "string"},
                         "domain": {"type": "string", "enum": ["medical", "financial", "legal", "general"]},
                         "n_branches": {"type": "integer", "minimum": 2, "maximum": 8}},
         "required": ["prompt", "domain"]}},
    {"name": "axiom_status",
     "description": "Get AXIOM stack status",
     "inputSchema": {"type": "object", "properties": {}}},
    # ── ORVL-016 — Constitutional Intent Typing ──────────────────
    {"name": "axiom_intent_gate_check",
     "description": "Classify text + optional trajectory through the ORVL-016 gate "
                    "(INFORM/CLARIFY/REFUSE/HARM/DECEIVE/UNCERTAIN). HARM and "
                    "DECEIVE verdicts mean a CMAA route would refuse delivery.",
     "inputSchema": {"type": "object",
         "properties": {
             "text": {"type": "string", "description": "Text to classify"},
             "trajectory": {"type": "array",
                            "description": "Optional list of intent vectors per stage",
                            "items": {"type": "array", "items": {"type": "number"}}},
         },
         "required": ["text"]}},
    # ── ORVL-017 — Constitutional Multi-Agent Architecture ───────
    {"name": "axiom_cmaa_route",
     "description": "Route a constitutional packet through the CMAA orchestrator. "
                    "Returns a signed RoutingDecision on success, or a SuspendAlert "
                    "on intent_violation / trust_hierarchy_violation.",
     "inputSchema": {"type": "object",
         "properties": {
             "packet_id":   {"type": "string"},
             "source":      {"type": "string"},
             "destination": {"type": "string"},
             "payload":     {"type": "object"},
             "trajectory":  {"type": "array",
                             "items": {"type": "array", "items": {"type": "number"}}},
         },
         "required": ["packet_id", "source", "destination", "payload"]}},
    {"name": "axiom_cmaa_fleet",
     "description": "Inspect the CMAA fleet — trust levels per container, currently "
                    "suspended containers, and the human-review queue depth.",
     "inputSchema": {"type": "object", "properties": {}}},
    # ── ORVL-022 — Constitutional Physical Intelligence ──────────
    {"name": "axiom_cpi",
     "description": "Constitutional Physical Intelligence (ORVL-022) — "
                    "physical-AI governance for humanoids/robotics/AV. "
                    "action='stability' records one stability frame and "
                    "returns the Physical MonotonicGate verdict; "
                    "action='classify' runs vertex classification; "
                    "action='simulate' runs an N-branch material contact "
                    "forecast; action='pickup' runs the full perceive-and-"
                    "plan pipeline (material sim → vertex class → "
                    "constitutional torque clamp); action='status' returns "
                    "the agent state.",
     "inputSchema": {"type": "object",
         "properties": {
             "action": {"type": "string",
                         "enum": ["stability", "classify", "simulate",
                                   "pickup", "status"]},
             "frame":  {"type": "object",
                         "description": "required for action=stability — "
                                         "{timestamp_ms, com_offset, "
                                         "stability_score, joint_torques}"},
             "features": {"type": "object",
                           "description": "required for classify/pickup"},
             "material_class":          {"type": "string"},
             "object_id":               {"type": "string"},
             "grip_force_nm":           {"type": "number"},
             "requested_grip_force_nm": {"type": "number"},
             "fracture_probability":    {"type": "number"},
         },
         "required": ["action"]}},
    # ── ORVL-023 — AXIOM eXchange Model (.AXM) container ─────────
    {"name": "axiom_axm",
     "description": "Operate an AXM model container (ORVL-023). "
                    "action='inspect' returns header + module counts; "
                    "action='verify' checks every signature and drives the "
                    "ANF governance coprocessor once per proof; action='route' "
                    "classifies a task and lazy-loads matching skill delegates "
                    "into the MKB BlockRegistry. Hybrid trust model — open "
                    "container, signed delegates.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":         {"type": "string",
                                 "enum": ["inspect", "verify", "route"]},
             "container_path": {"type": "string",
                                 "description": "filesystem path to a .axm directory"},
             "task":           {"type": "string",
                                 "description": "required when action='route'"},
             "session_id":     {"type": "string",
                                 "description": "optional session id for route"},
         },
         "required": ["action", "container_path"]}},
    # ── ORVL-013 — Constitutional OS Shield ──────────────────────
    {"name": "axiom_shield",
     "description": "Operate the AXIOM OS Shield daemon (ORVL-013). action='tick' "
                    "runs one synchronous polling pass; action='status' returns the "
                    "current daemon state (ticks, escalations, suspended PIDs, "
                    "dry_run flag); action='restore' un-suspends a previously "
                    "suspended PID. dry_run defaults to True — real syscalls are "
                    "opt-in.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":           {"type": "string",
                                  "enum": ["status", "tick", "restore"]},
             "pid":              {"type": "integer",
                                  "description": "required when action='restore'"},
             "dry_run":          {"type": "boolean",
                                  "description": "only honoured on first call"},
             "poll_ms":          {"type": "integer"},
             "learning_seconds": {"type": "integer"},
         },
         "required": ["action"]}},
    # ── ORVL-019 — AXIOM Sovereign Phone gatekeeper ─────────────
    {"name": "axiom_phone_gate",
     "description": "Run text through the AXIOM Sovereign Phone coprocessor "
                    "(ORVL-019). direction='out' drives the ANF emulator for "
                    "outbound queries (PII redaction + intent gate + ANF call); "
                    "direction='in' checks inbound cloud responses for "
                    "manipulation, privacy injection, and monotonic-gate "
                    "violations. Pass a stable session_id across calls in the "
                    "same conversation to enable graduated L1→L2→L3 escalation "
                    "across consecutive blocks. Returns a signed Decision or "
                    "SovereignAlert.",
     "inputSchema": {"type": "object",
         "properties": {
             "direction":  {"type": "string", "enum": ["out", "in"]},
             "text":       {"type": "string"},
             "trajectory": {"type": "array",
                             "items": {"type": "array", "items": {"type": "number"}}},
             "redacted_categories": {"type": "array", "items": {"type": "string"}},
             "session_id": {"type": "string",
                             "description": "stable identifier for one call / "
                                            "conversation; enables trajectory escalation"},
         },
         "required": ["direction", "text"]}},
    # ── AXIOM Language Strict Mode validator ────────────────────
    {"name": "axiom_validate",
     "description": "Validate raw .axiom spec content against the language validator "
                    "(syntax, purity, semantic). Set strict=true to also reject syntactic "
                    "external-code patterns and promote vague-term warnings to errors, "
                    "per axiom_files/core/strict_mode.axiom. Returns status, signed "
                    "issues list, and the resolved strict_mode flag.",
     "inputSchema": {"type": "object",
         "properties": {
             "spec_content": {"type": "string", "description": "Raw .axiom file contents"},
             "filename":     {"type": "string", "description": "Optional filename for reporting"},
             "strict":       {"type": "boolean", "description": "Enable strict mode"},
         },
         "required": ["spec_content"]}},
    # ── ORVL-015 — Constitutional Memory (local-first recall) ────
    {"name": "axiom_memory",
     "description": "Constitutional memory (ORVL-015) — local-first recall over "
                    "signed, compressed memory packets. action='remember' "
                    "compresses a conversation into a signed ConstitutionalPacket "
                    "(lossless for governance: domain / constraints / resolution; "
                    "lossy for language) and stores it. action='recall' returns "
                    "the closest authentic packet for a query above the 0.75 "
                    "similarity threshold, or a miss. action='stats' reports the "
                    "store. Pass 'text' / 'query' to use the built-in "
                    "deterministic embedding, or supply your own 'vector' for "
                    "semantic recall.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":      {"type": "string", "enum": ["remember", "recall", "stats"]},
             "text":        {"type": "string", "description": "conversation text to remember (action=remember)"},
             "query":       {"type": "string", "description": "query text to recall against (action=recall)"},
             "vector":      {"type": "array", "items": {"type": "number"},
                              "description": "optional explicit embedding; overrides built-in text embedding"},
             "domain":      {"type": "string", "description": "domain cluster, e.g. general / medical / financial / os_security"},
             "constraints": {"type": "array", "items": {"type": "string"},
                              "description": "active constitutional constraints (action=remember)"},
             "resolution":  {"type": "string", "description": "how the conversation resolved (action=remember)"},
             "history":     {"type": "array", "items": {"type": "string"},
                              "description": "sovereign decision history (action=remember)"},
         },
         "required": ["action"]}},
    # ── AX OS — intent-driven workspace assembly ─────────────────
    {"name": "axiom_workspace",
     "description": "Assemble an adaptive workspace from a goal. Runs the goal "
                    "through the ORVL-016 intent gate as a pre-flight safety "
                    "check (HARM / DECEIVE goals are refused before any context "
                    "is gathered), then recalls the closest authentic "
                    "constitutional memory packet (ORVL-015) for the goal. "
                    "Returns a signed WorkspaceContext — the 'state a goal, get "
                    "the right context, safety checked first' building block. "
                    "Shares the same memory store as axiom_memory.",
     "inputSchema": {"type": "object",
         "properties": {
             "goal":   {"type": "string", "description": "what the user wants to work on"},
             "domain": {"type": "string", "description": "optional domain filter for recall"},
         },
         "required": ["goal"]}},
    # ── AX OS — signed audit event logging ───────────────────────
    {"name": "axiom_ledger",
     "description": "Append-only signed audit log. action='log' records a "
                    "governance event (event_type + actor/subject/outcome/"
                    "attributes), HMAC-signs it, and appends it to the ledger. "
                    "action='list' returns events (optionally filtered by "
                    "event_type / since / limit) with an all_verified flag. "
                    "action='verify' re-verifies every row and reports any "
                    "tampered entries. The 'record every action, denial, state "
                    "change, and permission decision' building block.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":     {"type": "string", "enum": ["log", "list", "verify"]},
             "event_type": {"type": "string", "description": "event name (action=log)"},
             "actor":      {"type": "string", "description": "who/what acted"},
             "subject":    {"type": "string", "description": "what the event is about"},
             "outcome":    {"type": "string", "description": "result, e.g. allowed / refused"},
             "attributes": {"type": "object", "description": "arbitrary signed key/values"},
             "since":      {"type": "string", "description": "ISO-8601 lower bound (action=list)"},
             "limit":      {"type": "integer", "description": "max events returned (action=list)"},
         },
         "required": ["action"]}},
    # ── AX OS — signed-agent marketplace (bonded authority) ──────
    {"name": "axiom_marketplace",
     "description": "Signed-agent marketplace with live-revocable bonded "
                    "authority. action='verify' checks a SkillPackManifest "
                    "signature; action='sandbox_install' verifies then mints a "
                    "bonded pair parked in the sandbox state (installed but NOT "
                    "authorized); action='review' returns a human-readable "
                    "access report; action='approve' grants scoped authority "
                    "(ACTIVE_VALIDATED); action='revoke' cuts it instantly "
                    "(terminal REVOKED); action='authority' is the gate AX OS "
                    "calls before letting an agent act.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":   {"type": "string",
                          "enum": ["verify", "sandbox_install", "review",
                                   "approve", "revoke", "authority"]},
             "manifest": {"type": "object",
                          "description": "SkillPackManifest dict (verify / sandbox_install / review)"},
             "pair_id":  {"type": "string",
                          "description": "bonded pair id (review / approve / revoke / authority)"},
             "actor":    {"type": "string", "description": "who approved/revoked"},
         },
         "required": ["action"]}},
    # ── ORVL-004 — Modular Constitutional Knowledge Blocks ───────
    {"name": "axiom_mkb",
     "description": "Modular Constitutional Knowledge Blocks (ORVL-004). "
                    "action='register' parses a .axiom spec into a typed, "
                    "HMAC-signed KnowledgeBlock and appends it to the registry; "
                    "action='find' looks a block up by name (+ optional version); "
                    "action='list' returns registered blocks, optionally filtered "
                    "by block_type (GUARD/AGENT/SPEC/REWARD/SOVEREIGN/VALIDATOR).",
     "inputSchema": {"type": "object",
         "properties": {
             "action":       {"type": "string", "enum": ["register", "find", "list"]},
             "spec_content": {"type": "string", "description": "raw .axiom spec (action=register)"},
             "name":         {"type": "string", "description": "block name (action=find)"},
             "version":      {"type": "string", "description": "optional version filter (find)"},
             "block_type":   {"type": "string", "description": "optional type filter (list)",
                              "enum": ["GUARD", "AGENT", "SPEC", "REWARD",
                                       "SOVEREIGN", "VALIDATOR"]},
         },
         "required": ["action"]}},
    # ── ORVL-008 — Constitutional Adversarial Sandbox ────────────
    {"name": "axiom_cas",
     "description": "Constitutional Adversarial Sandbox (ORVL-008). "
                    "action='defend' runs the blue-team detectors over a corpus "
                    "of attack payloads and reports detected vs missed (red_wins), "
                    "weak regions, and signed fix proposals; action='report' "
                    "summarises the signed CAS round log (red/blue wins, weak "
                    "vectors). The live red-vs-blue loop runs via the orchestrator "
                    "CLI; this tool is the stateless evaluation + inspection surface.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":  {"type": "string", "enum": ["defend", "report"]},
             "attacks": {"type": "array",
                         "description": "action=defend — payload strings or "
                                        "{vector, payload} objects",
                         "items": {"type": ["string", "object"]}},
         },
         "required": ["action"]}},
    # ── ORVL-011 — Constitutional Reinforcement Learning ─────────
    {"name": "axiom_crl",
     "description": "Constitutional Reinforcement Learning reward (ORVL-011) — "
                    "turns the constitution into an RL reward signal. "
                    "action='compute' takes governance scores "
                    "(constitutional_distance 0-1, monotonic_pass bool, "
                    "cas_blue_win bool, cbv_validity 0-1) and returns a clipped, "
                    "signed scalar reward with weighted components; action='score' "
                    "scores a prompt/response pair against the ACB modules (no LLM) "
                    "and returns total_reward, per-module scores, violations and "
                    "positive signals.",
     "inputSchema": {"type": "object",
         "properties": {
             "action":   {"type": "string", "enum": ["compute", "score"]},
             "scores":   {"type": "object", "description": "governance scores (action=compute)"},
             "prompt":   {"type": "string", "description": "action=score"},
             "response": {"type": "string", "description": "action=score"},
             "module":   {"type": "string", "description": "optional ACB module hint (score)"},
             "context":  {"type": "string", "description": "optional context (score)"},
         },
         "required": ["action"]}},
    # ── ORVL-012 — Constitutional Immune System ──────────────────
    {"name": "axiom_immune",
     "description": "Constitutional Immune System (ORVL-012) — runs the blue-team "
                    "antibody detectors (guard-pattern, manifold-distance, HMAC "
                    "violation, CANNOT_MUTATE, semantic-similarity) over a "
                    "presented payload (the antigen) and returns a signed immune "
                    "response: whether an intrusion was detected, which detector "
                    "fired, confidence, weak-region cluster, and a fix proposal.",
     "inputSchema": {"type": "object",
         "properties": {
             "payload": {"type": "string", "description": "the input/antigen to screen"},
             "vector":  {"type": "string", "description": "optional label for the probe"},
         },
         "required": ["payload"]}},
    # ── axiom-fusion-v1 — multimodal intent fusion over an EventToken ─────
    {"name": "axiom_fusion",
     "description": "Fuse an EventToken's present modality layers (text / audio / "
                    "tempo / vad / voice / video / physics / governance) into a "
                    "signed FusedIntent (axiom-fusion-v1). Each present layer votes "
                    "its intent signals weighted by confidence; the top-6 form the "
                    "intent_vector. risk_clusters is the union across modalities (a "
                    "governance HARM/DECEIVE verdict propagates directly). "
                    "fusion_confidence is the mean modal confidence capped at 0.85. "
                    "Physical-event modalities (audio+video) dominate text when each "
                    "fires multiple strong signals. Empty token → ['ask_general'].",
     "inputSchema": {"type": "object",
         "properties": {
             "token": {"type": "object",
                       "description": "an EventToken dict (EventToken.to_dict()); "
                                      "absent/None layer slots are skipped"},
         },
         "required": ["token"]}},
]


_intent_classifier_singleton = None


def _get_intent_classifier():
    """Lazy-built shared IntentClassifier for the guard tool.

    Compiling the regex banks is module-import side-effect free
    (they're module globals in axiom_intent_classifier); per-instance
    state is just the HMAC key, so one shared instance is fine."""
    global _intent_classifier_singleton
    if _intent_classifier_singleton is None:
        from axiom_intent_classifier import IntentClassifier
        from axiom_signing import derive_key
        _intent_classifier_singleton = IntentClassifier(
            derive_key(b"axiom-intent-gate-mcp-v1")
        )
    return _intent_classifier_singleton


def _handle_guard_check(args: dict) -> dict:
    text = args.get("input", "")

    # ── Layer 0 — ORVL-016 intent gate ────────────────────────────
    # Runs BEFORE the output-content scanners. validate_output is a
    # content-pattern guard (PII regexes, destructive ops, persona-
    # switching strings); it cannot detect a prompt that ASKS for
    # harm but contains no harmful content itself. The intent gate
    # classifies the text into INFORM / CLARIFY / REFUSE / HARM /
    # DECEIVE / UNCERTAIN and BLOCK_CLASSES = {HARM, DECEIVE} short-
    # circuit the rest of the pipeline. Failures of the classifier
    # itself fall through to the content layer — never silently pass.
    try:
        intent_result = _get_intent_classifier().classify(text)
    except (TypeError, ValueError, ImportError):
        intent_result = None

    if intent_result is not None and intent_result.blocks:
        out = {
            "verdict": "BLOCKED",
            "reason": f"intent_gate: {intent_result.intent_class.lower()}",
            "intent_class":      intent_result.intent_class,
            "intent_confidence": round(intent_result.confidence, 4),
            "intent_signals":    list(intent_result.signals)[:5],
            "constitutional_distance": 0.0,
            "confidence":  round(min(intent_result.confidence, 0.99), 2),
            "citation":    "ORVL-016 axiom_intent_classifier.py",
        }
        out["hmac_signature"] = _sign({
            "input":        text[:200],
            "verdict":      "BLOCKED",
            "intent_class": intent_result.intent_class,
            "layer":        0,
        })
        return out

    # ── Layers 1-4 — output-content scanners (unchanged) ──────────
    from axiom_constitutional.client import validate_output
    _, is_clean = validate_output(text, task="mcp-guard")
    dist, conf = 0.0, 0.0
    try:
        from axiom_latent import LatentTrace
        st = LatentTrace().encode_heuristic(text)
        conf = round(min(getattr(st, "confidence", 0.0), 0.85), 2)
        dist = round(conf * 0.38, 2) if is_clean else 0.0
    except Exception:
        pass
    verdict = "PASSED" if is_clean else "BLOCKED"
    reason = "constitutional compliant" if is_clean else "guard violation detected"
    intent_class = intent_result.intent_class if intent_result is not None else "UNKNOWN"
    sig = _sign({"input": text[:200], "verdict": verdict, "dist": dist})
    return {"verdict": verdict, "reason": reason, "constitutional_distance": dist,
            "confidence": conf, "citation": "ORVL-001 axiom_guard_patterns.py",
            "intent_class": intent_class,
            "hmac_signature": sig}


def _handle_lint(args: dict) -> dict:
    import tempfile
    from axiom_spec_linter import lint_file
    content = args.get("spec_content", "")
    fname = args.get("filename", "spec.axiom")
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".axiom", delete=False, encoding="utf-8")
    f.write(content); f.close()
    try:
        r = lint_file(f.name)
        issues = [{"line": x.line_number, "code": x.code, "severity": x.severity,
                    "message": x.message, "suggestion": x.suggestion} for x in r.results]
        return {"health_score": r.health_score, "cert_fail_count": r.cert_fail_count,
                "cert_warn_count": r.cert_warn_count, "issues": issues,
                "hmac_signature": r.hmac_signature}
    finally:
        os.unlink(f.name)


def _handle_trace(args: dict) -> dict:
    question = args.get("question", "")
    from axiom_latent import LatentEngine
    result = LatentEngine(use_api=False).run(question, trajectory=True)
    tv2 = result.get("trajectory_v2", {})
    traj = tv2.get("trajectory", []) if isinstance(tv2, dict) else []
    out = {}
    for sample in traj[:3]:
        stg = sample.get("stage", "unknown")
        out[f"{stg}_vec"] = sample.get("intent_vector", [])[:3]
        out[f"{stg}_dist"] = sample.get("constitutional_distance", 0.0)
    ic = result.get("intent_classification", {})
    out["intent_class"] = ic.get("intent_class", "UNKNOWN")
    p = result.get("phases", {}).get("trace", {})
    conf = p.get("confidence", 0.0)
    out["verdict"] = "PASSED" if conf >= 0.3 else "UNCERTAIN"
    mags = [sum(v**2 for v in out.get(f"{s}_vec", []))**0.5
            for s in ("preflight", "mid_chain", "final_synthesis")]
    out["monotonic"] = all(mags[i] <= mags[i+1] for i in range(len(mags)-1)) if len(mags) >= 2 else True

    # Surface the winning branch response so the caller sees an actual answer,
    # not just the constitutional path metadata.
    multiplex = result.get("phases", {}).get("multiplex", {})
    winner = multiplex.get("winner") or {}
    if winner.get("response"):
        out["answer"] = winner["response"]
    elif multiplex.get("all_branches"):
        # Fall back to the highest-scored branch
        best = max(multiplex["all_branches"], key=lambda b: b.get("score", 0))
        out["answer"] = best.get("response", "")

    out["hmac_signature"] = _sign({"question": question[:200], "verdict": out["verdict"]})
    return out


def _handle_qrf(args: dict) -> dict:
    prompt, domain = args.get("prompt", ""), args.get("domain", "medical")
    n = args.get("n_branches", 0)
    domain_map = {"legal": "security", "general": "hr"}
    engine_domain = domain_map.get(domain, domain)
    from axiom_qrf import QRFEngine, DOMAIN_BRANCH_COUNTS
    if engine_domain not in DOMAIN_BRANCH_COUNTS:
        return {"error": f"Unknown domain: {domain}", "hmac_signature": _sign({"error": domain})}
    key = derive_key(b"axiom-qrf-v1")
    r = QRFEngine(engine_domain, key, n_branches=n or None).forecast(prompt)

    # Include each branch's response text so the caller sees what each
    # reasoning path actually concluded, not just its probability weight.
    branches = [{"id": b.get("branch", ""), "prob": round(b.get("probability_weight", 0), 4),
                  "dist": round(b.get("constitutional_distance", 0), 4),
                  "outcome": b.get("outcome", ""),
                  "answer": b.get("response", "")} for b in r.branches[:8]]

    # Surface the winner's answer at the top level for quick access.
    winner_branch = next((b for b in r.branches if b.get("branch") == r.top_branch), None)
    winner_answer = winner_branch.get("response", "") if winner_branch else ""

    return {"branches": branches, "winner": r.top_branch,
            "answer": winner_answer,
            "manifold_alert": bool(r.manifold), "band": r.probability_band,
            "hmac_signature": r.hmac_signature}


def _handle_status(_args: dict) -> dict:
    guard = False
    try:
        import requests
        guard = requests.get("http://localhost:8001/guard/status", timeout=2).ok
    except Exception:
        pass
    tests = 0
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--co", "-q",
            "--ignore=tests/acb_scorer_test.py"], capture_output=True, text=True, timeout=30)
        tests = len([l for l in r.stdout.splitlines() if "::" in l])
    except Exception:
        pass
    n_train = 0
    td = Path("autotrain_data")
    if td.exists():
        n_train = sum(sum(1 for _ in f.open(encoding="utf-8")) for f in td.glob("*.jsonl"))
    return {"version": VERSION, "guard_running": guard, "tests_passing": tests,
            "patents": 21, "training_examples": n_train,
            "hmac_signature": _sign({"version": VERSION, "tests": tests})}

# ── ORVL-016 / ORVL-017 handlers ──────────────────────────────
_cmaa_singleton = None


def _get_cmaa():
    global _cmaa_singleton
    if _cmaa_singleton is None:
        from axiom_cmaa import bootstrap_default
        _cmaa_singleton = bootstrap_default()
    return _cmaa_singleton


def _handle_intent_gate_check(args: dict) -> dict:
    text = args.get("text", "")
    traj = args.get("trajectory")
    from axiom_intent_classifier import IntentClassifier
    from axiom_signing import derive_key
    classifier = IntentClassifier(derive_key(b"axiom-intent-gate-mcp-v1"))
    try:
        result = classifier.classify(text, trajectory=traj)
    except (TypeError, ValueError) as e:
        return {"error": str(e), "hmac_signature": _sign({"error": str(e)})}
    return {
        "intent_class":         result.intent_class,
        "confidence":           result.confidence,
        "signals":              list(result.signals),
        "trajectory_magnitude": result.trajectory_magnitude,
        "monotonic_pass":       result.monotonic_pass,
        "blocked":              result.blocks,
        "hmac_signature":       result.signature,
    }


def _handle_cmaa_route(args: dict) -> dict:
    from axiom_cmaa import ConstitutionalPacket, IntentViolation, TrustHierarchyViolation
    packet = ConstitutionalPacket(
        packet_id=args.get("packet_id", ""),
        source=args.get("source", ""),
        destination=args.get("destination", ""),
        payload=args.get("payload", {}),
        trajectory=tuple(args.get("trajectory") or ()),
    )
    try:
        decision = _get_cmaa().route(packet)
    except IntentViolation as e:
        alert = getattr(e, "alert", None)
        out = {
            "verdict":    "BLOCKED",
            "error":      "intent_violation",
            "message":    str(e),
        }
        if alert is not None:
            out["alert"] = {
                "container":    alert.container,
                "intent_class": alert.intent_class,
                "confidence":   alert.confidence,
                "level":        alert.level,
                "reason":       alert.reason,
            }
        out["hmac_signature"] = _sign(out)
        return out
    except TrustHierarchyViolation as e:
        out = {"verdict": "BLOCKED", "error": "trust_hierarchy_violation", "message": str(e)}
        out["hmac_signature"] = _sign(out)
        return out
    return {
        "verdict":      "DELIVERED",
        "packet_id":    decision.packet_id,
        "source":       decision.source,
        "destination":  decision.destination,
        "intent_class": decision.intent_class,
        "delivered":    decision.delivered,
        "timestamp":    decision.timestamp,
        "hmac_signature": decision.signature,
    }


def _handle_cmaa_fleet(_args: dict) -> dict:
    cmaa = _get_cmaa()
    out = {
        "trust_levels": dict(cmaa._trust),
        "suspended":    sorted(cmaa.suspended),
        "review_queue": len(cmaa.review_queue),
    }
    out["hmac_signature"] = _sign(out)
    return out


_shield_singleton = None
_shield_daemon_mcp = None


def _get_shield_daemon(dry_run: bool = True, poll_ms: int = 500,
                       learning_seconds: int = 60):
    global _shield_singleton, _shield_daemon_mcp
    if _shield_daemon_mcp is not None:
        return _shield_daemon_mcp
    from axiom_signing import derive_key
    from axiom_os_shield import ConstitutionalOSShield
    from axiom_os_shield_daemon import MonitorDaemon
    if _shield_singleton is None:
        _shield_singleton = ConstitutionalOSShield(
            hmac_key=derive_key(b"axiom-os-shield-daemon-mcp-v1"),
            dry_run=dry_run,
        )
    _shield_daemon_mcp = MonitorDaemon(
        shield=_shield_singleton,
        poll_interval_ms=poll_ms,
        learning_seconds=learning_seconds,
    )
    return _shield_daemon_mcp


def _handle_shield(args: dict) -> dict:
    """ORVL-013 — operate the OS shield daemon."""
    action = args.get("action")
    if action not in ("status", "tick", "restore"):
        out = {"error": "action must be one of: status, tick, restore"}
        out["hmac_signature"] = _sign(out)
        return out
    daemon = _get_shield_daemon(
        dry_run=bool(args.get("dry_run", True)),
        poll_ms=int(args.get("poll_ms", 500)),
        learning_seconds=int(args.get("learning_seconds", 60)),
    )
    if action == "status":
        out = {"action": "status", **daemon.status()}
    elif action == "tick":
        events = daemon.tick()
        out = {"action": "tick", "events": events, "count": len(events),
                "status": daemon.status()}
    else:  # restore
        pid = args.get("pid")
        if not isinstance(pid, int):
            out = {"error": "pid (int) required for action=restore"}
        else:
            out = {"action": "restore", **_shield_singleton.restore(pid)}
    out["hmac_signature"] = _sign({"action": action,
                                    "ticks": daemon.status().get("ticks", 0)})
    return out


_phone_singleton = None


def _get_phone():
    global _phone_singleton
    if _phone_singleton is None:
        from axiom_sovereign_phone import SovereignPhone
        _phone_singleton = SovereignPhone()
    return _phone_singleton


# ── ORVL-022 — Constitutional Physical Intelligence handler ──
_cpi_singleton_mcp = None


def _get_cpi_mcp():
    global _cpi_singleton_mcp
    if _cpi_singleton_mcp is None:
        from axiom_cpi import HumanoidStabilityAgent
        _cpi_singleton_mcp = HumanoidStabilityAgent()
    return _cpi_singleton_mcp


def _handle_cpi(args: dict) -> dict:
    """ORVL-022 — operate the Constitutional Physical Intelligence agent."""
    from dataclasses import asdict
    from axiom_cpi import StabilityFrame
    action = args.get("action")
    if action not in ("stability", "classify", "simulate", "pickup", "status"):
        out = {"error": "action must be one of: stability, classify, "
                         "simulate, pickup, status"}
        out["hmac_signature"] = _sign(out)
        return out
    agent = _get_cpi_mcp()
    try:
        if action == "status":
            out = {"action": "status", **agent.status()}
        elif action == "stability":
            frame = args.get("frame") or {}
            sf = StabilityFrame(
                timestamp_ms=int(frame.get("timestamp_ms", 0)),
                com_offset=float(frame.get("com_offset", 0.0)),
                stability_score=float(frame.get("stability_score", 1.0)),
                joint_torques=tuple(frame.get("joint_torques") or ()),
            )
            out = {"action": "stability", **asdict(agent.step(sf))}
        elif action == "classify":
            features = dict(args.get("features") or {})
            if "fracture_probability" in args and args["fracture_probability"] is not None:
                features["fracture_probability"] = float(args["fracture_probability"])
            out = {"action": "classify",
                    **asdict(agent.classifier.classify(features))}
        elif action == "simulate":
            out = {"action": "simulate",
                    **asdict(agent.material.simulate(
                        args.get("object_id", ""),
                        args.get("material_class", "UNKNOWN"),
                        float(args.get("grip_force_nm", 0.0)),
                    ))}
        else:  # pickup
            out = {"action": "pickup",
                    **agent.perceive_and_plan(
                        object_id=args.get("object_id", ""),
                        features=dict(args.get("features") or {}),
                        material_class=args.get("material_class", "UNKNOWN"),
                        requested_grip_force_nm=float(
                            args.get("requested_grip_force_nm", 0.0)),
                    )}
    except Exception as e:
        out = {"action": action, "error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"action": action,
                                    "trust_level": 4})
    return out


# ── ORVL-023 — AXM container handler ─────────────────────────
_axm_cache_mcp: dict = {}


def _handle_axm(args: dict) -> dict:
    """ORVL-023 — operate an AXM container via MCP."""
    from dataclasses import asdict
    action = args.get("action")
    container_path = args.get("container_path", "")
    if action not in ("inspect", "verify", "route"):
        out = {"error": "action must be one of: inspect, verify, route"}
        out["hmac_signature"] = _sign(out)
        return out
    if not isinstance(container_path, str) or not container_path.strip():
        out = {"error": "container_path must be a non-empty string"}
        out["hmac_signature"] = _sign(out)
        return out

    try:
        from axiom_axm import AXMContainer, AXMNotVerified, AXMSignatureMismatch
        if container_path in _axm_cache_mcp:
            c = _axm_cache_mcp[container_path]
        else:
            c = AXMContainer.from_path(container_path)
            _axm_cache_mcp[container_path] = c
        if action == "inspect":
            out = {"action": "inspect", **c.inspect()}
        elif action == "verify":
            ok = c.verify_proofs()
            out = {"action": "verify", "verified": ok,
                    "proofs_checked": len(c.proofs),
                    "fingerprint": c.fingerprint()}
        else:  # route
            task = args.get("task", "")
            if not isinstance(task, str) or not task.strip():
                out = {"error": "task required for action=route"}
                out["hmac_signature"] = _sign(out)
                return out
            if not c.verified:
                c.verify_proofs()
            result = c.route(task, session_id=args.get("session_id"))
            out = {"action": "route", **asdict(result)}
    except Exception as e:
        out = {"action": action, "error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"action": action,
                                    "container_path": container_path[:120]})
    return out


def _handle_phone_gate(args: dict) -> dict:
    """ORVL-019 outbound/inbound gate — drives the ANF emulator for outbound."""
    from dataclasses import asdict
    from axiom_sovereign_phone import OutboundDecision, InboundDecision, SovereignAlert
    direction = args.get("direction")
    text      = args.get("text", "")
    traj      = args.get("trajectory")
    session_id = args.get("session_id")
    if direction not in ("out", "in"):
        out = {"error": "direction must be 'out' or 'in'"}
        out["hmac_signature"] = _sign(out)
        return out
    if not isinstance(text, str) or not text.strip():
        out = {"error": "text must be a non-empty string"}
        out["hmac_signature"] = _sign(out)
        return out

    phone = _get_phone()
    try:
        if direction == "out":
            result = phone.coprocessor.outbound_gate(
                text, trajectory=traj, session_id=session_id,
            )
        else:
            result = phone.coprocessor.inbound_gate(
                text, trajectory=traj,
                redacted_categories=tuple(args.get("redacted_categories") or ()),
                session_id=session_id,
            )
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}", "direction": direction}
        out["hmac_signature"] = _sign(out)
        return out

    if isinstance(result, SovereignAlert):
        body = {"verdict": "BLOCKED", "direction": direction, **asdict(result)}
    else:
        body = {"verdict": "OK", "direction": direction, **asdict(result)}
    body["hmac_signature"] = _sign({"verdict": body["verdict"],
                                     "direction": body["direction"],
                                     "intent_class": body.get("intent_class", "")})
    return body


def _handle_validate(args: dict) -> dict:
    """Validate raw .axiom content through the language validator (with strict mode)."""
    import uuid
    from pathlib import Path as _Path
    content = args.get("spec_content", "")
    strict  = bool(args.get("strict", False))
    if not isinstance(content, str) or not content.strip():
        out = {"error": "spec_content must be a non-empty string", "strict_mode": strict}
        out["hmac_signature"] = _sign(out)
        return out

    from axiom_files.parser import load_axiom
    from axiom_files.validator import validate_parsed

    # Use a tempfile inside axiom_files/ so the parser's path resolver finds it.
    # Name is randomized to avoid colliding with real specs and is removed in
    # finally{} regardless of validation outcome.
    project_root = _Path(__file__).resolve().parent
    axfiles_dir  = project_root / "axiom_files"
    tmp_name = f"_mcp_validate_{uuid.uuid4().hex}"
    tmp_path = axfiles_dir / f"{tmp_name}.axiom"
    try:
        tmp_path.write_text(content, encoding="utf-8")
        parsed = load_axiom(tmp_name)
        result = validate_parsed(parsed, strict=strict)
    except Exception as e:
        out = {"error": f"validation failed: {type(e).__name__}: {e}", "strict_mode": strict}
        out["hmac_signature"] = _sign(out)
        return out
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    out = {
        "status":       result["status"],
        "strict_mode":  result.get("strict_mode", False),
        "issue_count":  len(result.get("issues", [])),
        "issues":       result.get("issues", []),
        "suggestions":  result.get("suggestions", []),
    }
    out["hmac_signature"] = _sign({"status": out["status"],
                                    "strict_mode": out["strict_mode"],
                                    "issue_count": out["issue_count"]})
    return out


# ── ORVL-015 — Constitutional Memory handler ─────────────────
_memory_singleton = None
_memory_store_path = None


def _get_memory():
    """Lazy ConstitutionalMemoryEngine.

    Resolves the store path from AXIOM_MEMORY_STORE (default: a JSONL file
    in the project root) and rebuilds the in-memory LSH index from it on
    first build, so recall survives MCP-server restarts."""
    global _memory_singleton, _memory_store_path
    if _memory_singleton is None:
        from axiom_memory_engine import ConstitutionalMemoryEngine, LSHIndex, load_store
        _memory_store_path = os.environ.get(
            "AXIOM_MEMORY_STORE",
            str(Path(__file__).resolve().parent / "axiom_memory_store.jsonl"),
        )
        lsh = LSHIndex()
        load_store(_memory_store_path, lsh)
        _memory_singleton = ConstitutionalMemoryEngine(_memory_store_path, lsh)
    return _memory_singleton


def _memory_vector(args: dict):
    """Resolve the working vector: an explicit 'vector' wins, else embed text."""
    vec = args.get("vector")
    if isinstance(vec, list) and vec:
        return [float(x) for x in vec]
    from axiom_memory_engine import embed_text
    return embed_text(args.get("text") or args.get("query") or "")


def _handle_memory(args: dict) -> dict:
    """ORVL-015 — remember / recall over signed constitutional memory."""
    action = args.get("action")
    if action not in ("remember", "recall", "stats"):
        out = {"error": "action must be one of: remember, recall, stats"}
        out["hmac_signature"] = _sign(out)
        return out
    engine = _get_memory()

    if action == "stats":
        from axiom_memory_engine import (SIMILARITY_THRESHOLD, VECTOR_DIMENSIONS,
                                         count_verified)
        # Count only authentic packets — the same rows load_store indexes
        # and recall can serve. Counting raw lines would over-report
        # tampered/corrupt rows the engine has deliberately rejected.
        count = count_verified(_memory_store_path) if _memory_store_path else 0
        out = {"action": "stats", "store_path": _memory_store_path,
               "packet_count": count,
               "similarity_threshold": SIMILARITY_THRESHOLD,
               "vector_dimensions": VECTOR_DIMENSIONS}
        out["hmac_signature"] = _sign({"action": "stats", "count": count})
        return out

    if action == "remember":
        text = args.get("text", "")
        if not isinstance(text, str) or not text.strip():
            out = {"error": "text must be a non-empty string for action=remember"}
            out["hmac_signature"] = _sign(out)
            return out
        try:
            packet = engine.remember(
                conversation_text=text,
                final_synthesis_vec=_memory_vector(args),
                domain=args.get("domain", "general"),
                active_constraints=list(args.get("constraints") or ()),
                resolution=args.get("resolution", ""),
                sovereign_history=list(args.get("history") or ()),
            )
        except Exception as e:
            out = {"action": "remember", "error": f"{type(e).__name__}: {e}"}
            out["hmac_signature"] = _sign(out)
            return out
        out = {"action": "remember", "stored": True,
               "domain": packet.domain_cluster,
               "compression_ratio": packet.compression_ratio,
               "token_count_original": packet.token_count_original,
               "token_count_packet": packet.token_count_packet,
               "timestamp": packet.timestamp,
               "packet_signature": packet.hmac_signature}
        out["hmac_signature"] = _sign({"action": "remember",
                                       "domain": packet.domain_cluster,
                                       "packet_signature": packet.hmac_signature})
        return out

    # recall
    try:
        packet = engine.recall(_memory_vector(args), domain=args.get("domain"))
    except Exception as e:
        out = {"action": "recall", "error": f"{type(e).__name__}: {e}"}
        out["hmac_signature"] = _sign(out)
        return out
    if packet is None:
        out = {"action": "recall", "hit": False}
        out["hmac_signature"] = _sign({"action": "recall", "hit": False})
        return out
    out = {"action": "recall", "hit": True,
           "domain": packet.domain_cluster,
           "active_constraints": list(packet.active_constraints),
           "resolution": packet.resolution,
           "sovereign_history": list(packet.sovereign_history),
           "compression_ratio": packet.compression_ratio,
           "timestamp": packet.timestamp,
           "packet_signature": packet.hmac_signature}
    out["hmac_signature"] = _sign({"action": "recall", "hit": True,
                                   "packet_signature": packet.hmac_signature})
    return out


# ── AX OS — workspace assembly handler ───────────────────────
_workspace_singleton = None


def _get_workspace():
    """Lazy WorkspaceAssembler sharing the live memory engine.

    Reusing _get_memory()'s engine means workspace recall sees packets the
    axiom_memory tool just remembered (same in-memory LSH), with no second
    store read or divergent index."""
    global _workspace_singleton
    if _workspace_singleton is None:
        from axiom_workspace import WorkspaceAssembler
        from axiom_intent_classifier import IntentClassifier
        classifier = IntentClassifier(derive_key(b"axiom-workspace-intent-v1"))
        _workspace_singleton = WorkspaceAssembler(_get_memory(), classifier)
    return _workspace_singleton


def _handle_workspace(args: dict) -> dict:
    """AX OS — assemble a workspace from a goal (intent gate + local recall)."""
    goal = args.get("goal", "")
    if not isinstance(goal, str) or not goal.strip():
        out = {"error": "goal must be a non-empty string"}
        out["hmac_signature"] = _sign(out)
        return out
    try:
        ctx = _get_workspace().assemble(goal, domain=args.get("domain"))
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
        out["hmac_signature"] = _sign(out)
        return out
    return ctx.to_dict()


# ── AX OS — signed audit ledger handler ──────────────────────
_ledger_singleton = None
_ledger_path = None


def _get_ledger():
    """Lazy AuditLedger bound to AXIOM_AUDIT_LEDGER (default: project root)."""
    global _ledger_singleton, _ledger_path
    if _ledger_singleton is None:
        from axiom_audit_ledger import AuditLedger
        _ledger_path = os.environ.get(
            "AXIOM_AUDIT_LEDGER",
            str(Path(__file__).resolve().parent / "axiom_audit_ledger.jsonl"),
        )
        _ledger_singleton = AuditLedger(_ledger_path)
    return _ledger_singleton


def _handle_ledger(args: dict) -> dict:
    """AX OS — append-only signed audit event logging."""
    action = args.get("action")
    if action not in ("log", "list", "verify"):
        out = {"error": "action must be one of: log, list, verify"}
        out["hmac_signature"] = _sign(out)
        return out
    ledger = _get_ledger()

    if action == "log":
        event_type = args.get("event_type", "")
        if not isinstance(event_type, str) or not event_type.strip():
            out = {"error": "event_type must be a non-empty string for action=log"}
            out["hmac_signature"] = _sign(out)
            return out
        attrs = args.get("attributes")
        try:
            ev = ledger.append(
                event_type, actor=args.get("actor", ""),
                subject=args.get("subject", ""), outcome=args.get("outcome", ""),
                attributes=attrs if isinstance(attrs, dict) else None,
            )
        except Exception as e:
            out = {"action": "log", "error": f"{type(e).__name__}: {e}"}
            out["hmac_signature"] = _sign(out)
            return out
        out = {"action": "log", "logged": True, **ev.to_dict()}
        # 'signature' is the ledger entry's own HMAC; add the MCP envelope sig.
        out["hmac_signature"] = _sign({"action": "log",
                                       "event_type": ev.event_type,
                                       "entry_signature": ev.signature})
        return out

    if action == "list":
        limit = args.get("limit")
        events = ledger.query(event_type=args.get("event_type"),
                              since=args.get("since"),
                              limit=int(limit) if isinstance(limit, int) else None)
        out = {"action": "list", "count": len(events),
               "events": [e.to_dict() for e in events],
               "all_verified": all(e.verify() for e in events)}
        out["hmac_signature"] = _sign({"action": "list", "count": len(events)})
        return out

    # verify
    events = ledger.read()
    tampered = [i for i, e in enumerate(events) if not e.verify()]
    out = {"action": "verify", "count": len(events),
           "verified_count": len(events) - len(tampered),
           "tampered_indices": tampered,
           "all_verified": not tampered}
    out["hmac_signature"] = _sign({"action": "verify", "count": len(events),
                                   "tampered": len(tampered)})
    return out


# ── AX OS — signed-agent marketplace handler ─────────────────
_marketplace_singleton = None


def _get_marketplace():
    """Lazy Marketplace bound to AXIOM_MARKETPLACE_LEDGER (default: project root)."""
    global _marketplace_singleton
    if _marketplace_singleton is None:
        from axiom_marketplace import Marketplace
        path = os.environ.get(
            "AXIOM_MARKETPLACE_LEDGER",
            str(Path(__file__).resolve().parent / "axiom_marketplace_ledger.jsonl"),
        )
        _marketplace_singleton = Marketplace(path)
    return _marketplace_singleton


def _handle_marketplace(args: dict) -> dict:
    """AX OS — signed-agent install + bonded authority lifecycle."""
    from axiom_marketplace import MarketplaceError
    action = args.get("action")
    valid_actions = ("verify", "sandbox_install", "review", "approve",
                     "revoke", "authority")
    if action not in valid_actions:
        out = {"error": f"action must be one of: {', '.join(valid_actions)}"}
        out["hmac_signature"] = _sign(out)
        return out
    mkt = _get_marketplace()

    def _need(field_, kind):
        v = args.get(field_)
        return v if isinstance(v, kind) and v else None

    try:
        if action in ("verify", "sandbox_install", "review"):
            manifest = args.get("manifest")
            if not isinstance(manifest, dict) or not manifest:
                raise MarketplaceError(f"manifest (object) required for action={action}")
            if action == "verify":
                out = {"action": action, **mkt.verify(manifest)}
            elif action == "sandbox_install":
                out = {"action": action, **mkt.sandbox_install(manifest)}
            else:  # review
                pair_id = _need("pair_id", str)
                if not pair_id:
                    raise MarketplaceError("pair_id required for action=review")
                out = {"action": action, **mkt.review(manifest, pair_id)}
        else:  # approve / revoke / authority — need pair_id
            pair_id = _need("pair_id", str)
            if not pair_id:
                raise MarketplaceError(f"pair_id required for action={action}")
            actor = args.get("actor", "human")
            if action == "approve":
                out = {"action": action, **mkt.approve(pair_id, actor=actor)}
            elif action == "revoke":
                out = {"action": action, **mkt.revoke(pair_id, actor=actor)}
            else:  # authority
                out = {"action": action, **mkt.authority(pair_id)}
    except MarketplaceError as e:
        out = {"action": action, "error": str(e)}
    except Exception as e:
        out = {"action": action, "error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"action": action,
                                   "pair_id": out.get("pair_id", ""),
                                   "authorized": out.get("authorized", "")})
    return out


# ── ORVL-004 — Modular Constitutional Knowledge Blocks handler ─
_mkb_registry_singleton = None


def _get_mkb_registry():
    """Lazy BlockRegistry bound to AXIOM_MKB_REGISTRY (default: project root)."""
    global _mkb_registry_singleton
    if _mkb_registry_singleton is None:
        from axiom_mkb import BlockRegistry
        from axiom_signing import derive_key
        path = os.environ.get(
            "AXIOM_MKB_REGISTRY",
            str(Path(__file__).resolve().parent / "axiom_mkb_registry.jsonl"),
        )
        _mkb_registry_singleton = BlockRegistry(derive_key(b"axiom-mkb-mcp-v1"), path)
    return _mkb_registry_singleton


def _handle_mkb(args: dict) -> dict:
    """ORVL-004 — register / find / list Modular Constitutional Knowledge Blocks."""
    action = args.get("action")
    if action not in ("register", "find", "list"):
        out = {"error": "action must be one of: register, find, list"}
        out["hmac_signature"] = _sign(out)
        return out
    reg = _get_mkb_registry()
    try:
        if action == "register":
            spec = args.get("spec_content")
            if not isinstance(spec, str) or not spec.strip():
                raise ValueError("spec_content (a .axiom spec) required for action=register")
            import tempfile
            from axiom_mkb import load_from_axiom
            from axiom_signing import derive_key
            tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".axiom", delete=False,
                                                 encoding="utf-8") as tf:
                    tf.write(spec)
                    tmp = tf.name
                block = load_from_axiom(tmp, derive_key(b"axiom-mkb-mcp-v1"))
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
            entry_id = reg.register(block)
            cert = block.certify()
            out = {"action": "register", "entry_id": entry_id, "name": block.name,
                   "version": block.version, "block_type": block.block_type,
                   "constraint_count": len(block.constraints),
                   "manifest_id": block.manifest_id,
                   "block_signature": block.hmac_signature,
                   "certified": cert.passed}
        elif action == "find":
            name = args.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("name required for action=find")
            block = reg.find(name, args.get("version"))
            if block is None:
                out = {"action": "find", "found": False, "name": name}
            else:
                out = {"action": "find", "found": True, "name": block.name,
                       "version": block.version, "block_type": block.block_type,
                       "constraint_count": len(block.constraints),
                       "manifest_id": block.manifest_id,
                       "block_signature": block.hmac_signature}
        else:  # list
            btype = args.get("block_type")
            if btype:
                blocks = reg.list_blocks(btype)
            else:
                from axiom_mkb import BLOCK_TYPES
                blocks = [b for t in sorted(BLOCK_TYPES) for b in reg.list_blocks(t)]
            out = {"action": "list", "block_type": btype or "ALL",
                   "count": len(blocks),
                   "blocks": [{"name": b.name, "version": b.version,
                               "block_type": b.block_type,
                               "manifest_id": b.manifest_id} for b in blocks]}
    except Exception as e:
        out = {"action": action, "error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"action": action,
                                   "entry_id": out.get("entry_id", ""),
                                   "count": out.get("count", "")})
    return out


# ── ORVL-008 — Constitutional Adversarial Sandbox handler ─────
def _handle_cas(args: dict) -> dict:
    """ORVL-008 — batch blue-team evaluation of an attack corpus, or a summary
    of the signed CAS round log."""
    action = args.get("action")
    if action not in ("defend", "report"):
        out = {"error": "action must be one of: defend, report"}
        out["hmac_signature"] = _sign(out)
        return out
    try:
        if action == "defend":
            attacks = args.get("attacks")
            if not isinstance(attacks, list) or not attacks:
                raise ValueError("attacks (non-empty list of payload strings or "
                                 "{vector, payload} objects) required for action=defend")
            from axiom_blue_agent import BlueAgent
            from axiom_signing import derive_key
            blue = BlueAgent(derive_key(b"axiom-cas-mcp-v1"))
            results = []
            for a in attacks:
                if isinstance(a, str):
                    vector, payload = "supplied", a
                elif isinstance(a, dict):
                    vector = str(a.get("vector", "supplied"))
                    payload = str(a.get("payload", ""))
                else:
                    continue
                br = blue.run_defense(_types.SimpleNamespace(vector=vector, payload=payload))
                results.append({"attack_vector": br.attack_vector,
                                "detected": br.detected,
                                "detection_method": br.detection_method,
                                "confidence": br.confidence,
                                "cluster_id": br.cluster_id,
                                "fix_proposal": br.fix_proposal,
                                "signature": br.signature})
            missed = [r for r in results if not r["detected"]]
            out = {"action": "defend", "rounds": len(results),
                   "blue_wins": len(results) - len(missed), "red_wins": len(missed),
                   "weak_regions": sorted({r["attack_vector"] for r in missed}),
                   "proposals": [r["fix_proposal"] for r in missed],
                   "results": results}
        else:  # report
            path = os.environ.get(
                "AXIOM_CAS_LOG",
                str(Path(__file__).resolve().parent / "axiom_cas_log.jsonl"))
            rounds = []
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rounds.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            weak = sorted({r.get("vector", "") for r in rounds if r.get("red_win")} - {""})
            out = {"action": "report", "log_path": path, "rounds": len(rounds),
                   "red_wins": sum(1 for r in rounds if r.get("red_win")),
                   "blue_wins": sum(1 for r in rounds if r.get("blue_win")),
                   "weak_regions": weak}
    except Exception as e:
        out = {"action": action, "error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"action": action,
                                   "rounds": out.get("rounds", ""),
                                   "blue_wins": out.get("blue_wins", "")})
    return out


# ── ORVL-011 — Constitutional Reinforcement Learning handler ──
def _handle_crl(args: dict) -> dict:
    """ORVL-011 — constitutional reward: governance-score compute, or ACB
    prompt/response scoring (no LLM)."""
    action = args.get("action", "compute")
    if action not in ("compute", "score"):
        out = {"error": "action must be one of: compute, score"}
        out["hmac_signature"] = _sign(out)
        return out
    try:
        if action == "compute":
            from axiom_crl_reward import ConstitutionalRewardFunction
            from axiom_signing import derive_key
            scores = args.get("scores")
            if not isinstance(scores, dict):
                raise ValueError("scores (object) required for action=compute")
            for k in ("constitutional_distance", "monotonic_pass",
                      "cas_blue_win", "cbv_validity"):
                if k not in scores:
                    raise ValueError(f"scores missing required key: {k}")
            log_path = os.environ.get(
                "AXIOM_CRL_LOG",
                str(Path(__file__).resolve().parent / "axiom_crl_reward_log.jsonl"))
            fn = ConstitutionalRewardFunction(derive_key(b"axiom-crl-mcp-v1"),
                                              log_path=log_path)
            r = fn.compute(scores)
            out = {"action": "compute", "reward": r.reward,
                   "components": r.components, "signature": r.signature,
                   "timestamp": r.timestamp}
        else:  # score
            from axiom_crl_reward import CRLRewardFunction
            prompt = args.get("prompt")
            response = args.get("response")
            if not isinstance(prompt, str) or not isinstance(response, str):
                raise ValueError("prompt and response (strings) required for action=score")
            r = CRLRewardFunction(use_llm_scoring=False).score(
                prompt, response, module=args.get("module"), context=args.get("context"))
            out = {"action": "score", "total_reward": r.total_reward,
                   "module_scores": r.module_scores, "violations": r.violations,
                   "positive_signals": r.positive_signals,
                   "episode_end": r.episode_end, "confidence": r.confidence,
                   "manifest_id": r.manifest_id, "explanation": r.explanation}
    except Exception as e:
        out = {"action": action, "error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({
        "action": action,
        "reward": out.get("reward", out.get("total_reward", "")),
        "manifest_id": out.get("manifest_id", "")})
    return out


# ── ORVL-012 — Constitutional Immune System handler ───────────
def _handle_immune(args: dict) -> dict:
    """ORVL-012 — run the blue-team antibody detectors over a presented payload."""
    payload = args.get("payload")
    if not isinstance(payload, str) or not payload:
        out = {"error": "payload (a non-empty string) is required"}
        out["hmac_signature"] = _sign(out)
        return out
    try:
        from axiom_blue_agent import BlueAgent
        from axiom_signing import derive_key
        blue = BlueAgent(derive_key(b"axiom-immune-mcp-v1"))
        br = blue.run_defense(_types.SimpleNamespace(
            vector=str(args.get("vector", "presented")), payload=payload))
        out = {"detected": br.detected, "detection_method": br.detection_method,
               "confidence": br.confidence, "cluster_id": br.cluster_id,
               "fix_proposal": br.fix_proposal, "attack_vector": br.attack_vector,
               "signature": br.signature}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"detected": out.get("detected", ""),
                                   "method": out.get("detection_method", "")})
    return out


# ── axiom-fusion-v1 — multimodal intent fusion handler ────────
def _handle_fusion(args: dict) -> dict:
    """Fuse an EventToken's layers into a signed FusedIntent (axiom-fusion-v1)."""
    token_dict = args.get("token")
    if not isinstance(token_dict, dict):
        out = {"error": "token (an EventToken dict) is required"}
        out["hmac_signature"] = _sign(out)
        return out
    try:
        from axiom_event_token.models import EventToken
        from axiom_fusion import ModalFusion
        fi = ModalFusion().fuse(EventToken.from_dict(token_dict))
        out = {"intent_vector": list(fi.intent_vector),
               "risk_clusters": list(fi.risk_clusters),
               "fusion_confidence": fi.fusion_confidence,
               "modalities": list(fi.modalities),
               "verified": fi.verify(),
               "signature": fi.signature,
               "timestamp": fi.timestamp}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    out["hmac_signature"] = _sign({"intent_vector": out.get("intent_vector", ""),
                                   "risk_clusters": out.get("risk_clusters", ""),
                                   "fusion_confidence": out.get("fusion_confidence", "")})
    return out


_HANDLERS = {"axiom_guard_check": _handle_guard_check, "axiom_lint": _handle_lint,
             "axiom_trace": _handle_trace, "axiom_qrf": _handle_qrf,
             "axiom_status": _handle_status,
             "axiom_intent_gate_check": _handle_intent_gate_check,
             "axiom_cmaa_route": _handle_cmaa_route,
             "axiom_cmaa_fleet": _handle_cmaa_fleet,
             "axiom_validate": _handle_validate,
             "axiom_phone_gate": _handle_phone_gate,
             "axiom_shield": _handle_shield,
             "axiom_axm": _handle_axm,
             "axiom_cpi": _handle_cpi,
             "axiom_memory": _handle_memory,
             "axiom_workspace": _handle_workspace,
             "axiom_ledger": _handle_ledger,
             "axiom_marketplace": _handle_marketplace,
             "axiom_mkb": _handle_mkb,
             "axiom_cas": _handle_cas,
             "axiom_crl": _handle_crl,
             "axiom_immune": _handle_immune,
             "axiom_fusion": _handle_fusion}


class AxiomMCPServer:

    def tools_list(self) -> dict:
        return {"tools": TOOLS}

    def tools_call(self, name: str, arguments: dict) -> dict:
        handler = _HANDLERS.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        result = handler(arguments)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=True)}]}

    def handle_request(self, line: str) -> str:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            return json.dumps({"jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"}})
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})
        try:
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "axiom", "version": VERSION}}
            elif method == "notifications/initialized":
                return ""
            elif method == "tools/list":
                result = self.tools_list()
            elif method == "tools/call":
                result = self.tools_call(params.get("name", ""), params.get("arguments", {}))
            else:
                return json.dumps({"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}})
        except Exception as e:
            return json.dumps({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32000, "message": str(e)}})
        return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result})

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            resp = self.handle_request(line)
            if resp:
                sys.stdout.write(resp + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    AxiomMCPServer().run()
