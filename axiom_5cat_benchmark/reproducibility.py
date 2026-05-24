"""Capture the surface needed to reproduce a benchmark run later.

Records:
  - AXIOM commit SHA (so a future verifier can check out the same code)
  - master key fingerprint (so a verifier with the matching key knows
    this is "their" run)
  - seed + temperature + start/end UTC
  - adapter versions (model_id strings + SDK versions)

Output is the ``meta`` block that gets signed and embedded in
results.json.
"""
from __future__ import annotations

import datetime as _dt
import importlib.metadata as _md
import os
import subprocess
from pathlib import Path
from typing import Any

from axiom_5cat_benchmark import __version__ as _BENCH_VERSION
from axiom_5cat_benchmark.signing import master_key_fingerprint


SCHEMA_TAG = "axiom-5cat-bench/v1"


def _axiom_commit() -> str:
    """Repo HEAD SHA, short form. Falls back to env-supplied tag."""
    env = os.environ.get("AXIOM_COMMIT_SHA")
    if env:
        return env.strip()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(__file__).resolve().parent.parent,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _sdk_version(pkg: str) -> str:
    try:
        return _md.version(pkg)
    except _md.PackageNotFoundError:
        return "absent"


def adapter_versions() -> dict[str, str]:
    """Best-effort version map for adapter-backing SDKs.

    Records the version of every SDK we *might* call out to, even if
    a given run only used a subset — the meta block then tells a
    verifier exactly which library versions could have produced any
    observed completions."""
    return {
        "anthropic": _sdk_version("anthropic"),
        "openai":    _sdk_version("openai"),
        "local":     "openai-compat",
        "stub":      "stub-v1",
        "bench_pkg": _BENCH_VERSION,
    }


def utcnow_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_meta(
    *,
    seed: int,
    temperature: float,
    started_utc: str,
    ended_utc: str,
) -> dict[str, Any]:
    """Construct the signed meta block.

    Caller embeds the returned dict at results['meta'] then signs the
    whole top-level result; signing.sign_and_attach can be used."""
    return {
        "schema":                 SCHEMA_TAG,
        "axiom_commit":           _axiom_commit(),
        "adapter_versions":       adapter_versions(),
        "master_key_fingerprint": master_key_fingerprint(),
        "started_utc":            started_utc,
        "ended_utc":              ended_utc,
        "seed":                   seed,
        "temperature":            temperature,
    }
