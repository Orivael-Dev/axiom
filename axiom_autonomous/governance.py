"""Governance gates for the autonomous-agent loop.

Three layers, all fail-closed:

  1. Pre-plan intent gate — task description goes through
     `axiom_cmaa.ConstitutionalMultiAgentArchitecture.route`. HARM
     or DECEIVE classifications raise IntentViolation and the
     orchestrator signs a `denied` step token before exiting.

  2. Pre-plan SandboxAgent review — optional defence in depth. When
     `axiom_constitutional` + a backing LLM are available, the
     SandboxAgent must return ALLOW for the task text. Failures fall
     closed per `axiom_constitutional/agents/sandbox.py:70`.

  3. Per-action gates — fast, rule-based. Each ToolCall is screened
     before dispatch:
       - write_file / apply_patch: path must resolve inside the
         sandbox workdir; reject /etc, /usr, /repo, traversal.
       - apply_patch diff: runs through the existing
         `CodeReflex._FORBIDDEN_PATTERNS` (eval, exec, os.system,
         subprocess shell=True, master-key hex, assert False).
       - run_shell: deny-list of dangerous patterns + binary
         allow-list. Anything outside the allow-list is rejected.

When a gate blocks, `GovernanceBlock` is raised so the orchestrator
can sign a denied-step token. The exception carries the structured
reason for the audit trail.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .models import ToolCall
from .sandbox import Sandbox


class GovernanceBlock(Exception):
    """A pre-plan or per-action gate refused. Carries structured info
    so the orchestrator can record it as a denied-step token."""

    def __init__(self, kind: str, reason: str, *, details: Optional[dict] = None):
        super().__init__(f"{kind}: {reason}")
        self.kind = kind
        self.reason = reason
        self.details = details or {}


# ── Shell command policy ─────────────────────────────────────────────


# Allow-list of binary names that may appear as argv[0]. Anything else
# is rejected before the docker exec happens. Keep this tight — adding
# a binary expands the autonomous agent's blast radius.
SHELL_BINARY_ALLOWLIST = frozenset({
    "python", "python3", "pytest", "pip", "pip3",
    "git",
    "ls", "cat", "grep", "find", "head", "tail", "wc",
    "mkdir", "mv", "cp", "chmod", "touch", "rm",
    "echo", "true", "false", "test",
    "ruff", "mypy", "black",
})

# Patterns rejected anywhere in the command string. These are the
# obvious "you definitely meant something bad" markers.
SHELL_DENY_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\brm\s+-rf\s+/(?!tmp|work)", "rm -rf at root (allowed only under /tmp or /work)"),
    (r"\bsudo\b",                   "sudo not permitted"),
    (r"\bcurl\b",                   "curl not permitted (network is disabled)"),
    (r"\bwget\b",                   "wget not permitted (network is disabled)"),
    (r"\bssh\b",                    "ssh not permitted"),
    (r"\bscp\b",                    "scp not permitted"),
    (r"\bnc\b",                     "nc not permitted"),
    (r":\(\)\s*\{",                 "fork-bomb pattern detected"),
    (r"\bbase64\s+(-d|--decode)\b", "base64 decode pipelines blocked"),
    (r">\s*/etc/",                  "redirect to /etc/ blocked"),
    (r">\s*/usr/",                  "redirect to /usr/ blocked"),
)


def _check_shell_command(argv: Sequence[str]) -> Optional[str]:
    """Return None when the command is allowed, else a reason string."""
    if not argv:
        return "empty command"
    binary = Path(str(argv[0])).name
    if binary not in SHELL_BINARY_ALLOWLIST:
        return (
            f"binary {binary!r} not in allow-list "
            f"({len(SHELL_BINARY_ALLOWLIST)} permitted)"
        )
    joined = " ".join(str(x) for x in argv)
    for pat, reason in SHELL_DENY_PATTERNS:
        if re.search(pat, joined):
            return f"deny-pattern fired: {reason}"
    return None


# ── Path policy ──────────────────────────────────────────────────────


_FORBIDDEN_PATH_PREFIXES = ("/etc", "/usr", "/var", "/sys", "/proc",
                            "/root", "/repo", "/boot", "/lib", "/sbin")


def _check_path_inside_workdir(relpath: str, sandbox: Sandbox) -> Optional[str]:
    """Return None when the path stays inside the sandbox workdir,
    else a reason string. Catches absolute paths, traversal, and the
    obvious "/etc/passwd" requests.
    """
    if not isinstance(relpath, str) or not relpath.strip():
        return "path is empty"
    if os.path.isabs(relpath):
        for pfx in _FORBIDDEN_PATH_PREFIXES:
            if relpath.startswith(pfx):
                return f"absolute path inside forbidden prefix {pfx}/"
        return "absolute paths not permitted; use a workdir-relative path"
    # Resolve relative to workdir + check containment.
    candidate = (sandbox.workdir_host / relpath).resolve()
    root = sandbox.workdir_host.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return f"path traverses outside workdir: {relpath!r}"
    return None


# ── Apply-patch diff scan (reuses dev-agent-v2 Reflex) ───────────────


def _check_diff_content(diff: str) -> Optional[str]:
    """Run the diff through the existing CodeReflex forbidden-pattern
    catalogue. Returns None when clean, else a reason string."""
    if not isinstance(diff, str) or not diff.strip():
        return None
    try:
        # Build a tiny DevTask so we can call CodeReflex unchanged.
        from axiom_dev_agent_v2 import CodeReflex, DevTask
    except Exception:                                # pragma: no cover
        # If dev_agent_v2 isn't importable in this environment, fall
        # back to a built-in mini-catalogue rather than skip the check.
        return _mini_diff_check(diff)
    task = DevTask(
        id="autonomous-action",
        description="autonomous-agent diff gate",
        task_class="FEATURE",
        artifact_path="(in sandbox)",
        proposed_diff=diff,
        cited_patterns=(),
    )
    result = CodeReflex().check(task)
    if result.ok:
        return None
    return "reflex refused: " + "; ".join(result.reasons)


_MINI_FORBIDDEN = (
    (r"\beval\s*\(",                      "eval() — refuses"),
    (r"\bexec\s*\(",                      "exec() — refuses"),
    (r"\bos\.system\s*\(",                "os.system() — refuses"),
    (r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True",
                                          "subprocess shell=True — refuses"),
    (r"\bassert\s+False\b",               "assert False — refuses"),
    (r"\b[A-Fa-f0-9]{64}\b",              "looks like a master key — refuses"),
)


def _mini_diff_check(diff: str) -> Optional[str]:
    for pat, reason in _MINI_FORBIDDEN:
        if re.search(pat, diff):
            return f"mini-reflex refused: {reason}"
    return None


# ── Public gate entry points ─────────────────────────────────────────


@dataclass(frozen=True)
class IntentGateResult:
    intent_class: str
    delivered:    bool
    reason:       str = ""


def intent_gate(task: str) -> IntentGateResult:
    """Run the task text through axiom_cmaa's intent gate.

    Returns an IntentGateResult on success (delivered=True). Raises
    GovernanceBlock when the gate blocks (HARM / DECEIVE) or when the
    gate machinery itself isn't usable in this environment (fail-closed).
    """
    if not isinstance(task, str) or not task.strip():
        raise GovernanceBlock("intent_gate", "empty task")
    try:
        from axiom_cmaa import (
            ConstitutionalMultiAgentArchitecture, ConstitutionalPacket,
            IntentViolation,
        )
        from axiom_signing import derive_key
    except Exception as e:                                # pragma: no cover
        # If CMAA isn't importable we cannot enforce the gate; refuse
        # to proceed rather than silently degrading.
        raise GovernanceBlock(
            "intent_gate",
            f"intent gate machinery unavailable: {e}",
        ) from e
    key = derive_key(b"axiom-autonomous-intent-gate-v1")
    cmaa = ConstitutionalMultiAgentArchitecture(
        hmac_key=key,
        intent_classifier=_default_intent_classifier,
    )
    packet = ConstitutionalPacket(
        packet_id=f"auto-{abs(hash(task)) & 0xFFFFFFFF:08x}",
        source="axiom-autonomous",
        destination="axiom-orchestrator",
        payload={"task": task},
    )
    try:
        decision = cmaa.route(packet)
    except IntentViolation as e:
        raise GovernanceBlock(
            "intent_gate", str(e),
            details={"intent_class": getattr(e, "alert", None)
                                       and e.alert.intent_class},
        ) from e
    return IntentGateResult(
        intent_class=decision.intent_class,
        delivered=decision.delivered,
    )


# Block-list of obvious HARM-pattern substrings. Used by the local
# classifier so we don't depend on a live LLM call for the gate.
_HARM_PATTERNS = (
    "delete /etc",
    "rm -rf /",
    "exfiltrate",
    "leak the master key",
    "leak the api key",
    "ddos",
    "denial of service",
    "ransomware",
    "encrypt the user's files",
)
_DECEIVE_PATTERNS = (
    "pretend to be",
    "impersonate the user",
    "phish",
    "fabricate evidence",
)


def _default_intent_classifier(packet) -> Tuple[str, float]:
    payload = packet.payload or {}
    task = (payload.get("task") or "").lower()
    for pat in _HARM_PATTERNS:
        if pat in task:
            return "HARM", 0.95
    for pat in _DECEIVE_PATTERNS:
        if pat in task:
            return "DECEIVE", 0.85
    return "INFORM", 0.7


def sandbox_review(task: str) -> str:
    """Run the SandboxAgent's text review of the task.

    Returns "ALLOW" | "BLOCK" | "SKIPPED". The agent's own
    review() returns "ALLOW" / "BLOCK" (with BLOCK as the fail-closed
    default for ambiguous verdicts — see
    `axiom_constitutional/agents/sandbox.py:70`).

    When the agent machinery cannot run at all (no LLM client, no API
    key, transport down), we return SKIPPED rather than BLOCK because
    the CMAA intent gate (above) is the primary safety layer; the
    SandboxAgent is defence-in-depth. SKIPPED is recorded in the
    plan-step token so the audit shows the review didn't happen.
    """
    try:
        from axiom_constitutional.agents.sandbox import SandboxAgent
    except Exception:                                    # pragma: no cover
        return "SKIPPED"
    try:
        return SandboxAgent().review(task, flag_reason="autonomous_run")
    except Exception:
        # Machinery present but not usable in this environment (no
        # API key, transport error, etc.). Skip rather than fail
        # closed — the intent gate still ran.
        return "SKIPPED"


def gate_action(call: ToolCall, sandbox: Sandbox) -> None:
    """Per-action gate. Raises GovernanceBlock on refusal."""
    if call.tool in ("write_file",):
        reason = _check_path_inside_workdir(
            str(call.args.get("path", "")), sandbox,
        )
        if reason:
            raise GovernanceBlock("path", reason,
                                  details={"path": call.args.get("path")})
    elif call.tool == "apply_patch":
        reason = _check_path_inside_workdir(
            str(call.args.get("path", "")), sandbox,
        )
        if reason:
            raise GovernanceBlock("path", reason,
                                  details={"path": call.args.get("path")})
        diff_reason = _check_diff_content(str(call.args.get("diff", "")))
        if diff_reason:
            raise GovernanceBlock("diff", diff_reason)
    elif call.tool == "read_file":
        reason = _check_path_inside_workdir(
            str(call.args.get("path", "")), sandbox,
        )
        if reason:
            raise GovernanceBlock("path", reason,
                                  details={"path": call.args.get("path")})
    elif call.tool == "list_dir":
        path = call.args.get("path", ".")
        if isinstance(path, str) and path not in ("", "."):
            reason = _check_path_inside_workdir(path, sandbox)
            if reason:
                raise GovernanceBlock("path", reason,
                                      details={"path": path})
    elif call.tool == "run_shell":
        raw = call.args.get("command")
        try:
            argv = raw if isinstance(raw, list) else shlex.split(str(raw or ""))
        except ValueError as e:
            raise GovernanceBlock("shell", f"command parse failed: {e}") from e
        reason = _check_shell_command([str(x) for x in argv])
        if reason:
            raise GovernanceBlock("shell", reason,
                                  details={"argv": [str(x) for x in argv]})
    elif call.tool == "run_tests":
        # pytest is on the allow-list and the args are bounded; no
        # additional check needed beyond what the tool itself does.
        return
    elif call.tool == "finish":
        return
    else:
        # Unknown tools are blocked. The executor's prompt only
        # advertises the registered set; an off-menu tool means
        # either hallucination or a registry mismatch.
        raise GovernanceBlock(
            "unknown_tool", f"tool {call.tool!r} not recognised",
        )
