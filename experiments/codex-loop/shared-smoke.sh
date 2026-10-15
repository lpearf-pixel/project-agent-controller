#!/usr/bin/env bash
# M3 Parallels smoke test: source repo, disposable code repo and output worktree
# all live on the shared mount. Controller state/locks remain Ubuntu-local.
set -euo pipefail

source_root="$(cd "$(dirname "$0")/../.." && pwd -P)"
shared_root="${PAC_CODEX_SHARED_ROOT:-$(dirname "$source_root")}"
case "$shared_root" in
  /media/psf/*) ;;
  *) echo "ERROR: expected shared root under /media/psf; set PAC_CODEX_SHARED_ROOT" >&2; exit 2 ;;
esac
test -w "$shared_root" || { echo "ERROR: shared root not writable: $shared_root" >&2; exit 2; }
command -v git >/dev/null || { echo "ERROR: git not installed" >&2; exit 2; }
command -v codex >/dev/null || { echo "ERROR: Codex CLI not installed" >&2; exit 2; }
python3 "$source_root/experiments/codex-loop/loop.py" doctor
# The standalone sandbox probe runs no model calls and avoids burning a Codex task
# when Ubuntu AppArmor/bubblewrap cannot create required namespaces.
if codex sandbox linux --help >/dev/null 2>&1; then
  echo "[codex-loop] Preflighting Linux sandbox on the Parallels shared mount..."
  if ! (cd "$shared_root" && codex sandbox linux -- /usr/bin/true); then
    echo "ERROR: Linux sandbox failed before any Codex model call." >&2
    echo "Run: bash experiments/codex-loop/repair-ubuntu24-sandbox.sh" >&2
    exit 2
  fi
fi
code_root="$shared_root/codex-worktrees"
mkdir -p "$code_root"
sandbox="$(mktemp -d "$shared_root/orch-sandbox-XXXXXXXX")"
echo "Disposable test repository: $sandbox"
echo "Generated code worktrees: $code_root"
git -C "$sandbox" init -b main
git -C "$sandbox" config user.name "Local Pilot"
git -C "$sandbox" config user.email "pilot@example.invalid"
printf 'Initial project\n' > "$sandbox/README.md"
git -C "$sandbox" add README.md
git -C "$sandbox" commit -m "init"
printf 'Create hello.py that prints hello when executed. Do not modify any other files.\n' > "$sandbox/task.md"
echo "[codex-loop] Authentication ready. Starting bounded real Codex smoke (max 180s per attempt)."
python3 "$source_root/experiments/codex-loop/loop.py" run \
  --repo "$sandbox" \
  --task "$sandbox/task.md" \
  --worktree-root "$code_root" \
  --check "python3 hello.py" \
  --codex-timeout 180 \
  --check-timeout 30 \
  --repairs 1
echo "Shared worktrees: $code_root"
echo "Task state: $HOME/.local/share/pac-codex-loop/runs"
echo "No GitHub push was attempted."
