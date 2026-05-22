"""Sanity checks on deploy/firewall/Dockerfile.

These tests don't actually build the image (Docker isn't in CI yet);
they parse the Dockerfile + lockfile and assert the shape we expect.
That catches the class of bugs where the runtime tree on disk diverges
from what dashboard.py reads at request time — e.g. the /help-404 bug
caused by forgetting to COPY docs/firewall/ into /app.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT  = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "deploy" / "firewall" / "Dockerfile"
LOCKFILE   = REPO_ROOT / "deploy" / "firewall" / "requirements.txt"
SOURCE_IN  = REPO_ROOT / "deploy" / "firewall" / "requirements.in"


def _dockerfile_text() -> str:
    assert DOCKERFILE.exists(), f"missing {DOCKERFILE}"
    return DOCKERFILE.read_text(encoding="utf-8")


# ─── /help docs must ship in the image ─────────────────────────────

def test_dockerfile_copies_firewall_docs():
    """Regression for the `/help` → 404 bug: dashboard.py reads
    `BASE_DIR.parent / "docs" / "firewall"`, which resolves to
    /app/docs/firewall inside the container. If the Dockerfile doesn't
    COPY that tree in, /help returns
    `{"detail":"no firewall docs at /app/docs/firewall"}`.
    """
    text = _dockerfile_text()
    # Match either `COPY docs/firewall/ ./docs/firewall/` (preferred)
    # or `COPY docs/ ./docs/` (also fine — broader copy).
    has_targeted_copy = re.search(
        r"^COPY\s+docs/firewall/?\s+\./docs/firewall/?\b",
        text,
        re.MULTILINE,
    ) is not None
    has_broad_copy = re.search(
        r"^COPY\s+docs/?\s+\./docs/?\b",
        text,
        re.MULTILINE,
    ) is not None
    assert has_targeted_copy or has_broad_copy, (
        "Dockerfile must COPY docs/firewall/ into the image — "
        "otherwise /help renders 'no firewall docs at /app/docs/firewall'."
    )


def test_help_doc_files_exist_in_repo():
    """The COPY in the Dockerfile is only useful if these files exist
    on disk at build time. Catches accidental rename/delete of the
    docs the in-dashboard /help page expects to render."""
    docs_dir = REPO_ROOT / "docs" / "firewall"
    assert docs_dir.is_dir(), f"missing {docs_dir}"
    # Slugs the existing /help tests + nav rely on:
    for slug in ("index", "quickstart", "billing", "skill-packs",
                 "api-reference"):
        p = docs_dir / f"{slug}.md"
        assert p.exists(), f"missing required help doc: {p}"


# ─── Lockfile-based install (issue #4) ─────────────────────────────

def test_dockerfile_installs_from_lockfile_with_hashes():
    """The pip install line in the Dockerfile must consume the
    pip-compile'd lockfile, not inline version pins. --require-hashes
    means a tampered wheel mid-supply-chain fails the build."""
    text = _dockerfile_text()
    assert "requirements.txt" in text, \
        "Dockerfile no longer references the requirements.txt lockfile"
    assert "--require-hashes" in text, \
        "pip install must run with --require-hashes for supply-chain safety"
    # Wildcard pins are the bug class we're moving away from.
    assert re.search(r"==\d+\.\d+\.\*", text) is None, (
        "Dockerfile still has wildcard pins (==X.Y.*) — those defeat the "
        "lockfile. Move all version pins into deploy/firewall/requirements.in."
    )


def test_lockfile_present_and_hashed():
    assert LOCKFILE.exists(), (
        f"missing {LOCKFILE}. Generate with: "
        "pip-compile --generate-hashes deploy/firewall/requirements.in "
        "-o deploy/firewall/requirements.txt"
    )
    body = LOCKFILE.read_text(encoding="utf-8")
    # Every locked dep must carry at least one --hash=sha256:... line,
    # otherwise --require-hashes will reject the install at build time.
    assert "--hash=sha256:" in body, \
        "lockfile is missing hashes — regenerate with --generate-hashes"
    # The lockfile must mention the framework deps we actually use.
    for pkg in ("fastapi", "uvicorn", "jinja2", "itsdangerous",
                "python-multipart", "stripe"):
        # Allow optional [extras] like `uvicorn[standard]==`.
        assert re.search(rf"(?im)^{re.escape(pkg)}(\[[^\]]+\])?==", body), \
            f"lockfile is missing pinned entry for {pkg}"


def test_requirements_in_pins_exact_versions():
    """deploy/firewall/requirements.in is the source pip-compile reads.
    Each line must be an exact == pin so the resolved lockfile is
    deterministic across runs."""
    assert SOURCE_IN.exists(), f"missing {SOURCE_IN}"
    for line in SOURCE_IN.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        assert "==" in s, \
            f"requirements.in entry is not exact-pinned: {s!r}"
        assert not s.endswith(".*"), \
            f"requirements.in entry uses wildcard pin: {s!r}"


# ─── Dependabot config (issue #4) ─────────────────────────────────

def test_dependabot_config_watches_firewall_lockfile():
    """The whole point of the lockfile is to be watched. If Dependabot
    isn't pointed at deploy/firewall, security bumps stall."""
    cfg = REPO_ROOT / ".github" / "dependabot.yml"
    assert cfg.exists(), f"missing {cfg}"
    body = cfg.read_text(encoding="utf-8")
    assert "/deploy/firewall" in body, \
        "dependabot.yml must watch /deploy/firewall for the pip lockfile"
    assert "github-actions" in body, \
        "dependabot.yml should also watch /.github/workflows/ (action SHAs)"
