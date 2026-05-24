"""Extract a structured ToolCall from a fenced-block model response.

Backends in this codebase (NIMBackend, LocalNanoBackend, DeepSeekBackend,
CustomBackend) do not expose native function calling. The executor
enforces a strict output contract instead:

    <thought>optional free-form reasoning</thought>
    ```tool
    {"tool": "write_file",
     "args": {"path": "primes.py", "content": "..."}}
    ```

This module's `parse_tool_call(text)` returns a ToolCall or raises
ParseError. The orchestrator retries up to MAX_REPARSE_ATTEMPTS times
before treating it as a failed step.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Tuple

from .models import ToolCall


MAX_REPARSE_ATTEMPTS = 2

# Match a ```tool ... ``` fenced block. DOTALL so the JSON body can
# span lines. Non-greedy so a model emitting multiple fenced blocks
# only contributes the first one.
_TOOL_FENCE_RE = re.compile(
    r"```(?:tool|json)\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

_THOUGHT_RE = re.compile(
    r"<thought>\s*(.*?)\s*</thought>",
    re.DOTALL | re.IGNORECASE,
)


class ParseError(ValueError):
    """The model output did not contain a parseable tool call."""


def parse_tool_call(text: str) -> ToolCall:
    """Pull a `{"tool": ..., "args": {...}}` payload out of `text`.

    Raises ParseError when no fenced block is found, the JSON is
    malformed, or the required fields are missing.
    """
    if not isinstance(text, str) or not text.strip():
        raise ParseError("empty model output")

    thought = ""
    m_thought = _THOUGHT_RE.search(text)
    if m_thought:
        thought = m_thought.group(1).strip()

    payload_text = _extract_fenced_payload(text)
    if payload_text is None:
        # Fallback: maybe the whole response is bare JSON.
        payload_text = _extract_bare_json(text)
    if payload_text is None:
        raise ParseError(
            "no ```tool fenced block (or bare JSON) found in model output"
        )

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as e:
        raise ParseError(f"tool block is not valid JSON: {e}") from e

    if not isinstance(payload, dict):
        raise ParseError("tool block must be a JSON object")

    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ParseError("tool block missing required string field 'tool'")

    args = payload.get("args", {})
    if not isinstance(args, dict):
        raise ParseError("tool block 'args' must be an object")

    return ToolCall(tool=tool.strip(), args=args, thought=thought)


def _extract_fenced_payload(text: str) -> Optional[str]:
    m = _TOOL_FENCE_RE.search(text)
    if not m:
        return None
    return m.group(1).strip()


def _extract_bare_json(text: str) -> Optional[str]:
    """Scan for the first `{...}` block that parses as JSON.

    Used as a forgiveness pass when the model forgets the fence.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return candidate
    return None


_PLAN_FENCE_RE = re.compile(
    r"```(?:plan|json)\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def parse_plan(text: str) -> Tuple[list, str]:
    """Pull a list of subgoals out of a planner response.

    Expected shape:
        ```plan
        {"subgoals": [
            {"id": "s1", "description": "write primes.py"},
            {"id": "s2", "description": "write tests"},
            {"id": "s3", "description": "run pytest"}
        ]}
        ```

    Returns (subgoals_list, raw_payload_text). Raises ParseError on
    malformed input.
    """
    if not isinstance(text, str) or not text.strip():
        raise ParseError("empty planner output")

    m = _PLAN_FENCE_RE.search(text)
    payload_text = m.group(1).strip() if m else _extract_bare_json(text)
    if payload_text is None:
        raise ParseError("no ```plan or bare-JSON block in planner output")

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as e:
        raise ParseError(f"plan block is not valid JSON: {e}") from e

    if not isinstance(payload, dict) or "subgoals" not in payload:
        raise ParseError("plan block must be an object with 'subgoals'")

    subgoals = payload["subgoals"]
    if not isinstance(subgoals, list) or not subgoals:
        raise ParseError("'subgoals' must be a non-empty list")

    for i, sg in enumerate(subgoals):
        if not isinstance(sg, dict):
            raise ParseError(f"subgoal #{i} is not an object")
        if not isinstance(sg.get("id"), str) or not sg["id"].strip():
            raise ParseError(f"subgoal #{i} missing string id")
        if not isinstance(sg.get("description"), str) or not sg["description"].strip():
            raise ParseError(f"subgoal #{i} missing string description")

    return subgoals, payload_text


_VERDICT_FENCE_RE = re.compile(
    r"```(?:verdict|json)\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def parse_verdict(text: str) -> dict:
    """Pull a verdict dict out of a verifier response.

    Expected shape:
        ```verdict
        {"kind": "success", "reason": "tests pass"}
        ```

    Returns the dict (with `kind` and optional `reason`). Raises
    ParseError on malformed input.
    """
    if not isinstance(text, str) or not text.strip():
        raise ParseError("empty verifier output")
    m = _VERDICT_FENCE_RE.search(text)
    payload_text = m.group(1).strip() if m else _extract_bare_json(text)
    if payload_text is None:
        raise ParseError("no ```verdict or bare-JSON block in verifier output")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as e:
        raise ParseError(f"verdict block is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ParseError("verdict block must be an object")
    kind = payload.get("kind")
    if kind not in ("success", "retry", "replan", "abort"):
        raise ParseError(
            f"verdict 'kind' must be one of "
            f"success|retry|replan|abort (got {kind!r})"
        )
    return payload
