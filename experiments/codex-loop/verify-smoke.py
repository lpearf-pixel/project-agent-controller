#!/usr/bin/env python3
"""Trusted disposable-repo validator: hello.py must print exactly 'hello'."""
import subprocess
import sys
from pathlib import Path

def main():
    hello = Path.cwd() / "hello.py"
    if not hello.is_file():
        print(f"FAIL: missing {hello}", file=sys.stderr)
        return 1
    if hello.is_symlink():
        print("FAIL: hello.py is a symlink", file=sys.stderr)
        return 1
    try:
        result = subprocess.run([sys.executable, "-I", str(hello)],
                                capture_output=True, text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        print("FAIL: hello.py timed out after 15 seconds", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"FAIL: exit code {result.returncode}: {result.stderr[-400:]}", file=sys.stderr)
        return 1
    if result.stdout.strip().lower() != "hello":
        print(f"FAIL: expected output 'hello', got {result.stdout[:160]!r}", file=sys.stderr)
        return 1
    print(f"PASS: {hello} exists, executes, and prints hello")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
