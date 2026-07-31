from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.domain.models import TaskTemplateConfig


class TaskPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    classification: str
    exit_code: int | None
    stdout: str
    stderr: str
    output_truncated: bool


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self._remaining = limit
        self._lock = threading.Lock()
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.truncated = False

    def consume(self, stream: BinaryIO, target: bytearray) -> None:
        while chunk := stream.read(8192):
            with self._lock:
                accepted = chunk[: self._remaining]
                target.extend(accepted)
                self._remaining -= len(accepted)
                if len(accepted) < len(chunk):
                    self.truncated = True


class TaskExecutor:
    def __init__(
        self,
        local_repos_root: Path,
        data_dir: Path,
        git_executable: Path | None = None,
        execution_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self.local_repos_root = local_repos_root.resolve()
        self.data_dir = data_dir
        discovered_git = shutil.which("git") if git_executable is None else git_executable
        self.git_executable = None if discovered_git is None else Path(discovered_git).resolve()
        self.execution_allowed = execution_allowed or (lambda: True)

    def execute(self, task: TaskTemplateConfig) -> ExecutionResult:
        try:
            if not self.execution_allowed():
                return self._error("blocked", "controller does not allow task execution")
            repository = self._resolve_repository(task.repository_ref)
            if self.git_executable is None or not self.git_executable.is_file():
                return self._error("infrastructure_error", "git executable is unavailable")
            executable = shutil.which(task.executable)
            if executable is None:
                return self._error("infrastructure_error", "configured executable is unavailable")
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix="task-", dir=self.data_dir) as temporary:
                workspace = Path(temporary) / "workspace"
                workspace.mkdir(mode=0o700)
                workspace = workspace.resolve()
                self._extract_head(repository, workspace)
                if not self.execution_allowed():
                    return self._error("blocked", "controller does not allow task execution")
                working_directory = (workspace / task.working_directory).resolve()
                if (
                    not working_directory.is_relative_to(workspace)
                    or not working_directory.is_dir()
                ):
                    return self._error(
                        "policy_error", "configured working directory is unavailable"
                    )
                return self._run(task, Path(executable).resolve(), workspace, working_directory)
        except TaskPolicyError:
            return self._error("policy_error", "task violates the workspace policy")
        except (OSError, subprocess.SubprocessError, tarfile.TarError, ValueError):
            return self._error("infrastructure_error", "isolated workspace preparation failed")

    def _resolve_repository(self, repository_ref: str) -> Path:
        if not repository_ref.startswith("local://"):
            raise ValueError("unsupported repository reference")
        relative = repository_ref.removeprefix("local://")
        candidate = (self.local_repos_root / relative).resolve()
        if not candidate.is_relative_to(self.local_repos_root):
            raise TaskPolicyError("repository escaped local root")
        if not candidate.is_dir():
            raise ValueError("repository is unavailable")
        return candidate

    def _extract_head(self, repository: Path, workspace: Path) -> None:
        archive_path = workspace.parent / "head.tar"
        with archive_path.open("wb") as archive:
            completed = subprocess.run(
                [
                    str(self.git_executable),
                    "-C",
                    str(repository),
                    "archive",
                    "--format=tar",
                    "HEAD",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=archive,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        if completed.returncode != 0:
            raise ValueError("git archive failed")
        with tarfile.open(archive_path, mode="r:") as bundle:
            for member in bundle.getmembers():
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("unsafe archive member")
            bundle.extractall(workspace, filter="data")

    def _run(
        self,
        task: TaskTemplateConfig,
        executable: Path,
        workspace: Path,
        working_directory: Path,
    ) -> ExecutionResult:
        home = workspace.parent / "home"
        home.mkdir(mode=0o700)
        environment = {
            "HOME": str(home),
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "CI": "1",
            **task.environment,
        }
        process = subprocess.Popen(
            [str(executable), *task.arguments],
            cwd=working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("subprocess pipes were not created")
        capture = _BoundedCapture(task.output_max_bytes)
        readers = [
            threading.Thread(target=capture.consume, args=(process.stdout, capture.stdout)),
            threading.Thread(target=capture.consume, args=(process.stderr, capture.stderr)),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + task.timeout_seconds
        classification: str | None = None
        exit_code: int | None = None
        while classification is None:
            if not self.execution_allowed():
                classification = "blocked"
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                classification = "timeout"
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                break
            try:
                exit_code = process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue
            classification = "success" if exit_code == 0 else "failed"
        for reader in readers:
            reader.join(timeout=5)
        stdout = self._sanitize(bytes(capture.stdout), workspace.parent)
        stderr = self._sanitize(bytes(capture.stderr), workspace.parent)
        return ExecutionResult(
            classification=classification,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_truncated=capture.truncated,
        )

    @staticmethod
    def _sanitize(value: bytes, temporary_root: Path) -> str:
        decoded = value.decode("utf-8", errors="replace").replace(
            str(temporary_root), "<workspace>"
        )
        redacted = Redactor().redact(decoded)
        return redacted.text if redacted.safe_to_export else "<unsafe-output-redacted>"

    @staticmethod
    def _error(classification: str, message: str) -> ExecutionResult:
        return ExecutionResult(
            classification=classification,
            exit_code=None,
            stdout="",
            stderr=message,
            output_truncated=False,
        )
