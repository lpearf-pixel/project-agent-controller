#!/usr/bin/env bash
set -euo pipefail
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python >=3.11 is needed on i9." >&2
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python >=3.11 required"'
command -v git >/dev/null
command -v ssh >/dev/null
python3 -m unittest discover -s tests/dual_agent -v
echo "Local checks passed. Set MESH_M2_SSH=user@M2_IP and run scripts/connect-m2-agent.sh"
echo "After SSH tunnel connects, run python3 dual_agent.py doctor and python3 dual_agent.py smoke"
if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI is optional for analysis; install and log in before running with --code." >&2
fi
