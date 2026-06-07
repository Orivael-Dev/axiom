"""Axiom SRD Container Studio — business logic.

Export generators and verify logic used by the studio routes in dashboard.py.
No FastAPI imports here — pure functions that return bytes or dicts.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent
_REPO_URL    = "https://github.com/orivael-dev/axiom.git"
_REPO_BRANCH = "claude/srd-prototype-benchmark-JRtv1"

# ── Tier configuration ────────────────────────────────────────────────────────

SLOT_LIMITS: dict[str, int] = {
    "free":       3,
    "indie":      5,
    "team":       5,
    "enterprise": 5,
}

EXPORT_FMTS: dict[str, list[str]] = {
    "free":       ["colab"],
    "indie":      ["colab", "jupyter", "python", "json"],
    "team":       ["colab", "jupyter", "python", "json"],
    "enterprise": ["colab", "jupyter", "python", "json"],
}

# Slots locked behind premium tier
LOCKED_SLOTS: set[str] = {"audio", "video", "physics", "adapter"}

CONTAINER_CAP: dict[str, int] = {
    "free": 3,
}  # paid tiers have no cap

# Preset model IDs for the datalist
MODEL_PRESETS = [
    "unsloth/Llama-3.2-1B-Instruct",
    "unsloth/Llama-3.2-3B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "Qwen/Qwen2.5-Coder-1.5B",
    "Qwen/Qwen2.5-Coder-7B-Instruct",
    "microsoft/phi-2",
]

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SlotConfig:
    slot_type: str   # "text" | "audio" | "video" | "governance" | "physics"
    params: dict     # slot-specific form values


@dataclass
class StudioConfig:
    model_id: str
    slots: list[SlotConfig]
    hardware_map: str   # "gpu" | "cpu" | "compile_on_load"
    export_format: str  # "colab" | "jupyter" | "python" | "json"
    quant_scheme: str = "srd_alpha0"   # always srd_alpha0 for now


def config_from_dict(d: dict) -> StudioConfig:
    slots = [SlotConfig(slot_type=s["slot_type"], params=s.get("params", {}))
             for s in d.get("slots", [])]
    return StudioConfig(
        model_id=d.get("model_id", "").strip(),
        slots=slots,
        hardware_map=d.get("hardware_map", "gpu"),
        export_format=d.get("export_format", "colab"),
        quant_scheme=d.get("quant_scheme", "srd_alpha0"),
    )


# ── Verify ────────────────────────────────────────────────────────────────────

MAX_AXM_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MB — covers test/1B containers

def verify_axm_bytes(axm_bytes: bytes) -> dict:
    """Write bytes to a temp file, run axm_cli.py verify, return parsed JSON.

    Returns {verified, proofs_checked, fingerprint} or {verified: False, error}.
    """
    axm_cli = _REPO / "axm_cli.py"
    if not axm_cli.is_file():
        return {"verified": False, "error": "axm_cli.py not found on server"}

    with tempfile.NamedTemporaryFile(suffix=".axm", delete=False) as f:
        f.write(axm_bytes)
        tmp = Path(f.name)

    try:
        r = subprocess.run(
            ["python3", str(axm_cli), "verify", str(tmp)],
            capture_output=True, text=True, timeout=30,
            cwd=str(_REPO),
        )
        try:
            return json.loads(r.stdout)
        except Exception:
            return {"verified": False,
                    "error": (r.stdout + r.stderr)[-400:] or "empty output"}
    except subprocess.TimeoutExpired:
        return {"verified": False, "error": "verification timed out (30s)"}
    finally:
        tmp.unlink(missing_ok=True)


# ── Notebook helpers ──────────────────────────────────────────────────────────

def _md_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def _code_cell(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def _nbformat(cells: list[dict], colab: bool = False) -> dict:
    meta: dict = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python", "version": "3.10.0"},
    }
    if colab:
        meta["colab"] = {"provenance": []}
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": meta,
        "cells": cells,
    }


def _slot_notes(cfg: StudioConfig) -> str:
    lines = []
    for s in cfg.slots:
        if s.slot_type == "governance":
            lines.append("# Governance slot — evaluates all other slots automatically")
        elif s.slot_type == "audio":
            p = s.params
            lines.append(f'# Audio slot: impact={p.get("impact_profile","sharp_transient")}  '
                         f'material={p.get("material_signature","glass-like")}  '
                         f'depth={p.get("depth", 0.5)}  width={p.get("width", 0.5)}')
        elif s.slot_type == "video":
            p = s.params
            lines.append(f'# Video slot: motion={p.get("motion_class","downward")}  '
                         f'impact_detected={p.get("impact_detected", False)}  '
                         f'object_count={p.get("object_count", 1)}')
        elif s.slot_type == "physics":
            p = s.params
            lines.append(f'# Physics slot: material={p.get("material","brittle_glass")}  '
                         f'surface={p.get("surface","hard_surface")}  '
                         f'depth_class={p.get("depth_class","near")}')
    return "\n".join(lines) if lines else "# No extra slots configured"


def _pack_cell_code(cfg: StudioConfig, colab: bool) -> str:
    base_dir = "/content/axiom" if colab else "/workspace/axiom"
    out_dir  = "/content"       if colab else "/workspace/srd_output"
    short_name = cfg.model_id.split("/")[-1].lower().replace(".", "-")
    return f'''\
import json, os, subprocess, sys, time
from pathlib import Path

AXIOM_DIR  = Path("{base_dir}")
OUTPUT_DIR = Path("{out_dir}")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(AXIOM_DIR))

MODEL_ID   = "{cfg.model_id}"
SHORT_NAME = "{short_name}"
AXM_PATH   = OUTPUT_DIR / f"{{SHORT_NAME}}_srd.axm"
STATS_JSON = OUTPUT_DIR / f"{{SHORT_NAME}}_pack_stats.json"

PACK_SCRIPT = AXIOM_DIR / "research" / "quant" / "pack_to_axm.py"

{_slot_notes(cfg)}

if AXM_PATH.exists():
    print(f"✓ AXM already packed: {{AXM_PATH}}")
else:
    print(f"Packing {{MODEL_ID}} with SRD alpha=0 ...")
    t0 = time.time()
    r = subprocess.run([
        sys.executable, str(PACK_SCRIPT),
        "--model",      MODEL_ID,
        "--output",     str(AXM_PATH),
        "--top-k-pct",  "0.25",
        "--group-size", "64",
        "--stats-json", str(STATS_JSON),
    ], cwd=str(AXIOM_DIR))
    elapsed = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError(f"Pack failed (rc={{r.returncode}})")
    print(f"✓ Done {{elapsed/60:.1f}} min — {{AXM_PATH.name}}")

if STATS_JSON.exists():
    s = json.loads(STATS_JSON.read_text())
    print(f"  fingerprint: {{s.get('fingerprint')}}")
    print(f"  bpw:         {{s.get('bpw_theoretical')}}")
    print(f"  proofs:      {{s.get('proofs')}}")
'''


def _verify_cell_code(cfg: StudioConfig, colab: bool) -> str:
    return '''\
import json, subprocess, sys

out = subprocess.run(
    [sys.executable, str(AXIOM_DIR / "axm_cli.py"), "verify", str(AXM_PATH)],
    cwd=str(AXIOM_DIR), capture_output=True, text=True,
)
try:
    data = json.loads(out.stdout)
except Exception:
    data = {"verified": False, "error": out.stdout[-400:] + out.stderr[-200:]}

ok = data.get("verified", False)
print(f"{'✓ VERIFIED' if ok else '✗ FAILED'}")
print(f"  fingerprint:    {data.get('fingerprint', '?')}")
print(f"  proofs_checked: {data.get('proofs_checked', '?')}")
if not ok:
    raise RuntimeError(f"Verification failed: {data.get('error', 'unknown')}")
'''


def _extract_cell_code(cfg: StudioConfig, colab: bool) -> str:
    base_dir = "/content/axiom" if colab else "/workspace/axiom"
    out_dir  = "/content"       if colab else "/workspace/srd_output"
    return f'''\
import subprocess, sys, time

AXM_TO_GGUF  = AXIOM_DIR / "research" / "quant" / "axm_to_gguf.py"
LLAMACPP_DIR = OUTPUT_DIR / "llama.cpp"
GGUF_PATH    = OUTPUT_DIR / (AXM_PATH.stem + "_q4km.gguf")

if not LLAMACPP_DIR.is_dir():
    print("Cloning llama.cpp ...")
    subprocess.run(["git", "clone", "--depth", "1",
                    "https://github.com/ggerganov/llama.cpp",
                    str(LLAMACPP_DIR)], check=True)

if GGUF_PATH.exists():
    print(f"✓ GGUF already exists: {{GGUF_PATH}}")
else:
    print("Extracting AXM → GGUF Q4_K_M ...")
    t0 = time.time()
    r = subprocess.run([
        sys.executable, str(AXM_TO_GGUF),
        "--container", str(AXM_PATH),
        "--gguf-out",  str(GGUF_PATH),
        "--llamacpp",  str(LLAMACPP_DIR),
        "--quant",     "Q4_K_M",
    ], cwd=str(AXIOM_DIR))
    elapsed = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError(f"GGUF extraction failed")
    gb = GGUF_PATH.stat().st_size / 1024**3
    print(f"✓ {{GGUF_PATH.name}}  ({{gb:.2f}} GB)  {{elapsed/60:.1f}} min")
'''


# ── Export generators ─────────────────────────────────────────────────────────

def generate_colab_notebook(cfg: StudioConfig) -> bytes:
    model_slug = cfg.model_id.split("/")[-1].lower().replace(".", "-")
    has_extract = any(s.slot_type in {"text", "audio", "video", "physics"}
                      for s in cfg.slots) or True  # always offer GGUF

    cells = [
        _md_cell(f"""\
# Axiom SRD Container — {model_slug}

Generated by [Axiom SRD Studio](https://firewall.orivael.dev/dashboard/studio).

**Pipeline:** SRD α=0 (4.5 bpw) → signed `.axm` → GGUF Q4_K_M

**Model:** `{cfg.model_id}`

**Slots:** {", ".join(s.slot_type for s in cfg.slots) or "text only"}

Run cells top-to-bottom on a **Colab A100** or **T4 High-RAM** runtime.
"""),

        _code_cell(f"""\
# Cell 1 — Install dependencies
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
    "transformers>=4.45", "accelerate", "bitsandbytes",
    "huggingface_hub", "safetensors", "sentencepiece", "tqdm",
], check=True)
print("✓ packages ready")
"""),

        _code_cell(f"""\
# Cell 2 — Clone axiom repo + set AXIOM_MASTER_KEY
import os, secrets, subprocess, sys
from pathlib import Path

AXIOM_DIR  = Path("/content/axiom")
BRANCH     = "{_REPO_BRANCH}"
KEY_FILE   = Path("/content/axiom_master.key")

if not AXIOM_DIR.is_dir():
    subprocess.run(["git", "clone", "--depth", "1", "--branch", BRANCH,
                    "{_REPO_URL}", str(AXIOM_DIR)], check=True)
    print("✓ axiom cloned")
sys.path.insert(0, str(AXIOM_DIR))

if os.environ.get("AXIOM_MASTER_KEY"):
    print("AXIOM_MASTER_KEY: from environment")
elif KEY_FILE.is_file():
    os.environ["AXIOM_MASTER_KEY"] = KEY_FILE.read_text().strip()
    print(f"AXIOM_MASTER_KEY: restored from {{KEY_FILE}}")
else:
    key = secrets.token_hex(32)
    os.environ["AXIOM_MASTER_KEY"] = key
    KEY_FILE.write_text(key)
    print(f"AXIOM_MASTER_KEY: generated → {{KEY_FILE}}")
    print("  ⚠ back this up — required to verify the .axm later")
"""),

        _code_cell(_pack_cell_code(cfg, colab=True)),
        _code_cell(_verify_cell_code(cfg, colab=True)),
        _code_cell(_extract_cell_code(cfg, colab=True)),

        _code_cell("""\
# Cell 6 — Download files
from google.colab import files
files.download(str(AXM_PATH))
files.download(str(GGUF_PATH))
print("✓ Download triggered")
"""),
    ]

    nb = _nbformat(cells, colab=True)
    return json.dumps(nb, indent=1).encode()


def generate_jupyter_notebook(cfg: StudioConfig) -> bytes:
    model_slug = cfg.model_id.split("/")[-1].lower().replace(".", "-")

    cells = [
        _md_cell(f"""\
# Axiom SRD Container — {model_slug}

Generated by Axiom SRD Studio.

**Model:** `{cfg.model_id}`  |  **Slots:** {", ".join(s.slot_type for s in cfg.slots) or "text only"}

Run on a machine with a GPU and sufficient VRAM (≥14 GB for 7B models, ≥4 GB for 1B).
"""),

        _code_cell(f"""\
# Cell 1 — Install dependencies
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
    "transformers>=4.45", "accelerate", "bitsandbytes",
    "huggingface_hub", "safetensors", "sentencepiece", "tqdm",
], check=True)
print("✓ packages ready")
"""),

        _code_cell(f"""\
# Cell 2 — Clone axiom repo + set AXIOM_MASTER_KEY
import os, secrets, subprocess, sys
from pathlib import Path

AXIOM_DIR  = Path("/workspace/axiom")   # edit to your preferred path
BRANCH     = "{_REPO_BRANCH}"
KEY_FILE   = Path("/workspace/axiom_master.key")

if not AXIOM_DIR.is_dir():
    subprocess.run(["git", "clone", "--depth", "1", "--branch", BRANCH,
                    "{_REPO_URL}", str(AXIOM_DIR)], check=True)
    print("✓ axiom cloned")
sys.path.insert(0, str(AXIOM_DIR))

if os.environ.get("AXIOM_MASTER_KEY"):
    print("AXIOM_MASTER_KEY: from environment")
elif KEY_FILE.is_file():
    os.environ["AXIOM_MASTER_KEY"] = KEY_FILE.read_text().strip()
    print(f"AXIOM_MASTER_KEY: restored from {{KEY_FILE}}")
else:
    key = secrets.token_hex(32)
    os.environ["AXIOM_MASTER_KEY"] = key
    KEY_FILE.write_text(key)
    print(f"AXIOM_MASTER_KEY: generated → {{KEY_FILE}}")
    print("  ⚠ back this up — required to verify the .axm later")
"""),

        _code_cell(_pack_cell_code(cfg, colab=False)),
        _code_cell(_verify_cell_code(cfg, colab=False)),
        _code_cell(_extract_cell_code(cfg, colab=False)),

        _code_cell("""\
# Cell 6 — Summary
print(f"AXM:  {AXM_PATH}  ({AXM_PATH.stat().st_size/1024**3:.3f} GB)")
print(f"GGUF: {GGUF_PATH}  ({GGUF_PATH.stat().st_size/1024**3:.3f} GB)")
"""),
    ]

    nb = _nbformat(cells, colab=False)
    return json.dumps(nb, indent=1).encode()


def generate_python_script(cfg: StudioConfig) -> bytes:
    model_slug = cfg.model_id.split("/")[-1].lower().replace(".", "-")
    slot_lines = _slot_notes(cfg)

    script = f'''\
#!/usr/bin/env python3
"""
Axiom SRD Container packing script
Generated by Axiom SRD Studio — https://firewall.orivael.dev/dashboard/studio

Model: {cfg.model_id}
Slots: {", ".join(s.slot_type for s in cfg.slots) or "text only"}

Usage:
  python3 {model_slug}_srd_pack.py \\
      --output-dir /workspace/srd_output \\
      [--llamacpp  /workspace/llama.cpp]  \\
      [--skip-extract]                    \\
      [--smoke-test]
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

AXIOM_DIR  = Path("/workspace/axiom")   # change if axiom is elsewhere
BRANCH     = "{_REPO_BRANCH}"
REPO_URL   = "{_REPO_URL}"
MODEL_ID   = "{cfg.model_id}"
SHORT_NAME = "{model_slug}"

{slot_lines}


def _ensure_repo():
    if not AXIOM_DIR.is_dir():
        print(f"Cloning axiom (branch: {{BRANCH}}) ...")
        subprocess.run(["git", "clone", "--depth", "1", "--branch", BRANCH,
                        REPO_URL, str(AXIOM_DIR)], check=True)
    sys.path.insert(0, str(AXIOM_DIR))


def _ensure_master_key(output_dir: Path):
    key_file = output_dir / "axiom_master.key"
    if os.environ.get("AXIOM_MASTER_KEY"):
        return
    if key_file.is_file():
        os.environ["AXIOM_MASTER_KEY"] = key_file.read_text().strip()
        print(f"AXIOM_MASTER_KEY: restored from {{key_file}}")
    else:
        key = secrets.token_hex(32)
        os.environ["AXIOM_MASTER_KEY"] = key
        key_file.write_text(key)
        print(f"AXIOM_MASTER_KEY: generated → {{key_file}}")
        print("  ⚠ back this up — required to verify the .axm later")


def pack(output_dir: Path) -> Path:
    axm_path   = output_dir / f"{{SHORT_NAME}}_srd.axm"
    stats_json = output_dir / f"{{SHORT_NAME}}_pack_stats.json"

    if axm_path.exists():
        print(f"✓ AXM already exists: {{axm_path}}")
        return axm_path

    pack_script = AXIOM_DIR / "research" / "quant" / "pack_to_axm.py"
    print(f"Packing {{MODEL_ID}} ...")
    t0 = time.time()
    r = subprocess.run([
        sys.executable, str(pack_script),
        "--model",      MODEL_ID,
        "--output",     str(axm_path),
        "--top-k-pct",  "0.25",
        "--group-size", "64",
        "--stats-json", str(stats_json),
    ], cwd=str(AXIOM_DIR))
    elapsed = time.time() - t0

    if r.returncode != 0:
        sys.exit(f"Pack failed (rc={{r.returncode}})")
    gb = axm_path.stat().st_size / 1024**3
    print(f"✓ {{axm_path.name}}  ({{gb:.3f}} GB)  {{elapsed/60:.1f}} min")

    if stats_json.exists():
        s = json.loads(stats_json.read_text())
        print(f"  fingerprint: {{s.get('fingerprint')}}")
        print(f"  bpw:         {{s.get('bpw_theoretical')}}")

    return axm_path


def verify(axm_path: Path):
    axm_cli = AXIOM_DIR / "axm_cli.py"
    r = subprocess.run([sys.executable, str(axm_cli), "verify", str(axm_path)],
                       cwd=str(AXIOM_DIR), capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
    except Exception:
        data = {{"verified": False, "error": r.stdout[-300:]}}
    ok = data.get("verified", False)
    print(f"{{'✓ VERIFIED' if ok else '✗ FAILED'}}  fingerprint={{data.get('fingerprint','?')}}  "
          f"proofs={{data.get('proofs_checked','?')}}")
    if not ok:
        sys.exit(f"Verification failed: {{data.get('error')}}")


def extract(axm_path: Path, output_dir: Path, llamacpp_dir: Path):
    gguf_path = output_dir / (axm_path.stem + "_q4km.gguf")
    if gguf_path.exists():
        print(f"✓ GGUF exists: {{gguf_path}}")
        return gguf_path

    axm_to_gguf = AXIOM_DIR / "research" / "quant" / "axm_to_gguf.py"
    print("Extracting AXM → GGUF Q4_K_M ...")
    t0 = time.time()
    r = subprocess.run([
        sys.executable, str(axm_to_gguf),
        "--container", str(axm_path),
        "--gguf-out",  str(gguf_path),
        "--llamacpp",  str(llamacpp_dir),
        "--quant",     "Q4_K_M",
    ], cwd=str(AXIOM_DIR))
    elapsed = time.time() - t0
    if r.returncode != 0:
        sys.exit("GGUF extraction failed")
    gb = gguf_path.stat().st_size / 1024**3
    print(f"✓ {{gguf_path.name}}  ({{gb:.2f}} GB)  {{elapsed/60:.1f}} min")
    return gguf_path


def smoke_test(gguf_path: Path, llamacpp_dir: Path):
    cli = llamacpp_dir / "llama-cli"
    if not cli.is_file():
        cli = llamacpp_dir / "build" / "bin" / "llama-cli"
    if not cli.is_file():
        print("⚠ llama-cli not found — skipping smoke test")
        return
    r = subprocess.run([
        str(cli), "-m", str(gguf_path),
        "--n-gpu-layers", "99", "--ctx-size", "256",
        "--n-predict", "20", "--log-disable",
        "--prompt", "In one sentence, what is machine learning?",
    ], capture_output=True, text=True, timeout=120)
    print("Smoke test:", "✓ OK" if r.returncode == 0 else "✗ FAILED")
    if r.stdout.strip():
        print("  →", r.stdout.strip()[:200])


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", type=Path, default=Path("/workspace/srd_output"))
    p.add_argument("--llamacpp",   type=Path, default=Path("/workspace/llama.cpp"))
    p.add_argument("--skip-extract", action="store_true")
    p.add_argument("--smoke-test",   action="store_true")
    args = p.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _ensure_repo()
    _ensure_master_key(args.output_dir)

    axm_path = pack(args.output_dir)
    verify(axm_path)

    if not args.skip_extract:
        gguf_path = extract(axm_path, args.output_dir, args.llamacpp)
        if args.smoke_test:
            smoke_test(gguf_path, args.llamacpp)

    print()
    print(f"Done.  AXM → {{axm_path}}")


if __name__ == "__main__":
    main()
'''
    return script.encode()


def generate_json_config(cfg: StudioConfig) -> bytes:
    model_slug = cfg.model_id.split("/")[-1].lower().replace(".", "-")
    layers: list[dict] = []

    for s in cfg.slots:
        layer: dict = {"agent": s.slot_type, "confidence": 0.85,
                       "signature": "<HMAC-SHA256-computed-at-runtime>"}
        if s.slot_type == "text":
            layer["payload"] = {
                "phrase": f"[input text for {cfg.model_id}]",
                "intent_class": "INFORM",
                "confidence": 0.85,
                "signals": [],
            }
        elif s.slot_type == "audio":
            p = s.params
            layer["payload"] = {
                "impact_profile":    p.get("impact_profile", "sharp_transient"),
                "material_signature": p.get("material_signature", "glass-like"),
                "rhythm":            p.get("rhythm", "single_impact"),
                "depth":             float(p.get("depth", 0.5)),
                "width":             float(p.get("width", 0.5)),
            }
        elif s.slot_type == "video":
            p = s.params
            layer["payload"] = {
                "motion_class":    p.get("motion_class", "downward"),
                "impact_detected": bool(p.get("impact_detected", False)),
                "object_count":    int(p.get("object_count", 1)),
            }
        elif s.slot_type == "physics":
            p = s.params
            layer["payload"] = {
                "material":        p.get("material", "brittle_glass"),
                "surface":         p.get("surface", "hard_surface"),
                "depth_class":     p.get("depth_class", "near"),
                "material_response": p.get("material_response", "brittle_break"),
            }
        elif s.slot_type == "governance":
            layer["payload"] = {
                "verdict": "INFORM",
                "note": "computed from sibling layer reports at runtime",
            }
        layers.append(layer)

    config = {
        "axiom_studio_config": "1.0",
        "model_id":    cfg.model_id,
        "hardware_map": cfg.hardware_map,
        "quant_scheme": cfg.quant_scheme,
        "slots": [s.slot_type for s in cfg.slots],
        "event_token_skeleton": {
            "token_id": f"<uuid-at-runtime>",
            "coordinator_sig": "<HMAC-SHA256-computed-at-runtime>",
            "layers": layers,
        },
        "pack_command": (
            f"python3 research/quant/pack_to_axm.py "
            f"--model {cfg.model_id} "
            f"--output {model_slug}_srd.axm "
            f"--top-k-pct 0.25 --group-size 64"
        ),
        "verify_command": f"python3 axm_cli.py verify {model_slug}_srd.axm",
    }
    return json.dumps(config, indent=2).encode()
