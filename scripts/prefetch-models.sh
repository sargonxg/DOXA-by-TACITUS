#!/usr/bin/env bash
set -euo pipefail
# This is an explicit model download, not an inference or cloud deployment command.
model="${1:-qwen3.5:4b}"
if command -v ollama >/dev/null 2>&1; then
  exec ollama pull "$model"
fi
if command -v docker >/dev/null 2>&1; then
  exec docker compose --profile ollama exec ollama ollama pull "$model"
fi
printf '%s\n' 'Install native Ollama, or start docker compose --profile ollama up -d first.' >&2
exit 1
