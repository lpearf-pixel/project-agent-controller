"""One-task-at-a-time PAC runner engine."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

LOOP_ROOT = Path(__file__).resolve().parents[1]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))
from github import GitHubClient
from isolation import check
from manifest import TaskManifest, parse_issue_body
from state import StateStore
from ci_check import classify_checks, classify_statuses

HERE = LOOP_ROOT


class Runner:
    def __init__(self, *, data: Path, repository: str, trusted_issue: int,
                 trusted_author: str, codex_user: str = "pac-codex",
                 publisher_user: str = "pac-publisher", repo_path: Path | None = None):
        self.store = StateStore(data)
        self.repository = repository
        self.trusted_issue = trusted_issue
        self.trusted_author = trusted_author
        self.codex_user = codex_user
        self.publisher_user = publisher_user
        self.repo_path = (repo_path or Path(os.environ.get("PAC_REPO_PATH", ""))).expanduser()

    def doctor(self) -> tuple[bool, list[str]]:
        return check(codex_user=self.codex_user, publisher_user=self.publisher_user)

    def discover(self, client: GitHubClient) -> list[TaskManifest]:
        tasks = []
        for item in client.tasks():
            author = ((item.get("author") or {}).get("login") if isinstance(item.get("author"), dict)
                      else None)
            try:
                tasks.append(parse_issue_body(item.get("body", ""), issue_number=int(item["number"]),
                                              author=author or "", trusted_author=self.trusted_author,
                                              trusted_repo=self.repository, trusted_issue=self.trusted_issue))
            except (KeyError, TypeError, ValueError):
                continue
        return tasks

    def run_once(self, *, execute: bool = False) -> dict:
        # A locked file descriptor must remain open for the complete task.
        with self.store.lock():
            return self._run_once_locked(execute=execute)

    def _run_once_locked(self, *, execute: bool = False) -> dict:
        ok, errors = self.doctor()
        if not ok:
            return {"status": "BLOCKED", "reason": "credential isolation failed", "errors": errors}
        client = GitHubClient(self.repository)
        tasks = self.discover(client)
        if not tasks:
            return {"status": "IDLE", "reason": "no trusted queued task"}
        manifest = tasks[0]
        fingerprint = hashlib.sha256(
            json.dumps(asdict(manifest), sort_keys=True).encode("utf-8")
        ).hexdigest()
        state = self.store.create(
            manifest.task_id,
            {
                "repo": manifest.repo,
                "base_sha": manifest.base_sha,
                "base_branch": manifest.base_branch,
                "issue_number": manifest.issue_number,
                "allowed_paths": list(manifest.allowed_paths),
                "checks": list(manifest.validators),
                "budget": manifest.budget,
                "execution_type": manifest.execution_type,
                "manifest_sha256": fingerprint,
            },
        )
        if state.get("manifest_sha256") != fingerprint:
            return {
                "status": "BLOCKED",
                "reason": "task manifest changed after first discovery",
                "task_id": manifest.task_id,
            }
        if state["status"] in {"CI_PASS", "BLOCKED", "FAILED", "CI_FAIL", "TIMEOUT", "NEEDS_HARDWARE"}:
            return state
        if not execute:
            return {"status": "DRY_RUN", "task_id": manifest.task_id, "repo": manifest.repo,
                    "base_sha": manifest.base_sha, "validators": list(manifest.validators)}
        return self.execute(manifest, state)

    def execute(self, manifest: TaskManifest, state: dict) -> dict:
        task_id = manifest.task_id
        run_dir = self.store.runs / task_id
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        prompt = run_dir / "prompt.md"
        prompt.write_text(manifest.prompt, encoding="utf-8")
        if state["status"] == "QUEUED":
            state = self.store.transition(task_id, "CLAIMED", "trusted manifest claimed")
        if state["status"] == "CLAIMED":
            state = self.store.transition(task_id, "CODING", "starting bounded Codex generation")
            if not self.repo_path.is_dir():
                return self.store.transition(task_id, "BLOCKED", "configured local checkout is missing")
            # Only validated, preapproved checks from manifest.py reach loop.py.
            # Without --check, the existing loop rejects every task.
            command = [
                sys.executable, str(HERE / "loop.py"), "run",
                "--repo", str(self.repo_path), "--task", str(prompt),
                "--repairs", str(manifest.budget["repairs"]),
                "--codex-timeout", str(manifest.budget["codexTimeoutSec"]),
            ]
            for validator in manifest.validators:
                command.extend(["--check", validator])
            try:
                result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=manifest.budget["codexTimeoutSec"],
                                        check=False)
            except subprocess.TimeoutExpired:
                return self.store.transition(task_id, "TIMEOUT", "Codex process exceeded budget")
            if result.returncode not in (0, 1):
                return self.store.transition(task_id, "FAILED", "Codex loop invocation failed")
            try:
                loop_state = json.loads(result.stdout)
            except (ValueError, IndexError):
                return self.store.transition(task_id, "FAILED", "Codex loop returned no state")
            if loop_state.get("status") == "BLOCKED":
                return self.store.transition(task_id, "BLOCKED", "Codex loop reported a safety block")
            if loop_state.get("status") != "LOCAL_PASS":
                return self.store.transition(task_id, "FAILED", "Codex loop did not reach LOCAL_PASS")
            changed = set(loop_state.get("changed_files", []))
            if not changed or not changed.issubset(set(manifest.allowed_paths)):
                return self.store.transition(task_id, "BLOCKED", "Codex changed a path outside the task manifest")
            state = self.store.transition(task_id, "VALIDATING", "Codex produced a local candidate",
                                          loop_task_id=loop_state.get("task_id"),
                                          worktree=loop_state.get("worktree"),
                                          changed_files=sorted(changed),
                                          artifact_sha256=loop_state.get("artifact_sha256"))
        if state["status"] == "VALIDATING":
            # Existing publisher performs the authoritative strict revalidation.
            state = self.store.transition(task_id, "LOCAL_PASS", "saved Codex loop reported local validation",
                                          artifact_sha256=state.get("artifact_sha256"))
        if state["status"] == "LOCAL_PASS":
            state = self.store.transition(task_id, "PUBLISHING", "ready for isolated Publisher")
            return self.publish(manifest, state)
        return state

    def publish(self, manifest: TaskManifest, state: dict) -> dict:
        """Stop until a credential-isolated publisher transport exists.

        A systemd --user process cannot safely invoke runuser to switch to
        pac-publisher, and merely changing HOME is not a security boundary.
        """
        return self.store.transition(
            manifest.task_id, "BLOCKED",
            "no verified cross-user publisher transport; cannot publish unattended",
        )

    def _legacy_publish_disabled(self, manifest: TaskManifest, state: dict) -> dict:
        # Never invoked: retained only for audit while a safe broker is designed.
        command = ["runuser", "-u", self.publisher_user, "--", sys.executable,
                   str(HERE / "publish.py"), "--task-id", state["loop_task_id"],
                   "--repository", manifest.repo, "--execute"]
        try:
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            return self.store.transition(manifest.task_id, "TIMEOUT", "Publisher exceeded budget")
        if result.returncode:
            return self.store.transition(manifest.task_id, "FAILED", "isolated Publisher failed")
        try:
            published = json.loads(result.stdout)
        except (ValueError, IndexError):
            return self.store.transition(manifest.task_id, "FAILED", "Publisher returned no state")
        if published.get("status") != "PR_DRAFT":
            return self.store.transition(manifest.task_id, "FAILED", "Publisher did not create Draft PR")
        state = self.store.transition(manifest.task_id, "PR_DRAFT", "Draft PR created",
                                      pr_url=published.get("pr_url"), publish_commit=published.get("publish_commit"))
        return self.check_ci(manifest, state)

    def check_ci(self, manifest: TaskManifest, state: dict) -> dict:
        state = self.store.transition(manifest.task_id, "CI_CHECKING", "observing exact Draft PR head")
        client = GitHubClient(manifest.repo, max_calls=manifest.budget["ciApiCalls"])
        deadline = time.monotonic() + manifest.budget["ciTimeoutSec"]
        interval = float(os.environ.get("PAC_CI_POLL_INTERVAL", "15"))
        while time.monotonic() <= deadline and client.calls < client.max_calls:
            pr = client.pr_view(state["pr_url"])
            sha = state.get("publish_commit")
            if pr.get("isDraft") is not True or pr.get("headRefOid") != sha:
                return self.store.transition(manifest.task_id, "FAILED", "Draft PR head or draft flag changed")
            checks = json.loads(client.call(["api", f"repos/{manifest.repo}/commits/{sha}/check-runs?per_page=100"]))
            statuses = json.loads(client.call(["api", f"repos/{manifest.repo}/commits/{sha}/status?per_page=100"]))
            check_state, _ = classify_checks(checks, sha)
            status_state, _ = classify_statuses(statuses)
            if "FAIL" in (check_state, status_state):
                return self.store.transition(manifest.task_id, "CI_FAIL", "GitHub CI reported failure")
            if check_state == "PASS" and status_state in ("PASS", "ABSENT"):
                return self.store.transition(manifest.task_id, "CI_PASS", "exact head SHA CI passed")
            if interval:
                time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        return self.store.transition(manifest.task_id, "TIMEOUT", "CI budget exhausted")


def load_runner(data: Path) -> Runner:
    return Runner(data=data, repository=os.environ.get("PAC_GITHUB_REPOSITORY", "lpearf-pixel/project-agent-controller"),
                  trusted_issue=int(os.environ.get("PAC_TRUSTED_ISSUE", "7")),
                  trusted_author=os.environ.get("PAC_TRUSTED_AUTHOR", "lpearf-pixel"),
                  codex_user=os.environ.get("PAC_CODEX_USER", "pac-codex"),
                  publisher_user=os.environ.get("PAC_PUBLISHER_USER", "pac-publisher"),
                  repo_path=Path(os.environ.get("PAC_REPO_PATH", "")) if os.environ.get("PAC_REPO_PATH") else None)
