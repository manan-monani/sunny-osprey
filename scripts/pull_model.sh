#!/bin/sh
# Pull the Gemma 3n E2B model if not already present
# Uses Q4_K_M quantization by default (fits in 4GB VRAM)
MODEL_NAME="${OLLAMA_MODEL:-gemma3n:e2b}"

echo "=== Gazeguard - Ollama Model Setup ==="
echo "Checking for model: $MODEL_NAME"

# Check if model already exists
if ollama list | grep -q "$MODEL_NAME"; then
    echo "✅ Model '$MODEL_NAME' already present, skipping download."
else
    echo "📥 Pulling model '$MODEL_NAME' (this may take a while on first run)..."
    ollama pull "$MODEL_NAME"
    if [ $? -eq 0 ]; then
        echo "✅ Model '$MODEL_NAME' pulled successfully."
    else
        echo "❌ Failed to pull model '$MODEL_NAME'."
        exit 1
    fi
fi

echo "🚀 Ollama is ready with model: $MODEL_NAME"
# Keep container alive (ollama serve runs in background)
tail -f /dev/null
