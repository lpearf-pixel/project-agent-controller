#!/usr/bin/env python3
"""Summarize a failed Codex pilot run without dumping raw transcripts or credentials."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess

DEFAULT_ROOT = Path(os.environ.get("PAC_CODEX_LOOP_DATA", str(Path.home() / ".local/share/pac-codex-loop")))
SENSITIVE = re.compile(
    r"(?:sk-[a-zA-Z0-9_-]{12,}|github_pat_[a-zA-Z0-9_]{12,}|ghp_[a-zA-Z0-9]{12,}|"
    r"Bearer\s+\S+|(?:token|password|secret|api_key)\s*[:=]\s*\S+)",
    flags=re.IGNORECASE,
)


def clean(text, limit=450):
    text = SENSITIVE.sub("[REDACTED]", str(text))
    return text.replace("\r", " ").replace("\n", " ")[:limit]


def main():
    parser = argparse.ArgumentParser(description="Inspect one Codex loop task's saved local evidence")
    parser.add_argument("task_id", help="12-hex task ID from the failed task JSON")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{12}", args.task_id):
        parser.error("task_id must be exactly 12 lowercase hex characters")
    root = DEFAULT_ROOT.expanduser() / "runs" / args.task_id
    statefile = root / "state.json"
    if not statefile.is_file():
        parser.error(f"run state not found: {statefile}")
    state = json.loads(statefile.read_text(encoding="utf-8"))
    print("TASK:", args.task_id, "| STATUS:", state.get("status"))
    print("REASON:", clean(state.get("reason", "(none)")))
    print("WORKTREE:", state.get("worktree", "(unknown)"))
    logs = sorted(root.glob("codex-*.jsonl"))
    print("CODEX LOGS:", len(logs))
    if not logs:
        print("DIAGNOSIS: no Codex event logs; preflight or process startup failed.")
    for path in logs:
        counts = Counter()
        failures = []
        last_message = ""
        malformed = 0
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 30000:
                    failures.append("Log exceeds 30000 lines, display truncated")
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                event_type = str(event.get("type", "unknown"))
                counts[event_type] += 1
                item = event.get("item")
                if not isinstance(item, dict):
                    item = {}
                if event_type in ("error", "turn.failed", "turn.cancelled"):
                    details = event.get("message") or event.get("error") or event.get("reason") or ""
                    failures.append(event_type + ": " + clean(details))
                if event_type == "item.completed":
                    typ = item.get("type", "")
                    if typ == "agent_message":
                        last_message = clean(item.get("text", ""))
                    elif typ == "command_execution":
                        exit_code = item.get("exit_code")
                        if exit_code not in (None, 0):
                            details = item.get("aggregated_output") or item.get("status") or ""
                            failures.append("command exit " + str(exit_code) + ": " + clean(details))
                    elif typ == "file_change" and item.get("status") == "failed":
                        failures.append("file_change failed: " + clean(item.get("changes", "unknown")))
        print("\n" + path.name)
        print(" EVENTS:", ", ".join(f"{k}={v}" for k, v in counts.most_common(12)))
        print(" MALFORMED LINES:", malformed)
        for f in failures[-8:]:
            print(" FAILURE:", f)
        if not failures and counts.get("turn.completed", 0) == 0:
            print(" WARNING: no turn.completed found (incomplete event stream)")
        if last_message:
            print(" LAST AGENT MESSAGE:", last_message)
    work = Path(state.get("worktree", "/does-not-exist"))
    if work.is_dir():
        p = subprocess.run(["git", "-C", str(work), "status", "--short", "--untracked-files=all"],
                           capture_output=True, text=True, timeout=15, check=False)
        print("\nGIT CHANGES:")
        print(clean(p.stdout, 1200) or "(none)")
        print("GIT STATUS ERROR:", clean(p.stderr) if p.returncode else "(none)")
        print("HELLO.PY EXISTS:", (work / "hello.py").is_file())
    else:
        print("WORKTREE NOT FOUND:", work)
    print("\nNo retries or filesystem changes were made by this diagnostic.")


if __name__ == "__main__":
    main()
