"""Baseline tests — the agent must not break these while adding --json."""
import io
import sys

import cli


def test_text_default_preserved(capsys):
    rc = cli.main(["--name", "alice"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hello, alice" in captured.out
