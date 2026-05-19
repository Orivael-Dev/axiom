"""DelegateAgent — adapts an AXM SkillDelegate into the EventToken Agent ABC.

Reads the delegate's optional `system_prompt.txt` sibling, enforces
the manifest's `prompt_budget` / `output_budget`, calls the configured
SLMBackend, and emits a signed LayerReport carrying the transport-level
facts (delegate_name, backend, model, input_tokens, output_tokens,
latency_ms, output text).

This is the bridge that lets the existing Coordinator orchestrate
LLM-backed delegates without knowing they exist — DelegateAgent IS-A
Agent, so the agent loop in coordinator.py needs no changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .agents import Agent
from .backends import SLMBackend, BackendError, default_backend
from .models import LayerReport


# Rough token estimator: ~4 chars per token for English text. Cheap and
# good enough for budget-truncation decisions. NIM + Ollama both return
# real token counts in the response, so this is only used pre-flight.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _truncate_to_token_budget(text: str, budget_tokens: int) -> str:
    """Right-truncate text so its estimated token count fits the budget.

    The system prompt is sacred — only event-content text is shortened.
    """
    max_chars = budget_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _compact_inputs(inputs: dict[str, Any]) -> str:
    """Render coordinator inputs as a compact, deterministic line per layer.

    Output looks like:
        text: <verbatim text>
        audio.impact_profile: <value>
        ...
        ---
        CONTEXT (do not echo back; use as background):
        <extra_context block>
        ---
    Empty / None values are skipped so the prompt stays tight. The
    `extra_context` block is appended LAST so it's the first thing
    trimmed when the prompt budget is tight — text remains sacred.
    """
    lines: list[str] = []
    text = inputs.get("text")
    if isinstance(text, str) and text:
        lines.append(f"text: {text}")
    for key in ("audio", "video", "physics", "qrf"):
        sub = inputs.get(key)
        if isinstance(sub, dict) and sub:
            for k, v in sorted(sub.items()):
                if v is None or v == "":
                    continue
                lines.append(f"{key}.{k}: {v}")
    extra = inputs.get("extra_context")
    if isinstance(extra, dict) and extra:
        rendered = "\n".join(
            f"{k}: {v}" for k, v in sorted(extra.items())
            if v not in (None, "", {})
        )
    elif isinstance(extra, str) and extra.strip():
        rendered = extra.strip()
    else:
        rendered = ""
    if rendered:
        lines.append("---")
        lines.append("CONTEXT (do not echo back; use as background):")
        lines.append(rendered)
        lines.append("---")
    return "\n".join(lines)


class DelegateAgent(Agent):
    """Wrap an AXM SkillDelegate as a runtime Agent.

    Usage:
        from axiom_axm import AXMContainer
        from axiom_event_token.delegate_runtime import DelegateAgent
        from axiom_event_token.backends import default_backend

        container = AXMContainer.from_path("...")
        agent = DelegateAgent(
            delegate=container.delegates[0],
            axm_root=container.path,
            backend=default_backend(),
        )
        report = agent.run({"text": "..."})
        assert report.verify()
    """

    def __init__(
        self,
        *,
        delegate,                                # SkillDelegate
        axm_root: Path,
        backend:  Optional[SLMBackend] = None,
        system_prompt: Optional[str]   = None,
    ) -> None:
        self._delegate = delegate
        self._axm_root = Path(axm_root)
        self._backend  = backend or default_backend()
        if system_prompt is not None:
            self._system = system_prompt
        else:
            self._system = self._load_system_prompt()
        self.agent_name = delegate.name

    def _load_system_prompt(self) -> str:
        """Read delegates/<name>/system_prompt.txt if present, else default."""
        path = self._axm_root / "delegates" / self._delegate.name / "system_prompt.txt"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return (
            f"You are the '{self._delegate.name}' delegate inside the AXIOM "
            f"event-token runtime. Be concise and answer in one short "
            f"paragraph."
        )

    def run(self, inputs: dict[str, Any]) -> LayerReport:
        d = self._delegate
        sys_tokens = _estimate_tokens(self._system)
        content_budget = max(64, int(d.prompt_budget) - sys_tokens)

        rendered = _compact_inputs(inputs)
        truncated = _truncate_to_token_budget(rendered, content_budget)
        budget_exceeded = len(truncated) < len(rendered)

        try:
            result = self._backend.generate(
                system=self._system,
                prompt=truncated,
                max_output_tokens=int(d.output_budget),
            )
            backend_used = result.backend
            model_used   = result.model
            text_out     = result.text
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            latency_ms   = result.latency_ms
            error        = None
        except BackendError as e:
            backend_used = "unknown"
            model_used   = "unknown"
            text_out     = ""
            input_tokens = _estimate_tokens(self._system) + _estimate_tokens(truncated)
            output_tokens = 0
            latency_ms   = 0
            error        = str(e)

        payload = {
            "delegate":        d.name,
            "backend":         backend_used,
            "model":           model_used,
            "input_tokens":    input_tokens,
            "output_tokens":   output_tokens,
            "latency_ms":      latency_ms,
            "prompt_budget":   int(d.prompt_budget),
            "output_budget":   int(d.output_budget),
            "budget_exceeded": budget_exceeded,
            "output":          text_out,
        }
        if error is not None:
            payload["error"] = error
        confidence = 0.0 if error else 0.8
        return LayerReport.signed(
            agent=d.name,
            payload=payload,
            confidence=confidence,
        )
