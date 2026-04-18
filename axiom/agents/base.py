"""
AXIOM BaseAgent
Holds a system prompt, calls the NIM client, and integrates with the prompt store.
Loads seed prompt from .axiom file if available; falls back to hardcoded seed.
All three agent roles (Worker, Evaluator, Rewriter) extend this class.
"""
from axiom import client
from axiom import store as prompt_store


def _load_axiom_prompt(role: str) -> str | None:
    """Try to load system prompt from axiom_files/{role}.axiom. Returns None if missing."""
    try:
        from axiom_files.parser import get_prompt
        return get_prompt(role)
    except Exception:
        return None


class BaseAgent:
    role: str = "base"
    seed_prompt: str = "You are an AI agent."

    def __init__(self, task_description: str):
        self.task_description = task_description
        self._current_prompt: str | None = None

    @property
    def system_prompt(self) -> str:
        if self._current_prompt is None:
            # Priority:
            #   1) Task-scoped evolved prompt  (store.best_prompt)
            #   2) Global best across sessions (shared_memory.best_global)
            #   3) .axiom file default         (get_prompt)
            #   4) Hardcoded seed_prompt
            saved = prompt_store.best_prompt(self.task_description, self.role)
            if saved is not None:
                self._current_prompt = saved
            else:
                from axiom.shared_memory import best_global
                global_best = best_global(self.role)
                if global_best is not None:
                    self._current_prompt = global_best
                else:
                    axiom = _load_axiom_prompt(self.role)
                    self._current_prompt = axiom if axiom is not None else self.seed_prompt
        return self._current_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._current_prompt = value

    def axiom_rewrite(self, new_parsed: dict) -> None:
        """
        Persist a rewritten .axiom definition to disk AND update the in-memory
        system prompt from it. This is how agents rewrite themselves at the DSL level.
        """
        from axiom_files.parser import save_axiom, to_system_prompt
        save_axiom(self.role, new_parsed)
        self._current_prompt = to_system_prompt(new_parsed)

    def _call(
        self,
        user_message: str,
        temperature: float = 0.7,
    ) -> str:
        return client.chat(
            system_prompt=self.system_prompt,
            user_message=user_message,
            temperature=temperature,
        )

    def _call_json(self, user_message: str, temperature: float = 0.3) -> dict:
        return client.chat_json(
            system_prompt=self.system_prompt,
            user_message=user_message,
            temperature=temperature,
        )

    def record(self, prompt: str, score: float) -> int:
        """Persist a prompt version to the store and return its version index."""
        return prompt_store.save_iteration(
            task_description=self.task_description,
            agent=self.role,
            prompt=prompt,
            score=score,
        )
