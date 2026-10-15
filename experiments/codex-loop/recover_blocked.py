#!/usr/bin/env python3
"""Safely reclassify a false BLOCKED Codex run, without calling Codex.

The operator must explicitly run --execute after examining --dry-run.
Uses the original recorded validator list on the existing shared worktree;
never commits, pushes, merges or changes production source branches.
"""
import argparse
import fcntl
import json
import os
import re
import shlex
import sys
from pathlib import Path

from loop import RUNTIME, artifact_snapshot, call, changed, codex_result, disallowed, git, save

ID = re.compile(r"[0-9a-f]{12}")
FORBIDDEN_CHECKS = {"sh", "bash", "sudo", "curl", "wget", "docker"}


def under(path, root):
    return path == root or root in path.parents


def recheck(task_id, execute=False, timeout=180):
    if not ID.fullmatch(task_id):
        raise RuntimeError("task ID must be 12 lowercase hex characters")
    run_dir = RUNTIME / "runs" / task_id
    state_path = run_dir / "state.json"
    shared = Path(os.environ.get("PAC_CODEX_SHARED_ROOT", "/media/psf/chatgpt")).resolve()
    if not run_dir.is_dir():
        raise RuntimeError("saved task not found")
    with (RUNTIME / "controller.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another controller task is running") from exc
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("status") != "BLOCKED"
                or not str(state.get("reason", "")).startswith(
                    "Linux sandbox/namespace initialization failed")):
            raise RuntimeError("only historical sandbox-classified BLOCKED tasks are eligible")
        if state.get("validation") != "PENDING":
            raise RuntimeError("task already has a final validation result")
        repo = Path(state["repo"]).resolve(strict=True)
        work = Path(state["worktree"]).resolve(strict=True)
        if not under(repo, shared) or not under(work, shared) or work == repo:
            raise RuntimeError("source or worktree is outside the approved shared directory")
        if not (repo / ".git").exists() or not (work / ".git").exists():
            raise RuntimeError("recorded Git repositories not found")
        branch = "auto/codex-" + task_id
        if state.get("branch") != branch or git(work, "branch", "--show-current") != branch:
            raise RuntimeError("worktree branch drift detected")
        if git(work, "rev-parse", "HEAD") != state.get("base_sha"):
            raise RuntimeError("worktree HEAD no longer matches original base; inspect manually")
        evidence = run_dir / "codex-0.jsonl"
        if not evidence.is_file():
            raise RuntimeError("original Codex event log missing; cannot recover")
        accepted, explanation = codex_result(evidence)
        if not accepted:
            raise RuntimeError("Codex evidence still signals a failure: " + explanation)
        if (run_dir / "codex-1.jsonl").exists():
            raise RuntimeError("unexpected further Codex attempt; manual review required")
        names = changed(work)
        if not names:
            raise RuntimeError("Codex produced no changed files; cannot recover")
        bad = disallowed(names, work)
        if bad:
            raise RuntimeError("protected files changed: " + ", ".join(bad[:8]))
        digest, _ = artifact_snapshot(work)
        saved_checks = state.get("checks")
        if not isinstance(saved_checks, list) or not saved_checks:
            raise RuntimeError("missing recorded validation commands")
        checks = []
        for raw in saved_checks:
            command = shlex.split(raw)
            if not command or command[0] in FORBIDDEN_CHECKS:
                raise RuntimeError("untrusted/blocked saved validator")
            checks.append(command)
        if not execute:
            print(json.dumps({
                "mode": "DRY_RUN",
                "task_id": task_id,
                "classification": "historical text triggered a false sandbox BLOCKED",
                "worktree": str(work),
                "changed_files": names,
                "validation_commands": saved_checks,
                "next": "Re-run with --execute to validate existing files without Codex",
            }, indent=2))
            return 0
        for index, command in enumerate(checks):
            env = {k: v for k, v in os.environ.items()
                   if k in ("PATH", "LANG", "LC_ALL", "TZ")}
            env["HOME"] = str(run_dir)
            try:
                status, output = call(
                    command, cwd=work, env=env, timeout=timeout,
                    logfile=run_dir / f"recovery-validator-{index}.log",
                )
            except RuntimeError as exc:
                state["recovery_failure"] = str(exc)[:500]
                save(state_path, state)
                raise
            if status:
                state["recovery_failure"] = (
                    f"check {index} exited {status}: " + output[-600:]
                )
                save(state_path, state)
                raise RuntimeError("recovery validation failed; original BLOCKED retained")
        after = changed(work)
        if disallowed(after, work) or artifact_snapshot(work)[0] != digest:
            state["recovery_failure"] = "validation mutated worktree or protected paths"
            save(state_path, state)
            raise RuntimeError("validator changed artifacts; original BLOCKED retained")
        previous_reason = state["reason"]
        state.update(
            status="LOCAL_PASS",
            validation="PASS",
            validated_on_attempt=1,
            recovered_from_false_sandbox_block=True,
            original_block_reason=previous_reason,
            changed_files=after,
            artifact_sha256=digest,
        )
        state.pop("reason", None)
        state.pop("recovery_failure", None)
        save(state_path, state)
        print(json.dumps({
            "task_id": task_id, "status": "LOCAL_PASS", "validation": "PASS",
            "recovered": True, "changed_files": after,
            "worktree": str(work), "published": False,
        }, indent=2))
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Safely revalidate an existing false-sandbox-blocked Codex task"
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--check-timeout", type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.check_timeout <= 300:
        parser.error("--check-timeout must be 1..300 seconds")
    try:
        return recheck(args.task_id, args.execute, args.check_timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR:", str(exc)[:850], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
