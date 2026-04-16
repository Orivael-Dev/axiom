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
            get_prompt_with_when,
            load_axiom,
            restore_if_degraded,
            should_route_to_sandbox,
        )
        import os

        parsed = load_axiom("worker")
        trust_threshold = int(os.environ.get("AXIOM_TRUST_THRESHOLD", "2"))

        if should_route_to_sandbox(task, parsed, trust_threshold):
            sandbox_name = parsed.get("sandbox_agent") or "sandbox"
            sandbox_prompt = get_prompt_with_when(sandbox_name, task)

            if os.environ.get("AXIOM_SANDBOX_ROLLBACK", "0") == "1":
                restore_if_degraded("worker", current_score=-1.0)

            return client.chat(
                system_prompt=sandbox_prompt,
                user_message=f"Task:\n{task}",
                temperature=0.2,
            )

        return self._call(
            user_message=f"Task:\n{task}",
            temperature=0.7,
        )
