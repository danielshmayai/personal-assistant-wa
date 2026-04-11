#!/usr/bin/env bash
# Pull the Gemma 3 4B QAT model into Ollama.
# Run AFTER `docker compose up -d` has started the ollama container.
#
# Gemma 3 4B QAT: ~2.5GB download, ~3GB VRAM at runtime.
# QAT = Quantization-Aware Trained (not post-hoc) — preserves BF16-level quality.
#
# Usage: bash scripts/pull_model.sh

set -euo pipefail

CONTAINER="pa-ollama"
MODEL="gemma3:4b-it-qat"

echo ">>> Waiting for Ollama API..."
until docker exec "$CONTAINER" curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
  sleep 2
  echo "    ...still waiting"
done
echo ">>> Ollama API is up."

echo ">>> Pulling $MODEL (this may take a few minutes on first run)..."
docker exec "$CONTAINER" ollama pull "$MODEL"

echo ">>> Verifying model is available..."
docker exec "$CONTAINER" ollama list

echo ">>> Quick smoke test (expect a short response)..."
docker exec "$CONTAINER" ollama run "$MODEL" "Reply with exactly: OK" --verbose 2>&1 | head -20

echo ">>> Done. Model $MODEL is ready."
