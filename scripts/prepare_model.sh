#!/usr/bin/env bash
# Builds the Gemma 3n E2B GGUF files served by the llama.cpp "llm" container (the default model).
# Runs inside ghcr.io/ggml-org/llama.cpp:full-cuda (see the model-prep service in docker-compose.webcam.yaml).
# On the project's test clips Gemma 3n E2B scored 6/7 vs 4/7 for Gemma 4 E2B (scripts/prepare_gemma4.sh).
#
# Gemma 3n image input needs GGUFs produced by a llama.cpp converter that includes the MobileNetV5
# vision encoder (llama.cpp PR #18256, Jan 2026); older community GGUFs are text-only. So we convert
# the original weights ourselves rather than downloading a prebuilt GGUF.
set -euo pipefail

HF_REPO="${HF_REPO:-unsloth/gemma-3n-E2B-it}"   # ungated mirror of google/gemma-3n-E2B-it
HF_REVISION="${HF_REVISION:-c4291af6285b4f90d88bdf6f9a634a8d2f262cc8}"
QUANT="${QUANT:-Q4_K_M}"
HF_DIR=/models/hf/gemma-3n-E2B-it
OUT_DIR=/models/gguf
BF16="$OUT_DIR/gemma-3n-E2B-it-bf16.gguf"   # bf16 = source dtype; avoids an extra 4 GB f16 copy (OOM in 8 GB Docker VMs)
QUANTIZED="$OUT_DIR/gemma-3n-E2B-it-$QUANT.gguf"
MMPROJ="$OUT_DIR/mmproj-gemma-3n-E2B-it-f16.gguf"

mkdir -p "$OUT_DIR"

if [ ! -f "$HF_DIR/model-00003-of-00003.safetensors" ]; then
    echo "==> Downloading $HF_REPO@$HF_REVISION"
    pip install -q --break-system-packages 'huggingface_hub[hf_xet]' 2>/dev/null || pip install -q 'huggingface_hub[hf_xet]'
    hf download "$HF_REPO" --revision "$HF_REVISION" --local-dir "$HF_DIR"
fi

if [ ! -f "$MMPROJ" ]; then
    echo "==> Converting vision projector (MobileNetV5, audio encoder dropped) -> $MMPROJ"
    python3 "$(dirname "$0")/convert_mmproj_vision_only.py" "$HF_DIR" --mmproj --outtype f16 --outfile "$MMPROJ"
fi

if [ ! -f "$QUANTIZED" ]; then
    if [ ! -f "$BF16" ]; then
        echo "==> Converting text model -> $BF16"
        python3 "$(dirname "$0")/convert_text_lowmem.py" "$HF_DIR" --outtype bf16 --outfile "$BF16"
    fi
    echo "==> Quantizing to $QUANT -> $QUANTIZED"
    /app/llama-quantize "$BF16" "$QUANTIZED" "$QUANT"
    rm -f "$BF16"
fi

echo "==> Done"
ls -lh "$OUT_DIR"
