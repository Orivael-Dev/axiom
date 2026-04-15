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
        return self._call(
            user_message=f"Task:\n{task}",
            temperature=0.7,
        )
