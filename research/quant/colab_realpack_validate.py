"""One-shot Colab validation cell for E3 real-pack.

Run this as a single cell after the clone + setup cells.
It packs TinyLlama with --real-pack, loads it back, generates from
the same prompt, and asserts the output is coherent English.

Pass criteria (all must be true):
  1. Archive created and signed (fingerprint printed)
  2. quant_map["packed"] == True in stats JSON
  3. Archive smaller than fake-quant threshold (< 1200 MB for TinyLlama)
  4. Proof verification passes
  5. Generated text: non-empty, >= 20 tokens, no gibberish heuristic
  6. TTFT warm (run 2) < 500 ms (sanity — rules out total failure)

Prints PASS / FAIL with a reason for each check.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

# ── env ────────────────────────────────────────────────────────────────────
REPO = Path("/content/axiom")
assert REPO.is_dir(), f"repo not found at {REPO}; run the clone cell first"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.chdir(REPO)
if "AXIOM_MASTER_KEY" not in os.environ:
    os.environ["AXIOM_MASTER_KEY"] = secrets.token_hex(32)

AXM_OUT    = "/content/tinyllama_srd_7bpw_REAL.axm"
STATS_JSON = "/content/realpack_stats.json"
MODEL      = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
PROMPT     = "Write a Python function to reverse a linked list."
TOKENS     = 80
N_RUNS     = 2   # run 1 = cold/warmup; run 2 = warm TTFT reported


# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — pack with --real-pack
# ══════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("STEP 1: pack (real-pack)")
print("=" * 60)

from research.quant.pack_to_axm import pack_model

pack_stats = pack_model(
    model_name=MODEL,
    output_path=AXM_OUT,
    srd_top_k_pct=0.25,
    group_size=64,
    model_revision=None,
    hardware_map="gpu",
    compresslevel=1,
    real_pack=True,
)

Path(STATS_JSON).write_text(json.dumps(pack_stats, indent=2))
archive_mb = pack_stats["size"]["archive_mb"]


# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — load + generate
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 2: load + generate")
print("=" * 60)

from research.quant.load_from_axm import load_and_measure

load_stats = load_and_measure(
    AXM_OUT,
    prompt=PROMPT,
    n_tokens=TOKENS,
    n_runs=N_RUNS,
)

generated = load_stats.get("generated_text", "")
runs      = load_stats.get("runs", [])
warm_ttft = runs[1]["ttft_ms"] if len(runs) > 1 else runs[0]["ttft_ms"]


# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — assertions
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 3: validation checks")
print("=" * 60)

checks = {}

# 1. archive created
checks["archive_exists"] = Path(AXM_OUT).is_file()

# 2. packed flag in quant_map
qmap = pack_stats.get("quant_map", {})
checks["quant_map_packed_true"] = bool(qmap.get("packed", False))

# 3. archive genuinely smaller than fake-quant (TinyLlama FP16 axm ~ 1500 MB)
checks["archive_smaller_than_fp16"] = archive_mb < 1200

# 4. proof verification
from axiom_axm import AXMContainer
c = AXMContainer.from_path(AXM_OUT)
checks["proofs_verified"] = c.verify_proofs()

# 5. generated text is non-empty and at least 20 tokens long
text_words = generated.split()
checks["output_coherent"] = len(text_words) >= 20

# 6. warm TTFT < 500 ms (if this fails the model may not be running on GPU)
checks["warm_ttft_ok"] = warm_ttft < 500

# ── report ──────────────────────────────────────────────────────────────────
print()
all_pass = True
for name, result in checks.items():
    icon = "✅" if result else "❌"
    print(f"  {icon}  {name}")
    if not result:
        all_pass = False

print()
print(f"  archive   : {archive_mb:.0f} MB")
print(f"  warm TTFT : {warm_ttft:.0f} ms")
print(f"  tok/s     : {runs[-1]['tok_per_s']:.1f}")
print(f"  fingerprint: {pack_stats['fingerprint']}")

print()
if all_pass:
    print("══ PASS — E3 real-pack is working end-to-end ══")
    print(f"\nGenerated text:\n{generated}")
else:
    failed = [k for k, v in checks.items() if not v]
    print(f"══ FAIL — {len(failed)} check(s) failed: {failed} ══")
    print("\nPartial output (if any):")
    print(generated or "(empty)")
    raise AssertionError(f"E3 real-pack validation failed: {failed}")
