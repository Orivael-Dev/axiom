"""Tests for the Colab/Jupyter training-data adapter.

The Colab-specific paths (`upload`, `drive:`) can't be tested without
a live Colab kernel, so we test:
  * format detection (all three input shapes)
  * normalisation into both output formats
  * filtering / dedupe semantics
  * error messages for malformed inputs
  * sample file ships and parses cleanly
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from notebooks.axiom_colab import (
    DEFAULT_SYSTEM_PROMPT,
    _chatml_text_to_messages,
    _detect_shape,
    _messages_to_chatml_text,
    _raw_to_messages,
    _read_jsonl,
    _resolve_source,
    _SAMPLE_PATH,
    load_training_data,
)


# ── Sample shipping ──────────────────────────────────────────────────


def test_sample_exists_and_parses():
    assert _SAMPLE_PATH.exists(), \
        f"bundled sample missing at {_SAMPLE_PATH}"
    rows = _read_jsonl(_SAMPLE_PATH)
    assert len(rows) >= 10, "sample should have plenty of examples"
    for r in rows[:3]:
        assert "messages" in r
        roles = [m["role"] for m in r["messages"]]
        assert roles[0] == "system"


def test_load_sample_default():
    examples = load_training_data("sample")
    assert len(examples) >= 10
    assert all("messages" in ex for ex in examples)


def test_load_sample_as_chatml_text():
    examples = load_training_data("sample", output_format="text")
    assert len(examples) >= 10
    assert all("text" in ex for ex in examples)
    assert all("<|im_start|>" in ex["text"] for ex in examples)


# ── Format detection ─────────────────────────────────────────────────


def test_detect_messages_shape():
    row = {"messages": [{"role": "user", "content": "hi"}]}
    assert _detect_shape(row) == "messages"


def test_detect_raw_shape():
    row = {"instruction": "do X", "input": "", "output": "done"}
    assert _detect_shape(row) == "raw"
    assert _detect_shape({"instruction": "do X", "response": "ok"}) == "raw"


def test_detect_chatml_text_shape():
    row = {"text": "<|im_start|>user\nhi<|im_end|>"}
    assert _detect_shape(row) == "chatml_text"


def test_detect_unknown_shape_raises():
    with pytest.raises(ValueError, match="unrecognised row shape"):
        _detect_shape({"foo": "bar"})


# ── Normalisation ────────────────────────────────────────────────────


def test_raw_to_messages_basic():
    msgs = _raw_to_messages(
        {"instruction": "write a guard", "input": "", "output": "OK done"},
        DEFAULT_SYSTEM_PROMPT,
    )
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert msgs[1]["content"] == "write a guard"
    assert msgs[2]["content"] == "OK done"


def test_raw_to_messages_appends_input():
    msgs = _raw_to_messages(
        {"instruction": "review code", "input": "def f(): pass",
         "output": "looks fine"},
        DEFAULT_SYSTEM_PROMPT,
    )
    assert "review code" in msgs[1]["content"]
    assert "def f(): pass" in msgs[1]["content"]


def test_raw_to_messages_accepts_response_alias():
    msgs = _raw_to_messages(
        {"instruction": "X", "response": "Y"},
        DEFAULT_SYSTEM_PROMPT,
    )
    assert msgs[2]["content"] == "Y"


def test_chatml_text_roundtrip():
    msgs = [
        {"role": "system", "content": "you are AXIOM"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    text = _messages_to_chatml_text(msgs)
    parsed = _chatml_text_to_messages(text)
    assert parsed == msgs


# ── End-to-end on synthetic inputs ───────────────────────────────────


def _write_jsonl(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "data.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def test_loads_raw_jsonl(tmp_path):
    src = _write_jsonl(tmp_path, [
        {"instruction": "do A", "input": "", "output": "x" * 40},
        {"instruction": "do B", "input": "", "output": "y" * 40},
    ])
    out = load_training_data(str(src))
    assert len(out) == 2
    assert out[0]["messages"][0]["role"] == "system"


def test_dedupe_drops_duplicate_instructions(tmp_path):
    src = _write_jsonl(tmp_path, [
        {"instruction": "do same", "input": "", "output": "a" * 40},
        {"instruction": "do same", "input": "", "output": "b" * 40},
        {"instruction": "do diff", "input": "", "output": "c" * 40},
    ])
    out = load_training_data(str(src))
    assert len(out) == 2


def test_min_output_chars_filter(tmp_path):
    src = _write_jsonl(tmp_path, [
        {"instruction": "good", "input": "", "output": "x" * 40},
        {"instruction": "short", "input": "", "output": "ok"},   # 2 chars
    ])
    out = load_training_data(str(src))
    assert len(out) == 1
    assert out[0]["messages"][1]["content"] == "good"


def test_messages_format_passes_through(tmp_path):
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    src = _write_jsonl(tmp_path, [{"messages": msgs}, {"messages": msgs}])
    out = load_training_data(str(src))
    assert len(out) == 2
    assert out[0]["messages"] == msgs


def test_chatml_text_input_to_messages_output(tmp_path):
    text = ("<|im_start|>system\nbe good<|im_end|>\n"
            "<|im_start|>user\nhi<|im_end|>\n"
            "<|im_start|>assistant\nhello<|im_end|>")
    src = _write_jsonl(tmp_path, [{"text": text}])
    out = load_training_data(str(src))
    assert [m["role"] for m in out[0]["messages"]] == \
        ["system", "user", "assistant"]
    assert out[0]["messages"][2]["content"] == "hello"


def test_text_output_format(tmp_path):
    src = _write_jsonl(tmp_path, [
        {"instruction": "X", "input": "", "output": "Y" * 40},
    ])
    out = load_training_data(str(src), output_format="text")
    assert "text" in out[0]
    assert "<|im_start|>" in out[0]["text"]


# ── Error paths ──────────────────────────────────────────────────────


def test_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        load_training_data(str(tmp_path / "nope.jsonl"))


def test_empty_file_raises(tmp_path):
    src = tmp_path / "empty.jsonl"
    src.write_text("\n\n  \n")
    with pytest.raises(ValueError, match="empty"):
        load_training_data(str(src))


def test_malformed_json_reports_line(tmp_path):
    src = tmp_path / "bad.jsonl"
    src.write_text(textwrap.dedent("""\
        {"messages": [{"role": "user", "content": "ok"}]}
        not json
        """))
    with pytest.raises(ValueError, match=r"bad\.jsonl:2"):
        load_training_data(str(src))


def test_invalid_output_format_raises(tmp_path):
    src = _write_jsonl(tmp_path, [
        {"instruction": "x", "input": "", "output": "y" * 40},
    ])
    with pytest.raises(ValueError, match="output_format"):
        load_training_data(str(src), output_format="parquet")


def test_resolve_source_unknown_prefix_treated_as_path(tmp_path):
    fake = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError):
        _resolve_source(str(fake))
