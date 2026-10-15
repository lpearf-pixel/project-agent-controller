#!/usr/bin/env bash
# Ubuntu 24.04: install distribution bubblewrap and the narrow AppArmor
# profile recommended by OpenAI Codex's Linux sandbox documentation.
# Explicit operator action; never disables the host-wide AppArmor restriction.
set -euo pipefail
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "ERROR: this helper is intended only for Ubuntu 24.04. Found ${PRETTY_NAME:-unknown}." >&2
  exit 2
fi

echo "[codex-sandbox] Current CLI: $(codex --version)"
echo "[codex-sandbox] Current bwrap: $(command -v bwrap || echo not-installed)"
echo "[codex-sandbox] No global sysctl, sudo Codex, sandbox bypass or AppArmor disabling."
echo "[codex-sandbox] Installing Ubuntu bubblewrap and the scoped AppArmor profile; sudo may prompt."
sudo apt-get update
sudo apt-get install -y bubblewrap apparmor-profiles apparmor-utils
profile=/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
if [[ ! -f "$profile" ]]; then
  echo "ERROR: official profile not found at $profile. Stop; do not disable AppArmor globally." >&2
  exit 2
fi
sudo install -m 0644 "$profile" /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
echo "[codex-sandbox] Profile installed. Verifying sandbox command on shared mount."
shared_root="${PAC_CODEX_SHARED_ROOT:-/media/psf/chatgpt}"
test -d "$shared_root" || { echo "ERROR: shared directory missing: $shared_root" >&2; exit 2; }
if codex sandbox linux --help >/dev/null 2>&1; then
  ( cd "$shared_root" && codex sandbox linux -- /usr/bin/true ) || {
    echo "ERROR: Codex sandbox still unavailable on Parallels shared mount; keep existing safety restrictions." >&2
    exit 1
  }
else
  echo "NOTE: Codex CLI does not expose the standalone sandbox probe; use the real disposable-worktree smoke next."
fi
echo "[codex-sandbox] Setup finished without weakening global AppArmor policy."
