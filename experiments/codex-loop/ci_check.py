#!/usr/bin/env python3
"""Read-only CI gate for a previously published Draft PR.

Unknown, absent and pending checks NEVER count as CI success. No auto-merge.
"""
import argparse
import json
import re
import sys

from loop import RUNTIME, call

TASK_ID = re.compile(r"[a-f0-9]{12}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def check(task_id, repository):
    if not TASK_ID.fullmatch(task_id) or not REPOSITORY.fullmatch(repository):
        raise RuntimeError("invalid task ID or repository")
    path = RUNTIME / "runs" / task_id / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("status") != "PR_DRAFT":
        raise RuntimeError("task does not have a recorded Draft PR")
    url = state.get("pr_url", "")
    if not url.startswith("https://github.com/" + repository + "/pull/"):
        raise RuntimeError("recorded PR URL does not belong to selected repository")
    code, output = call(
        ["gh", "pr", "view", url, "--repo", repository,
         "--json", "isDraft,headRefName,headRefOid,baseRefName"], timeout=35,
    )
    if code:
        raise RuntimeError("cannot verify Draft PR metadata: " + output[-300:])
    pr = json.loads(output)
    if (pr.get("isDraft") is not True or pr.get("headRefOid") != state.get("publish_commit")
            or pr.get("headRefName") != state.get("branch")
            or pr.get("baseRefName") != state.get("base_branch")):
        raise RuntimeError("PR no longer matches the recorded Draft branch and commit")
    code, output = call(
        ["gh", "pr", "checks", url, "--repo", repository, "--json", "name,bucket,state"],
        timeout=40,
    )
    if not output.strip():
        raise RuntimeError("no PR checks information returned")
    try:
        rows = json.loads(output)
    except ValueError as exc:
        raise RuntimeError("PR checks JSON could not be parsed") from exc
    if not isinstance(rows, list):
        raise RuntimeError("PR checks returned unexpected data")
    if not rows:
        status = "UNKNOWN"
    elif any(str(r.get("bucket", "")).lower() in ("fail", "cancel")
             for r in rows):
        status = "FAIL"
    elif all(str(r.get("bucket", "")).lower() in ("pass", "skipping")
             for r in rows):
        status = "PASS"
    else:
        status = "PENDING"
    # gh may exit 1 when one or more checks fail. Only the structured response
    # decides the outcome; a failed invocation with all-pass data is suspicious.
    if code and status == "PASS":
        status = "UNKNOWN"
    print(json.dumps({
        "task_id": task_id, "ci_status": status, "pr_url": url,
        "checks": rows,
    }, indent=2))
    return 0 if status == "PASS" else 1


def main():
    p = argparse.ArgumentParser(description="Read-only Draft PR CI gate")
    p.add_argument("--task-id", required=True)
    p.add_argument("--repository", required=True)
    a = p.parse_args()
    try:
        return check(a.task_id, a.repository)
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR:", str(exc)[:450], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
