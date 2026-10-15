# Codex Loop pilot for M3 Parallels Ubuntu

This experimental sidecar is not connected to PAC's existing read-only observer or v0.2 controlled runner. It does not auto-start, merge, deploy, or read Baby Monitor runtime data. It is for trusted test repositories only.

## Install / update — actual shared folder on M3

Your **existing source checkout** is `/media/psf/chatgpt/project-agent-controller-codex-pilot`; do not clone to `~/projects` or create a second PAC checkout.

```bash
cd /media/psf/chatgpt/project-agent-controller-codex-pilot
git status --short
git pull --ff-only origin feat/codex-loop-v0-20261015
bash experiments/codex-loop/bootstrap.sh
```

If the worktree has uncommitted changes or Git refuses to pull, inspect the problem rather than running `git reset --hard`. Codex CLI login: `codex login` only if not already authenticated.

The **only** expected shared root is `/media/psf/chatgpt/`:

```text
/media/psf/chatgpt/
  project-agent-controller-codex-pilot/   # versioned orchestrator source
  orch-sandbox/                           # disposable example repository
  codex-worktrees/                        # generated code; visible on macOS & Ubuntu
```

Task state, logs and the process lock stay on Ubuntu-native `~/.local/share/pac-codex-loop` to avoid relying on the Parallels shared filesystem for POSIX flock semantics. The `--worktree-root` flag can override the generated code location.

## Ubuntu 24.04 Linux sandbox fails before writing files

A reproduced Parallels VM task `8f1d67f6278f` reported `network-namespace error` and repeated `file_change failed`, despite `codex login status` being healthy and the shared Git worktree being created. This is a **sandbox infrastructure** issue, not a Python coding problem. Per OpenAI's official Linux sandbox prerequisite docs, use the distribution `bubblewrap` package and a scoped `bwrap-userns-restrict` AppArmor profile.

After reviewing what the helper does, run the Ubuntu-24.04-only repair script **once**. It uses sudo for apt and to load a bwrap-specific AppArmor profile, without disabling global AppArmor user namespace restrictions or starting Codex as root.

```bash
cd /media/psf/chatgpt/project-agent-controller-codex-pilot
bash experiments/codex-loop/repair-ubuntu24-sandbox.sh
```

On success, retry only the disposable shared-folder smoke:

```bash
bash experiments/codex-loop/shared-smoke.sh
```

If the standalone Codex sandbox test still fails after installing the profile, **stop**. Preserve the failure output for review rather than adding `--dangerously-bypass-approvals-and-sandbox`, `danger-full-access`, `sysctl kernel.apparmor_restrict_unprivileged_userns=0`, a setuid bwrap binary, or a root Codex process. More isolation options can be evaluated separately. The controller now detects Codex JSONL file-change/sandbox failures and blocks the task immediately instead of consuming repair attempts.

Reference: https://developers.openai.com/codex/concepts/sandboxing

## Recommended: one-command shared-folder smoke test

Run from the existing checkout after `git pull --ff-only`:

```bash
bash experiments/codex-loop/shared-smoke.sh
```

This creates a uniquely named disposable `orch-sandbox-XXXXXXXX` Git repository inside `/media/psf/chatgpt/`, invokes real Codex once against it, and saves generated code under `/media/psf/chatgpt/codex-worktrees/<task_id>/`. It does not publish. Source code and generated code are visible on both operating systems; the task state and controller lock intentionally remain on Ubuntu ext4. **The shared mount's actual Git semantics must pass the smoke test before wider use.**

## Minimal dry run — shared source AND generated code

```bash
ROOT=/media/psf/chatgpt
mkdir -p "$ROOT/orch-sandbox"
cd "$ROOT/orch-sandbox"
git init -b main
git config user.name 'Local Pilot'
git config user.email 'pilot@example.invalid'
printf 'start\n' > README.md
git add README.md && git commit -m init
printf 'Write a Python file hello.py that prints hello.\n' > task.md

cd "$ROOT/project-agent-controller-codex-pilot"
python3 experiments/codex-loop/loop.py run \
  --repo "$ROOT/orch-sandbox" \
  --task "$ROOT/orch-sandbox/task.md" \
  --worktree-root "$ROOT/codex-worktrees" \
  --check 'python3 hello.py' \
  --repairs 2
```

The original `orch-sandbox` branch remains untouched. The generated `hello.py` is found in `/media/psf/chatgpt/codex-worktrees/<task_id>/hello.py` and is visible from the Mac as well.

Before real projects, verify the Parallels mount supports `git worktree add`, executable permissions, and Git operations; the Mac/VM shared path is **not** equivalent to native ext4. Use trusted disposable test projects only.

The original checkout is not modified; a separate `auto/codex-*` worktree is created. The default is **no push**. `--publish` (trusted repos only) attempts new-branch push + Draft PR; never auto-merges. No CI polling, crash recovery, credential isolation, or scheduled background loop exists in this pilot. Do not use unattended on your real baby monitor.

See [audit](../../docs/architecture/codex-loop-m3-pilot-audit-20261015.md).
Note: a test that creates bytecode caches or other generated artifacts must first configure the repository's `.gitignore`; the pilot fails closed on `.pyc`, secrets, and symlink changes. `--publish` is not recommended until the separate-publisher security gate is complete.
