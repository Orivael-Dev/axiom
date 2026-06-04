#!/usr/bin/env bash
# OOM-safe llama.cpp launcher for Orin Nano (5.5 GB unified memory).
#
# Reads /proc/meminfo, subtracts model footprint, picks the highest config
# that fits without OOM, then exec's llama-cli with --flash-attn and
# quantized KV cache.
#
# Usage:
#   bash orin_safe_launch.sh <gguf> <llama-cli> "<prompt>"
#
#   bash ~/axiom/research/quant/orin_safe_launch.sh \
#       /mnt/nvme/models/mistral_srd4_q4km.gguf \
#       ~/llama.cpp/build/bin/llama-cli \
#       "Explain edge AI in one paragraph:"
#
# Environment overrides (skip auto-select):
#   ORIN_NGL=32  ORIN_CTX=8192  ORIN_CTK=q8_0  bash orin_safe_launch.sh ...

set -euo pipefail

GGUF="${1:-/mnt/nvme/models/mistral_srd4_q4km.gguf}"
LLAMA="${2:-${HOME}/llama.cpp/build/bin/llama-cli}"
PROMPT="${3:-Explain edge AI in one paragraph:}"
N_PREDICT="${ORIN_N_PREDICT:-256}"

# ── sanity checks ─────────────────────────────────────────────────────────────
if [ ! -f "$GGUF" ]; then
    echo "ERROR: GGUF not found: $GGUF" >&2
    exit 1
fi
if [ ! -x "$LLAMA" ]; then
    echo "ERROR: llama-cli not found or not executable: $LLAMA" >&2
    echo "  Build it first: cmake --build ~/llama.cpp/build -j4 --target llama-cli" >&2
    exit 1
fi

# ── memory math ──────────────────────────────────────────────────────────────
# Use MemAvailable (kernel's estimate of allocatable memory without swapping)
FREE_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
# Q4_K_M 7B ~ 4.07 GB on disk; mmap loads ~4.2 GB into page cache at full ctx
MODEL_MB=4200
HEADROOM=$((FREE_MB - MODEL_MB))

echo "=== Orin Safe Launch ==="
printf "  Free RAM  :  %d MB\n" "$FREE_MB"
printf "  Model est :  %d MB\n" "$MODEL_MB"
printf "  Headroom  :  %d MB\n" "$HEADROOM"

# KV memory per token (Mistral-7B):
#   f16   = 32 layers × 2 (K+V) × 8 heads × 128 dim × 2 bytes = 128 KB/token
#   q8_0  = 64 KB/token   (~8K ctx → 512 MB,  ~16K ctx → 1024 MB)
#   q4_0  = 32 KB/token   (~8K ctx → 256 MB,  ~32K ctx → 1024 MB)
#
# Tiers leave ~250 MB safety margin above estimated KV usage.

# Allow env overrides for testing / manual tuning
if [ -n "${ORIN_NGL:-}" ] && [ -n "${ORIN_CTX:-}" ] && [ -n "${ORIN_CTK:-}" ]; then
    NGL="$ORIN_NGL"
    CTX="$ORIN_CTX"
    CTK="$ORIN_CTK"
    label="manual  (ngl=${NGL}  ${CTK}-KV  ctx=${CTX})"
elif [ "$HEADROOM" -ge 1400 ]; then
    # 1.4 GB free → f16 KV at 8K = 1024 MB, leaves ~376 MB margin
    NGL=32; CTX=8192;  CTK=f16;  label="A-full  (ngl=32  f16-KV  ctx=8K)"
elif [ "$HEADROOM" -ge 900 ]; then
    # 900 MB free → q8 KV at 16K = ~1024 MB (just over — use q8 headroom more carefully)
    # Actually q8 at 13K = ~832 MB, leaving ~68 MB — too tight. Use 12K.
    NGL=32; CTX=12000; CTK=q8_0; label="A-q8    (ngl=32  q8-KV   ctx=12K)"
elif [ "$HEADROOM" -ge 500 ]; then
    # 500 MB free → q8 KV at 6K = ~384 MB, leaves ~116 MB margin
    NGL=32; CTX=6144;  CTK=q8_0; label="A-q8-safe (ngl=32  q8-KV  ctx=6K)"
elif [ "$HEADROOM" -ge 200 ]; then
    # 200 MB free → q4 KV at 4K = ~128 MB, leaves ~72 MB margin
    NGL=22; CTX=4096;  CTK=q4_0; label="B-q4    (ngl=22  q4-KV   ctx=4K)"
else
    # Very tight — minimal config
    NGL=16; CTX=2048;  CTK=q4_0; label="minimal (ngl=16  q4-KV   ctx=2K)"
    echo ""
    echo "  WARNING: Only ${HEADROOM} MB headroom after model load."
    echo "  Consider: sudo kill \$(pidof <other-process>) or adding NVMe swap."
    echo "  NVMe swap setup:"
    echo "    sudo fallocate -l 8G /mnt/nvme/swapfile && sudo chmod 600 /mnt/nvme/swapfile"
    echo "    sudo mkswap /mnt/nvme/swapfile && sudo swapon /mnt/nvme/swapfile"
fi

printf "  Config    :  %s\n" "$label"
printf "  n-predict :  %s tokens\n" "$N_PREDICT"
echo ""

exec "$LLAMA" \
    -m        "$GGUF"   \
    --ngl      "$NGL"   \
    --ctx-size "$CTX"   \
    -ctk       "$CTK"   \
    -ctv       "$CTK"   \
    --flash-attn        \
    --n-predict "$N_PREDICT" \
    --prompt   "$PROMPT"
