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

## Check the existing task without rerunning Codex

A prior run may report `LOCAL_PASS` with a stale `last_failure` from an earlier failed attempt. A passing exit code of `python3 hello.py` did not verify the printed output. Run this local, deterministic check in the shared worktree:

```bash
cd /media/psf/chatgpt/codex-worktrees/e475dce86248
python3 /media/psf/chatgpt/project-agent-controller-codex-pilot/experiments/codex-loop/verify-smoke.py
```

If it prints `PASS`, the specific smoke requirement is satisfied without spending more model tokens. If it prints `FAIL`, inspect the generated file and rerun the disposable smoke with the stronger validator. The original sandbox source branch remains unchanged.

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
  --check "python3 $ROOT/project-agent-controller-codex-pilot/experiments/codex-loop/verify-smoke.py" \
  --repairs 2
```

The original `orch-sandbox` branch remains untouched. The generated `hello.py` is found in `/media/psf/chatgpt/codex-worktrees/<task_id>/hello.py` and is visible from the Mac as well.

Before real projects, verify the Parallels mount supports `git worktree add`, executable permissions, and Git operations; the Mac/VM shared path is **not** equivalent to native ext4. Use trusted disposable test projects only.

The original checkout is not modified; a separate `auto/codex-*` worktree is created. The default is **no push**. The former combined `loop.py run --publish` mode has been retired. Never auto-merge or deploy. The publisher remains under the **same Ubuntu account**, so there is no separate-user credential isolation. Do not use unattended on Baby Monitor.

## Phase 2 real GitHub E2E: dedicated private fixture

The Ubuntu user needs `gh` installed, `gh auth status -h github.com` authenticated as `lpearf-pixel`, and Git HTTPS credentials set up via `gh auth setup-git`. Do **not** point the previous `orch-sandbox` at the PAC controller remote: that disposable repository was created only for local smoke testing.

A single opt-in pilot script is provided in the existing macOS/Ubuntu shared checkout. First run it without flags to preview effects:

```bash
cd /media/psf/chatgpt/project-agent-controller-codex-pilot
bash experiments/codex-loop/e2e-smoke.sh
```

If the displayed repository and paths are correct, explicitly invoke:

```bash
bash experiments/codex-loop/e2e-smoke.sh --execute
```

**Remote effects of `--execute`:** creates a new **private** GitHub repository `lpearf-pixel/pac-codex-loop-e2e-20261015` with only synthetic test files and a minimal PR CI workflow; pushes its initial `main`; calls real Codex to generate `hello.py` in `/media/psf/chatgpt/codex-worktrees/<task_id>/`; performs strict local output validation; commits and pushes only `auto/codex-<task_id>`; opens a **Draft** pull request; checks its GitHub Actions status. It never merges, updates PAC main, touches the baby-monitor repository or deploys anything. Its saved task state and logs remain Ubuntu-local.

The E2E command refuses to overwrite an existing fixture repository or local directory. If it fails partway through, inspect the output and saved task state. **Do not delete the partially created private repository to rerun without investigating.** The separate publisher supports resume after push/PR failures for a task already marked `LOCAL_PASS` or `PUSHED`; see the commands below.

GitHub confirmed in May 2025 that Draft PRs are available in private repositories on GitHub Free. This script has only been exercised by offline mocks/CI so far; the first full GitHub E2E run must take place in the authorized Ubuntu host environment.

## Phase 2: optional GitHub publishing (explicit, offline-tested only)

**Prerequisites:** `gh auth status -h github.com` succeeds under the Ubuntu user; the local source repository has a valid `origin` pointing to exactly the explicitly named GitHub repository; the task was created by the **new** runner after its validation snapshot feature was added. Older task `e475dce86248` was created before the snapshot was recorded and its disposable source repository has no GitHub remote; **it cannot be safely published**. Do not attach the controller's own remote to a disposable repository.

For an eligible previously validated task in a trusted **real GitHub checkout**, publishing takes two deliberate steps; neither re-runs Codex:

```bash
cd /media/psf/chatgpt/project-agent-controller-codex-pilot
python3 experiments/codex-loop/publish.py \
  --task-id YOUR_NEW_TASK_ID \
  --repository OWNER/REPOSITORY
# Once the printed DRY_RUN summary is correct, explicitly publish:
python3 experiments/codex-loop/publish.py \
  --task-id YOUR_NEW_TASK_ID \
  --repository OWNER/REPOSITORY \
  --execute
```

The publisher re-checks saved validators, detects changes after `LOCAL_PASS`, verifies the exact GitHub `origin`, GitHub write access, immutable source base and worktree branch, then commits and pushes only the new `auto/codex-<task-id>` branch and creates a **Draft PR**. It never force pushes, edits `main`, changes real hardware, deploys, or merges. Recovery from a push followed by Draft PR creation failure can be retried with the same command. Unexpected partial commits or shifted branches deliberately require manual reconciliation.

After the resulting status is `PR_DRAFT`, the read-only, one-shot CI gate is:

```bash
python3 experiments/codex-loop/ci_check.py \
  --task-id YOUR_NEW_TASK_ID \
  --repository OWNER/REPOSITORY
```

This checks the exact Draft PR and commit. **No checks, pending checks, failures, or a mismatched PR cannot return CI PASS.** It never modifies the branch or merges. CI-failure automatic repair, live remote publisher E2E validation, auto-scheduling and per-user credential isolation are **not yet implemented**. This phase is for trusted pilot repositories, not production.

See [audit](../../docs/architecture/codex-loop-m3-pilot-audit-20261015.md).
Note: a test that creates bytecode caches or other generated artifacts must first configure the repository's `.gitignore`; the pilot fails closed on `.pyc`, secrets, and symlink changes. `--publish` is not recommended until the separate-publisher security gate is complete.
