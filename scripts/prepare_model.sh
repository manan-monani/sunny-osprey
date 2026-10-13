#!/usr/bin/env sh
# Downloads the Gemma 4 E2B files served by the llama.cpp "llm" container.
# Runs inside python:3.12-slim (see the model-prep service in docker-compose.webcam.yaml).
#
# Gemma 4 E2B is the smallest Gemma 4 model and is ungated (no Hugging Face token needed).
#   - text model: Google's official quantization-aware-trained (QAT) Q4_0 GGUF
#   - vision projector: llama.cpp's official Q8_0 mmproj (0.56 GB vs 0.99 GB BF16, matters on 4 GB GPUs)
set -eu

OUT_DIR=/models/gguf
MODEL_REPO=google/gemma-4-E2B-it-qat-q4_0-gguf
MODEL_REV=675cff42a74c774d6cb76f76d8eacb49b48c9b93
MODEL_FILE=gemma-4-E2B_q4_0-it.gguf
MMPROJ_REPO=ggml-org/gemma-4-E2B-it-GGUF
MMPROJ_REV=b4243c156154b6dca9324415f8c7ccc098b4aed1
MMPROJ_FILE=mmproj-gemma-4-E2B-it-Q8_0.gguf

mkdir -p "$OUT_DIR"
pip install -q 'huggingface_hub[hf_xet]'

[ -f "$OUT_DIR/$MODEL_FILE" ] || hf download "$MODEL_REPO" "$MODEL_FILE" --revision "$MODEL_REV" --local-dir "$OUT_DIR"
[ -f "$OUT_DIR/$MMPROJ_FILE" ] || hf download "$MMPROJ_REPO" "$MMPROJ_FILE" --revision "$MMPROJ_REV" --local-dir "$OUT_DIR"

echo "==> Done"
ls -lh "$OUT_DIR"/*.gguf
