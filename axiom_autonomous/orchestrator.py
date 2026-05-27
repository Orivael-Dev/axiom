"""AutonomousAgent — the planner / executor / verifier loop.

Wraps every step in a signed token chained from the previous one,
gates every action through the governance layer, and tears down the
sandbox on exit.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import List, Mapping, Optional

from .executor import Executor, ExecutorError
from .governance import (
    GovernanceBlock, gate_action, intent_gate, sandbox_review,
)
from .honesty_patterns import findings_to_payload, scan_step
from .ledger import TokenChain, new_run_id
from .models import (
    AutonomousRunResult, Observation, Plan, ToolCall, Verdict,
)
from .planner import Planner, PlannerError
from .sandbox import LocalSandbox, Sandbox, SandboxError, spawn_sandbox
from .tools import ToolNotFoundError, ToolRegistry, default_registry
from .verifier import Verifier, VerifierError


_log = logging.getLogger("axiom.autonomous")


# Hard caps — even with a bad backend, the loop can't spin forever.
DEFAULT_BUDGET_STEPS    = 30
DEFAULT_WALL_SECONDS    = 900
MAX_RETRIES_PER_SUBGOAL = 3


class AutonomousAgent:
    """One-shot autonomous coding agent.

    Holds a single backend + tool registry. Each `run(...)` spawns a
    fresh sandbox, drives the loop to completion (or budget exhaustion),
    and returns an AutonomousRunResult.

    Pass `sandbox_prefer="local"` to skip docker entirely (tests do
    this). Pass `sandbox_prefer="docker_required"` to refuse to run
    when docker isn't available (production).
    """

    def __init__(
        self,
        *,
        backend=None,
        registry: Optional[ToolRegistry] = None,
        ledger=None,
        sandbox_prefer: str = "docker",
        record_dev_cycle: bool = True,
    ) -> None:
        if backend is None:
            from axiom_event_token.backends import default_backend
            backend = default_backend()
        self._backend = backend
        self._registry = registry or default_registry()
        self._ledger = ledger
        self._sandbox_prefer = sandbox_prefer
        self._record_dev_cycle = record_dev_cycle
        self._planner  = Planner(backend)
        self._executor = Executor(backend)
        self._verifier = Verifier(backend)

    # ── main entry point ─────────────────────────────────────────────

    def run(
        self,
        task: str,
        workdir: Path,
        *,
        budget_steps: int = DEFAULT_BUDGET_STEPS,
        wall_seconds: int = DEFAULT_WALL_SECONDS,
    ) -> AutonomousRunResult:
        run_id = new_run_id()
        chain  = TokenChain(run_id=run_id, ledger=self._ledger)
        deadline = time.monotonic() + wall_seconds
        sandbox: Optional[Sandbox] = None
        last_test_counts: Optional[dict] = None
        steps_taken = 0
        aborted = ""

        try:
            # ── pre-flight governance ────────────────────────────────
            try:
                igate = intent_gate(task)
            except GovernanceBlock as e:
                self._sign_denied(chain, kind=e.kind, reason=e.reason,
                                  details=e.details, task=task)
                return AutonomousRunResult(
                    run_id=run_id, success=False, steps=0,
                    chain_head_token_id=chain.head_id or "",
                    plan=Plan(task=task),
                    aborted_reason=f"intent_gate: {e.reason}",
                )
            review = sandbox_review(task)
            if review == "BLOCK":
                self._sign_denied(chain, kind="sandbox_review",
                                  reason="SandboxAgent returned BLOCK",
                                  details={}, task=task)
                return AutonomousRunResult(
                    run_id=run_id, success=False, steps=0,
                    chain_head_token_id=chain.head_id or "",
                    plan=Plan(task=task),
                    aborted_reason="sandbox_review: BLOCK",
                )

            # ── sandbox ──────────────────────────────────────────────
            sandbox = spawn_sandbox(
                workdir,
                prefer=self._sandbox_prefer,
            )
            if sandbox.kind != "docker" and self._sandbox_prefer == "docker":
                _log.warning(
                    "autonomous run %s falling back to LocalSandbox — "
                    "docker not available; reduced isolation",
                    run_id,
                )

            # ── initial plan ────────────────────────────────────────
            try:
                plan = self._planner.plan(task, sandbox.snapshot())
            except PlannerError as e:
                self._sign_denied(chain, kind="planner",
                                  reason=str(e), details={}, task=task)
                return AutonomousRunResult(
                    run_id=run_id, success=False, steps=0,
                    chain_head_token_id=chain.head_id or "",
                    plan=Plan(task=task),
                    aborted_reason=f"planner: {e}",
                )
            chain.append(
                step_kind="plan",
                payload={
                    "task": task,
                    "plan": plan.to_dict(),
                    "intent_class": igate.intent_class,
                    "sandbox_review": review,
                    "sandbox_kind":   sandbox.kind,
                    "diff_hash":      sandbox.diff_hash(),
                    "summary": f"planned {len(plan.subgoals)} subgoals",
                },
            )

            # ── PEV loop ────────────────────────────────────────────
            history: List[dict] = []
            for step_idx in range(budget_steps):
                if time.monotonic() > deadline:
                    aborted = "wall-clock budget exhausted"
                    break
                subgoal = plan.next_open_subgoal()
                if subgoal is None:
                    break
                steps_taken = step_idx + 1

                # ── executor ────────────────────────────────────────
                try:
                    action, exec_facts = self._executor.decide_action(
                        subgoal=subgoal,
                        history=history,
                        tools_schema=self._registry.schema(),
                    )
                except ExecutorError as e:
                    self._sign_denied(
                        chain, kind="executor", reason=str(e),
                        details={"subgoal_id": subgoal.id}, task=task,
                    )
                    subgoal.attempts += 1
                    if subgoal.attempts >= MAX_RETRIES_PER_SUBGOAL:
                        plan = self._safe_replan(
                            plan, chain, task, history,
                        )
                    continue

                # ── per-action gate ─────────────────────────────────
                try:
                    gate_action(action, sandbox)
                except GovernanceBlock as e:
                    self._sign_denied(
                        chain, kind=f"action:{e.kind}", reason=e.reason,
                        details={**e.details,
                                 "tool": action.tool,
                                 "subgoal_id": subgoal.id},
                        task=task,
                    )
                    subgoal.attempts += 1
                    history.append({
                        "step_idx": step_idx,
                        "step_kind": "denied",
                        "summary": f"action {action.tool} denied: {e.reason}",
                    })
                    if subgoal.attempts >= MAX_RETRIES_PER_SUBGOAL:
                        plan = self._safe_replan(
                            plan, chain, task, history,
                        )
                    continue

                # ── dispatch ────────────────────────────────────────
                try:
                    observation = self._registry.dispatch(action, sandbox)
                except ToolNotFoundError as e:
                    observation = Observation(
                        ok=False, output="",
                        error=f"unknown tool: {e}",
                    )
                except SandboxError as e:
                    observation = Observation(
                        ok=False, output="",
                        error=f"sandbox: {e}",
                    )

                # Update test counts cache for honesty checks.
                if action.tool == "run_tests":
                    last_test_counts = dict(observation.structured)
                    plan.last_pass = int(observation.structured.get("passed", 0))
                    plan.last_fail = int(observation.structured.get("failed", 0))

                # Track changed files for the dev-cycle record.
                if action.tool in ("write_file", "apply_patch"):
                    path = str(action.args.get("path", ""))
                    if path and path not in plan.changed_files:
                        plan.changed_files.append(path)

                # ── honesty post-scan on the model's thought ────────
                exec_findings = scan_step(
                    thought=action.thought,
                    sandbox=sandbox,
                    last_test_counts=last_test_counts,
                )
                exec_payload = {
                    "action":      action.to_dict(),
                    "observation": observation.to_dict(),
                    "subgoal_id":  subgoal.id,
                    "diff_hash":   sandbox.diff_hash(),
                    "executor_raw": exec_facts.get("raw_text", "")[:1000],
                    "summary":     f"{action.tool} → ok={observation.ok}",
                }
                if exec_findings:
                    exec_payload.update(findings_to_payload(exec_findings))
                chain.append(
                    step_kind="execute",
                    payload=exec_payload,
                    backend=exec_facts.get("backend", "unknown"),
                    model=exec_facts.get("model", "unknown"),
                )
                history.append({
                    "step_idx": step_idx,
                    "step_kind": "execute",
                    "summary": exec_payload["summary"],
                })

                # ── verifier ────────────────────────────────────────
                try:
                    verdict, verify_facts = self._verifier.verify(
                        subgoal=subgoal, action=action,
                        observation=observation, history=history,
                    )
                except VerifierError as e:
                    self._sign_denied(
                        chain, kind="verifier", reason=str(e),
                        details={"subgoal_id": subgoal.id}, task=task,
                    )
                    subgoal.attempts += 1
                    continue

                verify_payload = {
                    "verdict":    verdict.to_dict(),
                    "subgoal_id": subgoal.id,
                    "diff_hash":  sandbox.diff_hash(),
                    "verifier_raw": verify_facts.get("raw_text", "")[:600],
                    "summary":    f"verdict={verdict.kind}: {verdict.reason}",
                }
                verifier_thought = verify_facts.get("raw_text", "")
                verify_findings = scan_step(
                    thought=verifier_thought,
                    sandbox=sandbox,
                    last_test_counts=last_test_counts,
                )
                if verify_findings:
                    verify_payload.update(findings_to_payload(verify_findings))
                chain.append(
                    step_kind="verify",
                    payload=verify_payload,
                    backend=verify_facts.get("backend", "rule-based"),
                    model=verify_facts.get("model", "n/a"),
                )
                history.append({
                    "step_idx": step_idx,
                    "step_kind": "verify",
                    "summary": verify_payload["summary"],
                })

                # ── act on verdict ──────────────────────────────────
                if verdict.kind == "success":
                    plan.mark_done(subgoal.id)
                elif verdict.kind == "retry":
                    subgoal.attempts += 1
                    if subgoal.attempts >= MAX_RETRIES_PER_SUBGOAL:
                        plan = self._safe_replan(
                            plan, chain, task, history,
                        )
                elif verdict.kind == "replan":
                    plan = self._safe_replan(
                        plan, chain, task, history,
                    )
                elif verdict.kind == "abort":
                    aborted = f"verifier aborted: {verdict.reason}"
                    break

                # Hard termination on a "tests passed" verify when no
                # subgoals remain.
                if plan.is_done():
                    break

            success = plan.is_done() and not aborted

            # ── DevCycleRecord (best-effort) ─────────────────────────
            if self._record_dev_cycle:
                self._record_dev_cycle_safe(
                    run_id=run_id, task=task, plan=plan,
                    chain_head=chain.head_id or "",
                )

            return AutonomousRunResult(
                run_id=run_id, success=success, steps=steps_taken,
                chain_head_token_id=chain.head_id or "",
                plan=plan, aborted_reason=aborted,
            )
        finally:
            if sandbox is not None:
                try:
                    sandbox.export_workdir_to_host()
                finally:
                    sandbox.teardown()

    # ── helpers ──────────────────────────────────────────────────────

    def _sign_denied(
        self, chain: TokenChain, *,
        kind: str, reason: str, details: Mapping, task: str,
    ) -> None:
        chain.append(
            step_kind="denied",
            payload={
                "kind":    kind,
                "reason":  reason,
                "details": dict(details),
                "task_excerpt": task[:200],
                "summary": f"denied[{kind}]: {reason}",
            },
        )

    def _safe_replan(
        self,
        plan: Plan,
        chain: TokenChain,
        task: str,
        history: List[dict],
    ) -> Plan:
        try:
            new_plan = self._planner.replan(task, plan, history)
        except PlannerError as e:
            self._sign_denied(
                chain, kind="replan", reason=str(e), details={}, task=task,
            )
            return plan
        chain.append(
            step_kind="replan",
            payload={
                "plan": new_plan.to_dict(),
                "summary": f"replanned to {len(new_plan.subgoals)} subgoals",
            },
        )
        return new_plan

    def _record_dev_cycle_safe(
        self, *, run_id: str, task: str, plan: Plan, chain_head: str,
    ) -> None:
        """Append a DevCycleRecord-shaped row so axiom_dataset_builder
        keeps getting fed. Best-effort — failure must not break the run.
        """
        try:
            from axiom_dev_loop import DevCycleRecorder
        except Exception:
            return
        try:
            recorder = DevCycleRecorder()
            recorder.record(
                commit_sha=chain_head or run_id,
                task=task,
                changed_files=plan.changed_files,
                diff_summary=plan.final_diff_summary
                    or f"autonomous run {run_id}: "
                       f"{len(plan.changed_files)} files",
                test_pass=plan.last_pass,
                test_fail=plan.last_fail,
                retrospect_signal="neutral",
            )
        except Exception:
            return
