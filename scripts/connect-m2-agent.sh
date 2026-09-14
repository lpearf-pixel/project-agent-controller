#!/usr/bin/env bash
set -euo pipefail
: "${MESH_M2_SSH:?Set MESH_M2_SSH=user@M2_IP (M2 must enable Remote Login)}"
MESH_TUNNEL_PORT="${MESH_TUNNEL_PORT:-11435}"
if curl -fsS --max-time 2 "http://127.0.0.1:$MESH_TUNNEL_PORT/api/tags" >/dev/null 2>&1; then
  echo "Local tunnel already responds on $MESH_TUNNEL_PORT"
  exit 0
fi
ssh -fN -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -L "127.0.0.1:$MESH_TUNNEL_PORT:127.0.0.1:11434" "$MESH_M2_SSH"
curl -fsS --max-time 10 "http://127.0.0.1:$MESH_TUNNEL_PORT/api/tags" >/dev/null
echo "SSH tunnel ready: 127.0.0.1:$MESH_TUNNEL_PORT -> M2 Ollama"
