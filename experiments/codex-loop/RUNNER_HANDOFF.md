# M4 Runner handoff: recover local changes without weakening the sandbox

Status: a Runner implementation exists only as uncommitted files in the
Parallels shared checkout. The former checkout reported a read-only `.git`;
the dedicated remote branch now exists, created at the latest PAC pilot head
`387b589f342b9812e3ddd2495fa1beb17e7e68a5`.
No Ubuntu Runner service has been activated or E2E validated.

**Never run `git reset --hard`, `sudo chmod -R` on `/media/psf`, disable
AppArmor, or force-push to fix a read-only Parallels share.** A Linux ext4
checkout is the safer home for the controller's `.git` and sensitive runtime
files. Existing firmware and controller source can remain Mac-visible.

## One-time Ubuntu import — no Codex calls or system changes

The `parallels` account with its existing `gh` login can make a new checkout
to stage files, **but must not execute untrusted generated source while holding
those publisher credentials**.

```bash
set -e
SRC=/media/psf/chatgpt/project-agent-controller-codex-pilot
DEST="$HOME/pac-runner-stage"

cd "$SRC"
git status --short
git rev-parse HEAD
findmnt -T "$SRC/.git" -o TARGET,FSTYPE,OPTIONS

# This checkout must be a new path under Ubuntu's home (native filesystem).
test ! -e "$DEST"
gh repo clone lpearf-pixel/project-agent-controller "$DEST" -- \
  --branch feat/pac-ubuntu-autonomous-runner

# Read-only plan. Source .git is not written.
python3 "$DEST/experiments/codex-loop/migrate_runner.py" \
  --source "$SRC" --destination "$DEST"
```

Review the printed `accepted`, `rejected`, `conflicts` and
`ignored_unrelated` lists. `rejected` or `conflicts` results **stop
without copying**; reconcile against the latest branch rather than reset.

When the preview is correct, **copy only approved Runner files**:

```bash
python3 "$HOME/pac-runner-stage/experiments/codex-loop/migrate_runner.py" \
  --source /media/psf/chatgpt/project-agent-controller-codex-pilot \
  --destination "$HOME/pac-runner-stage" --apply

git -C "$HOME/pac-runner-stage" status --short
git -C "$HOME/pac-runner-stage" diff --check
```

The script copies files only, **never commits or pushes**. Keep the source
checkout untouched until the local edits are safely published and reviewed.
The import refuses overwriting remote-modified tracked files rather than
silently applying stale code.

## Blocking next gate: separate OS identities

A successful file import is **not** permission to start the service.
`parallels` currently holds Codex and `gh` credentials and cannot safely
host unattended untrusted code and an automated publisher in one identity.

Before enabling the autonomous Runner:
- Provision `pac-codex` for untrusted code generation with **no GitHub write
  token, no `~/.config/gh` access, and no shared publisher HOME/mount**.
- Provision `pac-publisher` for GitHub branch and Draft PR writes. Never grant
  it an interactive Codex role; place its keys/tokens in its private home.
- Give `pac-codex` an allowed worktree and preapproved test commands only;
  use a narrow validated artifact-transfer channel, not arbitrary user code
  run inside the publisher.
- Make the dispatcher require signed/allowlisted structured manifests, immutable
  base SHAs, finite execution budgets and atomic idempotent state transitions.
  An Issue or PR comment is **untrusted task content**, not a shell script.
- Verify one offline fake-Codex credential isolation test before any live
  model invocation. Then independently test a disposable GitHub Draft PR.
- Require one-time OS administration and independent `gh` authentication for
  the publisher identity. Do not share secrets in ChatGPT or Codex transcripts.

If the setup is missing or fails, status remains `BLOCKED_CREDENTIAL_ISOLATION`.
Do **not** enable a systemd service or timer yet.

## Firmware/physical work is separate

OEPL Issue #7 and Draft PR #8 track the S3/C6 effort. No flashing, reset,
erase, pin drive, or tag programming. Software may safely proceed without the
owner; actual UART connectivity remains `NEEDS_HARDWARE` until physical
TX/RX/GND wiring is confirmed. Do not infer a valid link from a simulated CI.

A successful Stage M4 report must distinguish: copied locally, tests passed,
credential isolation proved, systemd active, real Draft PR CI passed. No
automatic claim of deployment from a report generated in a read-only checkout.
