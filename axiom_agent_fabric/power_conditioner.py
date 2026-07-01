"""Power Conditioner Agent — adaptive inference configuration for battery-constrained
and backup-power scenarios.

Profiles
--------
FULL      : battery ≥70 % or charging.  α=1.0, ctx=8192, all MET chunks.
STANDARD  : battery 40–70 %.            α=0.5, ctx=4096, clarify MET.
CONSERVE  : battery 20–40 %.            α=0.0, ctx=2048, inform MET + compress.
CRITICAL  : battery <20 %.              α=0.0, ctx=512,  inform MET + compress.
EMERGENCY : backup/UPS power detected.  α=0.0, ctx=512,  inform + compress
                                         + optional fallback to 135M model.

For models >1B the α knob is the primary power lever:
  Mistral-7B α=1.0 → 13 bpw → ~11 GB  (won't fit 6 GB VRAM)
  Mistral-7B α=0.0 → 4.5 bpw →  ~4 GB  (fits in 6 GB with headroom)
Context reduction is secondary — it cuts KV cache (128 KB/token for 7B)
but doesn't touch the weights.

Power sensors (tried in order)
-------------------------------
linux_sys : /sys/class/power_supply/* — laptop, desktop-UPS, Raspberry Pi
termux    : termux-battery-status JSON (Android / Termux)
jetson    : detect via /etc/nv_tegra_release — always FULL (plugged device)
mock      : fallback — returns FULL; safe without any sensor

Input conditioner
-----------------
Rule-based (no neural pass): split into sentences, score by query-term
overlap, greedy-select within context budget in original order.  Safe on
135M-class devices with no extra inference cost.

Patent pending — Orivael Inc.
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from axiom_signing import derive_key

_POWER_KEY_NS = b"axiom-power-conditioner-v1"
_power_key: Optional[bytes] = None


def _get_key() -> bytes:
    global _power_key
    if _power_key is None:
        _power_key = derive_key(_POWER_KEY_NS)
    return _power_key


def _sign(d: dict) -> str:
    payload = {k: v for k, v in d.items() if k != "signature"}
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_get_key(), data, hashlib.sha256).hexdigest()


# ── Data types ────────────────────────────────────────────────────────────────

class PowerProfile(str, enum.Enum):
    FULL      = "FULL"
    STANDARD  = "STANDARD"
    CONSERVE  = "CONSERVE"
    CRITICAL  = "CRITICAL"
    EMERGENCY = "EMERGENCY"


@dataclass
class PowerState:
    battery_pct:  int    # 0–100; 100 when no battery (desktop, Jetson)
    is_charging:  bool   # True if AC/USB power connected
    is_backup:    bool   # True if on UPS or generator (AC lost)
    thermal_ok:   bool   # False if device is throttling due to heat
    source:       str    # sensor that produced this reading
    signature:    str = ""

    def sign(self) -> "PowerState":
        d = asdict(self)
        d["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.signature = _sign(d)
        return self


@dataclass
class InferenceConfig:
    """Runtime inference parameters selected for the current power state."""
    profile:        str    # PowerProfile value
    alpha:          float  # SRD α — 0.0=4.5bpw compact, 1.0=13bpw full-quality
    context_window: int    # max context tokens to pass to the model
    met_policy:     str    # "all" | "clarify" | "inform" — MET chunk loading
    compress_input: bool   # whether to run InputConditioner before inference
    compress_budget: int   # target token count after compression
    model_fallback: str    # model_id to switch to in EMERGENCY ("" = no switch)
    reason:         str    # human-readable explanation
    signature:      str = ""

    def sign(self) -> "InferenceConfig":
        self.signature = _sign(asdict(self))
        return self

    def verify(self) -> bool:
        return hmac.compare_digest(self.signature, _sign(asdict(self)))


# ── Profile → InferenceConfig table ──────────────────────────────────────────

# Fallback model for EMERGENCY mode — 135M, ~103 MB GGUF, minimal RAM floor.
_EMERGENCY_FALLBACK = "HuggingFaceTB/SmolLM2-135M-Instruct"

PROFILE_CONFIGS: dict[PowerProfile, InferenceConfig] = {
    PowerProfile.FULL: InferenceConfig(
        profile        = "FULL",
        alpha          = 1.0,
        context_window = 8192,
        met_policy     = "all",
        compress_input = False,
        compress_budget= 8192,
        model_fallback = "",
        reason         = "Battery adequate or charging — full quality.",
    ),
    PowerProfile.STANDARD: InferenceConfig(
        profile        = "STANDARD",
        alpha          = 0.5,
        context_window = 4096,
        met_policy     = "clarify",
        compress_input = False,
        compress_budget= 4096,
        model_fallback = "",
        reason         = "Battery 40–70 %: moderate quality save (-33 % RAM).",
    ),
    PowerProfile.CONSERVE: InferenceConfig(
        profile        = "CONSERVE",
        alpha          = 0.0,
        context_window = 2048,
        met_policy     = "inform",
        compress_input = True,
        compress_budget= 1024,
        model_fallback = "",
        reason         = "Battery 20–40 %: power-save mode (-46 % RAM vs FULL).",
    ),
    PowerProfile.CRITICAL: InferenceConfig(
        profile        = "CRITICAL",
        alpha          = 0.0,
        context_window = 512,
        met_policy     = "inform",
        compress_input = True,
        compress_budget= 384,
        model_fallback = "",
        reason         = "Battery <20 %: critical power-save — short context only.",
    ),
    PowerProfile.EMERGENCY: InferenceConfig(
        profile        = "EMERGENCY",
        alpha          = 0.0,
        context_window = 512,
        met_policy     = "inform",
        compress_input = True,
        compress_budget= 256,
        model_fallback = _EMERGENCY_FALLBACK,
        reason         = "Backup/UPS power detected — minimum-footprint emergency mode.",
    ),
}

# RAM budget reference (Qwen3-1.7B Q4_K_M, from measured sidecar)
# Used for display / logging only — not a runtime gate.
RAM_BUDGET_MB: dict[PowerProfile, dict] = {
    PowerProfile.FULL:      {"qwen3_1b7": 1409, "tinyllama_1b": 685},
    PowerProfile.STANDARD:  {"qwen3_1b7":  944, "tinyllama_1b": 380},
    PowerProfile.CONSERVE:  {"qwen3_1b7":  768, "tinyllama_1b": 227},
    PowerProfile.CRITICAL:  {"qwen3_1b7":  768, "tinyllama_1b": 227},
    PowerProfile.EMERGENCY: {"qwen3_1b7":   25, "tinyllama_1b":  25},  # 135M fallback
}


# ── Profile selector ──────────────────────────────────────────────────────────

def select_profile(state: PowerState) -> PowerProfile:
    """Determine the inference profile from a PowerState reading.

    Priority (first match wins):
      EMERGENCY  — backup/UPS power (AC lost), regardless of battery level
      CRITICAL   — battery <20 %, not charging
      CONSERVE   — battery <40 %, not charging
      STANDARD   — battery <70 %, not charging
      FULL       — battery ≥70 % or charging (default)
    """
    if state.is_backup:
        return PowerProfile.EMERGENCY
    if not state.is_charging:
        if state.battery_pct <= 20:
            return PowerProfile.CRITICAL
        if state.battery_pct <= 40:
            return PowerProfile.CONSERVE
        if state.battery_pct < 70:
            return PowerProfile.STANDARD
    return PowerProfile.FULL


# ── Power sensor ──────────────────────────────────────────────────────────────

class PowerSensor:
    """Multi-platform power state reader.

    Tries sensors in order: linux_sys → termux → jetson → mock_full.
    Each reader returns a PowerState or None on failure/unavailability.
    """

    def read(self) -> PowerState:
        for reader in (self._linux_sys, self._termux, self._jetson):
            try:
                state = reader()
                if state is not None:
                    return state.sign()
            except Exception:
                pass
        return PowerState(
            battery_pct=100, is_charging=True, is_backup=False,
            thermal_ok=True, source="mock_full",
        ).sign()

    # ── platform readers ──────────────────────────────────────────────────

    def _linux_sys(self) -> Optional[PowerState]:
        """Read /sys/class/power_supply on Linux (laptop, Pi, desktop+UPS)."""
        supply = Path("/sys/class/power_supply")
        if not supply.exists():
            return None

        bat_pct   = 100
        charging  = True
        backup    = False
        has_bat   = False
        ac_online = True   # assume AC until we see otherwise

        for dev in supply.iterdir():
            t = (dev / "type")
            if not t.exists():
                continue
            dev_type = t.read_text().strip()

            if dev_type == "Battery":
                has_bat = True
                cap = dev / "capacity"
                if cap.exists():
                    bat_pct = int(cap.read_text().strip())
                status = dev / "status"
                if status.exists():
                    charging = status.read_text().strip() in ("Charging", "Full")

            elif dev_type in ("Mains", "USB"):
                online = dev / "online"
                if online.exists():
                    ac_online = int(online.read_text().strip()) == 1

            elif dev_type == "UPS":
                # UPS present and discharging → backup power
                status = dev / "status"
                if status.exists() and status.read_text().strip() == "Discharging":
                    backup = True

        # Desktop without battery: if AC goes offline → UPS territory
        if not has_bat and not ac_online:
            backup = True

        # Thermal check: first cpu_thermal zone if present
        thermal_ok = True
        temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp_path.exists():
            try:
                temp_mc = int(temp_path.read_text().strip())  # millicelsius
                thermal_ok = temp_mc < 85_000
            except ValueError:
                pass

        return PowerState(
            battery_pct=bat_pct, is_charging=charging,
            is_backup=backup, thermal_ok=thermal_ok, source="linux_sys",
        )

    def _termux(self) -> Optional[PowerState]:
        """Read termux-battery-status on Android / Termux.

        Returns: {"percentage":85,"status":"DISCHARGING","plugged":"UNPLUGGED",
                  "health":"GOOD","temperature":28.5}
        """
        result = subprocess.run(
            ["termux-battery-status"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        pct      = int(data["percentage"])
        charging = data.get("status") in ("CHARGING", "FULL")
        temp     = float(data.get("temperature", 30.0))
        return PowerState(
            battery_pct=pct, is_charging=charging, is_backup=False,
            thermal_ok=temp < 45.0, source="termux",
        )

    def _jetson(self) -> Optional[PowerState]:
        """Detect Jetson Orin/Nano — always plugged in, no battery."""
        if not Path("/etc/nv_tegra_release").exists():
            return None
        # Jetson is mains-powered; no battery or UPS sensor by default.
        # Thermal: read via /sys/class/thermal if available.
        thermal_ok = True
        temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp_path.exists():
            try:
                thermal_ok = int(temp_path.read_text().strip()) < 85_000
            except ValueError:
                pass
        return PowerState(
            battery_pct=100, is_charging=True, is_backup=False,
            thermal_ok=thermal_ok, source="jetson",
        )


# ── Input conditioner ─────────────────────────────────────────────────────────

class InputConditioner:
    """Rule-based context compressor — no extra inference pass required.

    Splits the input into sentences, scores each by term overlap with the
    query, then greedily selects the highest-scoring sentences up to
    compress_budget tokens (whitespace-split approximation), preserving
    original sentence order.  Always keeps the first and last sentences to
    maintain narrative framing.
    """

    _SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])|(?<=\n)\s*(?=\S)')

    def compress(self, text: str, query: str, budget: int) -> tuple[str, int, int]:
        """Return (compressed_text, original_tokens, compressed_tokens).

        If text is already within budget, returns it unchanged.
        """
        tokens = text.split()
        if len(tokens) <= budget:
            return text, len(tokens), len(tokens)

        sentences  = [s.strip() for s in self._SENTENCE_RE.split(text) if s.strip()]
        if len(sentences) <= 2:
            # Too short to meaningfully compress; truncate.
            truncated = " ".join(tokens[:budget])
            return truncated, len(tokens), len(truncated.split())

        query_terms = set(re.findall(r'\b\w{3,}\b', query.lower()))

        def _score(sentence: str) -> float:
            words = set(re.findall(r'\b\w{3,}\b', sentence.lower()))
            if not words:
                return 0.0
            return len(words & query_terms) / len(words)

        scored = [(i, s, _score(s)) for i, s in enumerate(sentences)]

        # Always include first and last
        kept_indices = {0, len(sentences) - 1}
        token_count  = sum(len(s.split()) for i, s in enumerate(sentences)
                          if i in kept_indices)

        # Greedy select by score descending, skipping already-kept
        for idx, sent, _ in sorted(scored, key=lambda x: x[2], reverse=True):
            if idx in kept_indices:
                continue
            n = len(sent.split())
            if token_count + n > budget:
                continue
            kept_indices.add(idx)
            token_count += n

        result = " ".join(sentences[i] for i in sorted(kept_indices))
        return result, len(tokens), len(result.split())


# ── Main agent ────────────────────────────────────────────────────────────────

@dataclass
class ConditionedInput:
    original_text:   str
    conditioned_text: str
    original_tokens: int
    conditioned_tokens: int
    power_state:     PowerState
    profile:         PowerProfile
    config:          InferenceConfig
    compressed:      bool


class PowerConditionerAgent:
    """Adaptive inference configurator for battery and backup-power scenarios.

    Usage
    -----
        agent = PowerConditionerAgent()

        # Full cycle (reads live sensor):
        result = agent.condition("Your long prompt here...", query="what is X?")
        print(result.config.alpha, result.conditioned_text)

        # Force a profile (testing / simulation):
        result = agent.condition(text, query, state=PowerState(battery_pct=10, ...))

    Large-model notes (>1B, no GPU)
    --------------------------------
    For models like Mistral-7B in SRD format:
      - FULL   (α=1.0): 13 bpw → ~11 GB.  Requires high-VRAM GPU.
      - CONSERVE (α=0.0): 4.5 bpw → ~4 GB.  Fits 6 GB VRAM; usable on CPU.
    The α knob is more impactful than context trimming for >1B:
      context 4096→512 saves ~450 MB KV cache on 7B;
      α 1.0→0.0 saves ~7 GB weight memory on 7B.
    """

    def __init__(
        self,
        sensor: Optional[PowerSensor] = None,
        conditioner: Optional[InputConditioner] = None,
    ) -> None:
        self._sensor     = sensor or PowerSensor()
        self._conditioner = conditioner or InputConditioner()

    def read_state(self) -> PowerState:
        return self._sensor.read()

    def select_profile(self, state: PowerState) -> PowerProfile:
        return select_profile(state)

    def get_config(self, profile: PowerProfile) -> InferenceConfig:
        cfg = PROFILE_CONFIGS[profile]
        # Return a signed copy (don't mutate the template)
        import copy
        c = copy.copy(cfg)
        return c.sign()

    def condition(
        self,
        text:    str,
        query:   str = "",
        state:   Optional[PowerState] = None,
    ) -> ConditionedInput:
        """Run the full conditioning pipeline.

        1. Read (or accept) power state.
        2. Select inference profile.
        3. Compress input if the profile calls for it.
        4. Return ConditionedInput with config wired in.
        """
        if state is None:
            state = self.read_state()

        profile = self.select_profile(state)
        config  = self.get_config(profile)

        compressed     = False
        out_text       = text
        orig_tokens    = len(text.split())
        out_tokens     = orig_tokens

        if config.compress_input and orig_tokens > config.compress_budget:
            out_text, orig_tokens, out_tokens = self._conditioner.compress(
                text, query or text[:200], config.compress_budget,
            )
            compressed = True

        return ConditionedInput(
            original_text      = text,
            conditioned_text   = out_text,
            original_tokens    = orig_tokens,
            conditioned_tokens = out_tokens,
            power_state        = state,
            profile            = profile,
            config             = config,
            compressed         = compressed,
        )

    def ram_budget_mb(self, profile: PowerProfile, model: str = "qwen3_1b7") -> int:
        """Estimated peak RAM for the given profile + model key.

        model keys: "qwen3_1b7", "tinyllama_1b"
        Returns -1 if the model key is unknown.
        """
        return RAM_BUDGET_MB.get(profile, {}).get(model, -1)
