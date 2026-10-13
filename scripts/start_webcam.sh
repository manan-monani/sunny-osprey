#!/usr/bin/env bash
# ── Sunny Osprey — Webcam Edition Startup Script ────────────────────────────
# Starts Docker services (Gemma 4 LLM + Dashboard) and the native webcam pipeline.
#
# Usage:
#   bash scripts/start_webcam.sh            # Start everything
#   bash scripts/start_webcam.sh --no-open  # Don't open browser automatically

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.webcam.yaml"
OPEN_BROWSER=true

# Parse args
for arg in "$@"; do
  case $arg in
    --no-open) OPEN_BROWSER=false ;;
  esac
done

echo ""
echo "🦅 ═══════════════════════════════════════════════════════"
echo "   Sunny Osprey — AI Security Monitor (Webcam Edition)"
echo "   Hardware: RTX 2050 4GB VRAM | Model: Gemma 4 E2B (llama.cpp)"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Step 1: Start Docker services
echo "🐳 Starting Docker services (Gemma 4 LLM + Dashboard)..."
docker compose -f "$COMPOSE_FILE" up -d llm dashboard   # capture runs natively below

echo ""
echo "⏳ Waiting for the LLM server to load Gemma 4..."
echo "   (Model files come from: docker compose -f docker-compose.webcam.yaml --profile setup run --rm model-prep)"
echo ""

# Wait for the LLM server to be ready
MAX_WAIT=300
ELAPSED=0
while ! curl -sf http://localhost:8081/health > /dev/null 2>&1; do
  if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "❌ LLM server did not start within ${MAX_WAIT}s. Check logs:"
    echo "   docker compose -f $COMPOSE_FILE logs llm"
    exit 1
  fi
  printf "."
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done
echo ""
echo "✅ LLM server is ready!"

# Wait for dashboard
echo "⏳ Waiting for dashboard..."
ELAPSED=0
while ! curl -sf http://localhost:8080/api/health > /dev/null 2>&1; do
  if [ $ELAPSED -ge 60 ]; then
    echo "⚠️  Dashboard slow to start, continuing anyway..."
    break
  fi
  printf "."
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done
echo ""
echo "✅ Dashboard ready at http://localhost:8080"

# Open browser
if $OPEN_BROWSER; then
  if command -v xdg-open > /dev/null; then
    xdg-open http://localhost:8080
  elif command -v open > /dev/null; then
    open http://localhost:8080
  elif command -v start > /dev/null; then
    start http://localhost:8080
  fi
fi

# Step 2: Install native requirements if needed
echo ""
echo "📦 Checking native Python dependencies..."
pip show opencv-python > /dev/null 2>&1 || pip install -r "$PROJECT_DIR/requirements-native.txt"

# Step 3: Start webcam pipeline
echo ""
echo "🎥 Starting webcam pipeline..."
echo "   Press Ctrl+C to stop | Press 'q' in preview window to quit"
echo "   Press 'r' to reset background model"
echo ""

cd "$PROJECT_DIR"
python src/sunny_osprey/webcam_capture.py --config sunny-osprey-config.yaml "$@"
