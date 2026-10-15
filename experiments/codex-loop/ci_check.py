#!/usr/bin/env python3
"""Read-only GitHub CI gate for a previously published Draft PR.

Compatible with Ubuntu 24.04's older gh: use 'gh api', not unsupported
'gh pr checks --json'. Unknown, absent, pending, failed and skipped-only
checks NEVER count as PASS. This file never merges or edits remote state.
"""
import argparse
import json
import re
import sys

from loop import RUNTIME, call

TASK_ID = re.compile(r"[a-f0-9]{12}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA = re.compile(r"[a-f0-9]{40}")
FAILED = {"failure", "error", "cancelled", "timed_out", "action_required", "stale",
          "startup_failure"}
NEUTRAL = {"neutral", "skipped"}


def api_json(endpoint):
    """Obtain a verified API JSON document without confusing CLI errors with JSON."""
    code, output = call(["gh", "api", endpoint], timeout=40)
    if code:
        raise RuntimeError(
            "GitHub REST request failed (gh api, exit "
            + str(code) + "). Check gh auth status -h github.com and API permissions."
        )
    try:
        result = json.loads(output)
    except ValueError as exc:
        raise RuntimeError(
            "gh api returned non-JSON output; check gh version and GitHub access"
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError("unexpected GitHub REST response structure")
    return result


def classify_checks(data, sha):
    rows = data.get("check_runs")
    count = data.get("total_count")
    if not isinstance(rows, list) or not isinstance(count, int):
        raise RuntimeError("missing check_runs/total_count in GitHub response")
    if count > 100 or count != len(rows):
        return "UNKNOWN", []
    formatted = []
    for run in rows:
        if not isinstance(run, dict) or run.get("head_sha") != sha:
            return "UNKNOWN", formatted
        status = str(run.get("status", "")).lower()
        conclusion = run.get("conclusion")
        conclusion = str(conclusion).lower() if conclusion is not None else ""
        formatted.append({
            "name": run.get("name", "unknown"),
            "status": status,
            "conclusion": conclusion or None,
        })
    if not formatted:
        return "UNKNOWN", formatted
    if any(row["conclusion"] in FAILED for row in formatted):
        return "FAIL", formatted
    if any(row["status"] != "completed" for row in formatted):
        return "PENDING", formatted
    if any(row["conclusion"] not in ({"success"} | NEUTRAL) for row in formatted):
        return "UNKNOWN", formatted
    if any(row["conclusion"] == "success" for row in formatted):
        return "PASS", formatted
    return "UNKNOWN", formatted  # skipped-only never passes


def classify_statuses(data):
    statuses = data.get("statuses")
    if not isinstance(statuses, list):
        raise RuntimeError("missing statuses in GitHub response")
    # Combined-status endpoint includes earlier entries for the same context.
    # GitHub returns newest first: use the first for each named context.
    latest = {}
    for item in statuses:
        if not isinstance(item, dict) or not isinstance(item.get("context"), str):
            return "UNKNOWN", []
        context = item["context"]
        if context not in latest:
            latest[context] = str(item.get("state", "")).lower()
    rows = [{"name": k, "state": v} for k, v in latest.items()]
    if not rows:
        return "ABSENT", rows
    if any(v in ("failure", "error") for v in latest.values()):
        return "FAIL", rows
    if any(v == "pending" for v in latest.values()):
        return "PENDING", rows
    if all(v == "success" for v in latest.values()):
        return "PASS", rows
    return "UNKNOWN", rows


def check(task_id, repository):
    if not TASK_ID.fullmatch(task_id) or not REPOSITORY.fullmatch(repository):
        raise RuntimeError("invalid task ID or repository")
    path = RUNTIME / "runs" / task_id / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("status") != "PR_DRAFT":
        raise RuntimeError("task does not have a recorded Draft PR")
    url = state.get("pr_url", "")
    expected_url = "https://github.com/" + repository + "/pull/"
    if not (isinstance(url, str) and url.startswith(expected_url)
            and url[len(expected_url):].isdigit()):
        raise RuntimeError("recorded PR URL does not belong to selected repository")
    sha = state.get("publish_commit", "")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise RuntimeError("invalid published commit SHA")
    code, output = call(
        ["gh", "pr", "view", url, "--repo", repository,
         "--json", "isDraft,headRefName,headRefOid,baseRefName"], timeout=35,
    )
    if code:
        raise RuntimeError("cannot verify Draft PR metadata via gh pr view")
    try:
        pr = json.loads(output)
    except ValueError as exc:
        raise RuntimeError("gh pr view returned non-JSON output") from exc
    if (pr.get("isDraft") is not True or pr.get("headRefOid") != sha
            or pr.get("headRefName") != state.get("branch")
            or pr.get("baseRefName") != state.get("base_branch")):
        raise RuntimeError("PR no longer matches the recorded Draft branch and commit")

    base = "repos/" + repository + "/commits/" + sha
    checks = api_json(base + "/check-runs?per_page=100")
    statuses = api_json(base + "/status?per_page=100")
    check_state, check_rows = classify_checks(checks, sha)
    status_state, status_rows = classify_statuses(statuses)
    if "FAIL" in (check_state, status_state):
        overall = "FAIL"
    elif "PENDING" in (check_state, status_state):
        overall = "PENDING"
    elif (check_state in ("PASS", "UNKNOWN")
          and status_state in ("PASS", "ABSENT")
          and "PASS" in (check_state, status_state)):
        overall = "PASS" if check_state == "PASS" else "UNKNOWN"
    elif check_state == "PASS" and status_state == "ABSENT":
        overall = "PASS"
    else:
        overall = "UNKNOWN"
    # If statuses have a result but no check runs, those can be valid,
    # but this pilot specifically requires an actual passing Actions check run.
    if check_state != "PASS" and overall == "PASS":
        overall = "UNKNOWN"
    print(json.dumps({
        "task_id": task_id,
        "ci_status": overall,
        "pr_url": url,
        "head_sha": sha,
        "check_runs": check_rows,
        "commit_statuses": status_rows,
    }, indent=2))
    return 0 if overall == "PASS" else 1


def main():
    p = argparse.ArgumentParser(description="Read-only Draft PR CI gate (older gh supported)")
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
