# -*- coding: utf-8 -*-
"""
AXIOM OS Shield Tests — ORVL-013 Constitutional OS Protection
==============================================================
3 BLOCKED + 3 PASSED + 3 INVARIANTS

BLOCKED: ransomware, privilege escalation, kernel access — must escalate
PASSED:  antivirus scan, software update, developer build — must NOT escalate

BUG-003: UTF-8 output encoding
BUG-007: HMAC hexdigest finalization
BUG-008: explicit utf-8 encode before HMAC
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# BUG-003: UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_os_shield"

HMAC_KEY = b"os-shield-test-key"


def _make_snapshot(**overrides):
    """Build a ProcessSnapshot with safe defaults."""
    from axiom_os_shield import ProcessSnapshot
    defaults = dict(
        pid=1234,
        name="test_process",
        file_access_rate=5.0,
        child_procs=2,
        network_conns=1,
        memory_mb=150.0,
        cpu_percent=10.0,
        ancestry_chain=["init", "shell", "test_process"],
    )
    defaults.update(overrides)
    return ProcessSnapshot(**defaults)


def _make_baseline():
    """Build a typical baseline profile dict."""
    return {
        "file_access_rate": 5.0,
        "child_procs": 2,
        "network_conns": 1,
        "memory_mb": 150.0,
        "cpu_percent": 10.0,
    }


# ===========================================================================
# SECTION 1 — BLOCKED: threats the shield must escalate
# ===========================================================================

class TestBlocked:

    def test_blocked_ransomware_enumeration(self):
        """BLOCKED: Ransomware pattern (extreme file access + encryption-like
        behavior) must trigger L3+ escalation."""
        from axiom_os_shield import ProcessManifold, ConstitutionalOSShield

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            manifold = ProcessManifold("suspicious.exe", "AGENT")
            manifold.baseline = _make_baseline()

            # Ransomware: 500x normal file access, spawning child processes
            ransomware_snap = _make_snapshot(
                name="suspicious.exe",
                file_access_rate=2500.0,   # 500x baseline of 5.0
                child_procs=30,            # 15x baseline
                memory_mb=800.0,           # massive memory growth
                cpu_percent=95.0,          # pegged CPU
            )

            dist = manifold.measure_distance(ransomware_snap)
            assert dist < 0.02, f"Ransomware distance {dist} should be < L3 threshold 0.02"

            shield = ConstitutionalOSShield(
                hmac_key=HMAC_KEY, log_path=log_path
            )
            level = shield.determine_level(dist)
            assert level >= 3, f"Ransomware should trigger L3+, got L{level}"
        finally:
            os.unlink(log_path)

    def test_blocked_privilege_escalation_ancestry(self):
        """BLOCKED: Suspicious ancestry chain (shell -> unknown -> system)
        must trigger L2+ escalation."""
        from axiom_os_shield import (
            ProcessManifold, ConstitutionalOSShield, FP_L1_CUTOFF,
        )

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            manifold = ProcessManifold("svchost.exe", "AGENT")
            manifold.baseline = _make_baseline()

            # Privilege escalation: normal metrics but suspicious ancestry
            priv_snap = _make_snapshot(
                name="svchost.exe",
                ancestry_chain=["init", "bash", "curl", "chmod", "svchost.exe"],
                child_procs=10,
                network_conns=15,
            )

            shield = ConstitutionalOSShield(
                hmac_key=HMAC_KEY, log_path=log_path
            )
            fp_conf = shield.compute_fp_confidence(priv_snap, manifold)
            # Suspicious ancestry should produce high FP confidence
            # (i.e., high confidence this is NOT a false positive)
            assert fp_conf >= FP_L1_CUTOFF, (
                f"Privilege escalation ancestry fp_conf {fp_conf} "
                f"should meet FP_L1_CUTOFF {FP_L1_CUTOFF}"
            )
        finally:
            os.unlink(log_path)

    def test_blocked_kernel_access_attempt(self):
        """BLOCKED: Kernel access attempt must trigger L4 immediately."""
        from axiom_os_shield import ConstitutionalOSShield

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            shield = ConstitutionalOSShield(
                hmac_key=HMAC_KEY, log_path=log_path
            )
            snap = _make_snapshot(
                name="exploit.bin",
                ancestry_chain=["init", "bash", "wget", "exploit.bin"],
            )
            level = shield.check_kernel_access(snap)
            assert level == 4, f"Kernel access must be L4, got L{level}"
        finally:
            os.unlink(log_path)


# ===========================================================================
# SECTION 2 — PASSED: legitimate behavior must NOT escalate
# ===========================================================================

class TestPassed:

    def test_passed_antivirus_scan_no_escalation(self):
        """PASSED: Antivirus scan has high file access but stays within
        manifold for its process type — no escalation above L1."""
        from axiom_os_shield import ProcessManifold

        manifold = ProcessManifold("antivirus_scan", "GUARD")
        # Antivirus baseline: naturally high file access
        manifold.baseline = {
            "file_access_rate": 200.0,  # AV scans many files
            "child_procs": 4,
            "network_conns": 2,
            "memory_mb": 300.0,
            "cpu_percent": 40.0,
        }

        # AV doing a full scan — elevated but within its own manifold
        av_snap = _make_snapshot(
            name="antivirus_scan",
            file_access_rate=350.0,  # 1.75x baseline, not anomalous for AV
            child_procs=6,
            memory_mb=400.0,
            cpu_percent=60.0,
        )

        dist = manifold.measure_distance(av_snap)
        assert dist > 0.06, (
            f"AV scan distance {dist} should be above L1 threshold 0.06"
        )

    def test_passed_software_update_no_escalation(self):
        """PASSED: Software update modifies own files but stays within
        manifold for update process type — no escalation above L1."""
        from axiom_os_shield import ProcessManifold

        manifold = ProcessManifold("updater", "AGENT")
        manifold.baseline = {
            "file_access_rate": 50.0,
            "child_procs": 5,
            "network_conns": 3,
            "memory_mb": 200.0,
            "cpu_percent": 25.0,
        }

        update_snap = _make_snapshot(
            name="updater",
            file_access_rate=80.0,   # 1.6x baseline
            child_procs=8,           # spawns installer processes
            network_conns=5,         # downloads updates
            memory_mb=300.0,
            cpu_percent=45.0,
        )

        dist = manifold.measure_distance(update_snap)
        assert dist > 0.06, (
            f"Update distance {dist} should be above L1 threshold 0.06"
        )

    def test_passed_developer_build_no_escalation(self):
        """PASSED: Developer build has high CPU + process spawn but stays
        within manifold for development process type — no escalation."""
        from axiom_os_shield import ProcessManifold

        manifold = ProcessManifold("cargo", "AGENT")
        manifold.baseline = {
            "file_access_rate": 100.0,
            "child_procs": 20,       # compiler spawns many processes
            "network_conns": 2,
            "memory_mb": 500.0,
            "cpu_percent": 70.0,
        }

        build_snap = _make_snapshot(
            name="cargo",
            file_access_rate=150.0,  # 1.5x baseline
            child_procs=30,          # 1.5x baseline (parallel compile)
            memory_mb=700.0,
            cpu_percent=90.0,
        )

        dist = manifold.measure_distance(build_snap)
        assert dist > 0.06, (
            f"Dev build distance {dist} should be above L1 threshold 0.06"
        )


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestInvariants:

    def test_safety_ceiling_cannot_mutate(self):
        """SAFETY_CEILING must be 0.25 and not writable."""
        import axiom_os_shield as m
        assert m.SAFETY_CEILING == 0.25
        with pytest.raises((AttributeError, TypeError)):
            m.SAFETY_CEILING = 0.99

    def test_l4_threshold_cannot_mutate(self):
        """L4_THRESHOLD must be 0.005 and not writable."""
        import axiom_os_shield as m
        assert m.L4_THRESHOLD == 0.005
        with pytest.raises((AttributeError, TypeError)):
            m.L4_THRESHOLD = 0.5

    def test_log_event_hmac_integrity(self):
        """Log events must be HMAC signed and verifiable (BUG-007/008)."""
        from axiom_os_shield import ConstitutionalOSShield

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            shield = ConstitutionalOSShield(
                hmac_key=HMAC_KEY, log_path=log_path
            )
            shield.log_event({
                "event_type": "test",
                "process_name": "test_proc",
                "level": 1,
                "distance": 0.05,
            })

            with open(log_path, "r", encoding="utf-8") as f:
                record = json.loads(f.readline())

            assert "signature" in record
            assert len(record["signature"]) == 64

            # Verify independently
            sig = record.pop("signature")
            canonical = json.dumps(
                record, sort_keys=True, ensure_ascii=True
            ).encode("utf-8")  # BUG-008
            expected = hmac.new(
                HMAC_KEY, canonical, hashlib.sha256
            ).hexdigest()  # BUG-007
            assert sig == expected, "Log event HMAC mismatch"
        finally:
            os.unlink(log_path)
