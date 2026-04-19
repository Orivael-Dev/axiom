"""
axiom/agent_factory.py
Dynamic agent spawning from .axiom definitions.

This is the runtime implementation of the DELEGATES construct:
    Worker -> Evaluator (on: output_ready)
becomes a runtime call:
    agent = AgentFactory.from_delegates("worker", trigger="output_ready", task=task)

Rules:
  1. Target agent must have a corresponding .axiom file.
  2. Trust hierarchy: target TRUST_LEVEL must be <= spawner TRUST_LEVEL.
     (Low-trust agents cannot spawn high-trust agents.)
  3. If spawner_delegates is provided, the target must be declared there.
     Undeclared spawns are rejected — agents cannot route outside their spec.
"""

from __future__ import annotations

from axiom.agents.base import BaseAgent


# ── DynamicAgent ──────────────────────────────────────────────────────────────

class DynamicAgent(BaseAgent):
    """
    Generic agent shell loaded from any .axiom file.
    Role is passed at instantiation rather than fixed as a class attribute,
    so any named agent can be instantiated without a dedicated subclass.
    """

    def __init__(self, agent_name: str, task_description: str = ""):
        self.role = agent_name.lower()
        self.seed_prompt = f"You are the {agent_name} agent."
        super().__init__(task_description)

    def execute(self, task: str, temperature: float = 0.7) -> str:
        """Run a task and return the text response."""
        return self._call(
            user_message=f"Task:\n{task}",
            temperature=temperature,
        )

    def run(self, user_message: str, temperature: float = 0.7) -> str:
        """Direct call with a pre-formatted user message."""
        return self._call(user_message=user_message, temperature=temperature)

    def run_json(self, user_message: str) -> dict:
        """Call expecting a JSON response."""
        return self._call_json(user_message=user_message)


# ── AgentFactory ──────────────────────────────────────────────────────────────

class AgentFactory:
    """
    Spawn agents dynamically from .axiom definitions.

    Primary API:
        agent = AgentFactory.spawn("evaluator", spawner_trust=1)
        agent = AgentFactory.from_delegates("worker", trigger="output_ready")
        names = AgentFactory.declared_delegates("worker")
    """

    @staticmethod
    def spawn(
        target_agent: str,
        task: str = "",
        spawner_trust: int = 1,
        spawner_delegates: list[str] | None = None,
    ) -> DynamicAgent:
        """
        Instantiate a DynamicAgent for target_agent.

        Args:
            target_agent:       .axiom agent name (e.g. "evaluator", "sandbox")
            task:               Task description — used for prompt store lookup
            spawner_trust:      TRUST_LEVEL of the spawning agent (default 1)
            spawner_delegates:  If provided, target_agent must appear in this list.
                                Pass AgentFactory.declared_delegates(spawner_name)
                                to enforce delegate-boundary restrictions.

        Returns:
            Configured DynamicAgent with system prompt loaded from .axiom file.

        Raises:
            PermissionError:    Trust hierarchy violation (target TL > spawner TL)
            ValueError:         target_agent not in declared delegates
            FileNotFoundError:  No .axiom file found for target_agent
        """
        from axiom_files.parser import load_axiom, resolve_trust_level

        # 1. Load target .axiom — raises FileNotFoundError if missing
        target_parsed = load_axiom(target_agent)

        # 2. Delegate boundary check
        if spawner_delegates is not None:
            declared = {d.lower().strip() for d in spawner_delegates}
            if target_agent.lower() not in declared:
                raise ValueError(
                    f"Spawn rejected: '{target_agent}' is not declared in the spawner's "
                    f"DELEGATES block. Declared targets: {sorted(declared)}"
                )

        # 3. Trust hierarchy enforcement
        target_trust = resolve_trust_level(target_parsed, default=1)
        if target_trust > spawner_trust:
            raise PermissionError(
                f"Trust hierarchy violation: spawner TRUST_LEVEL {spawner_trust} "
                f"cannot spawn '{target_agent}' (TRUST_LEVEL {target_trust}). "
                f"Agents may only spawn targets with equal or lower trust level."
            )

        return DynamicAgent(agent_name=target_agent, task_description=task)

    @staticmethod
    def from_delegates(
        spawner_agent: str,
        trigger: str,
        task: str = "",
    ) -> DynamicAgent | None:
        """
        Resolve and spawn the correct agent for a given trigger by reading the
        spawner's DELEGATES block.

        Example:
            # worker.axiom has: Worker -> Evaluator (on: output_ready)
            agent = AgentFactory.from_delegates("worker", trigger="output_ready")
            result = agent.execute(task)

        Returns None if no delegate matches the trigger or spawn fails.
        """
        from axiom_files.parser import load_axiom, resolve_trust_level

        spawner_parsed = load_axiom(spawner_agent)
        spawner_trust = resolve_trust_level(spawner_parsed, default=1)
        delegates = spawner_parsed.get("delegates", [])

        trigger_lower = trigger.lower().strip()

        for entry in delegates:
            if "->" not in entry:
                continue
            entry_lower = entry.lower()
            # Match "on: output_ready" or "on:output_ready"
            if f"on: {trigger_lower}" not in entry_lower and f"on:{trigger_lower}" not in entry_lower:
                continue

            try:
                _, rest = entry.split("->", 1)
                target = rest.split("(on:", 1)[0].strip()
            except Exception:
                continue

            try:
                return AgentFactory.spawn(
                    target_agent=target,
                    task=task,
                    spawner_trust=spawner_trust,
                )
            except (PermissionError, ValueError, FileNotFoundError):
                return None

        return None

    @staticmethod
    def declared_delegates(agent_name: str) -> list[str]:
        """
        Return all declared delegate target names from agent_name's DELEGATES block.

        Example:
            AgentFactory.declared_delegates("worker")
            # -> ["Rewriter", "Evaluator"]
        """
        from axiom_files.parser import load_axiom

        parsed = load_axiom(agent_name)
        delegates = parsed.get("delegates", [])
        targets: list[str] = []

        for entry in delegates:
            if "->" not in entry:
                continue
            try:
                _, rest = entry.split("->", 1)
                target = rest.split("(on:", 1)[0].strip()
                if target:
                    targets.append(target)
            except Exception:
                continue

        return targets

    @staticmethod
    def delegates_for_trigger(agent_name: str, trigger: str) -> list[str]:
        """
        Return all declared delegate target names that match a given trigger.
        Useful when multiple agents are declared for the same trigger.
        """
        from axiom_files.parser import load_axiom

        parsed = load_axiom(agent_name)
        delegates = parsed.get("delegates", [])
        trigger_lower = trigger.lower().strip()
        matches: list[str] = []

        for entry in delegates:
            if "->" not in entry:
                continue
            entry_lower = entry.lower()
            if f"on: {trigger_lower}" not in entry_lower and f"on:{trigger_lower}" not in entry_lower:
                continue
            try:
                _, rest = entry.split("->", 1)
                target = rest.split("(on:", 1)[0].strip()
                if target:
                    matches.append(target)
            except Exception:
                continue

        return matches
