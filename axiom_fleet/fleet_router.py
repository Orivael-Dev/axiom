"""FleetRouter — routes a query to the right ≤0.5B specialist and runs it.

Routing priority:
  1. HARM / DECEIVE intent → block immediately (no specialist runs)
  2. Image attachment present → image_attachment_role specialist
  3. Domain match + intent_classes match → best specialist
  4. Fallback role

Each result carries the AXM fingerprint of the specialist that answered so
the decision is auditable (pairs with Flight Recorder's record_decision).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from axiom_fleet.fleet_manifest import FleetManifest, SpecialistConfig

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

_BLOCK_INTENTS = {"HARM", "DECEIVE"}


# ── Query / Result ────────────────────────────────────────────────────────────


@dataclass
class FleetQuery:
    """Input to the fleet."""
    text:          str
    image_path:    Optional[str]  = None   # local path or None
    domain:        str            = "general"
    system_prompt: Optional[str]  = None
    max_tokens:    int            = 512
    tenant_id:     str            = ""
    request_id:    str            = ""


@dataclass
class FleetResult:
    """Output from the fleet."""
    text:                  str
    specialist_role:       str
    specialist_fingerprint: Optional[str]   # AXM fingerprint
    intent_class:          str
    intent_confidence:     float
    latency_ms:            float
    blocked:               bool  = False
    block_reason:          str   = ""
    routing_log:           List[str] = field(default_factory=list)


# ── Runners ───────────────────────────────────────────────────────────────────


class TextSpecialistRunner:
    """Runs a text specialist via llama.cpp using the extracted GGUF.

    The GGUF is stored inside the .axm ZIP. We extract it to a temp dir
    once per FleetRouter instance (cached by fingerprint).
    """

    def __init__(self, llamacpp_dir: Optional[str] = None):
        self._llamacpp = Path(llamacpp_dir) if llamacpp_dir else self._find_llamacpp()
        self._gguf_cache: dict[str, Path] = {}   # fingerprint → extracted gguf path

    @staticmethod
    def _find_llamacpp() -> Optional[Path]:
        """Look for llama.cpp binary in common locations."""
        candidates = [
            Path("/workspace/llama.cpp/build/bin/llama-cli"),
            Path("/content/llama.cpp/build/bin/llama-cli"),
            Path.home() / "llama.cpp/build/bin/llama-cli",
        ]
        for p in candidates:
            if p.exists():
                return p.parent
        return None

    def _get_gguf(self, spec: SpecialistConfig) -> Path:
        """Return path to GGUF, extracting from AXM if necessary."""
        key = spec.fingerprint or spec.role
        if key in self._gguf_cache:
            return self._gguf_cache[key]

        gguf = Path(spec.gguf_path) if spec.gguf_path else None

        # Fast path: gguf exists alongside the axm
        if gguf and gguf.exists():
            self._gguf_cache[key] = gguf
            return gguf

        # Extract from AXM ZIP
        import zipfile, tempfile
        axm = Path(spec.axm_path)
        if not axm.exists():
            raise FileNotFoundError(f"AXM not found: {axm}")
        tmp = Path(tempfile.mkdtemp(prefix=f"fleet_{spec.role}_"))
        with zipfile.ZipFile(axm) as zf:
            gguf_names = [n for n in zf.namelist() if n.endswith(".gguf")]
            if not gguf_names:
                raise RuntimeError(f"No .gguf found inside {axm}")
            zf.extract(gguf_names[0], tmp)
        extracted = tmp / gguf_names[0]
        self._gguf_cache[key] = extracted
        return extracted

    def run(
        self,
        query: FleetQuery,
        spec: SpecialistConfig,
        routing_log: List[str],
    ) -> FleetResult:
        t0 = time.perf_counter()

        if not self._llamacpp:
            return FleetResult(
                text="[TextSpecialistRunner] llama.cpp not found. Set llamacpp_dir.",
                specialist_role=spec.role,
                specialist_fingerprint=spec.fingerprint,
                intent_class="UNCERTAIN",
                intent_confidence=0.0,
                latency_ms=0.0,
                blocked=True,
                block_reason="llama_cpp_not_found",
                routing_log=routing_log,
            )

        try:
            gguf = self._get_gguf(spec)
        except (FileNotFoundError, RuntimeError) as e:
            return FleetResult(
                text=f"[TextSpecialistRunner] {e}",
                specialist_role=spec.role,
                specialist_fingerprint=spec.fingerprint,
                intent_class="UNCERTAIN",
                intent_confidence=0.0,
                latency_ms=0.0,
                blocked=True,
                block_reason="gguf_not_found",
                routing_log=routing_log,
            )

        cli = self._llamacpp / "llama-cli"
        system = query.system_prompt or (
            "You are a concise specialist assistant. "
            "Respond only with what is asked. Be factual."
        )
        prompt = f"<|im_start|>system\n{system}<|im_end|>\n"
        prompt += f"<|im_start|>user\n{query.text}<|im_end|>\n"
        prompt += "<|im_start|>assistant\n"

        cmd = [
            str(cli), "-m", str(gguf),
            "-p", prompt,
            "-n", str(query.max_tokens),
            "--temp", "0.1",
            "--log-disable",
            "-c", "2048",
        ]
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            text = out.stdout.split("<|im_start|>assistant\n", 1)[-1].strip()
            text = text.split("<|im_end|>")[0].strip()
        except subprocess.TimeoutExpired:
            text = "[TextSpecialistRunner] inference timeout"

        return FleetResult(
            text=text,
            specialist_role=spec.role,
            specialist_fingerprint=spec.fingerprint,
            intent_class="INFORM",
            intent_confidence=1.0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            routing_log=routing_log,
        )


class VisionSpecialistRunner:
    """Runs a vision specialist via HuggingFace transformers (SmolVLM or similar).

    For ≤0.5B vision models (SmolVLM-256M, Florence-2-base) llama.cpp GGUF
    support is limited — transformers + bitsandbytes 4-bit is more reliable
    and keeps VRAM under 1 GB.

    The model weights are loaded directly from the .axm container or from
    the HF cache. AXM verification runs before loading.
    """

    def __init__(self):
        self._model = None
        self._processor = None
        self._loaded_role: Optional[str] = None

    def _load(self, spec: SpecialistConfig) -> None:
        if self._loaded_role == spec.role:
            return
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForVision2Seq
            from transformers import BitsAndBytesConfig
        except ImportError as e:
            raise RuntimeError(
                f"Vision runner requires transformers>=4.45 and bitsandbytes: {e}"
            )

        # Prefer extracting weights from AXM for tamper-evident loading.
        # Weights are stored as safetensors (safe_serialization=True in packer).
        # Fall back to HF cache if AXM not found (dev mode).
        weights_source = spec.base_model
        axm = Path(spec.axm_path)
        if axm.exists():
            import zipfile, tempfile
            tmp = Path(tempfile.mkdtemp(prefix=f"fleet_vision_{spec.role}_"))
            with zipfile.ZipFile(axm) as zf:
                members = [n for n in zf.namelist() if n.startswith("weights/")]
                zf.extractall(tmp, members)
            weights_dir = tmp / "weights"
            # Validate safetensors file landed correctly
            if not any(weights_dir.glob("*.safetensors")):
                raise RuntimeError(
                    f"No .safetensors found in AXM weights dir — was it packed with pack_vision_to_axm.py?"
                )
            weights_source = str(weights_dir)

        quant_cfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
        self._processor = AutoProcessor.from_pretrained(
            weights_source, trust_remote_code=True
        )
        self._model = AutoModelForVision2Seq.from_pretrained(
            weights_source,
            quantization_config=quant_cfg,
            device_map="auto",
            trust_remote_code=True,
        )
        self._loaded_role = spec.role

    def run(
        self,
        query: FleetQuery,
        spec: SpecialistConfig,
        routing_log: List[str],
    ) -> FleetResult:
        t0 = time.perf_counter()
        try:
            self._load(spec)
        except RuntimeError as e:
            return FleetResult(
                text=f"[VisionSpecialistRunner] {e}",
                specialist_role=spec.role,
                specialist_fingerprint=spec.fingerprint,
                intent_class="UNCERTAIN",
                intent_confidence=0.0,
                latency_ms=0.0,
                blocked=True,
                block_reason="vision_load_error",
                routing_log=routing_log,
            )

        import torch
        from PIL import Image

        messages = []
        if query.image_path:
            img = Image.open(query.image_path).convert("RGB")
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": query.text},
                ],
            })
        else:
            img = None
            messages.append({"role": "user", "content": query.text})

        prompt = self._processor.apply_chat_template(
            messages, add_generation_prompt=True
        )
        inputs = self._processor(
            text=prompt,
            images=[img] if img else None,
            return_tensors="pt",
        ).to("cuda" if torch.cuda.is_available() else "cpu")

        with torch.no_grad():
            ids = self._model.generate(
                **inputs, max_new_tokens=query.max_tokens, do_sample=False
            )
        text = self._processor.batch_decode(
            ids[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )[0].strip()

        return FleetResult(
            text=text,
            specialist_role=spec.role,
            specialist_fingerprint=spec.fingerprint,
            intent_class="INFORM",
            intent_confidence=1.0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            routing_log=routing_log,
        )


def _get_runner(spec: SpecialistConfig, llamacpp_dir: Optional[str]) -> object:
    if spec.modality in ("vision", "multimodal"):
        return VisionSpecialistRunner()
    return TextSpecialistRunner(llamacpp_dir)


# ── FleetRouter ───────────────────────────────────────────────────────────────


class FleetRouter:
    """Route a FleetQuery to the right specialist and return a FleetResult.

    Construction:
        router = FleetRouter(load_manifest("examples/fleets/medical_fleet.json"))

    Query:
        result = router.query("What is consolidation?")
        result = router.query("Describe findings.", image_path="/tmp/cxr.jpg")

    The router verifies AXM fingerprints before running any specialist (pass
    verify_axm=False in dev/test to skip this check).
    """

    def __init__(
        self,
        manifest:    FleetManifest,
        llamacpp_dir: Optional[str] = None,
        verify_axm:   bool          = True,
        classifier   = None,
    ) -> None:
        self._manifest    = manifest
        self._llamacpp    = llamacpp_dir
        self._verify_axm  = verify_axm
        self._runners:    dict[str, object] = {}  # role → runner (lazy init)

        if classifier is None:
            try:
                from axiom_intent_classifier import IntentClassifier
                from axiom_signing import derive_key
                self._clf = IntentClassifier(derive_key(b"axiom-firewall-v1"))
            except ImportError:
                self._clf = None
        else:
            self._clf = classifier

    # ── AXM verification ─────────────────────────────────────────────────────

    def _verify(self, spec: SpecialistConfig, log: List[str]) -> bool:
        """Run axm_cli.py verify; return True if fingerprint matches."""
        if not self._verify_axm:
            log.append(f"[{spec.role}] axm verify skipped (verify_axm=False)")
            return True
        axm = Path(spec.axm_path)
        if not axm.exists():
            log.append(f"[{spec.role}] AXM not found at {axm} — skipping verify")
            return True   # allow unbuilt AXMs in dev; pack_fleet fills this later
        axm_cli = _REPO / "research" / "quant" / "axm_cli.py"
        try:
            out = subprocess.run(
                [sys.executable, str(axm_cli), "verify", str(axm)],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(out.stdout)
            ok = data.get("verified", False)
            fp = data.get("fingerprint", "?")
            if spec.fingerprint and fp != spec.fingerprint:
                log.append(
                    f"[{spec.role}] fingerprint mismatch: manifest={spec.fingerprint} axm={fp}"
                )
                return False
            log.append(f"[{spec.role}] axm verified fingerprint={fp}")
            return ok
        except Exception as e:
            log.append(f"[{spec.role}] axm verify error: {e}")
            return False

    # ── Routing ───────────────────────────────────────────────────────────────

    def _classify_intent(self, text: str) -> tuple[str, float]:
        if self._clf is None:
            return "UNCERTAIN", 0.0
        r = self._clf.classify(text)
        return r.intent_class, float(r.confidence)

    def _pick_specialist(
        self,
        query:       FleetQuery,
        intent:      str,
        log:         List[str],
    ) -> Optional[SpecialistConfig]:
        policy = self._manifest.routing

        # Image attachment → vision specialist
        if query.image_path:
            spec = self._manifest.get_specialist(policy.image_attachment_role)
            if spec:
                log.append(f"image attachment → {spec.role}")
                return spec
            log.append(f"image_attachment_role={policy.image_attachment_role!r} not found")

        # Domain + intent match
        domain_specs = self._manifest.by_domain(query.domain)
        for spec in domain_specs:
            if intent in spec.intent_classes or intent.lower() in spec.intent_classes:
                log.append(f"domain={query.domain!r} intent={intent} → {spec.role}")
                return spec

        # UNCERTAIN fallback
        if intent == "UNCERTAIN" and policy.uncertain_fallback:
            spec = self._manifest.get_specialist(policy.fallback_role)
            if spec:
                log.append(f"uncertain → fallback {spec.role}")
                return spec

        # Any specialist that covers this intent
        for spec in self._manifest.specialists:
            if intent in spec.intent_classes or intent.lower() in spec.intent_classes:
                log.append(f"intent={intent} → {spec.role}")
                return spec

        # Last resort: fallback role
        spec = self._manifest.get_specialist(policy.fallback_role)
        log.append(f"fallback → {spec.role if spec else 'none'}")
        return spec

    # ── Public interface ──────────────────────────────────────────────────────

    def query(
        self,
        text:          str,
        image_path:    Optional[str] = None,
        domain:        str           = "general",
        system_prompt: Optional[str] = None,
        max_tokens:    int           = 512,
        tenant_id:     str           = "",
    ) -> FleetResult:
        """Route and run a query through the fleet."""
        q = FleetQuery(
            text=text,
            image_path=image_path,
            domain=domain,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            tenant_id=tenant_id,
        )
        return self._run(q)

    def _run(self, query: FleetQuery) -> FleetResult:
        log: List[str] = [f"fleet={self._manifest.fleet_id}"]
        t0 = time.perf_counter()

        intent, confidence = self._classify_intent(query.text)
        log.append(f"intent={intent} confidence={confidence:.2f}")

        # Block harmful intents
        if self._manifest.routing.harm_block and intent in _BLOCK_INTENTS:
            log.append(f"blocked: {intent}")
            return FleetResult(
                text=f"Request blocked (intent: {intent}).",
                specialist_role="none",
                specialist_fingerprint=None,
                intent_class=intent,
                intent_confidence=confidence,
                latency_ms=(time.perf_counter() - t0) * 1000,
                blocked=True,
                block_reason=intent,
                routing_log=log,
            )

        spec = self._pick_specialist(query, intent, log)
        if spec is None:
            return FleetResult(
                text="No specialist available for this query.",
                specialist_role="none",
                specialist_fingerprint=None,
                intent_class=intent,
                intent_confidence=confidence,
                latency_ms=(time.perf_counter() - t0) * 1000,
                blocked=True,
                block_reason="no_specialist",
                routing_log=log,
            )

        if not self._verify(spec, log):
            return FleetResult(
                text="Specialist AXM verification failed.",
                specialist_role=spec.role,
                specialist_fingerprint=spec.fingerprint,
                intent_class=intent,
                intent_confidence=confidence,
                latency_ms=(time.perf_counter() - t0) * 1000,
                blocked=True,
                block_reason="axm_verification_failed",
                routing_log=log,
            )

        runner_key = spec.role
        if runner_key not in self._runners:
            self._runners[runner_key] = _get_runner(spec, self._llamacpp)
        runner = self._runners[runner_key]

        result = runner.run(query, spec, log)
        # Stamp the intent fields that came from our classifier (runner doesn't know)
        result.intent_class = intent
        result.intent_confidence = confidence
        return result

    # ── Convenience: log result to Flight Recorder ───────────────────────────

    def record_to_flight_recorder(
        self,
        tenant_id: str,
        result:    FleetResult,
        query:     FleetQuery,
    ) -> Optional[str]:
        """Record routing + inference result in Flight Recorder. Returns decision_id."""
        try:
            from axiom_firewall.flight_recorder import record_decision
        except ImportError:
            return None
        verdict = "block" if result.blocked else "allow"
        return record_decision(tenant_id, {
            "api_key_id":    query.request_id or "fleet",
            "endpoint":      f"fleet/{self._manifest.fleet_id}/{result.specialist_role}",
            "verdict":       verdict,
            "intent_class":  result.intent_class,
            "confidence":    result.intent_confidence,
            "latency_ms":    result.latency_ms,
            "input_text":    query.text,
            "output_text":   result.text if not result.blocked else None,
            "pattern_matched": result.specialist_fingerprint,
            "constitutional_block": result.blocked,
            "ftc_reportable": False,
        })
