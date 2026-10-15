#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
echo "[1/3] Offline test suite"
python3 -m unittest discover -s experiments/codex-loop/tests -v
echo "[2/3] Codex CLI/GitHub login doctor"
python3 experiments/codex-loop/loop.py doctor
echo "[3/3] Ready for disposable-repo smoke test"
printf 'Instructions: %s\n' "$ROOT/experiments/codex-loop/README.md"
printf 'No Codex work, push, device action or deployment was executed by this bootstrap.\n'
