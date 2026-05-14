"""
AXIOM OS Shield Daemon — ORVL-013 process-trajectory monitor.

Polls every process every poll_interval_ms, builds a ProcessSnapshot,
runs it through the ConstitutionalOSShield's existing decision engine,
and escalates real syscalls when out of dry-run mode.

The daemon is **dry-run by default**. To actually suspend / kill
anomalous processes the operator must:

  1. Construct the shield with ``dry_run=False`` explicitly, OR
  2. Pass ``--no-dry-run`` on the CLI.

Both paths log a one-line warning on start so a real-action daemon
can never be mistaken for a test fixture.

  Trust  : TRUST_LEVEL = 4  (CANNOT_MUTATE, inherited from shield)
  Encoding: UTF-8  BUG-003 compliant
  HMAC   : every escalation event is signed by ConstitutionalOSShield.log_event
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from axiom_os_shield import (
    ConstitutionalOSShield, ProcessSnapshot, ProcessManifold,
    LEARNING_WINDOW_HOURS,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LOG = logging.getLogger("axiom.os_shield_daemon")

_DEFAULT_POLL_MS = 500
_DEFAULT_LEARNING_SECONDS = 60   # short window for tests + dev; production overrides


class MonitorDaemon:
    """Background process-trajectory monitor for the AXIOM OS Shield.

    Public API mirrors a standard service lifecycle: ``start()``,
    ``stop()``, ``tick()``. ``tick()`` is also safe to call manually
    from tests — it runs one polling pass synchronously and returns the
    list of events it generated.
    """

    def __init__(self,
                 shield: ConstitutionalOSShield,
                 poll_interval_ms: int = _DEFAULT_POLL_MS,
                 learning_seconds: int = _DEFAULT_LEARNING_SECONDS,
                 max_processes: int = 200):
        self.shield = shield
        self.poll_interval = max(int(poll_interval_ms), 1) / 1000.0
        self.learning_seconds = max(int(learning_seconds), 0)
        self.max_processes = max(int(max_processes), 1)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._tick_count = 0
        self._event_count = 0
        self._learning_complete = False

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # idempotent
        self._stop_event.clear()
        self._started_at = time.time()
        if not self.shield.dry_run:
            LOG.warning(
                "axiom os shield daemon starting in REAL-ACTION mode — "
                "anomalous processes WILL be suspended/terminated"
            )
        self._thread = threading.Thread(
            target=self._run, name="axiom-os-shield-daemon", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:  # never let the daemon die silently
                LOG.warning("os shield daemon tick failed: %s", exc)
            # Use wait() so stop() interrupts an in-flight sleep.
            self._stop_event.wait(self.poll_interval)

    # ── Polling ───────────────────────────────────────────────────────
    def tick(self) -> list:
        """Run one polling pass. Returns the list of escalation events
        generated this tick (empty during the learning window)."""
        try:
            import psutil
        except ImportError:
            LOG.warning("psutil not available — os shield daemon is a no-op")
            return []

        in_learning = self._is_learning()
        events: list = []
        seen = 0
        for proc in psutil.process_iter(
            ["pid", "name", "num_threads", "memory_info",
             "cpu_percent", "ppid"]
        ):
            if seen >= self.max_processes:
                break
            seen += 1
            snap = _snapshot_from_psutil(proc)
            if snap is None:
                continue
            # Kernel-name + suspicious-ancestry check always runs, even in
            # the learning window — those are absolute boundaries.
            kernel_level = self.shield.check_kernel_access(snap)
            if kernel_level == 4:
                fp = self.shield.compute_fp_confidence(
                    snap, self._get_manifold(snap.name)
                )
                events.append(self.shield.escalate(4, snap, distance=0.0, fp_conf=fp))
                self._event_count += 1
                continue
            manifold = self._get_manifold(snap.name)
            if in_learning or not manifold.baseline:
                manifold.update_baseline(snap)
                continue
            distance = manifold.measure_distance(snap)
            level = self.shield.determine_level(distance)
            if level == 0:
                continue
            fp = self.shield.compute_fp_confidence(snap, manifold)
            events.append(self.shield.escalate(level, snap, distance, fp))
            self._event_count += 1
        self._tick_count += 1
        if not in_learning and not self._learning_complete:
            self._learning_complete = True
        return events

    # ── Helpers ───────────────────────────────────────────────────────
    def _is_learning(self) -> bool:
        if self._started_at is None:
            return True
        return (time.time() - self._started_at) < self.learning_seconds

    def _get_manifold(self, name: str) -> ProcessManifold:
        m = self.shield._manifolds.get(name)
        if m is None:
            m = ProcessManifold(name, block_type="PROCESS")
            self.shield._manifolds[name] = m
        return m

    def status(self) -> dict:
        return {
            "running":             self.is_running(),
            "started_at":          (datetime.fromtimestamp(self._started_at, tz=timezone.utc).isoformat()
                                     if self._started_at else None),
            "ticks":               self._tick_count,
            "escalations":         self._event_count,
            "poll_interval_ms":    int(self.poll_interval * 1000),
            "learning_seconds":    self.learning_seconds,
            "learning_complete":   self._learning_complete,
            "dry_run":             self.shield.dry_run,
            "suspended_pids":      sorted(self.shield.suspended),
            "manifolds_tracked":   len(self.shield._manifolds),
        }


def _snapshot_from_psutil(proc) -> Optional[ProcessSnapshot]:
    """Build a ProcessSnapshot from a psutil iterator entry. Returns
    None if the process disappeared mid-poll or access was denied."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        info = proc.info
        if info["pid"] is None or info["name"] is None:
            return None
        # Build ancestry: parents up the tree, capped at 8 deep.
        ancestry = []
        try:
            cur = proc
            for _ in range(8):
                parent = cur.parent()
                if parent is None or parent.pid == cur.pid:
                    break
                ancestry.append(parent.name())
                cur = parent
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        mem_mb = 0.0
        if info.get("memory_info") is not None:
            mem_mb = info["memory_info"].rss / (1024 * 1024)
        return ProcessSnapshot(
            pid=info["pid"],
            name=info["name"],
            # psutil doesn't expose files-per-second cheaply; we use
            # num_threads as a related "activity" channel. The manifold's
            # baseline learns the per-process normal range.
            file_access_rate=float(info.get("num_threads") or 0),
            child_procs=len(proc.children(recursive=False)) if hasattr(proc, "children") else 0,
            network_conns=0,  # net_connections() can be slow / privileged
            memory_mb=mem_mb,
            cpu_percent=float(info.get("cpu_percent") or 0.0),
            ancestry_chain=ancestry,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        return None


# ── CLI entrypoint ────────────────────────────────────────────────────
def _main(argv=None) -> int:
    import argparse
    from axiom_signing import derive_key

    parser = argparse.ArgumentParser(
        prog="axiom_os_shield_daemon",
        description="AXIOM OS Shield — ORVL-013 process-trajectory monitor",
    )
    parser.add_argument("--poll-ms", type=int, default=_DEFAULT_POLL_MS,
                        help="poll interval in milliseconds (default 500)")
    parser.add_argument("--learning-seconds", type=int, default=_DEFAULT_LEARNING_SECONDS,
                        help="learning window before escalations fire (default 60)")
    parser.add_argument("--no-dry-run", action="store_true",
                        help="enable REAL syscalls (suspend/terminate). "
                             "Without this flag the daemon only logs intended actions.")
    parser.add_argument("--log-path", default="axiom_os_shield_log.jsonl",
                        help="JSONL audit-log path (HMAC-signed entries)")
    parser.add_argument("--once", action="store_true",
                        help="run a single tick and exit (useful for smoke tests)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    key = derive_key(b"axiom-os-shield-daemon-v1")
    shield = ConstitutionalOSShield(
        hmac_key=key,
        log_path=args.log_path,
        dry_run=(not args.no_dry_run),
    )
    daemon = MonitorDaemon(
        shield=shield,
        poll_interval_ms=args.poll_ms,
        learning_seconds=args.learning_seconds,
    )
    if args.once:
        events = daemon.tick()
        print(f"tick complete — {len(events)} escalation(s)")
        for e in events:
            print(f"  L{e['level']:<1} {e['process_name']:<24}  {e['action_status']}")
        return 0

    print(f"axiom os shield daemon — dry_run={shield.dry_run}, "
          f"poll={args.poll_ms}ms, learn={args.learning_seconds}s")
    print("Ctrl-C to stop.")
    daemon.start()
    try:
        while daemon.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping daemon…")
    finally:
        daemon.stop()
    print(f"final status: {daemon.status()}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
