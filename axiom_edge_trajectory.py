"""
AXIOM Low-Power Edge Trajectory Agent — prototype from the architecture blueprint.
Manifest  : research/edge_trajectory
Trust     : TRUST_LEVEL = 1
Encoding  : UTF-8

Ingests compact biometric "Trajectory Tokens" from wearables, maintains a
sliding history window that SHRINKS when the host hardware throttles, runs a
cheap static matrix projection to predict an outcome class, and watches for
physiological drift via spatial centroids. No cloud backend, no backprop.

Token schema (from the blueprint, kept verbatim):
  t : epoch timestamp
  v : state vector  [HeartRate, HRV_ms, Active_METs, Motion_State_ID]
  p : power topology [Current_Watt_Draw, Power_Agent_Throttle_Flag]

Two deliberate deviations from the PDF, for the governance / signing story:
  1. The projection matrix is generated DETERMINISTICALLY from a fixed seed
     (random.Random) rather than np.random.randn, so predictions are
     reproducible and the matrix can be pinned and hashed.
  2. All linear algebra (flatten / pad / dot / argmax / mean / norm) is pure
     Python — no numpy. Faithful to the "bare-bones, static-allocation, edge"
     thesis and consistent with this repo's no-heavy-deps audio modules.

BUG mitigations:
  BUG-003 : sys.stdout reconfigured to utf-8
  BUG-007 : HMAC always finalised with .hexdigest()
  BUG-008 : payload strings encoded via ensure_ascii canonical JSON before HMAC
"""
from __future__ import annotations

import enum
import hashlib
import hmac as hmac_lib
import json
import math
import random
import sys
import types as _types
from dataclasses import dataclass, field, asdict
from typing import List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from axiom_signing import derive_key

SIGNING_KEY = derive_key(b"axiom-edge-trajectory-v1")

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL: int = 1
WINDOW_FULL: int = 5          # sliding window length at full power
WINDOW_THROTTLED: int = 3     # shrunken window when hardware throttles
STATE_DIM: int = 4            # len(v) = [HR, HRV_ms, METs, Motion_State_ID]
N_CLASSES: int = 3            # predictive target classes
DRIFT_THRESHOLD: float = 0.10  # aligned with axiom_latent_v2.DRIFT_THRESHOLD
_MATRIX_SEED: int = 20240611   # fixed seed → reproducible, pinnable projection

_FLAT_DIM: int = WINDOW_FULL * STATE_DIM   # 20 — projection input width

_FROZEN: frozenset = frozenset({
    "TRUST_LEVEL", "WINDOW_FULL", "WINDOW_THROTTLED", "STATE_DIM",
    "N_CLASSES", "DRIFT_THRESHOLD", "_MATRIX_SEED", "_FLAT_DIM",
})


def _module_setattr(self: object, name: str, value: object) -> None:
    if name in _FROZEN:
        raise AttributeError(f"{name} is CANNOT_MUTATE and may not be reassigned.")
    object.__setattr__(self, name, value)


_mod = sys.modules[__name__]
_mod.__class__ = type(
    "_FrozenModule", (_types.ModuleType,), {"__setattr__": _module_setattr},
)


# ── Signing helper (mirrors axiom_retrospect._sign) ───────────────────────

def _sign(data: dict) -> str:
    payload = {k: v for k, v in data.items() if k != "signature"}
    canon = json.dumps(payload, sort_keys=True,
                       ensure_ascii=True).encode("utf-8")        # BUG-008
    return hmac_lib.new(SIGNING_KEY, canon,
                        hashlib.sha256).hexdigest()              # BUG-007


# ── Data types ────────────────────────────────────────────────────────────

class DriftStatus(str, enum.Enum):
    STABLE = "STABLE_PATHWAY"
    DRIFT = "TRAJECTORY_DRIFT_DETECTED"
    NO_BASELINE = "NO_BASELINE"


@dataclass
class TrajectoryToken:
    """Compact biometric token. v / p are fixed-dimension arrays, not JSON blobs."""
    t: int                # epoch timestamp
    v: List[float]        # [HeartRate, HRV_ms, Active_METs, Motion_State_ID]
    p: List[float]        # [Current_Watt_Draw, Power_Agent_Throttle_Flag]
    signature: str = ""

    def sign(self) -> "TrajectoryToken":
        self.signature = _sign(asdict(self))
        return self

    def verify(self) -> bool:
        return hmac_lib.compare_digest(self.signature, _sign(asdict(self)))

    @property
    def throttle_flag(self) -> bool:
        return bool(self.p[1]) if len(self.p) > 1 else False


@dataclass
class TrajectoryPrediction:
    predicted_class: int
    window_used: int
    low_power_mode: bool
    drift_status: str
    confidence: float
    signature: str = ""

    def sign(self) -> "TrajectoryPrediction":
        self.signature = _sign(asdict(self))
        return self

    def verify(self) -> bool:
        return hmac_lib.compare_digest(self.signature, _sign(asdict(self)))


# ── Pure-Python linear algebra (no numpy) ─────────────────────────────────

def _build_projection_matrix() -> List[List[float]]:
    """Deterministic _FLAT_DIM × N_CLASSES gaussian matrix from a fixed seed."""
    rng = random.Random(_MATRIX_SEED)
    return [[rng.gauss(0.0, 1.0) for _ in range(N_CLASSES)] for _ in range(_FLAT_DIM)]


def _flatten(history: List[List[float]]) -> List[float]:
    return [x for row in history for x in row]


def _pad_to(vec: List[float], width: int) -> List[float]:
    if len(vec) >= width:
        return vec[:width]
    return vec + [0.0] * (width - len(vec))


def _dot_matrix(vec: List[float], matrix: List[List[float]]) -> List[float]:
    return [sum(vec[i] * matrix[i][j] for i in range(len(vec)))
            for j in range(N_CLASSES)]


def _argmax(scores: List[float]) -> int:
    best, best_i = scores[0], 0
    for i, s in enumerate(scores):
        if s > best:
            best, best_i = s, i
    return best_i


def _softmax_max(scores: List[float]) -> float:
    """Stable softmax, returns the largest probability as a [0,1] confidence."""
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps)
    return max(exps) / total if total else 0.0


def _centroid(history: List[List[float]]) -> List[float]:
    n = len(history)
    return [sum(row[d] for row in history) / n for d in range(STATE_DIM)]


def _euclidean(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ── Agent ─────────────────────────────────────────────────────────────────

class BareBonesTrajectoryAgent:
    """
    Power-aware sliding-window trajectory predictor.

    The projection matrix is built once at construction (static allocation);
    ingest/predict never allocate growing structures beyond the bounded window.
    """

    def __init__(self, baseline: Optional[dict] = None):
        self.state_history: List[List[float]] = []
        self.transition_matrix = _build_projection_matrix()
        # Optional drift baseline: {"mean": [STATE_DIM floats], "threshold": float}
        self.baseline = baseline

    # -- ingestion --------------------------------------------------------

    def ingest_token(self, token: TrajectoryToken) -> Optional[TrajectoryPrediction]:
        """Append a token's state vector; predict once the active window is full."""
        is_throttled = token.throttle_flag
        window = WINDOW_THROTTLED if is_throttled else WINDOW_FULL

        self.state_history.append(list(token.v))
        # Trim to the active window from the left (oldest first).
        while len(self.state_history) > window:
            self.state_history.pop(0)

        if len(self.state_history) == window:
            return self.predict_outcome(is_throttled)
        return None

    def ingest_with_power_state(
        self, token: TrajectoryToken, power_state,
    ) -> Optional[TrajectoryPrediction]:
        """Bridge to axiom_agent_fabric.power_conditioner: derive the throttle
        flag from a real PowerState (thermal throttling or backup power) instead
        of trusting the token's self-reported flag."""
        hw_throttled = (power_state.thermal_ok is False) or bool(power_state.is_backup)
        patched = TrajectoryToken(
            t=token.t,
            v=list(token.v),
            p=[token.p[0] if token.p else 0.0, 1.0 if hw_throttled else 0.0],
        ).sign()
        return self.ingest_token(patched)

    # -- prediction -------------------------------------------------------

    def predict_outcome(self, low_power_mode: bool) -> TrajectoryPrediction:
        flat = _flatten(self.state_history)
        if low_power_mode:
            flat = _pad_to(flat, _FLAT_DIM)   # zero-pad truncated window to 20
        scores = _dot_matrix(flat, self.transition_matrix)
        predicted = _argmax(scores)
        confidence = round(_softmax_max(scores), 4)
        return TrajectoryPrediction(
            predicted_class=predicted,
            window_used=len(self.state_history),
            low_power_mode=low_power_mode,
            drift_status=self.evaluate_trajectory_drift(self.baseline),
            confidence=confidence,
        ).sign()

    # -- drift calibration ------------------------------------------------

    def evaluate_trajectory_drift(self, baseline: Optional[dict]) -> str:
        """Centroid of the active window vs a cached baseline distribution.
        Mirrors the blueprint's evaluate_trajectory_drift; threshold defaults
        to DRIFT_THRESHOLD when the baseline omits one."""
        if not baseline or not self.state_history:
            return DriftStatus.NO_BASELINE.value
        current = _centroid(self.state_history)
        threshold = float(baseline.get("threshold", DRIFT_THRESHOLD))
        distance = _euclidean(current, baseline["mean"])
        if distance > threshold:
            return DriftStatus.DRIFT.value
        return DriftStatus.STABLE.value
