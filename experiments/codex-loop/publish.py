#!/usr/bin/env python3
"""Explicit, resumable Draft-PR publisher for a previously LOCAL_PASS Codex task.

This script NEVER calls Codex, edits the base branch, force-pushes, or merges.
It shares a Unix identity with validators; it is not a credential sandbox.
"""
import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from loop import RUNTIME, artifact_snapshot, call, changed, disallowed, git, save

REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ID = re.compile(r"[a-f0-9]{12}")


def github_origin(repo, expected):
    url = git(repo, "remote", "get-url", "origin").strip()
    pattern = r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?"
    match = re.fullmatch(pattern, url)
    if not match or match.group(1).lower() != expected.lower():
        raise RuntimeError("origin must be exactly the explicitly selected github.com repository")


def ensure_gh(repository):
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI (gh) is required")
    code, _ = call(["gh", "auth", "status", "--hostname", "github.com"], timeout=20)
    if code:
        raise RuntimeError("gh auth status failed; authorize the Ubuntu gh login")
    code, output = call(
        ["gh", "repo", "view", repository, "--json", "viewerPermission",
         "--jq", ".viewerPermission"], timeout=30,
    )
    if code or output.strip() not in ("ADMIN", "MAINTAIN", "WRITE"):
        raise RuntimeError("GitHub repository WRITE permission not verified")


def check_validators(work, run_dir, state, digest):
    for i, command in enumerate(state["checks"]):
        test = shlex.split(command)
        if not test or test[0] in ("bash", "sh", "sudo", "curl", "wget", "docker"):
            raise RuntimeError("unsafe saved validator; refuse publish")
        env = {k: v for k, v in os.environ.items()
               if k in ("PATH", "LANG", "LC_ALL", "TZ")}
        env["HOME"] = str(run_dir)
        code, output = call(
            test, cwd=work, env=env, timeout=180,
            logfile=run_dir / f"publish-test-{i}.log",
        )
        if code:
            raise RuntimeError(f"saved check {i} failed: {output[-500:]}")
    if disallowed(changed(work), work):
        raise RuntimeError("protected file modified during publish validation")
    new_digest, _ = artifact_snapshot(work)
    if new_digest != digest:
        raise RuntimeError("validator modified artifacts; refuse publish")


def current_pr(work, repository, branch, base):
    code, output = call(
        ["gh", "pr", "list", "--repo", repository, "--head", branch, "--state", "open",
         "--json", "url,isDraft,headRefName,baseRefName"], cwd=work, timeout=35,
    )
    if code:
        raise RuntimeError("failed to query PR: " + output[-300:])
    prs = json.loads(output)
    if len(prs) > 1:
        raise RuntimeError("multiple matching PRs: require manual intervention")
    if not prs:
        return None
    pr = prs[0]
    if (pr.get("isDraft") is not True or pr.get("headRefName") != branch
            or pr.get("baseRefName") != base):
        raise RuntimeError("existing PR is not the expected Draft PR")
    return pr["url"]


def run(args):
    if not ID.fullmatch(args.task_id) or not REPOSITORY.fullmatch(args.repository):
        raise RuntimeError("invalid task ID or owner/repository")
    run_dir = RUNTIME / "runs" / args.task_id
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    allowed = ("LOCAL_PASS", "COMMITTED", "PUSHED", "PR_DRAFT")
    if state.get("status") not in allowed:
        raise RuntimeError("task is not publishable: " + str(state.get("status")))
    if state.get("validation") != "PASS":
        raise RuntimeError("no recorded final PASS; rerun strict local smoke with new runner")
    repo = Path(state["repo"]).resolve(strict=True)
    work = Path(state["worktree"]).resolve(strict=True)
    if not (repo / ".git").exists() or not (work / ".git").exists():
        raise RuntimeError("source repository/worktree missing")
    branch = "auto/codex-" + args.task_id
    if state.get("branch") != branch or git(work, "branch", "--show-current") != branch:
        raise RuntimeError("worktree branch does not match the task")
    if git(repo, "rev-parse", state["base_branch"]) != state["base_sha"]:
        raise RuntimeError("base branch has advanced; manual reconciliation required")
    if state.get("base_branch", "").startswith("auto/"):
        raise RuntimeError("automatic branches cannot be used as PR base")
    github_origin(repo, args.repository)
    ensure_gh(args.repository)
    if state["status"] == "LOCAL_PASS":
        digest, names = artifact_snapshot(work)
        if not names or disallowed(names, work):
            raise RuntimeError("no safe changes eligible for publication")
        if digest != state.get("artifact_sha256"):
            raise RuntimeError("artifacts differ from recorded LOCAL_PASS snapshot")
        if git(work, "rev-parse", "HEAD") != state["base_sha"]:
            raise RuntimeError("LOCAL_PASS worktree HEAD changed; cannot infer safe recovery")
        check_validators(work, run_dir, state, digest)
    if not args.execute:
        print(json.dumps({
            "mode": "DRY_RUN", "task_id": args.task_id, "status": state["status"],
            "repository": args.repository, "base": state["base_branch"],
            "branch": branch, "worktree": str(work),
            "next": "Repeat with --execute to commit, push and create a Draft PR",
        }, indent=2))
        return 0
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / "controller.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another controller task is running") from exc
        try:
            if state["status"] == "LOCAL_PASS":
                if artifact_snapshot(work)[0] != state["artifact_sha256"]:
                    raise RuntimeError("worktree changed while acquiring publish lock")
                git(work, "add", "-A")
                git(work, "commit", "-m", "chore: automatic Codex task " + args.task_id)
                state.update(status="COMMITTED", publish_commit=git(work, "rev-parse", "HEAD"))
                save(state_path, state)
            if state["status"] == "COMMITTED":
                sha = git(work, "rev-parse", "HEAD")
                if sha != state["publish_commit"] or changed(work):
                    raise RuntimeError("committed worktree drift detected")
                git(work, "push", "--set-upstream", "origin",
                    "HEAD:refs/heads/" + branch, timeout=90)
                state["status"] = "PUSHED"
                save(state_path, state)
            if state["status"] == "PUSHED":
                url = current_pr(work, args.repository, branch, state["base_branch"])
                if url is None:
                    body = run_dir / "pr-body.md"
                    body.write_text(
                        "Generated by PAC Codex Loop; locally validated.\n"
                        "HUMAN REVIEW REQUIRED. No auto-merge.\n\n"
                        "Task: " + args.task_id + "\n"
                        "Base: " + state["base_sha"] + "\n",
                        encoding="utf-8",
                    )
                    code, out = call(
                        ["gh", "pr", "create", "--repo", args.repository,
                         "--draft", "--base", state["base_branch"],
                         "--head", branch, "--title", "Codex task " + args.task_id,
                         "--body-file", str(body)], cwd=work, timeout=90,
                    )
                    if code:
                        raise RuntimeError("branch pushed, but Draft PR creation failed: "
                                           + out[-500:])
                    url = out.strip()
                state.update(status="PR_DRAFT", pr_url=url)
                save(state_path, state)
            print(json.dumps({
                "task_id": args.task_id, "status": state["status"],
                "branch": branch, "pr_url": state.get("pr_url"),
            }, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            state["last_publish_error"] = str(exc)[:400]
            save(state_path, state)
            raise


def main():
    parser = argparse.ArgumentParser(description="Publish a prior PASS as a Draft PR")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repository", required=True, help="explicit owner/repo destination")
    parser.add_argument("--execute", action="store_true", help="perform Git push and Draft PR")
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print("ERROR:", str(exc)[:600], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
