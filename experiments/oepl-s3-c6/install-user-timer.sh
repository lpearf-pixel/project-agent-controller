#!/usr/bin/env bash
# Opt-in user-level monitoring; never installs privileged system services.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat >"$UNIT_DIR/pac-oepl-s3-c6.service" <<EOF
[Unit]
Description=PAC OpenEPaperLink ESP32-S3+C6 read-only inventory
[Service]
Type=oneshot
ExecStart=/usr/bin/bash $ROOT/experiments/oepl-s3-c6/run_on_ubuntu.sh --inspect
TimeoutStartSec=60
EOF
cat >"$UNIT_DIR/pac-oepl-s3-c6.timer" <<'EOF'
[Unit]
Description=PAC OpenEPaperLink read-only inventory each 30 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true
Unit=pac-oepl-s3-c6.service
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now pac-oepl-s3-c6.timer
systemctl --user start pac-oepl-s3-c6.service
systemctl --user --no-pager status pac-oepl-s3-c6.timer
echo "Read-only monitor installed; to disable:"
echo "systemctl --user disable --now pac-oepl-s3-c6.timer"
