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


def test_pack_lift_fixture_verifies(fixture_env):
    """audit_unsafe_with_packs.pdf — the demo fixture showing the score
    lift when the recommended kid packs are installed against the same
    weak system prompt as audit_unsafe.pdf."""
    from axiom_report.generator import verify_pdf
    pdf = (FIXTURE_DIR / "audit_unsafe_with_packs.pdf").read_bytes()
    sig = (FIXTURE_DIR / "audit_unsafe_with_packs.pdf.sig").read_text().strip()
    assert verify_pdf(pdf, sig) is True


def test_pack_lift_actually_lifts(fixture_env):
    """Re-run the audit pipeline with and without the recommended pack
    set against the same unsafe system prompt. Packs MUST move safety
    and privacy stars upward, otherwise the audit's pack-recommendation
    surface is misleading."""
    from axiom_report.audits import run_audit
    unsafe = (FIXTURE_DIR / "system_prompt_unsafe.txt").read_text()
    packs = ("coppa", "kid-voice-output", "prompt-injection-strict",
             "medical-deflect", "hate-deflect")

    baseline = run_audit(
        toy_name="X", vendor="Y", audit_date="2026-05-25",
        system_prompt=unsafe, installed_packs=(),
    )
    lifted = run_audit(
        toy_name="X", vendor="Y", audit_date="2026-05-25",
        system_prompt=unsafe, installed_packs=packs,
    )

    assert lifted.safety_stars > baseline.safety_stars, (
        f"safety should lift with packs installed: "
        f"baseline={baseline.safety_stars} lifted={lifted.safety_stars}"
    )
    assert lifted.privacy_stars > baseline.privacy_stars, (
        f"privacy should lift with packs installed: "
        f"baseline={baseline.privacy_stars} lifted={lifted.privacy_stars}"
    )
    # age_fit is heuristic on the system prompt itself — packs cannot
    # rescue a weak prompt, so it should remain at 1 star.
    assert lifted.age_fit_stars == baseline.age_fit_stars, (
        "age_fit MUST be unaffected by packs — it scores the prompt text"
    )


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


# ─── 6. CLI failure-mode hardening ──────────────────────────────────────


def _run_audit_cli(*args, env_key=FIXTURE_KEY) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_key is None:
        env.pop("AXIOM_MASTER_KEY", None)
    else:
        env["AXIOM_MASTER_KEY"] = env_key
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_kid_audit.py"), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_run_audit_exits_nonzero_when_master_key_missing(tmp_path):
    """No AXIOM_MASTER_KEY → friendly error, non-zero exit."""
    out_pdf = tmp_path / "x.pdf"
    r = _run_audit_cli(
        "--toy", "X", "--vendor", "Y",
        "--system-prompt", str(FIXTURE_DIR / "system_prompt_safe.txt"),
        "--out", str(out_pdf),
        env_key=None,
    )
    assert r.returncode != 0
    assert "AXIOM_MASTER_KEY" in r.stderr or "AXIOM_MASTER_KEY" in r.stdout
    assert not out_pdf.exists()


def test_run_audit_exits_nonzero_when_system_prompt_missing(tmp_path):
    """Missing --system-prompt file → friendly error, non-zero exit, no PDF written."""
    out_pdf = tmp_path / "x.pdf"
    r = _run_audit_cli(
        "--toy", "X", "--vendor", "Y",
        "--system-prompt", "/no/such/system_prompt.txt",
        "--out", str(out_pdf),
    )
    assert r.returncode != 0
    assert "System prompt file not found" in r.stderr or "not found" in r.stderr
    assert not out_pdf.exists()


def test_run_audit_exits_with_clean_error_on_unknown_pack(tmp_path):
    """Unknown --packs entry → wrapped ValueError, NOT a raw traceback."""
    out_pdf = tmp_path / "x.pdf"
    r = _run_audit_cli(
        "--toy", "X", "--vendor", "Y",
        "--system-prompt", str(FIXTURE_DIR / "system_prompt_safe.txt"),
        "--packs", "nonexistent-pack-xyz",
        "--out", str(out_pdf),
    )
    assert r.returncode != 0
    # No raw traceback in stderr — the script catches ValueError.
    assert "Traceback" not in r.stderr
    assert "Audit failed" in r.stderr
    assert "nonexistent-pack-xyz" in r.stderr
    assert not out_pdf.exists()


def test_verify_cli_handles_empty_signature_file(tmp_path):
    """Zero-byte signature file → FAIL (return 2), not crash."""
    pdf = FIXTURE_DIR / "audit_safe.pdf"
    empty_sig = tmp_path / "empty.sig"
    empty_sig.write_text("")
    r = _run_verify("--pdf", str(pdf), "--sig", str(empty_sig), "--quiet")
    # Verification must fail (return 2), not return success and not crash.
    assert r.returncode == 2, r.stdout + r.stderr


def test_verify_cli_handles_garbage_signature(tmp_path):
    """Non-hex garbage in signature file → FAIL (return 2)."""
    pdf = FIXTURE_DIR / "audit_safe.pdf"
    bad_sig = tmp_path / "garbage.sig"
    bad_sig.write_text("this is not a hex signature at all\n")
    r = _run_verify("--pdf", str(pdf), "--sig", str(bad_sig), "--quiet")
    assert r.returncode == 2, r.stdout + r.stderr


def test_inspect_cli_exits_nonzero_on_unknown_corpus():
    """`inspect --corpus does-not-exist` → friendly error, non-zero exit."""
    r = _run_inspect("summary", "--corpus", "does_not_exist_xyz")
    assert r.returncode != 0
    combined = (r.stdout + r.stderr).lower()
    assert "not found" in combined or "corpus" in combined


def test_inspect_cli_argparse_rejects_unknown_mode():
    """Unknown positional mode → argparse error (exit 2)."""
    r = _run_inspect("totally-not-a-mode")
    assert r.returncode == 2, r.stdout + r.stderr
