"""Strict, data-only GitHub task manifest validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SAFE_EXEC = {"codex"}
# Validator commands are security-sensitive programs, not GitHub Issue instructions.
# This is the sole preapproved command for the initial OEPL software pilot.
APPROVED_VALIDATORS = {
    "python3 -m unittest discover -s experiments/oepl-s3-c6/tests -v",
}
BLOCKED = (".git", ".codex", ".env", ".github/workflows", "AGENTS.md")
MANIFEST_RE = re.compile(r"```(?:json|pac-task)\s*(\{.*?\})\s*```", re.S | re.I)


@dataclass(frozen=True)
class TaskManifest:
    task_id: str
    repo: str
    base_sha: str
    base_branch: str
    allowed_paths: tuple[str, ...]
    validators: tuple[str, ...]
    budget: dict[str, int]
    execution_type: str
    prompt: str
    issue_number: int
    issue_author: str


def _safe_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("allowed path must be relative")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in value.split("/")) or path.as_posix() != value:
        raise ValueError("unsafe allowed path")
    if any(value == item or value.startswith(item + "/") or item in path.parts for item in BLOCKED):
        raise ValueError("protected path is not allowed")
    return value


def parse_issue_body(
    body: str,
    *,
    issue_number: int,
    author: str,
    trusted_author: str,
    trusted_repo: str,
    trusted_issue: int,
) -> TaskManifest:
    if issue_number != trusted_issue or author != trusted_author:
        raise ValueError("issue source is not trusted")
    match = MANIFEST_RE.search(body or "")
    if not match:
        raise ValueError("missing fixed PAC task manifest")
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid task manifest JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("task manifest must be an object")
    required = {
        "taskId",
        "repo",
        "baseSha",
        "baseBranch",
        "allowedPaths",
        "validators",
        "budget",
        "executionType",
        "prompt",
    }
    if set(raw) != required:
        raise ValueError("manifest fields must exactly match the fixed schema")
    task_id = raw["taskId"]
    repo = raw["repo"]
    base_sha = raw["baseSha"]
    base_branch = raw["baseBranch"]
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise ValueError("invalid taskId")
    if not isinstance(repo, str) or repo != trusted_repo or not REPO.fullmatch(repo):
        raise ValueError("repository is not allowlisted")
    if not isinstance(base_sha, str) or not SHA.fullmatch(base_sha):
        raise ValueError("invalid base SHA")
    if not isinstance(base_branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", base_branch):
        raise ValueError("invalid base branch")
    paths = raw["allowedPaths"]
    validators = raw["validators"]
    if not isinstance(paths, list) or not (1 <= len(paths) <= 100):
        raise ValueError("allowedPaths must be a non-empty list")
    if not isinstance(validators, list) or not (1 <= len(validators) <= 16):
        raise ValueError("validators must be a non-empty list")
    safe_paths = tuple(_safe_path(item) for item in paths)
    safe_validators = []
    for item in validators:
        if not isinstance(item, str) or not item or len(item) > 500:
            raise ValueError("invalid validator")
        if any(token in item for token in (";", "&&", "||", "|", "`", "$(", "\n")):
            raise ValueError("validator contains shell syntax")
        safe_validators.append(item)
    budget = raw["budget"]
    if not isinstance(budget, dict) or set(budget) != {
        "codexGenerations",
        "repairs",
        "codexTimeoutSec",
        "ciApiCalls",
        "ciTimeoutSec",
    }:
        raise ValueError("invalid budget schema")
    limits = {
        "codexGenerations": 1,
        "repairs": 1,
        "codexTimeoutSec": 1200,
        "ciApiCalls": 36,
        "ciTimeoutSec": 300,
    }
    safe_budget: dict[str, int] = {}
    for key, limit in limits.items():
        value = budget[key]
        if type(value) is not int or value < 0 or value > limit:
            raise ValueError(f"budget exceeds {key} limit")
        safe_budget[key] = value
    if safe_budget["codexGenerations"] != 1 or safe_budget["codexTimeoutSec"] == 0:
        raise ValueError("invalid generation budget")
    if safe_budget["ciApiCalls"] < 3 or safe_budget["ciTimeoutSec"] == 0:
        raise ValueError("invalid CI budget")
    if not isinstance(raw["executionType"], str) or raw["executionType"] not in SAFE_EXEC:
        raise ValueError("unsupported execution type")
    prompt = raw["prompt"]
    if not isinstance(prompt, str) or not (10 <= len(prompt) <= 40_000):
        raise ValueError("prompt length is invalid")
    return TaskManifest(
        task_id,
        repo,
        base_sha,
        base_branch,
        safe_paths,
        tuple(safe_validators),
        safe_budget,
        raw["executionType"],
        prompt,
        issue_number,
        author,
    )
