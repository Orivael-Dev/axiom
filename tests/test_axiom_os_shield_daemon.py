# -*- coding: utf-8 -*-
"""
AXIOM OS Shield daemon (ORVL-013) — unit + integration tests
=============================================================
3 BLOCKED + 4 PASSED + 2 INVARIANTS

Closes the L2/L3/L4-action gap I called out at the end of f37ce15. All
real-syscall tests use dry_run=True or psutil mocks so the test suite
never actually suspends or terminates a process.

BUG-003: UTF-8 output encoding
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not os.environ.get("AXIOM_MASTER_KEY"):
    os.environ["AXIOM_MASTER_KEY"] = "test_key_for_os_shield_daemon"

from axiom_signing import derive_key
from axiom_os_shield import ConstitutionalOSShield, ProcessSnapshot
from axiom_os_shield_daemon import MonitorDaemon


@pytest.fixture()
def shield(tmp_path):
    return ConstitutionalOSShield(
        hmac_key=derive_key(b"shield-test-key"),
        log_path=str(tmp_path / "shield.jsonl"),
        dry_run=True,
    )


def _snap(pid=99999, name="testproc", **overrides):
    base = dict(pid=pid, name=name, file_access_rate=0.0, child_procs=0,
                network_conns=0, memory_mb=10.0, cpu_percent=1.0)
    base.update(overrides)
    return ProcessSnapshot(**base)


# ===========================================================================
# SECTION 1 — BLOCKED (the safety rails that prevent self-harm)
# ===========================================================================

class TestShieldBlocked:

    def test_blocked_safe_pid_self_immune(self):
        """The shield must never suspend its own process, even out of dry_run."""
        s = ConstitutionalOSShield(
            hmac_key=derive_key(b"k"), dry_run=False)
        snap = _snap(pid=os.getpid(), name="python")
        result = s._apply_action(3, snap)
        assert result["applied"] is False
        assert result["mode"] == "safe_pid_skipped"

    def test_blocked_pid_1_immune(self):
        """init (PID 1) must always be safe — suspending init would lock the host."""
        s = ConstitutionalOSShield(
            hmac_key=derive_key(b"k"), dry_run=False)
        snap = _snap(pid=1, name="init")
        result = s._apply_action(4, snap)
        assert result["applied"] is False
        assert result["mode"] == "safe_pid_skipped"

    def test_blocked_dry_run_default_no_real_syscalls(self, shield):
        """Default shield never makes real syscalls. Verified by mocking
        psutil and asserting it is never reached."""
        snap = _snap(pid=99999)
        with patch("psutil.Process") as MockProc:
            result = shield._apply_action(3, snap)
            assert MockProc.called is False
        assert result["applied"] is False
        assert result["mode"] == "dry_run"


# ===========================================================================
# SECTION 2 — PASSED (the daemon actually runs, learns, escalates)
# ===========================================================================

class TestShieldPassed:

    def test_passed_tick_during_learning_no_escalations(self, shield):
        """Learning window must absorb ticks without escalating."""
        daemon = MonitorDaemon(shield, poll_interval_ms=10,
                                learning_seconds=60, max_processes=5)
        daemon._started_at = time.time()
        events = daemon.tick()
        assert events == []
        # Manifolds should have been populated during learning.
        assert daemon.status()["manifolds_tracked"] >= 1

    def test_passed_real_anomaly_escalates_with_action_status(self, shield):
        """Hand-fed manifold baseline + anomalous snapshot triggers L3
        with dry-run action_status reporting."""
        from axiom_os_shield import ProcessManifold
        baseline = [_snap(file_access_rate=1.0, child_procs=0, memory_mb=50,
                          cpu_percent=2.0) for _ in range(5)]
        m = ProcessManifold("testproc", "PROCESS")
        m.establish_baseline(baseline)
        shield._manifolds["testproc"] = m
        # An anomaly far from baseline.
        anomaly = _snap(file_access_rate=50.0, child_procs=10,
                         network_conns=20, memory_mb=500, cpu_percent=85.0)
        dist = m.measure_distance(anomaly)
        level = shield.determine_level(dist)
        assert level >= 1, f"expected escalation, distance={dist}"
        event = shield.escalate(level, anomaly, dist, fp_conf=0.9)
        assert event["level"] == level
        assert "action_status" in event
        if level >= 2:
            assert event["action_status"]["mode"] == "dry_run"

    def test_passed_suspend_restore_round_trip_under_mock(self, shield):
        """L3 with mocked psutil: pid is tracked in suspended set; restore
        clears it. No real OS calls."""
        shield._dry_run = False  # exercise the real-action path
        snap = _snap(pid=99999, name="mockproc")
        fake_proc = MagicMock()
        with patch("psutil.Process", return_value=fake_proc) as MockProc:
            result = shield._apply_action(3, snap)
            assert result["applied"] is True
            assert result["action"] == "suspend_process"
            fake_proc.suspend.assert_called_once()
            assert 99999 in shield.suspended
            # Restore
            restored = shield.restore(99999)
            assert restored["restored"] is True
            fake_proc.resume.assert_called_once()
            assert 99999 not in shield.suspended

    def test_passed_daemon_lifecycle(self, shield):
        """start() then stop() in under a second; status reflects state."""
        daemon = MonitorDaemon(shield, poll_interval_ms=20,
                                learning_seconds=0, max_processes=5)
        assert daemon.is_running() is False
        daemon.start()
        time.sleep(0.1)
        assert daemon.is_running() is True
        daemon.stop(timeout=2)
        assert daemon.is_running() is False
        st = daemon.status()
        assert st["ticks"] >= 1


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestShieldInvariants:

    def test_invariant_dry_run_default_true(self):
        """Every fresh shield is dry_run by default. Real syscalls require
        an explicit opt-in — this is the safety thesis of the daemon."""
        s = ConstitutionalOSShield(hmac_key=derive_key(b"k"))
        assert s.dry_run is True

    def test_invariant_escalation_event_signed_and_carries_action_status(self, shield):
        """Every escalation log entry must include an action_status sub-dict
        plus an HMAC signature."""
        snap = _snap(pid=99999, ancestry_chain=["modprobe", "bash"])
        # Force a kernel-ancestry hit so we get an L4 deterministically.
        kernel_level = shield.check_kernel_access(snap)
        assert kernel_level == 4
        event = shield.escalate(4, snap, distance=0.0, fp_conf=1.0)
        assert event["level"] == 4
        assert "action_status" in event
        assert event["action_status"]["action"] == "terminate_process"
        # Log file must contain the signed entry.
        log_path = Path(shield._log_path)
        assert log_path.exists()
        last_line = log_path.read_text().strip().splitlines()[-1]
        record = json.loads(last_line)
        assert "signature" in record
        assert len(record["signature"]) == 64
