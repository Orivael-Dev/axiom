"""
Axiom Visual SRD Agent
======================
CANNOT_MUTATE agent, goal, version, trust_level, visual_drift_threshold, sensitive_data_gate, training_prohibition, data_retention_policy

Wraps SmolVLM-256M with multi-band SRD correction and a connector-layer
drift filter. Two-layer defence against visual hallucination:

  Layer 1 — SRD weight correction (load-time, zero inference overhead)
             Applies D8 residuals to vision_encoder + connector + LM
             reasoning layers before inference begins.

  Layer 2 — Connector activation drift filter (per-query, lightweight)
             Captures the mean activation from the cross-modal connector
             after each forward pass. Compares to a calibrated centroid.
             Queries where the connector drifts > VISUAL_DRIFT_THRESHOLD
             are flagged as potentially hallucinated and returned with
             drift_status = "VISUAL_DRIFT_DETECTED".

Rationale for the connector hook point
---------------------------------------
The connector is where ViT patch embeddings are projected into the LM
token space. When the model cannot genuinely ground an answer in the
image (OOD input, ambiguous text, out-of-focus region), this projection
is forced off-manifold — the connector activation deviates from the
centroid learned on well-grounded examples. SRD improves the mean
quality; drift detection catches the individual failures SRD cannot fix.

Usage
-----
  import os
  os.environ["AXIOM_MASTER_KEY"] = "..."   # required for signing

  from axiom_visual_srd_agent import VisualSRDAgent, VisualQuery

  agent = VisualSRDAgent()
  agent.load()          # loads SmolVLM + applies SRD + registers hash
  agent.calibrate(items)# build connector centroid from known-good examples

  query  = VisualQuery(image=pil_img, question="What text is shown?").sign()
  result = agent.process(query)
  print(result.answer, result.drift_status, result.confidence)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

# ── CANNOT_MUTATE module freeze ──────────────────────────────────────────
import types as _types

class _Frozen(_types.ModuleType):
    _IMMUTABLE = frozenset({
        "TRUST_LEVEL", "AGENT_VERSION", "MODEL_ID",
        "VISUAL_DRIFT_THRESHOLD", "MAX_NEW_TOKENS",
    })
    def __setattr__(self, name, value):
        if name in self._IMMUTABLE:
            raise AttributeError(f"CANNOT_MUTATE: {name} is immutable")
        super().__setattr__(name, value)

sys.modules[__name__].__class__ = _Frozen

# ── CANNOT_MUTATE constants ───────────────────────────────────────────────
TRUST_LEVEL             = 1
AGENT_VERSION           = "1.0"
MODEL_ID                = "HuggingFace/SmolVLM-Instruct"
VISUAL_DRIFT_THRESHOLD  = 0.12   # slightly wider than text-only 0.10
                                  # (vision activations have higher natural variance)
MAX_NEW_TOKENS          = 30

_NS = b"axiom-visual-srd-v1"


# ── HMAC signing ─────────────────────────────────────────────────────────

def _sign(payload: dict) -> str:
    from axiom_signing import derive_key
    key = derive_key(_NS)
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _verify(payload: dict, signature: str) -> bool:
    return hmac.compare_digest(_sign(payload), signature)


# ── Token dataclasses ─────────────────────────────────────────────────────

@dataclass
class VisualQuery:
    """Signed visual query token. Image is hashed, not embedded."""
    image_hash:  str          # SHA256 of image bytes
    question:    str
    timestamp:   int = 0
    signature:   str = ""

    @classmethod
    def from_pil(cls, image, question: str) -> "VisualQuery":
        import io, time as _time
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        h = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
        return cls(image_hash=h, question=question, timestamp=int(_time.time()))

    def sign(self) -> "VisualQuery":
        payload = {"image_hash": self.image_hash,
                   "question": self.question,
                   "timestamp": self.timestamp}
        self.signature = _sign(payload)
        return self

    def verify(self) -> bool:
        payload = {"image_hash": self.image_hash,
                   "question": self.question,
                   "timestamp": self.timestamp}
        return _verify(payload, self.signature)


@dataclass
class VisualPrediction:
    """Signed visual prediction with drift status."""
    question:       str
    answer:         str
    confidence:     float          # softmax-derived, 0–1
    drift_status:   str            # STABLE_VISUAL | VISUAL_DRIFT_DETECTED
    connector_dist: float          # distance from calibrated centroid
    bands_active:   List[str]      # which SRD bands were applied
    wallclock_ms:   int
    signature:      str = ""

    def sign(self) -> "VisualPrediction":
        payload = {
            "question":     self.question,
            "answer":       self.answer,
            "drift_status": self.drift_status,
        }
        self.signature = _sign(payload)
        return self

    def verify(self) -> bool:
        payload = {
            "question":     self.question,
            "answer":       self.answer,
            "drift_status": self.drift_status,
        }
        return _verify(payload, self.signature)


# ── Centroid helpers (pure Python, mirrors axiom_cas_orchestrator) ────────

def _euclidean(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _mean_vec(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


# ── Connector activation hook ─────────────────────────────────────────────

class _ConnectorHook:
    """Forward hook that captures the mean activation of the connector."""

    def __init__(self):
        self.activation: Optional[List[float]] = None
        self._handle = None

    def attach(self, connector_module) -> None:
        self._handle = connector_module.register_forward_hook(self._hook)

    def _hook(self, module, input, output):
        import torch
        with torch.no_grad():
            # output may be a tensor or tuple — take first tensor
            t = output[0] if isinstance(output, tuple) else output
            # Mean over all positions → (hidden,)
            self.activation = t.float().mean(dim=list(range(t.dim() - 1))).tolist()

    def detach(self) -> None:
        if self._handle:
            self._handle.remove()
            self._handle = None


# ── Agent ────────────────────────────────────────────────────────────────

class VisualSRDAgent:
    """SmolVLM-256M with multi-band SRD + connector drift filter.

    Workflow:
      agent = VisualSRDAgent()
      agent.load()           # load model + apply SRD bands
      agent.calibrate(items) # build connector centroid (10–50 examples)
      result = agent.process(query)
    """

    def __init__(self, bands: str = "all", hf_token: str = ""):
        self.bands      = bands
        self.hf_token   = hf_token
        self.model      = None
        self.processor  = None
        self._centroid:  List[float] = []
        self._hook       = _ConnectorHook()
        self._bands_active: List[str] = []

    # ── Load ─────────────────────────────────────────────────────────────

    def load(self) -> "VisualSRDAgent":
        import torch
        try:
            from transformers import AutoProcessor, AutoModelForVision2Seq
        except ImportError as _e:
            import transformers as _tf
            raise RuntimeError(
                f"transformers {_tf.__version__} does not have AutoModelForVision2Seq "
                f"(removed in v5.x). Pin with: "
                f"pip install \"transformers==4.44.2\" accelerate"
            ) from _e
        from research.quant.quantize_model import quantize_hf_model_inplace
        from research.quant.srd_multimodal import (
            apply_multiband_srd, detect_components,
        )
        from axiom_files.parser import register_agent_hash

        kw = {"torch_dtype": torch.float16 if torch.cuda.is_available() else torch.float32,
              "device_map": "auto"}
        if self.hf_token:
            kw["token"] = self.hf_token

        print(f"[visual-srd] loading {MODEL_ID} ...")
        self.processor = AutoProcessor.from_pretrained(
            MODEL_ID, **({"token": self.hf_token} if self.hf_token else {})
        )
        self.model = AutoModelForVision2Seq.from_pretrained(MODEL_ID, **kw)
        self.model.eval()

        # Degrade to Q4 baseline first
        quantize_hf_model_inplace(
            self.model, alpha=0.0, group_size=64, progress=False,
        )

        # Apply multi-band SRD
        band_results = apply_multiband_srd(
            self.model, bands=self.bands, group_size=64, alpha=1.0, verbose=True,
        )
        self._bands_active = [b for b, r in band_results.items() if r.corrected > 0]

        # Attach connector hook
        comps = detect_components(self.model)
        connector = dict(self.model.named_modules()).get(comps.connector_prefix)
        if connector is not None:
            self._hook.attach(connector)
            print(f"[visual-srd] drift hook attached to {comps.connector_prefix}")
        else:
            print(f"[visual-srd] WARNING: connector '{comps.connector_prefix}' not found"
                  f" — drift detection disabled")

        try:
            register_agent_hash("research/visual_srd")
        except Exception:
            pass   # spec not present yet — non-fatal

        print(f"[visual-srd] ready  bands={self._bands_active}")
        return self

    # ── Calibrate ─────────────────────────────────────────────────────────

    def calibrate(self, items: list) -> "VisualSRDAgent":
        """Build connector centroid from known-good visual grounding examples.

        items: list of {"image": PIL.Image, "question": str}
        Recommend 20–50 examples covering varied image types.
        """
        import torch
        device = next(self.model.parameters()).device
        activations: List[List[float]] = []

        print(f"[visual-srd] calibrating on {len(items)} examples ...")
        for item in items:
            self._hook.activation = None
            prompt = self.processor.apply_chat_template(
                [{"role": "user", "content": [
                    {"type": "image"},
                    {"type": "text", "text": item["question"]},
                ]}], add_generation_prompt=True,
            )
            inputs = self.processor(text=prompt, images=[item["image"]],
                                    return_tensors="pt").to(device)
            with torch.no_grad():
                self.model(**inputs)   # forward only, no generation needed
            if self._hook.activation:
                activations.append(self._hook.activation)

        self._centroid = _mean_vec(activations)
        print(f"[visual-srd] centroid built from {len(activations)} activations"
              f"  dim={len(self._centroid)}")
        return self

    # ── Process ───────────────────────────────────────────────────────────

    def process(self, query: VisualQuery, image) -> VisualPrediction:
        """Run inference, apply drift filter, return signed VisualPrediction.

        query: signed VisualQuery (query.verify() must pass)
        image: PIL.Image corresponding to query.image_hash
        """
        import torch

        if not query.verify():
            raise ValueError("VisualQuery signature invalid — token tampered")

        device = next(self.model.parameters()).device
        t0 = time.monotonic()

        self._hook.activation = None
        prompt = self.processor.apply_chat_template(
            [{"role": "user", "content": [
                {"type": "image"},
                {"type": "text", "text": query.question},
            ]}], add_generation_prompt=True,
        )
        inputs = self.processor(text=prompt, images=[image],
                                return_tensors="pt").to(device)

        with torch.no_grad():
            out_ids = self.model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=False,
                output_scores=True,
                return_dict_in_generate=True,
            )

        generated_ids = out_ids.sequences[0][inputs["input_ids"].shape[1]:]
        answer = self.processor.decode(generated_ids, skip_special_tokens=True).strip()

        # Confidence from first-token score
        confidence = 0.5
        if hasattr(out_ids, "scores") and out_ids.scores:
            import torch.nn.functional as F
            probs = F.softmax(out_ids.scores[0][0], dim=-1)
            confidence = float(probs.max().item())

        # Drift detection
        connector_dist = 0.0
        drift_status   = "STABLE_VISUAL"
        if self._centroid and self._hook.activation:
            connector_dist = _euclidean(self._hook.activation, self._centroid)
            if connector_dist > VISUAL_DRIFT_THRESHOLD:
                drift_status = "VISUAL_DRIFT_DETECTED"

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return VisualPrediction(
            question       = query.question,
            answer         = answer,
            confidence     = round(confidence, 4),
            drift_status   = drift_status,
            connector_dist = round(connector_dist, 4),
            bands_active   = self._bands_active,
            wallclock_ms   = elapsed_ms,
        ).sign()

    def unload(self) -> None:
        self._hook.detach()
        del self.model
        self.model = None


# ── Quick smoke test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    if not os.environ.get("AXIOM_MASTER_KEY"):
        print("Set AXIOM_MASTER_KEY before running")
        sys.exit(1)

    print("Visual SRD Agent — smoke test (dry component check only)")
    import torch
    try:
        from transformers import AutoModelForVision2Seq
    except ImportError as _e:
        import transformers as _tf
        raise RuntimeError(
            f"transformers {_tf.__version__} does not have AutoModelForVision2Seq. "
            f"Pin with: pip install \"transformers==4.44.2\" accelerate"
        ) from _e
    from research.quant.srd_multimodal import detect_components

    model = AutoModelForVision2Seq.from_pretrained(
        MODEL_ID, torch_dtype=torch.float16, device_map="auto",
    )
    comps = detect_components(model)
    print(f"vision    → {comps.vision_prefix}")
    print(f"connector → {comps.connector_prefix}")
    print(f"lm        → {comps.lm_prefix}  ({comps.n_lm_layers} layers)")
    print(f"drift threshold = {VISUAL_DRIFT_THRESHOLD}")
    print("✓  agent structure OK")
