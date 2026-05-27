"""Tests for axiom_autonomous.parser — JSON-in-fenced-block extraction."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("AXIOM_MASTER_KEY", "test" + "0" * 60)


def test_parse_tool_call_happy_path():
    from axiom_autonomous.parser import parse_tool_call
    text = (
        "<thought>I'll scaffold the entry point.</thought>\n"
        "```tool\n"
        '{"tool": "write_file", '
        '"args": {"path": "primes.py", "content": "def f(): pass"}}\n'
        "```\n"
    )
    call = parse_tool_call(text)
    assert call.tool == "write_file"
    assert call.args["path"] == "primes.py"
    assert "scaffold" in call.thought


def test_parse_tool_call_bare_json_fallback():
    from axiom_autonomous.parser import parse_tool_call
    # Model forgot the fence — bare-JSON pass should still recover.
    text = '{"tool": "read_file", "args": {"path": "foo.py"}}'
    call = parse_tool_call(text)
    assert call.tool == "read_file"


def test_parse_tool_call_missing_tool_field_raises():
    from axiom_autonomous.parser import ParseError, parse_tool_call
    text = '```tool\n{"args": {"x": 1}}\n```'
    with pytest.raises(ParseError, match="'tool'"):
        parse_tool_call(text)


def test_parse_tool_call_malformed_json_raises():
    from axiom_autonomous.parser import ParseError, parse_tool_call
    text = "```tool\n{not valid json}\n```"
    with pytest.raises(ParseError, match="valid JSON"):
        parse_tool_call(text)


def test_parse_tool_call_empty_input_raises():
    from axiom_autonomous.parser import ParseError, parse_tool_call
    with pytest.raises(ParseError, match="empty"):
        parse_tool_call("")


def test_parse_plan_happy_path():
    from axiom_autonomous.parser import parse_plan
    text = (
        "```plan\n"
        '{"subgoals": ['
        '{"id": "s1", "description": "write file"},'
        '{"id": "s2", "description": "run tests"}'
        "]}\n```"
    )
    subgoals, raw = parse_plan(text)
    assert len(subgoals) == 2
    assert subgoals[0]["id"] == "s1"
    assert "subgoals" in raw


def test_parse_plan_rejects_empty_subgoals():
    from axiom_autonomous.parser import ParseError, parse_plan
    text = '```plan\n{"subgoals": []}\n```'
    with pytest.raises(ParseError, match="non-empty list"):
        parse_plan(text)


def test_parse_plan_rejects_missing_id():
    from axiom_autonomous.parser import ParseError, parse_plan
    text = '```plan\n{"subgoals": [{"description": "..."}]}\n```'
    with pytest.raises(ParseError, match="string id"):
        parse_plan(text)


def test_parse_verdict_happy_path():
    from axiom_autonomous.parser import parse_verdict
    text = '```verdict\n{"kind": "success", "reason": "tests pass"}\n```'
    v = parse_verdict(text)
    assert v["kind"] == "success"
    assert v["reason"] == "tests pass"


def test_parse_verdict_rejects_unknown_kind():
    from axiom_autonomous.parser import ParseError, parse_verdict
    text = '```verdict\n{"kind": "winning", "reason": "..."}\n```'
    with pytest.raises(ParseError, match="kind"):
        parse_verdict(text)
