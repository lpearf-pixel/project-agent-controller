#!/usr/bin/env bash
set -euo pipefail
if ! command -v ollama >/dev/null 2>&1; then
  echo "Install Ollama on M2 first: https://ollama.com/download/mac" >&2
  exit 1
fi
if ! curl -fsS --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "Start the existing Ollama app/service on M2; localhost:11434 must answer." >&2
  exit 1
fi
MESH_MODEL="${MESH_MODEL:-qwen2.5-coder:7b}"
if ! ollama list | awk 'NR>1 {print $1}' | grep -Fx "$MESH_MODEL" >/dev/null; then
  echo "Missing model $MESH_MODEL; run: ollama pull $MESH_MODEL" >&2
  exit 1
fi
echo "M2 ready: local Ollama and $MESH_MODEL. Keep SSH Remote Login enabled for i9."
