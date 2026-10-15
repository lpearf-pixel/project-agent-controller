#!/usr/bin/env bash
# Explicit M3 Parallels Ubuntu E2E test. Creates a dedicated private GitHub repo.
# Does not edit PAC main, merge PRs, deploy, or touch Baby Monitor.
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SHARED_ROOT="$(dirname "$SOURCE_ROOT")"
TARGET="lpearf-pixel/pac-codex-loop-e2e-20261015"
PROJECT_DIR="$SHARED_ROOT/pac-codex-loop-e2e-20261015"
RUNNER="$SOURCE_ROOT/experiments/codex-loop/loop.py"
PUBLISH="$SOURCE_ROOT/experiments/codex-loop/publish.py"
CI="$SOURCE_ROOT/experiments/codex-loop/ci_check.py"
VERIFY="$SOURCE_ROOT/experiments/codex-loop/verify-smoke.py"
RUN_OUT="$HOME/.local/share/pac-codex-loop/e2e-run-summary.json"

if [[ $# -eq 0 ]]; then
  echo "[dry-run] Separate GitHub repo: $TARGET (PRIVATE)"
  echo "[dry-run] Local source: $PROJECT_DIR"
  echo "[dry-run] Worktrees: $SHARED_ROOT/codex-worktrees"
  echo "[dry-run] No GitHub writes, Codex calls or deployments."
  echo "To execute: bash experiments/codex-loop/e2e-smoke.sh --execute"
  exit 0
fi
if [[ $# -ne 1 || "$1" != "--execute" ]]; then
  echo "Usage: bash experiments/codex-loop/e2e-smoke.sh [--execute]" >&2
  exit 2
fi

[[ "$SHARED_ROOT" == /media/psf/* ]] || {
  echo "ERROR: checkout must be on the Parallels shared mount" >&2; exit 2;
}
for tool in git gh codex python3; do
  command -v "$tool" >/dev/null || { echo "ERROR: missing $tool" >&2; exit 2; }
done
python3 "$RUNNER" doctor
gh auth status -h github.com >/dev/null
account="$(gh api user --jq .login)"
[[ "$account" == "lpearf-pixel" ]] || {
  echo "ERROR: expected GitHub login lpearf-pixel; got $account" >&2; exit 2;
}
if gh repo view "$TARGET" --json nameWithOwner >/dev/null 2>&1; then
  echo "ERROR: $TARGET already exists. Refusing overwrite/reuse." >&2; exit 2;
fi
[[ ! -e "$PROJECT_DIR" ]] || {
  echo "ERROR: shared project path exists: $PROJECT_DIR" >&2; exit 2;
}

echo "[1/6] Create an isolated local Git repository in the shared directory."
mkdir -p "$PROJECT_DIR/.github/workflows"
cd "$PROJECT_DIR"
git init -b main
git config user.name "PAC Pilot"
git config user.email "pac-pilot@example.invalid"
printf 'PAC isolated Codex E2E fixture, with no production data.\n' > README.md
printf '__pycache__/\n*.pyc\n' > .gitignore
cat > .github/workflows/hello.yml <<'YAML'
name: Test generated hello.py
on:
  pull_request:
    paths:
      - "hello.py"
      - ".github/workflows/hello.yml"
  workflow_dispatch:
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: Verify generated program
        run: |
          test -f hello.py
          test "$(python3 -I hello.py)" = "hello"
YAML
git add README.md .gitignore .github/workflows/hello.yml
git commit -m "test: isolated Codex publishing fixture"

echo "[2/6] Create a PRIVATE GitHub test repository; push only fixture base main."
gh repo create "$TARGET" --private \
  --description "PAC Codex loop isolated E2E fixture, never production" \
  --source "$PROJECT_DIR" --remote origin --push
git remote set-url origin "https://github.com/$TARGET.git"
[[ "$(git remote get-url origin)" == "https://github.com/$TARGET.git" ]] || exit 2

printf 'Create only hello.py as Python code that prints hello. Do not change other files.\n' > "$PROJECT_DIR/task.md"
mkdir -p "$(dirname "$RUN_OUT")"
echo "[3/6] Run real Codex once (at most one automatic repair)."
python3 "$RUNNER" run \
  --repo "$PROJECT_DIR" \
  --task "$PROJECT_DIR/task.md" \
  --worktree-root "$SHARED_ROOT/codex-worktrees" \
  --check "python3 $VERIFY" \
  --repairs 1 \
  --codex-timeout 180 \
  --check-timeout 30 | tee "$RUN_OUT"

task_id="$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1],encoding="utf-8")); assert s["status"]=="LOCAL_PASS"; print(s["task_id"])' "$RUN_OUT")"
printf 'Task ID: %s\n' "$task_id"

echo "[4/6] Check saved validation and GitHub write rights; no new Codex call."
python3 "$PUBLISH" --task-id "$task_id" --repository "$TARGET"

echo "[5/6] Push only auto/codex task branch; create DRAFT PR, never merge."
python3 "$PUBLISH" --task-id "$task_id" --repository "$TARGET" --execute

echo "[6/6] Check GitHub CI (read-only, cannot treat pending as success)."
status=1
for step in $(seq 1 12); do
  if python3 "$CI" --task-id "$task_id" --repository "$TARGET"; then
    status=0
    break
  fi
  if [[ "$step" -lt 12 ]]; then sleep 15; fi
done
echo "Fixture repository: https://github.com/$TARGET"
echo "Task ID: $task_id"
echo "Saved state: $HOME/.local/share/pac-codex-loop/runs/$task_id/state.json"
if [[ "$status" -eq 0 ]]; then
  echo "E2E PASS: Codex, strict local validation, Git push, Draft PR and CI."
else
  echo "E2E INCOMPLETE: GitHub CI not confirmed PASS; no merge was performed." >&2
  exit 1
fi
