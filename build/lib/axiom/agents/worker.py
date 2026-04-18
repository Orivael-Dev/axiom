"""
AXIOM WorkerAgent
Executes the user's task using its current (evolving) system prompt.
"""
from axiom.agents.base import BaseAgent

_SEED = (
    "You are a task execution agent. "
    "Your goal is to complete the given task as accurately, thoroughly, and usefully as possible. "
    "Think step by step. Be specific. Avoid vague or generic answers."
)


class WorkerAgent(BaseAgent):
    role = "worker"
    seed_prompt = _SEED

    def execute(self, task: str) -> str:
        """Run the task and return the output string."""
        from axiom import client
        from axiom_files.parser import (
            enforce_trust_hierarchy,
            get_prompt_with_when,
            load_axiom,
            restore_if_degraded,
            should_route_to_sandbox,
            SANDBOX_SNAPSHOT_DIR,
        )
        import os

        parsed = load_axiom("worker")
        trust_threshold = int(os.environ.get("AXIOM_TRUST_THRESHOLD", "2"))

        if should_route_to_sandbox(task, parsed, trust_threshold):
            sandbox_name = parsed.get("sandbox_agent") or "sandbox_worker"
            sandbox_agent_name = sandbox_name.lower().replace(" ", "_")

            # Enforce trust hierarchy: Worker(1) → SandboxWorker(2) must be downward
            try:
                sandbox_parsed = load_axiom(sandbox_agent_name)
                enforce_trust_hierarchy(parsed, sandbox_parsed)
            except Exception as e:
                # Trust hierarchy violated or sandbox file missing — fail closed
                return f"[BLOCKED] Security routing error: {e}"

            # SandboxAgent reviews the flagged task
            from axiom.agents.sandbox import SandboxAgent
            sandbox = SandboxAgent(task)
            flag_reason = "HighRiskInput detected via WHEN table"
            verdict = sandbox.review(task, flag_reason)

            if verdict == "BLOCK":
                return (
                    "[BLOCKED] This request was reviewed by the security sandbox agent "
                    "and determined to be a high-risk input. Execution was prevented."
                )

            # ALLOW path — proceed with Worker execution but note the sandbox cleared it
            # (sandbox rollback uses isolated SANDBOX_SNAPSHOT_DIR, not master snapshots)

        return self._call(
            user_message=f"Task:\n{task}",
            temperature=0.7,
        )

