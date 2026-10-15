# M3 + Parallels Ubuntu Codex Loop Audit (2026-10-15)

## Decision
Reuse `lpearf-pixel/project-agent-controller` instead of creating an overlapping third orchestrator. Existing PAC v0.2 observers, controlled runner, emergency-stop and SQLite storage remain unchanged. The new `experiments/codex-loop` is an **opt-in, independent pilot** only. Do not rewire existing PAC modules until acceptance gates pass. Existing `local-actions-runner-manager` remains separate.

## Why the earlier proposal was too ambitious
- Mac localhost is not Ubuntu VM localhost; test M3 HTTP proxy port 11285 or SOCKS5 1284 using a reachable host address.
- An active Codex login and correct CLI version are prerequisites. CLI installation is not proof that inference works.
- Codex workspace-write is not a security identity boundary: code execution and validators running as the current user can reach user-accessible files.
- Separate worktrees and committed base SHAs are necessary but insufficient for protecting against malicious code.
- Unknown project tests cannot be privileged; use explicit local trusted validators and recheck paths **after** tests.
- Automatic CI inspection, resumable state, PR idempotency and an isolated publisher are **not implemented in this pilot**; claiming a production-ready closed loop would be misleading.

## Pilot contracts
- One foreground job (nonblocking file lock); Python stdlib; no service or background daemon.
- Fresh `auto/codex-*` Git worktree based on committed HEAD; leave original checkout untouched.
- Noninteractive Codex `exec --json --sandbox workspace-write --ask-for-approval never`.
- 0..3 repair tries, bounded timeouts, test command arguments (no shell evaluation).
- Keep output/state under `~/.local/share/pac-codex-loop`, local only; protected path changes stop publication.
- No automatic remote operations unless `--publish` explicitly chosen. Publishing only attempts push of new branch and creation of **Draft PR**; never merge.
- No access to real baby monitoring devices, production database, or home automation.

## Offline acceptance performed
Assistant-side container executed `python3 -m unittest discover -s tests -v`, with 4 passing tests: normal local edit, protected-path block, repair-budget exhaustion, unsafe validator refusal. This is NOT a test of the actual M3 machine, Codex authentication, proxy or GitHub privileges.

## Required gates before unattended operation
1. Real Ubuntu doctor, proxy, `codex login status`, live smoke against disposable repository.
2. Separate operating-system identities or ephemeral sandbox for Codex and tests versus GitHub publisher; broker scoped credentials.
3. Stream bounded/redacted logs to disk rather than buffering process output in memory.
4. Crash recovery, task identity, duplicate PR handling and process group cleanup on user interruption.
5. Locked per-project validator registry, validator isolation, independent diff and secret/symlink audits.
6. Read exact SHA required CI checks and handle CI failures without endless retries.
7. Integrate with PAC's emergency stop, incident and recovery state machine, after code review.
8. Adversarial tests: prompt injection, file escape, credentials, risky network, modified workflows and git hooks.
9. Full end-to-end real low-risk repository trial before any Baby Monitor code is allowed.

## Rollback
The new implementation changes **only** files under `experiments/codex-loop` and this audit document on feature branch `feat/codex-loop-v0-20261015`. No need to restart PAC, change existing service files or merge to main. Delete experimental worktrees only after review and never force-delete an unknown branch.
