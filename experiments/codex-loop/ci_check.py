#!/usr/bin/env python3
"""Read-only GitHub CI gate for a previously published Draft PR.

Compatible with Ubuntu 24.04's older gh: use 'gh api', not unsupported
'gh pr checks --json'. Unknown, absent, pending, failed and skipped-only
checks NEVER count as PASS. This file never merges or edits remote state.
"""
import argparse
import fcntl
import json
import re
import sys
import time
from datetime import datetime, timezone

from loop import RUNTIME, call, save

TASK_ID = re.compile(r"[a-f0-9]{12}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA = re.compile(r"[a-f0-9]{40}")
FAILED = {"failure", "error", "cancelled", "timed_out", "action_required", "stale",
          "startup_failure"}
NEUTRAL = {"neutral", "skipped"}


def api_json(endpoint, timeout=40, caller=None):
    """Obtain a verified API JSON document without confusing CLI errors with JSON."""
    command = caller if caller is not None else call
    code, output = command(["gh", "api", endpoint], timeout=timeout)
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


def _state_path(task_id):
    return RUNTIME / "runs" / task_id / "state.json"


def _read_state(task_id, repository):
    path = _state_path(task_id)
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
    return path, state, url, sha


def _pr_metadata(url, repository, timeout=35, caller=None):
    command = caller if caller is not None else call
    code, output = command(
        ["gh", "pr", "view", url, "--repo", repository,
         "--json", "isDraft,headRefName,headRefOid,baseRefName"], timeout=timeout,
    )
    if code:
        raise RuntimeError("cannot verify Draft PR metadata via gh pr view")
    try:
        pr = json.loads(output)
    except ValueError as exc:
        raise RuntimeError("gh pr view returned non-JSON output") from exc
    if not isinstance(pr, dict):
        raise RuntimeError("gh pr view returned an unexpected response")
    return pr


def _validate_pr(pr, state, sha):
    return (pr.get("isDraft") is True
            and pr.get("headRefOid") == sha
            and pr.get("headRefName") == state.get("branch")
            and pr.get("baseRefName") == state.get("base_branch"))


def _one_observation(repository, state, url, sha, deadline, caller):
    """Read one exact-SHA snapshot. The caller owns retry/time budgets."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("monitor timeout")
    pr = _pr_metadata(url, repository, timeout=min(35, remaining), caller=caller)
    if not _validate_pr(pr, state, sha):
        return "UNKNOWN", {"reason": "SHA_MISMATCH", "head_sha": pr.get("headRefOid")}
    base = "repos/" + repository + "/commits/" + sha
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("monitor timeout")
    checks = api_json(base + "/check-runs?per_page=100",
                      timeout=min(40, remaining), caller=caller)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("monitor timeout")
    statuses = api_json(base + "/status?per_page=100",
                        timeout=min(40, remaining), caller=caller)
    check_state, check_rows = classify_checks(checks, sha)
    status_state, status_rows = classify_statuses(statuses)
    if "FAIL" in (check_state, status_state):
        overall = "FAIL"
    elif "PENDING" in (check_state, status_state):
        overall = "PENDING"
    elif check_state == "PASS" and status_state in ("PASS", "ABSENT"):
        overall = "PASS"
    else:
        overall = "UNKNOWN"
    return overall, {
        "head_sha": sha,
        "check_runs": check_rows,
        "commit_statuses": status_rows,
    }


def monitor(task_id, repository, *, max_attempts=12, max_api_calls=36,
            timeout_seconds=300, poll_interval=5):
    """Single-writer wrapper: monitor cannot race a publisher or another monitor."""
    if not TASK_ID.fullmatch(task_id) or not REPOSITORY.fullmatch(repository):
        raise RuntimeError("invalid task ID or repository")
    if not (1 <= max_attempts <= 100):
        raise RuntimeError("max attempts must be 1..100")
    if not (3 <= max_api_calls <= 300):
        raise RuntimeError("max API calls must be 3..300")
    if not (0 < timeout_seconds <= 3600):
        raise RuntimeError("timeout must be 1..3600 seconds")
    if not (0 <= poll_interval <= 300):
        raise RuntimeError("poll interval must be 0..300 seconds")
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / "controller.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another controller task is running") from exc
        return _monitor_locked(
            task_id, repository,
            max_attempts=max_attempts, max_api_calls=max_api_calls,
            timeout_seconds=timeout_seconds, poll_interval=poll_interval,
        )


def _monitor_locked(task_id, repository, *, max_attempts,
                    max_api_calls, timeout_seconds, poll_interval):
    """Exact, persisted request budget and wall-clock window across restarts."""
    path, state, url, sha = _read_state(task_id, repository)
    if state.get("ci_head_sha") not in (None, sha):
        raise RuntimeError("saved CI head SHA differs from recorded publish_commit")

    # Do not requery terminal snapshots or mutate their historical evidence.
    if state.get("ci_status") in {"PASS", "FAIL", "UNKNOWN", "TIMEOUT"}:
        result = {
            "task_id": task_id, "ci_status": state["ci_status"],
            "pr_url": url, "head_sha": sha,
            "observations": state.get("ci_observations", []),
        }
        print(json.dumps(result, indent=2))
        return 0 if result["ci_status"] == "PASS" else 1

    budget = {
        "max_attempts": max_attempts,
        "max_api_calls": max_api_calls,
        "timeout_seconds": timeout_seconds,
    }
    if state.get("ci_budget") not in (None, budget):
        raise RuntimeError("saved monitor budget differs; refuse to reset/widen limits")
    state["ci_budget"] = budget
    if "ci_started_epoch" not in state:
        state["ci_started_epoch"] = time.time()
    started_epoch = float(state["ci_started_epoch"])
    # Persisted epoch enforces the original total budget on resumed invocations.
    time_left = max(0.0, timeout_seconds - max(0.0, time.time() - started_epoch))
    deadline = time.monotonic() + time_left
    observations = list(state.get("ci_observations", []))
    if len(observations) > max_attempts:
        raise RuntimeError("saved observations exceed the configured attempt budget")
    api_calls = state.get("ci_api_calls", 0)
    if type(api_calls) is not int or not (0 <= api_calls <= max_api_calls):
        raise RuntimeError("invalid saved API call count")
    state["ci_head_sha"] = sha
    save(path, state)

    def counted_call(argv, *, timeout):
        """Reserve a request in the state file before issuing any remote API call."""
        nonlocal api_calls
        if api_calls >= max_api_calls:
            raise RuntimeError("monitor request budget exhausted")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("monitor timeout")
        api_calls += 1
        state["ci_api_calls"] = api_calls
        save(path, state)
        return call(argv, timeout=min(timeout, remaining))

    final = "PENDING" if observations else "TIMEOUT"
    for attempt in range(len(observations), max_attempts):
        if deadline - time.monotonic() <= 0 or api_calls >= max_api_calls:
            break
        try:
            outcome, detail = _one_observation(
                repository, state, url, sha, deadline, counted_call,
            )
        except RuntimeError as exc:
            message = str(exc)[:300]
            budget_expired = (
                "monitor timeout" in message or "timeout after" in message
                or "budget exhausted" in message
            )
            outcome = "TIMEOUT" if budget_expired else "UNKNOWN"
            detail = {"reason": message, "head_sha": sha}
        record = {
            "attempt": attempt + 1,
            "outcome": outcome,
            "api_calls": api_calls,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            **detail,
        }
        observations.append(record)
        state.update(
            ci_status=outcome,
            ci_head_sha=sha,
            ci_attempts=len(observations),
            ci_api_calls=api_calls,
            ci_observations=observations,
        )
        if outcome == "UNKNOWN" and detail.get("reason") == "SHA_MISMATCH":
            state["ci_reason"] = "PR head SHA changed unexpectedly"
        save(path, state)
        final = outcome
        if outcome in {"PASS", "FAIL", "UNKNOWN", "TIMEOUT"}:
            break
        if attempt + 1 < max_attempts and poll_interval:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

    if final == "PENDING" or (not observations and final == "TIMEOUT"):
        final = "TIMEOUT"
        state.update(ci_status=final, ci_reason="polling budget exhausted",
                     ci_api_calls=api_calls)
        save(path, state)
    result = {
        "task_id": task_id, "ci_status": final,
        "pr_url": url, "head_sha": sha,
        "observations": state.get("ci_observations", []),
        "api_calls": api_calls,
    }
    print(json.dumps(result, indent=2))
    return 0 if final == "PASS" else 1


def check(task_id, repository):
    if not TASK_ID.fullmatch(task_id) or not REPOSITORY.fullmatch(repository):
        raise RuntimeError("invalid task ID or repository")
    _path, state, url, sha = _read_state(task_id, repository)
    pr = _pr_metadata(url, repository)
    if not _validate_pr(pr, state, sha):
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
    elif check_state == "PASS" and status_state in ("PASS", "ABSENT"):
        # This pilot requires at least one completed successful check run.
        overall = "PASS"
    else:
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
    sub = p.add_subparsers(dest="command")
    one = sub.add_parser("check", help="one bounded CI snapshot (Phase 2 compatibility)")
    monitor_parser = sub.add_parser("monitor", help="bounded exact-SHA CI polling")
    for parser in (one, monitor_parser):
        parser.add_argument("--task-id", required=True)
        parser.add_argument("--repository", required=True)
    monitor_parser.add_argument("--max-attempts", type=int, default=12)
    monitor_parser.add_argument("--max-api-calls", type=int, default=36)
    monitor_parser.add_argument("--timeout-seconds", type=float, default=300)
    monitor_parser.add_argument("--poll-interval", type=float, default=5)
    # Keep the original invocation form working for Phase 2 operators.
    if len(sys.argv) > 1 and sys.argv[1] not in {"check", "monitor"}:
        a = p.parse_args(["check", *sys.argv[1:]])
    else:
        a = p.parse_args()
    try:
        if a.command == "monitor":
            return monitor(a.task_id, a.repository,
                           max_attempts=a.max_attempts,
                           max_api_calls=a.max_api_calls,
                           timeout_seconds=a.timeout_seconds,
                           poll_interval=a.poll_interval)
        return check(a.task_id, a.repository)
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR:", str(exc)[:450], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
