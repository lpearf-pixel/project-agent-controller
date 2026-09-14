"""Opt-in i9/M2 task runner. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL = os.environ.get("MESH_MODEL", "qwen2.5-coder:7b")
URL = os.environ.get("MESH_OLLAMA_URL", "http://127.0.0.1:11435").rstrip("/")
DATA = Path(os.environ.get("MESH_DATA_DIR", "~/.local/share/project-agent-mesh")).expanduser()
LIMIT = 12000


def http_json(path: str, payload: dict | None = None, timeout: int = 90) -> dict:
    if not URL.startswith("http://127.0.0.1:") and not URL.startswith("http://localhost:"):
        raise ValueError("MESH_OLLAMA_URL must use a local SSH tunnel")
    body = None if payload is None else json.dumps(payload).encode()
    req = Request(URL + path, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Ollama unavailable at {URL}: {exc}") from exc


def generate(role: str, text: str) -> dict:
    response = http_json("/api/generate", {
        "model": MODEL, "stream": False, "think": False,
        "system": role + "\nBe concise. Cite file paths when possible. Never claim tests passed without evidence.",
        "prompt": text[:LIMIT], "options": {"num_predict": 700},
    }, timeout=240)
    if not response.get("done") or not isinstance(response.get("response"), str):
        raise RuntimeError("Incomplete Ollama response")
    return {"text": response["response"][:6000],
            "input_tokens": response.get("prompt_eval_count"),
            "output_tokens": response.get("eval_count")}


def command(argv: list[str], cwd: Path, timeout: int = 900) -> dict:
    if not argv or not all(isinstance(s, str) and s for s in argv):
        raise ValueError("Command must be a nonempty array of strings")
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           errors="replace", timeout=timeout, check=False)
        return {"argv": argv, "exit_code": p.returncode,
                "output": (p.stdout + "\n" + p.stderr)[-LIMIT:]}
    except subprocess.TimeoutExpired:
        return {"argv": argv, "exit_code": 124, "output": f"Timed out after {timeout}s"}


def read_context(worktree: Path, paths: list[str]) -> str:
    chunks = []
    for item in paths[:12]:
        path = (worktree / item).resolve()
        if not path.is_relative_to(worktree.resolve()) or not path.is_file():
            raise ValueError(f"Context path missing or outside worktree: {item}")
        chunks.append(f"### {item}\n{path.read_text(errors='replace')[:3000]}")
    return "\n".join(chunks)[:9000]


def validate_task(task: dict) -> tuple[str, Path, str]:
    ident, repo, objective = task["id"], Path(task["repo"]).expanduser().resolve(), task["objective"]
    if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", ident):
        raise ValueError("Invalid task id")
    if not isinstance(objective, str) or not 5 <= len(objective) <= 2000:
        raise ValueError("Objective length must be 5..2000")
    if not repo.is_dir() or command(["git", "rev-parse", "--is-inside-work-tree"], repo)["exit_code"]:
        raise ValueError("repo must be a local Git repository")
    if not isinstance(task.get("context", []), list) or not isinstance(task.get("tests", []), list):
        raise ValueError("context and tests must be lists")
    for test in task.get("tests", []):
        if not isinstance(test, list) or not test or not all(isinstance(x, str) and x for x in test):
            raise ValueError("Each test must be an argv array")
    return ident, repo, objective


def run_task(task: dict, code: bool = False) -> dict:
    ident, repo, objective = validate_task(task)
    base = task.get("base", "HEAD")
    if not isinstance(base, str) or base.startswith("-") or not re.fullmatch(r"[a-zA-Z0-9_./-]+", base):
        raise ValueError("Invalid base ref")
    root = DATA.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    worktree = root / "worktrees" / ident
    report_file = root / "reports" / (ident + ".json")
    if worktree.exists() or report_file.exists():
        raise FileExistsError(f"Task {ident} already exists; choose another id")
    worktree.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if command(["git", "rev-parse", "--verify", base + "^{commit}"], repo)["exit_code"]:
        raise ValueError("Base ref is not a local commit; fetch manually if needed")
    created = command(["git", "worktree", "add", "--detach", str(worktree), base], repo)
    if created["exit_code"]:
        raise RuntimeError("Cannot create worktree: " + created["output"])
    report = {"id": ident, "worktree": str(worktree), "status": "running", "code_requested": code,
              "steps": {}, "local_tokens": {"input": 0, "output": 0}, "cloud_tokens": None}
    def save() -> None:
        tmp = report_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        tmp.chmod(0o600)
        tmp.replace(report_file)
    def record(name: str, result: dict) -> None:
        report["steps"][name] = result
        for field, key in (("input", "input_tokens"), ("output", "output_tokens")):
            report["local_tokens"][field] += result.get(key) or 0
        save()
    try:
        save()
        context = read_context(worktree, task.get("context", []))
        research = generate("Research worker: inspect only the supplied context; report uncertainties.",
                            f"Task: {objective}\n{context}")
        record("research", research)
        if code:
            if not shutil.which("codex"):
                raise RuntimeError("Codex CLI missing; install/login on i9 and retry with a new task id")
            prompt = (f"Task: {objective}\nResearch (unverified): {research['text'][:3000]}\n"
                      "Work only in this worktree. Do not commit, push, merge, or modify other repositories.")
            result = command(["codex", "exec", "--sandbox", "workspace-write", prompt],
                             worktree, timeout=1800)
            record("coding", result)
            if result["exit_code"]:
                raise RuntimeError("Codex failed; inspect report and worktree")
        tests = []
        for argv in task.get("tests", []):
            result = command(argv, worktree)
            tests.append(result)
            report["steps"]["tests"] = tests
            save()
            if result["exit_code"]:
                report["status"] = "tests_failed"
                break
        if not tests or all(t["exit_code"] == 0 for t in tests):
            diff = command(["git", "diff", "--no-ext-diff", "HEAD", "--"], worktree)
            untracked = command(["git", "ls-files", "--others", "--exclude-standard"], worktree)
            if diff["output"].strip() or untracked["output"].strip():
                review = generate("Reviewer: review supplied diff; untracked files require manual inspection.",
                                  f"Task: {objective}\nTests: {[(t['argv'], t['exit_code']) for t in tests]}\n"
                                  f"Diff: {diff['output'][:9500]}\nUntracked: {untracked['output'][:1000]}")
                record("review", review)
            report["status"] = "review_required" if code else "completed"
        save()
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)[:600]
        save()
        raise
    return report


def doctor() -> dict:
    tags = http_json("/api/tags")
    names = [m.get("name") for m in tags.get("models", [])]
    if MODEL not in names:
        raise RuntimeError(f"Model {MODEL} missing on M2; available: {names}")
    return {"ollama": URL, "model": MODEL, "git": bool(shutil.which("git")),
            "codex": bool(shutil.which("codex"))}


def smoke() -> dict:
    doctor()
    with tempfile.TemporaryDirectory(prefix="agent-mesh-smoke-") as d:
        repo = Path(d) / "repo"
        repo.mkdir()
        for argv in (["git", "init"], ["git", "config", "user.name", "Mesh Smoke"],
                     ["git", "config", "user.email", "mesh@example.invalid"]):
            result = command(argv, repo)
            if result["exit_code"]:
                raise RuntimeError(result["output"])
        (repo / "hello.txt").write_text("Smoke fixture\n")
        for argv in (["git", "add", "hello.txt"], ["git", "commit", "-m", "fixture"]):
            result = command(argv, repo)
            if result["exit_code"]:
                raise RuntimeError(result["output"])
        ident = "smoke-" + str(int(time.time() * 1000))
        return run_task({"id": ident, "repo": str(repo), "objective": "Summarize the smoke fixture.",
                         "context": ["hello.txt"], "tests": [["git", "status", "--porcelain"]]})


def main() -> int:
    parser = argparse.ArgumentParser(description="i9 orchestrator / M2 Ollama via SSH tunnel")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor")
    sub.add_parser("smoke")
    run = sub.add_parser("run")
    run.add_argument("task", type=Path)
    run.add_argument("--code", action="store_true", help="Explicitly allow Codex edits in new worktree")
    args = parser.parse_args()
    try:
        result = doctor() if args.action == "doctor" else (
            smoke() if args.action == "smoke" else
            run_task(json.loads(args.task.read_text()), code=args.code))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in ("failed", "tests_failed") else 1
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
