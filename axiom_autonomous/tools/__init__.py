"""Tool registry for the autonomous-agent loop.

Each tool is a frozen `Tool` dataclass: name + description + JSON
schema + a dispatch callable + a risk hint. `ToolRegistry` collects
them and provides `dispatch(call, sandbox)` + `schema()` for the
executor prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping

from ..models import Observation, ToolCall
from ..sandbox import Sandbox


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    json_schema: Mapping[str, str]      # {"path": "str", ...}
    dispatch: Callable[[ToolCall, Sandbox], Observation]
    risk: str = "low"                   # "low" | "medium" | "high"


class ToolNotFoundError(KeyError):
    """The executor produced a tool name the registry doesn't know."""


class ToolRegistry:
    """Holds the set of tools available to the executor.

    Use `default_registry()` for the v1 tool set. Tests can build a
    smaller registry and inject custom dispatch callables.
    """

    def __init__(self, tools: List[Tool]) -> None:
        self._tools: Dict[str, Tool] = {t.name: t for t in tools}

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> List[str]:
        return sorted(self._tools.keys())

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolNotFoundError(name)
        return self._tools[name]

    def schema(self) -> str:
        """Human-readable tool inventory the executor prompt embeds.

        One block per tool, deterministic order, plain text — small
        enough to live in every executor call without blowing the
        prompt budget.
        """
        lines: List[str] = []
        for name in self.names():
            t = self._tools[name]
            arg_lines = ", ".join(
                f"{k}: {v}" for k, v in t.json_schema.items()
            ) or "(no args)"
            lines.append(
                f"- {t.name}({arg_lines})  risk={t.risk}\n    {t.description}"
            )
        return "\n".join(lines)

    def dispatch(self, call: ToolCall, sandbox: Sandbox) -> Observation:
        """Find the tool + run it. Tools own their own error handling
        and return Observation(ok=False, error=...) on failure rather
        than raising."""
        if call.tool not in self._tools:
            raise ToolNotFoundError(call.tool)
        return self._tools[call.tool].dispatch(call, sandbox)


def default_registry() -> ToolRegistry:
    """The v1 tool set: filesystem + shell + run_tests + finish."""
    from . import fs, shell
    tools: List[Tool] = []
    tools.extend(fs.TOOLS)
    tools.extend(shell.TOOLS)
    return ToolRegistry(tools)


__all__ = [
    "Tool", "ToolRegistry", "ToolNotFoundError", "default_registry",
]
