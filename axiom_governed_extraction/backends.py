"""
Extractor backends. Each returns {field_name: {"value": ..., "confidence": float}}.

The backend is the *only* untrusted, swappable part of the pipeline — governance is
enforced downstream regardless of which backend runs. Today: a deterministic Mock
(offline, no key) and NIM llama-3.3-70b. Tomorrow: drop in a fine-tuned SmolLM-135M
exposing the same `.extract()` and the governance layer is unchanged.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any


# ── Mock backend ────────────────────────────────────────────────────────────────
# Heuristic regex extractor. Deliberately over-extracts (pulls identifiers) AND
# emits one ungrounded value, so the governance layer visibly fires on real docs.

class MockBackend:
    name = "mock-regex"

    def extract(self, text: str, schema: dict) -> dict[str, Any]:
        out: dict[str, Any] = {}

        def put(field, value, conf=0.95):
            if value:
                out[field] = {"value": value, "confidence": conf}

        m = re.search(r"(?:patient|name)\s*[:\-]\s*([A-Z][a-z]+ [A-Z][a-z]+)", text)
        if m: put("patient_name", m.group(1))
        m = re.search(r"MRN\s*[:\-]?\s*([A-Z0-9\-]{4,})", text, re.I)
        if m: put("mrn", m.group(1))
        m = re.search(r"\b(\d{3}-\d{2}-\d{4})\b", text)
        if m: put("ssn", m.group(1))
        m = re.search(r"DOB\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9/]{8,10})", text, re.I)
        if m: put("dob", m.group(1))
        m = re.search(r"(?:visit|admit|date)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text, re.I)
        if m: put("visit_date", m.group(1))
        m = re.search(r"(?:diagnos[ie]s|impression)\s*[:\-]\s*([^\n.]+)", text, re.I)
        if m: put("diagnosis", m.group(1).strip(), 0.88)
        meds = re.findall(r"\b([A-Z][a-z]+(?:in|ol|ide|one|pril|statin|micin))\b\s*\d*\s*mg", text)
        if meds: put("medications", meds, 0.82)
        m = re.search(r"(?:procedure)\s*[:\-]\s*([^\n.]+)", text, re.I)
        if m: put("procedure", m.group(1).strip(), 0.6)   # low confidence -> review path

        # Deliberate hallucination to demonstrate the grounding guard: claim a lab
        # value that does NOT appear in the source.
        out["lab_values"] = {"value": "HbA1c 14.9% (critical)", "confidence": 0.91}
        return out


# ── NIM backend (llama-3.3-70b) ──────────────────────────────────────────────────

class NimBackend:
    name = "nim/meta-llama-3.3-70b-instruct"

    def __init__(self, model: str = "meta/llama-3.3-70b-instruct",
                 base_url: str = "https://integrate.api.nvidia.com/v1"):
        from openai import OpenAI  # lazy import
        api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NIM_API_KEY")
        if not api_key:
            raise RuntimeError("NVIDIA_API_KEY / NIM_API_KEY not set for NimBackend")
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def extract(self, text: str, schema: dict) -> dict[str, Any]:
        field_names = list(schema.get("fields", {}).keys())
        sys = (
            "You are a medical-record field extractor. Return ONLY a JSON object mapping "
            "field names to {\"value\":..., \"confidence\":0-1}. Use only the allowed fields. "
            "Extract a value ONLY if it is explicitly present in the document; otherwise omit "
            "the field. Do not invent values. confidence reflects how certain the value is."
        )
        user = f"Allowed fields: {field_names}\n\nDOCUMENT:\n{text}\n\nJSON:"
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=800, temperature=0,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        )
        raw = resp.choices[0].message.content or "{}"
        return _parse_json_obj(raw)


# ── llama.cpp backend (local GGUF, e.g. SmolLM2-135M-Instruct SRD-Q4_K_M) ────────
# Talks to a running `llama-server` over its OpenAI-compatible /v1 endpoint, so the
# same extract() contract holds. This is the on-device 135M slot: governance is
# unchanged whether the fields come from llama-3.3-70b or a 119 MB local model.

class LlamaCppBackend:
    def __init__(self, base_url: str | None = None, model: str = "smollm2-135m-instruct-srd4"):
        from openai import OpenAI  # lazy import
        base_url = base_url or os.environ.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080/v1")
        self.model = model
        self.name = f"llama.cpp/{model}"
        self.client = OpenAI(api_key="sk-no-key", base_url=base_url)

    def extract(self, text: str, schema: dict) -> dict[str, Any]:
        field_names = list(schema.get("fields", {}).keys())
        sys = (
            "Extract medical fields from the document. Output ONLY a JSON object. "
            "Keys must be from this list and nothing else: " + ", ".join(field_names) + ". "
            "Each value is the exact text from the document, or omit the key if absent. "
            "Do not add commentary. Example: {\"diagnosis\": \"...\", \"visit_date\": \"...\"}"
        )
        user = f"DOCUMENT:\n{text}\n\nJSON:"
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=400, temperature=0,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        )
        raw = resp.choices[0].message.content or "{}"
        return _parse_json_obj(raw)


def _parse_json_obj(s: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response; tolerant of code fences."""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?|\n?```$", "", s).strip()
    try:
        obj = json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return {}
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return {}
    # normalise bare values to {"value","confidence"}
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if isinstance(v, dict) and "value" in v:
            out[k] = {"value": v["value"], "confidence": float(v.get("confidence", 0.9))}
        else:
            out[k] = {"value": v, "confidence": 0.9}
    return out


def get_backend(name: str):
    if name == "nim":
        return NimBackend()
    if name in ("llamacpp", "local", "gguf"):
        return LlamaCppBackend()
    return MockBackend()
