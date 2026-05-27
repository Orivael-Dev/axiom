"""A tiny string-utility module with one bug the scenario expects
the agent to find and fix.

Bug: `slugify` lowercases and replaces spaces, but it forgets to
strip leading/trailing dashes that arise from leading/trailing
spaces in the input. Tests in `test_stringutils.py` catch this.
"""
from __future__ import annotations

import re


def slugify(text: str) -> str:
    """Lowercase, replace non-alphanumerics with dashes, collapse runs."""
    out = re.sub(r"[^a-z0-9]+", "-", text.lower())
    # BUG: missing .strip("-") here — leading/trailing whitespace in
    # the input produces "-foo-bar-" instead of "foo-bar".
    return out
