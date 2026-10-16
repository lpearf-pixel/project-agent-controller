#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd -P)"
DATA="${PAC_CODEX_LOOP_DATA:-$HOME/.local/share/pac-codex-loop}"
if [[ "${1:-}" != "--dry-run" && "${1:-}" != "--install" ]]; then
  echo "Refusing to install before dry-run. Run: $0 --dry-run" >&2
  exit 2
fi
PYTHON="${PAC_PYTHON:-python3}"
if ! PAC_CODEX_LOOP_DATA="$DATA" PYTHONPATH="$ROOT/experiments/codex-loop/runner" PAC_RUNNER_INSTALL_DRY_RUN=1 "$PYTHON" \
  "$ROOT/experiments/codex-loop/runner/cli.py" doctor
then
  echo "Isolation doctor failed; timer remains disabled." >&2
  exit 2
fi
if [[ "$1" == "--dry-run" ]]; then
  cat <<EOF
DRY_RUN_OK
Would render $HOME/.config/systemd/user/pac-codex-runner.service and .timer.
The timer is not enabled. Dedicated pac-codex and pac-publisher accounts plus
separate credentials must be provisioned and rechecked before installation.
EOF
  exit 0
fi
mkdir -p "$HOME/.config/systemd/user"
sed -e "s#@ROOT@#$ROOT#g" -e "s#@DATA@#$DATA#g" \
  "$ROOT/experiments/codex-loop/runner/systemd/pac-codex-runner.service.in" \
  > "$HOME/.config/systemd/user/pac-codex-runner.service"
sed -e "s#@ROOT@#$ROOT#g" -e "s#@DATA@#$DATA#g" \
  "$ROOT/experiments/codex-loop/runner/systemd/pac-codex-runner.timer.in" \
  > "$HOME/.config/systemd/user/pac-codex-runner.timer"
chmod 600 "$HOME/.config/systemd/user/pac-codex-runner.service" "$HOME/.config/systemd/user/pac-codex-runner.timer"
systemctl --user daemon-reload
systemctl --user enable pac-codex-runner.timer
echo "Installed but not started; review isolation and service status before starting."
