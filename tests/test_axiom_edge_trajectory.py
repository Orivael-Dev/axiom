"""Smoke tests for the Low-Power Edge Trajectory Agent prototype."""
import time
from dataclasses import dataclass

import pytest

import axiom_edge_trajectory as et
from axiom_edge_trajectory import (
    BareBonesTrajectoryAgent,
    DriftStatus,
    TrajectoryToken,
    TrajectoryPrediction,
    WINDOW_FULL,
    WINDOW_THROTTLED,
    STATE_DIM,
)


def _tok(hr, throttle=0, t=None):
    return TrajectoryToken(
        t=t or int(time.time()),
        v=[float(hr), 54.0, 1.2, 1.0],
        p=[12.0, float(throttle)],
    ).sign()


# ── CANNOT_MUTATE ──────────────────────────────────────────────────────────

def test_cannot_mutate_window_full():
    with pytest.raises(AttributeError):
        et.WINDOW_FULL = 9


def test_cannot_mutate_drift_threshold():
    with pytest.raises(AttributeError):
        et.DRIFT_THRESHOLD = 0.5


# ── window filling ─────────────────────────────────────────────────────────

def test_returns_none_until_window_full():
    ag = BareBonesTrajectoryAgent()
    outs = [ag.ingest_token(_tok(hr)) for hr in (68, 70, 72, 74)]
    assert outs[:WINDOW_FULL - 1] == [None] * (WINDOW_FULL - 1)
    final = ag.ingest_token(_tok(76))
    assert isinstance(final, TrajectoryPrediction)
    assert final.window_used == WINDOW_FULL
    assert final.low_power_mode is False
    assert 0 <= final.predicted_class < et.N_CLASSES
    assert 0.0 <= final.confidence <= 1.0


# ── power-adaptive window ──────────────────────────────────────────────────

def test_throttle_shrinks_window_to_three():
    ag = BareBonesTrajectoryAgent()
    out = None
    for hr in (60, 62, 64):
        out = ag.ingest_token(_tok(hr, throttle=1))
    assert isinstance(out, TrajectoryPrediction)
    assert out.window_used == WINDOW_THROTTLED
    assert out.low_power_mode is True


def test_throttled_prediction_zero_pads():
    # 3 throttled steps = 12 dims, must pad to WINDOW_FULL*STATE_DIM = 20.
    ag = BareBonesTrajectoryAgent()
    for hr in (60, 62, 64):
        out = ag.ingest_token(_tok(hr, throttle=1))
    # If padding were wrong the dot product would raise; reaching here = ok.
    assert out.predicted_class in range(et.N_CLASSES)


# ── drift calibration ──────────────────────────────────────────────────────

def test_drift_stable_vs_detected():
    baseline = {"mean": [70.0, 54.0, 1.2, 1.0], "threshold": 5.0}
    ag = BareBonesTrajectoryAgent(baseline=baseline)
    out = None
    for hr in (69, 70, 71, 70, 69):
        out = ag.ingest_token(_tok(hr))
    assert out.drift_status == DriftStatus.STABLE.value

    ag2 = BareBonesTrajectoryAgent(baseline=baseline)
    out2 = None
    for hr in (150, 155, 160, 158, 162):   # far from baseline mean
        out2 = ag2.ingest_token(_tok(hr))
    assert out2.drift_status == DriftStatus.DRIFT.value


def test_drift_no_baseline():
    ag = BareBonesTrajectoryAgent()
    out = None
    for hr in (68, 70, 72, 74, 76):
        out = ag.ingest_token(_tok(hr))
    assert out.drift_status == DriftStatus.NO_BASELINE.value


# ── signatures ─────────────────────────────────────────────────────────────

def test_token_and_prediction_signatures_verify():
    ag = BareBonesTrajectoryAgent()
    tok = _tok(72)
    assert tok.verify()
    out = None
    for hr in (68, 70, 72, 74, 76):
        out = ag.ingest_token(_tok(hr))
    assert out.verify()
    # Tamper detection
    out.predicted_class = (out.predicted_class + 1) % et.N_CLASSES
    assert not out.verify()


# ── power_conditioner bridge ───────────────────────────────────────────────

def test_from_power_state_derives_throttle():
    @dataclass
    class FakePowerState:
        thermal_ok: bool
        is_backup: bool

    ag = BareBonesTrajectoryAgent()
    hot = FakePowerState(thermal_ok=False, is_backup=False)   # thermal throttle
    out = None
    for hr in (60, 62, 64):
        out = ag.ingest_with_power_state(_tok(hr, throttle=0), hot)
    assert out.window_used == WINDOW_THROTTLED
    assert out.low_power_mode is True


def test_deterministic_matrix_reproducible():
    a = BareBonesTrajectoryAgent().transition_matrix
    b = BareBonesTrajectoryAgent().transition_matrix
    assert a == b   # fixed seed → identical projection across instances
