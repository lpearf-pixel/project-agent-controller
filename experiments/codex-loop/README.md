# Codex Loop pilot for M3 Parallels Ubuntu

This experimental sidecar is not connected to PAC's existing read-only observer or v0.2 controlled runner. It does not auto-start, merge, deploy, or read Baby Monitor runtime data. It is for trusted test repositories only.

## Install

```bash
mkdir -p ~/projects && cd ~/projects
git clone -b feat/codex-loop-v0-20261015 https://github.com/lpearf-pixel/project-agent-controller.git
cd project-agent-controller
bash experiments/codex-loop/bootstrap.sh
```

Install current Codex CLI with `npm install -g @openai/codex` (if absent), then `codex login`. Install `gh` and `gh auth login` only if an explicitly approved draft PR is desired. On M3 Parallels Ubuntu, localhost is not the Mac localhost: verify Mac HTTP proxy port 11285 or SOCKS 1284 on an address reachable from the VM, and restrict exposure.

## Minimal dry run

```bash
mkdir -p ~/projects/orch-sandbox && cd ~/projects/orch-sandbox
git init -b main
git config user.name 'Local Pilot'
git config user.email 'pilot@example.invalid'
printf 'start\n' > README.md
git add README.md && git commit -m init
printf 'Write a Python file hello.py that prints hello.\n' > task.md
cd ~/projects/project-agent-controller
python3 experiments/codex-loop/loop.py run --repo ~/projects/orch-sandbox --task ~/projects/orch-sandbox/task.md --check 'python3 hello.py'
```

The original checkout is not modified; a separate `auto/codex-*` worktree is created. The default is **no push**. `--publish` (trusted repos only) attempts new-branch push + Draft PR; never auto-merges. No CI polling, crash recovery, credential isolation, or scheduled background loop exists in this pilot. Do not use unattended on your real baby monitor.

See [audit](../../docs/architecture/codex-loop-m3-pilot-audit-20261015.md).
Note: a test that creates bytecode caches or other generated artifacts must first configure the repository's `.gitignore`; the pilot fails closed on `.pyc`, secrets, and symlink changes. `--publish` is not recommended until the separate-publisher security gate is complete.
