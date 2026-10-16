#!/usr/bin/env python3
"""PAC Ubuntu runner command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from engine import load_runner


def data_dir() -> Path:
    return Path(os.environ.get("PAC_CODEX_LOOP_DATA", str(Path.home() / ".local/share/pac-codex-loop"))) / "runner"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("dry-run", "run-once", "health", "status", "doctor", "stop", "restart", "uninstall"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    root = data_dir()
    runner = load_runner(root)
    if args.command == "doctor":
        ok, errors = runner.doctor()
        print(json.dumps({"isolation": "PASS" if ok else "BLOCKED", "errors": errors}, indent=2))
        return 0 if ok else 2
    if args.command in ("dry-run", "run-once"):
        result = runner.run_once(execute=args.command == "run-once" and args.execute)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") in ("DRY_RUN", "IDLE", "LOCAL_PASS", "PUBLISHING", "PR_DRAFT", "CI_PASS") else 2
    if args.command == "health":
        print(json.dumps({"service": "pac-codex-runner", "data": str(root),
                          "runner_lock": (root / "runner.lock").exists()}, indent=2))
        return 0
    if args.command == "status":
        rows = []
        for path in sorted((root / "runs").glob("*/state.json")) if (root / "runs").exists() else []:
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                rows.append({"path": str(path), "status": "CORRUPT"})
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if args.command == "stop":
        return subprocess.call(["systemctl", "--user", "stop", "pac-codex-runner.timer", "pac-codex-runner.service"])
    if args.command == "restart":
        return subprocess.call(["systemctl", "--user", "restart", "pac-codex-runner.timer"])
    if args.command == "uninstall":
        if not args.force:
            print("Refusing uninstall without --force", file=sys.stderr)
            return 2
        for name in ("pac-codex-runner.service", "pac-codex-runner.timer"):
            path = Path.home() / ".config/systemd/user" / name
            if path.exists():
                path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print("Removed only PAC runner user units; task state was preserved.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
