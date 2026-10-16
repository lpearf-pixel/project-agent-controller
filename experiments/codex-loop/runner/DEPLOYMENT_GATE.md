# PAC M4 autonomous Runner — audit and deployment gate

Updated: 2026-10-16. Scope: Draft PR #9 only; no changes to main/stable,
no Ubuntu system or ESP32 hardware operations.

## Source and evidence

The original 12-file implementation was pushed from
`/home/parallels/pac-runner-stage` at `766dc985`.
The controller branch has since been advanced by the remote audit; **update
that ext4 checkout using `git pull --ff-only` before further local work**.

A GitHub Actions syntax and unit test result is NOT proof of systemd service
readiness, permission isolation, live Codex invocation, or S3↔C6 UART.

## Correctness and security issues found

1. `loop.py run` requires `--check`. Initial Runner omitted it and attempted
   to parse only the last line of pretty-printed JSON, which would fail even
   after a successful Codex task. Both integration points were repaired.
2. The Runner's task-level lock existed but wasn't used; run-once now acquires
   it across task execution.
3. Existing state on repeated task IDs did not reject a changed Issue
   manifest. A SHA-256 of the validated manifest is now bound at first
   discovery, and inconsistent replays fail closed.
4. Validators in GitHub Issue content were treated as arbitrary command
   arguments. The first OEPL pilot now accepts **only one installed approved
   command**: `python3 -m unittest discover -s experiments/oepl-s3-c6/tests -v`.
   Other arguments cannot be promoted into a shell command.
5. Allowed path components and integer budget fields now reject more
   normalization and boolean bypasses.
6. Merely finding two Unix passwd entries did not establish credential
   isolation; the isolation doctor now **always fails closed** until a real
   OS-enforced executor/publisher transport is implemented and validated.
7. `runuser` from an ordinary unprivileged user-systemd service is not a
   supported secure publisher implementation. The unsafe publisher call was
   removed; publishing currently transitions to BLOCKED if reached.
8. Previous CI was not discovering `runner/tests`; the main Codex Loop
   offline workflow now explicitly runs that directory.
9. Python/Ruff issues in the imported implementation were addressed without
   disabling checks. A temporary non-pushing formatter workflow was
   removed after use.

## Remaining blockers — do not start the timer

**BLOCKED_CREDENTIAL_ISOLATION**

- Implement an actual separate-identity execution broker or root-managed,
  narrowly scoped system services: `pac-codex` must not be able to read the
  publisher's authentication or runtime state; `pac-publisher` must not
  execute Codex or untrusted worktree code.
- A `systemd --user` unit owned by `parallels` is **not** by itself
  equivalent to two OS-enforced security identities.
- Cross-boundary artifact handoff must include immutable source SHA, content
  fingerprint, approved path restrictions, and revalidation by an isolated
  publisher. Never pass a shell command from GitHub Issue text across.
- Make `loop.py` receive bounded validators and verify the local checkout is
  the exact approved branch/base SHA before any generation.
- Implement restart/resume for `PUBLISHING` and `CI_CHECKING` without
  duplicating PRs or resetting API/time budgets. The current implementation
  still lacks robust recovery for these mid-flight states.
- The current publisher's stdout does not include `publish_commit`; align
  the protocol before CI may claim exact-SHA success.
- Reconcile the systemd timeout/writeable worktree layout with Codex runtime
  and CLI authentication in the distinct OS identity.
- After the broker and tests are built, have the owner perform any necessary
  **one-time** privileged account creation / credential authorization.
- Run a disposable real GitHub end-to-end test. Keep all PRs Draft, never
  auto-merge or flash devices.

## Safe status

Current safe use: code review, static scanning, offline unit tests and dry-run
validation, without live Codex, privilege changes or device access.

Unsafe to assert: fully autonomous GitHub→Ubuntu task execution, real CI
publication, powered device connectivity, C6 radio initialization or successful
UART handshake.

Hardware handoff remains a separate `NEEDS_HARDWARE` gate managed by
Issue #7 and Draft PR #8. UART TX/RX/GND wiring requires the user; software
CI is not physical evidence.
