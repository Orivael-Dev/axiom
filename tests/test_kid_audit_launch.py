"""Audit-launch readiness tests.

Locks in the auditor-facing surface so it can't silently drift:

  1. The baseline fixture audits (unsafe + safe) verify successfully
     under the same AXIOM_MASTER_KEY that produced them.
  2. Tampering with the PDF flips verify to FAIL.
  3. Tampering with the signature flips verify to FAIL.
  4. The standalone verify_kid_audit.py exits 0 on a good pair and
     2 on a bad pair — the contract auditors will script against.
  5. inspect_kid_corpus.py summary / detail / json all produce
     non-trivial output with the expected category names.

These tests do NOT regenerate the fixture PDFs — they read the
already-signed ones in fixtures/kid_audit_baseline/. That way the
fixtures themselves act as a regression boundary: if the PDF
renderer changes byte-for-byte output, this test fires and we
re-bless deliberately rather than silently.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "kid_audit_baseline"

# Same key used at scripts/run_kid_audit.py invocation that produced the fixtures.
# Documented in docs/AUDIT_LAUNCH.md — auditors receive this with the package.
FIXTURE_KEY = "audit_baseline_fixture_key_do_not_use_in_prod_ffffffffffffffff"


@pytest.fixture
def fixture_env(monkeypatch):
    monkeypatch.setenv("AXIOM_MASTER_KEY", FIXTURE_KEY)
    # Clear cached signing key in case axiom_signing memoizes
    for mod in list(sys.modules):
        if mod.startswith(("axiom_signing", "axiom_report")):
            sys.modules.pop(mod, None)
    yield


# ─── 1. Baseline PDFs verify ────────────────────────────────────────────


def test_unsafe_baseline_pdf_verifies(fixture_env):
    from axiom_report.generator import verify_pdf
    pdf = (FIXTURE_DIR / "audit_unsafe.pdf").read_bytes()
    sig = (FIXTURE_DIR / "audit_unsafe.pdf.sig").read_text().strip()
    assert verify_pdf(pdf, sig) is True


def test_safe_baseline_pdf_verifies(fixture_env):
    from axiom_report.generator import verify_pdf
    pdf = (FIXTURE_DIR / "audit_safe.pdf").read_bytes()
    sig = (FIXTURE_DIR / "audit_safe.pdf.sig").read_text().strip()
    assert verify_pdf(pdf, sig) is True


# ─── 2-3. Tampering is detected ─────────────────────────────────────────


def test_modified_pdf_fails_verify(fixture_env):
    from axiom_report.generator import verify_pdf
    pdf = bytearray((FIXTURE_DIR / "audit_safe.pdf").read_bytes())
    sig = (FIXTURE_DIR / "audit_safe.pdf.sig").read_text().strip()
    # Flip a byte in the body — the tail of the file is binary trailer
    pdf[len(pdf) // 2] ^= 0x01
    assert verify_pdf(bytes(pdf), sig) is False


def test_modified_signature_fails_verify(fixture_env):
    from axiom_report.generator import verify_pdf
    pdf = (FIXTURE_DIR / "audit_safe.pdf").read_bytes()
    sig = (FIXTURE_DIR / "audit_safe.pdf.sig").read_text().strip()
    # Flip the first hex char
    bad = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert verify_pdf(pdf, bad) is False


# ─── 4. verify_kid_audit.py CLI contract ────────────────────────────────


def _run_verify(*args, env_key=FIXTURE_KEY) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AXIOM_MASTER_KEY"] = env_key
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_kid_audit.py"), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_verify_cli_exits_zero_on_good_pair():
    r = _run_verify(
        "--pdf", str(FIXTURE_DIR / "audit_safe.pdf"),
        "--sig", str(FIXTURE_DIR / "audit_safe.pdf.sig"),
        "--quiet",
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_verify_cli_exits_two_on_wrong_key():
    r = _run_verify(
        "--pdf", str(FIXTURE_DIR / "audit_safe.pdf"),
        "--sig", str(FIXTURE_DIR / "audit_safe.pdf.sig"),
        "--quiet",
        env_key="wrongkey" + "0" * 56,
    )
    assert r.returncode == 2, r.stdout + r.stderr


def test_verify_cli_exits_one_when_key_missing(monkeypatch, tmp_path):
    # Explicit empty key — must not pick one up from the calling env
    env = os.environ.copy()
    env.pop("AXIOM_MASTER_KEY", None)
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_kid_audit.py"),
         "--pdf", str(FIXTURE_DIR / "audit_safe.pdf"),
         "--sig", str(FIXTURE_DIR / "audit_safe.pdf.sig")],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert r.returncode == 1
    assert "AXIOM_MASTER_KEY" in r.stderr


def test_verify_cli_exits_one_on_missing_files():
    r = _run_verify("--pdf", "/no/such/file.pdf", "--sig", "/no/such/file.sig")
    assert r.returncode == 1


# ─── 5. inspect_kid_corpus.py transparency tool ─────────────────────────


def _run_inspect(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "inspect_kid_corpus.py"), *args],
        capture_output=True, text=True, timeout=30,
    )


def test_inspect_summary_shows_all_categories():
    r = _run_inspect("summary")
    assert r.returncode == 0
    out = r.stdout
    for cat in ["pii", "predatory", "scary", "adult", "medical",
                "hate_provocation", "deception", "system_extraction",
                "dependency", "off_brand"]:
        assert cat in out, f"category {cat} missing from summary"
    assert "Total prompts:" in out
    assert "kid_safety_v1" in out


def test_inspect_detail_lists_individual_prompts():
    r = _run_inspect("detail")
    assert r.returncode == 0
    # First prompt id from the corpus — locks the format
    assert "pii-01" in r.stdout
    # Markdown table header present per category
    assert "| id | severity | expected | prompt | notes |" in r.stdout


def test_inspect_json_round_trips():
    r = _run_inspect("json")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["name"] == "kid_safety_v1"
    assert len(d["prompts"]) >= 40
    # Every prompt has the expected schema
    for p in d["prompts"]:
        for field in ("id", "category", "severity",
                      "expected_verdict", "prompt"):
            assert field in p, f"missing field {field} in {p}"
