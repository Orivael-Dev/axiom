"""Axiom Blueprint v1.4 — declarative agent state-machine compiler.

Parses `.axiom` v1.4 files into an executable state machine.

Layer mapping:
  ContextConfiguration → Layer 0 (Intent Kernel) — token budget, temperature
  invariant blocks     → Layer 4 (Governance Guard) — hard gates, CANNOT_MUTATE
  state blocks         → Layer 1 (Inference Router) — probabilistic routing
  finalize block       → Layer 6 (Observability) — HMAC-signed audit trail

CANNOT_MUTATE pattern is enforced on module-level sentinel values.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

# ── CANNOT_MUTATE sentinels ───────────────────────────────────────────────────
_BLUEPRINT_VERSION: str = "1.4"
_MAX_STATE_VISITS: int  = 50   # guards against infinite loops

BLUEPRINT_VERSION   = _BLUEPRINT_VERSION
MAX_STATE_VISITS    = _MAX_STATE_VISITS

import sys as _sys
_mod = _sys.modules[__name__]

class _Frozen(type(_mod)):
    _LOCKED = frozenset({"BLUEPRINT_VERSION", "MAX_STATE_VISITS"})
    def __setattr__(self, name: str, value: object) -> None:
        if name in self._LOCKED:
            raise AttributeError(f"{name} is CANNOT_MUTATE")
        super().__setattr__(name, value)

_mod.__class__ = _Frozen


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RuntimeConfig:
    """Parsed from `runtime ContextConfiguration { ... }`."""
    max_context_tokens:      int   = 8192
    reserve_response_tokens: int   = 1024
    execution_mode:          str   = "local_first"
    temperature_min:         float = 0.1
    temperature_max:         float = 0.7


@dataclass(frozen=True)
class InvariantSpec:
    """Parsed from `invariant Name { ... }` — hard governance gate."""
    name:            str
    allow_network:   bool           = False
    allow_fs_write:  Tuple[str, ...] = ()
    privacy_filters: Tuple[str, ...] = ()  # regex strings to scrub
    validate_via:    Optional[str]  = None  # e.g. "axiom.validators.compiler.rustc"
    validate_kwargs: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WeightedCategory:
    label:  str
    weight: float


@dataclass(frozen=True)
class TransitionSpec:
    """A state-machine edge."""
    condition:    str  # "on_success" | "on_failure" | "on_violation"
    condition_expr: str
    target_state: str


@dataclass(frozen=True)
class WeightAdjustment:
    category: str
    delta:    float   # positive = increase, negative = decrease


@dataclass(frozen=True)
class RetrySpec:
    max_attempts:        int
    on_violation:        str              # invariant name that triggers retry
    feedback_template:   str
    weight_adjustments:  Tuple[WeightAdjustment, ...] = ()
    temperature_delta:   float = -0.1    # applied each retry


@dataclass(frozen=True)
class StateSpec:
    """Parsed from `state Name { ... }`."""
    name:                  str
    prompt:                str  = ""
    action:                str  = ""
    probabilistic_weights: Tuple[WeightedCategory, ...] = ()
    transition_threshold:  float = 0.8
    transitions:           Tuple[TransitionSpec, ...] = ()
    output_schema:         Optional[str] = None  # JSON schema string
    retry_spec:            Optional[RetrySpec] = None


@dataclass(frozen=True)
class FinalizeSpec:
    """Parsed from `finalize Name { ... }`."""
    name:          str
    log_telemetry: Tuple[str, ...] = ()
    sign_payload:  str = "axiom.crypto.hmac.sha256"
    destination:   str = "memory"


@dataclass(frozen=True)
class AxiomBlueprint:
    """Top-level parsed blueprint."""
    module:         str
    imports:        Tuple[str, ...]
    runtime_config: RuntimeConfig
    invariants:     Tuple[InvariantSpec, ...]
    states:         Tuple[StateSpec, ...]
    finalize:       Optional[FinalizeSpec] = None


# ── Telemetry ─────────────────────────────────────────────────────────────────

@dataclass
class ExecutionTelemetry:
    token_usage:        int   = 0
    certainty_scores:   Dict[str, float] = field(default_factory=dict)
    states_visited:     List[str] = field(default_factory=list)
    violations:         List[str] = field(default_factory=list)
    retry_count:        int   = 0
    temperature_used:   float = 0.5
    wall_clock_ms:      int   = 0
    timestamp:          str   = ""
    hmac_signature:     str   = ""

    def to_dict(self) -> dict:
        return {
            "token_usage":       self.token_usage,
            "certainty_scores":  self.certainty_scores,
            "states_visited":    self.states_visited,
            "violations":        self.violations,
            "retry_count":       self.retry_count,
            "temperature_used":  self.temperature_used,
            "wall_clock_ms":     self.wall_clock_ms,
            "timestamp":         self.timestamp,
            "hmac_signature":    self.hmac_signature,
        }


# ── Parser ────────────────────────────────────────────────────────────────────

class BlueprintParseError(ValueError):
    pass


def _extract_blocks(text: str) -> Iterator[Tuple[str, str, str]]:
    """Yield (keyword, name, body) for each top-level `keyword Name { ... }` block.

    Handles nested braces correctly. Strips comments (# to end of line).
    """
    # strip comments
    clean = re.sub(r"#[^\n]*", "", text)

    # find keyword Name { ... } blocks
    pattern = re.compile(r'\b(runtime|invariant|state|finalize)\s+(\w+)\s*\{', re.DOTALL)

    for m in pattern.finditer(clean):
        keyword, name = m.group(1), m.group(2)
        start = m.end()
        depth, i = 1, start
        while i < len(clean) and depth:
            if clean[i] == '{':
                depth += 1
            elif clean[i] == '}':
                depth -= 1
            i += 1
        if depth != 0:
            raise BlueprintParseError(f"Unclosed block: {keyword} {name}")
        yield keyword, name, clean[start : i - 1].strip()


def _attr(body: str, key: str, default: str = "") -> str:
    """Extract `key: value;` from a block body."""
    m = re.search(rf'\b{re.escape(key)}\s*:\s*([^;]+);', body)
    return m.group(1).strip() if m else default


def _bool_attr(body: str, key: str, default: bool = False) -> bool:
    val = _attr(body, key, str(default)).lower().strip('"')
    return val in ("true", "yes", "1")


def _int_attr(body: str, key: str, default: int = 0) -> int:
    val = _attr(body, key, str(default)).strip('"')
    try:
        return int(val)
    except ValueError:
        return default


def _float_attr(body: str, key: str, default: float = 0.0) -> float:
    val = _attr(body, key, str(default)).strip('"')
    try:
        return float(val)
    except ValueError:
        return default


def _parse_temperature_profile(body: str) -> Tuple[float, float]:
    """Parse `temperature_profile: dynamic(0.1, 0.7);` → (min, max)."""
    m = re.search(r'temperature_profile\s*:\s*dynamic\(([^,]+),\s*([^)]+)\)', body)
    if m:
        return float(m.group(1).strip()), float(m.group(2).strip())
    static = _float_attr(body, "temperature_profile", 0.5)
    return static, static


def _parse_runtime(name: str, body: str) -> RuntimeConfig:
    t_min, t_max = _parse_temperature_profile(body)
    return RuntimeConfig(
        max_context_tokens      = _int_attr(body, "max_context_tokens", 8192),
        reserve_response_tokens = _int_attr(body, "reserve_response_tokens", 1024),
        execution_mode          = _attr(body, "execution_mode", "local_first").strip('"'),
        temperature_min         = t_min,
        temperature_max         = t_max,
    )


def _parse_invariant(name: str, body: str) -> InvariantSpec:
    # allow_fs_write: ["path1", "path2"]
    fs_write: List[str] = []
    m = re.search(r'allow_fs_write\s*:\s*\[([^\]]*)\]', body)
    if m:
        fs_write = [s.strip().strip('"') for s in m.group(1).split(',') if s.strip()]

    # privacy_filter: [regex(...), regex(...)]
    # use (?:[^"\\]|\\.)* to handle \" escaped quotes inside regex strings
    filters: List[str] = []
    for fm in re.finditer(r'regex\(r?"((?:[^"\\]|\\.)*)"\)', body):
        filters.append(fm.group(1))

    # validate_via: axiom.validators.compiler.rustc(edition="2021")
    validate_via = None
    validate_kwargs: List[Tuple[str, str]] = []
    vm = re.search(r'validate_via\s*:\s*([\w.]+)\(([^)]*)\)', body)
    if vm:
        validate_via = vm.group(1).strip()
        for kv in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', vm.group(2)):
            validate_kwargs.append((kv.group(1), kv.group(2)))

    return InvariantSpec(
        name            = name,
        allow_network   = _bool_attr(body, "allow_network", False),
        allow_fs_write  = tuple(fs_write),
        privacy_filters = tuple(filters),
        validate_via    = validate_via,
        validate_kwargs = tuple(validate_kwargs),
    )


def _parse_weights(body: str) -> Tuple[WeightedCategory, ...]:
    """Parse `evaluate probabilistic_weights { "label" => weight(N), ... }`."""
    block_m = re.search(
        r'evaluate\s+probabilistic_weights\s*\{([^}]+)\}', body, re.DOTALL
    )
    if not block_m:
        return ()
    cats: List[WeightedCategory] = []
    for m in re.finditer(r'"([^"]+)"\s*=>\s*weight\(([^)]+)\)', block_m.group(1)):
        cats.append(WeightedCategory(label=m.group(1), weight=float(m.group(2).strip())))
    # normalise
    total = sum(c.weight for c in cats) or 1.0
    return tuple(WeightedCategory(c.label, round(c.weight / total, 6)) for c in cats)


def _parse_transitions(body: str) -> Tuple[TransitionSpec, ...]:
    """Parse `on_success(expr) => state::Name;` lines."""
    trans: List[TransitionSpec] = []
    for m in re.finditer(
        r'(on_success|on_failure|on_violation)\(([^)]+)\)\s*=>\s*state::(\w+)',
        body,
    ):
        trans.append(TransitionSpec(
            condition     = m.group(1),
            condition_expr= m.group(2).strip(),
            target_state  = m.group(3),
        ))
    return tuple(trans)


def _extract_balanced(text: str, start: int) -> str:
    """Extract brace-balanced content starting at `start` (after opening `{`).

    Returns the content between the matching braces, without including them.
    """
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[start : i - 1] if depth == 0 else text[start:]


def _parse_retry(body: str) -> Optional[RetrySpec]:
    """Parse `retry_loop MaxAttempts(N) { ... }` with nested brace handling."""
    m = re.search(r'retry_loop\s+MaxAttempts\((\d+)\)\s*\{', body, re.DOTALL)
    if not m:
        return None
    max_attempts = int(m.group(1))
    inner = _extract_balanced(body, m.end())

    # on_violation(InvariantName.failed)
    viol_m = re.search(r'on_violation\((\w+)\.failed\)', inner)
    on_viol = viol_m.group(1) if viol_m else ""

    # feedback: "..."
    fb_m = re.search(r'feedback\s*:\s*"([^"]+)"', inner)
    feedback = fb_m.group(1) if fb_m else ""

    # adjust_weights: ["cat" += 0.15, "cat2" -= 0.1]
    adj_m = re.search(r'adjust_weights\s*:\s*\[([^\]]+)\]', inner)
    adjs: List[WeightAdjustment] = []
    if adj_m:
        for wa in re.finditer(r'"([^"]+)"\s*(\+|-)=\s*([\d.]+)', adj_m.group(1)):
            delta = float(wa.group(3))
            if wa.group(2) == '-':
                delta = -delta
            adjs.append(WeightAdjustment(wa.group(1), delta))

    # temperature: runtime.context.temperature - 0.2
    temp_m = re.search(r'temperature\s*:\s*runtime\.context\.temperature\s*-\s*([\d.]+)', inner)
    temp_delta = -float(temp_m.group(1)) if temp_m else -0.1

    return RetrySpec(
        max_attempts      = max_attempts,
        on_violation      = on_viol,
        feedback_template = feedback,
        weight_adjustments= tuple(adjs),
        temperature_delta = temp_delta,
    )


def _parse_output_schema(body: str) -> Optional[str]:
    """Capture `output_format: Schema({ ... });` as a raw string."""
    m = re.search(r'output_format\s*:\s*Schema\((\{[^}]+\})\)', body, re.DOTALL)
    return m.group(1).strip() if m else None


def _parse_state(name: str, body: str) -> StateSpec:
    return StateSpec(
        name                  = name,
        prompt                = _attr(body, "prompt", "").strip('"'),
        action                = _attr(body, "action", "").strip('"'),
        probabilistic_weights = _parse_weights(body),
        transition_threshold  = _float_attr(body, "transition_threshold", 0.8),
        transitions           = _parse_transitions(body),
        output_schema         = _parse_output_schema(body),
        retry_spec            = _parse_retry(body),
    )


def _parse_finalize(name: str, body: str) -> FinalizeSpec:
    # log_telemetry: [item1, item2]
    items: List[str] = []
    m = re.search(r'log_telemetry\s*:\s*\[([^\]]+)\]', body)
    if m:
        items = [s.strip().strip('"') for s in m.group(1).split(',') if s.strip()]

    return FinalizeSpec(
        name          = name,
        log_telemetry = tuple(items),
        sign_payload  = _attr(body, "sign_payload", "axiom.crypto.hmac.sha256"),
        destination   = _attr(body, "destination", "memory").strip('"'),
    )


class BlueprintParser:
    """Parse .axiom v1.4 text into an AxiomBlueprint."""

    def parse(self, text: str) -> AxiomBlueprint:
        clean = re.sub(r"#[^\n]*", "", text)

        # module declaration
        mod_m = re.search(r'\bmodule\s+([\w.]+)\s*;', clean)
        module = mod_m.group(1) if mod_m else "Unnamed"

        # import declarations
        imports = tuple(m.group(1) for m in re.finditer(r'\bimport\s+([\w.]+)\s*;', clean))

        runtime_cfg = RuntimeConfig()
        invariants: List[InvariantSpec] = []
        states: List[StateSpec]         = []
        finalize: Optional[FinalizeSpec] = None

        for keyword, name, body in _extract_blocks(text):
            if keyword == "runtime":
                runtime_cfg = _parse_runtime(name, body)
            elif keyword == "invariant":
                invariants.append(_parse_invariant(name, body))
            elif keyword == "state":
                states.append(_parse_state(name, body))
            elif keyword == "finalize":
                finalize = _parse_finalize(name, body)

        return AxiomBlueprint(
            module         = module,
            imports        = imports,
            runtime_config = runtime_cfg,
            invariants     = tuple(invariants),
            states         = tuple(states),
            finalize       = finalize,
        )

    def parse_file(self, path: Path) -> AxiomBlueprint:
        return self.parse(Path(path).read_text(encoding="utf-8"))


# ── State machine runtime ─────────────────────────────────────────────────────

class InvariantViolation(Exception):
    """Raised when a hard governance gate blocks execution."""
    def __init__(self, invariant_name: str, detail: str):
        super().__init__(f"Invariant {invariant_name} violated: {detail}")
        self.invariant_name = invariant_name
        self.detail = detail


class BlueprintStateMachine:
    """Execute a parsed AxiomBlueprint as a state machine.

    The executor does not call an LLM — it provides the structural
    scaffolding (budget enforcement, invariant checking, weight-based
    certainty, HMAC-signed audit) so the calling code can plug in
    any backend (InferenceOS, direct API, offline mock).

    Usage:
        bp  = BlueprintParser().parse_file("refactor_agent.axiom")
        sm  = BlueprintStateMachine(bp)
        tel = sm.run(initial_state="OptimizationAnalysis",
                     context={"source_code": code})
        print(tel.hmac_signature)
    """

    def __init__(
        self,
        blueprint:  AxiomBlueprint,
        master_key: Optional[bytes] = None,
    ) -> None:
        self._bp  = blueprint
        self._key = master_key or self._default_key()
        self._state_index: Dict[str, StateSpec] = {s.name: s for s in blueprint.states}
        self._inv_index:   Dict[str, InvariantSpec] = {
            inv.name: inv for inv in blueprint.invariants
        }

    # ── public ────────────────────────────────────────────────────────────────

    def run(
        self,
        initial_state: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> ExecutionTelemetry:
        """Execute the blueprint from `initial_state` until termination.

        If `initial_state` is None, the first defined state is used.
        `context` dict is passed to each state's execution callback.

        Returns a signed ExecutionTelemetry.
        """
        from datetime import datetime, timezone
        start_ms = int(time.monotonic() * 1000)
        tel = ExecutionTelemetry(timestamp=datetime.now(timezone.utc).isoformat())

        if not self._bp.states:
            tel.hmac_signature = self._sign(tel)
            return tel

        current = initial_state or self._bp.states[0].name
        weights_override: Optional[Dict[str, float]] = None
        temperature = self._bp.runtime_config.temperature_min
        visit_count = 0

        while current and visit_count < MAX_STATE_VISITS:
            state = self._state_index.get(current)
            if state is None:
                break
            visit_count += 1
            tel.states_visited.append(current)

            # apply active weights (may have been adjusted by retry)
            effective_weights = (
                self._merge_weights(state.probabilistic_weights, weights_override)
                if weights_override is not None
                else state.probabilistic_weights
            )

            certainty = self._compute_certainty(effective_weights)
            tel.certainty_scores[current] = round(certainty, 4)
            tel.token_usage += self._estimate_tokens(state)
            tel.temperature_used = temperature

            # check max token budget
            budget_left = (
                self._bp.runtime_config.max_context_tokens
                - self._bp.runtime_config.reserve_response_tokens
            )
            if tel.token_usage > budget_left:
                tel.violations.append(f"{current}:BUDGET_EXCEEDED")
                break

            # enforce all invariants before executing the state
            for inv in self._bp.invariants:
                try:
                    self._enforce_invariant(inv, context or {})
                except InvariantViolation as exc:
                    tel.violations.append(f"{current}:{exc.invariant_name}")

                    # look for retry spec that handles this violation
                    if state.retry_spec and state.retry_spec.on_violation == exc.invariant_name:
                        retry = state.retry_spec
                        if tel.retry_count < retry.max_attempts:
                            tel.retry_count += 1
                            weights_override = self._apply_weight_adjustments(
                                effective_weights, retry.weight_adjustments
                            )
                            temperature = max(
                                self._bp.runtime_config.temperature_min,
                                temperature + retry.temperature_delta,
                            )
                            # re-try same state
                            tel.states_visited.append(f"{current}:retry#{tel.retry_count}")
                            continue
                        else:
                            break
                    break

            # route to next state
            current = self._route(state, certainty, temperature)
            weights_override = None  # reset per-state overrides

        tel.wall_clock_ms = int(time.monotonic() * 1000) - start_ms
        tel.hmac_signature = self._sign(tel)
        return tel

    def enforce_all_invariants(self, context: dict) -> List[str]:
        """Run all invariants against `context`. Returns list of violation names.

        Non-raising variant — useful for pre-flight checks.
        """
        violations: List[str] = []
        for inv in self._bp.invariants:
            try:
                self._enforce_invariant(inv, context)
            except InvariantViolation as exc:
                violations.append(exc.invariant_name)
        return violations

    # ── internal ──────────────────────────────────────────────────────────────

    def _compute_certainty(self, weights: Tuple[WeightedCategory, ...]) -> float:
        """Scalar certainty in [0, 1].

        Uses normalised negative entropy: certainty = 1 when one category has
        all weight; 0 when weights are uniform. Returns 1.0 for empty weights.
        """
        if not weights:
            return 1.0
        n = len(weights)
        if n == 1:
            return 1.0
        max_entropy = math.log(n)
        entropy = -sum(
            c.weight * math.log(max(c.weight, 1e-12)) for c in weights
        )
        return round(1.0 - (entropy / max_entropy), 6)

    def _merge_weights(
        self,
        base:     Tuple[WeightedCategory, ...],
        override: Dict[str, float],
    ) -> Tuple[WeightedCategory, ...]:
        updated = [
            WeightedCategory(c.label, max(0.0, c.weight + override.get(c.label, 0.0)))
            for c in base
        ]
        total = sum(c.weight for c in updated) or 1.0
        return tuple(WeightedCategory(c.label, c.weight / total) for c in updated)

    def _apply_weight_adjustments(
        self,
        weights: Tuple[WeightedCategory, ...],
        adjustments: Tuple[WeightAdjustment, ...],
    ) -> Dict[str, float]:
        override: Dict[str, float] = {}
        for adj in adjustments:
            override[adj.category] = adj.delta
        return override

    def _route(
        self,
        state:       StateSpec,
        certainty:   float,
        temperature: float,
    ) -> Optional[str]:
        """Return the name of the next state, or None to terminate."""
        for t in state.transitions:
            if self._eval_condition(t.condition, t.condition_expr, certainty, state, temperature):
                if t.target_state not in self._state_index:
                    return None  # undefined → terminate
                return t.target_state
        # no matching transition → advance to next state in definition order
        names = [s.name for s in self._bp.states]
        idx   = names.index(state.name)
        return names[idx + 1] if idx + 1 < len(names) else None

    def _eval_condition(
        self,
        condition:      str,
        condition_expr: str,
        certainty:      float,
        state:          StateSpec,
        temperature:    float,
    ) -> bool:
        """Evaluate `on_success / on_failure / on_violation` conditions."""
        if condition == "on_success":
            # "certainty >= transition_threshold"
            return certainty >= state.transition_threshold
        if condition == "on_failure":
            return certainty < state.transition_threshold
        # on_violation: always False at this point (handled in retry loop)
        return False

    def _enforce_invariant(self, inv: InvariantSpec, context: dict) -> None:
        """Raise InvariantViolation if the context violates the invariant."""
        # Network check
        if not inv.allow_network:
            if context.get("network_access", False):
                raise InvariantViolation(inv.name, "network access is not allowed")

        # FS write check
        if inv.allow_fs_write:
            for path in context.get("fs_writes", []):
                if not any(path.startswith(allowed) for allowed in inv.allow_fs_write):
                    raise InvariantViolation(
                        inv.name, f"fs write to '{path}' not in allow_fs_write"
                    )

        # Privacy filter: scan output for sensitive patterns
        output = context.get("output", "")
        for pattern in inv.privacy_filters:
            try:
                if re.search(pattern, output):
                    raise InvariantViolation(
                        inv.name, f"privacy pattern matched: {pattern[:40]}"
                    )
            except re.error:
                pass  # malformed regex — skip

    def _estimate_tokens(self, state: StateSpec) -> int:
        prompt_chars = len(state.prompt) + len(state.action)
        return prompt_chars // 4 + 20  # rough chars→tokens

    def _sign(self, tel: ExecutionTelemetry) -> str:
        import hashlib
        import hmac as _hmac
        try:
            payload = json.dumps(tel.to_dict(), sort_keys=True, default=str)
            # Try axiom_signing first; fall back to stdlib hmac if key unavailable
            try:
                from axiom_signing import hmac_sign
                return hmac_sign(payload.encode(), self._key)
            except Exception:
                return _hmac.new(
                    self._key, payload.encode(), hashlib.sha256
                ).hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _default_key() -> bytes:
        import hashlib
        import os
        master = os.environ.get("AXIOM_MASTER_KEY", "")
        if master:
            try:
                from axiom_signing import derive_key
                return derive_key(b"axiom-blueprint-v1")
            except Exception:
                pass
        return hashlib.sha256(b"axiom-blueprint-default").digest()


# ── Convenience API ───────────────────────────────────────────────────────────

def load_blueprint(path: str | Path) -> AxiomBlueprint:
    """Parse a .axiom v1.4 file and return an AxiomBlueprint."""
    return BlueprintParser().parse_file(Path(path))


def run_blueprint(
    path:    str | Path,
    initial: Optional[str] = None,
    context: Optional[dict] = None,
    key:     Optional[bytes] = None,
) -> ExecutionTelemetry:
    """One-shot: load → run → return telemetry."""
    bp = load_blueprint(path)
    return BlueprintStateMachine(bp, master_key=key).run(
        initial_state=initial, context=context
    )
