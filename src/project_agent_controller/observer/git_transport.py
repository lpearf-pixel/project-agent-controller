from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class GitTransportError(RuntimeError):
    pass


class CompletedProcessLike(Protocol):
    stdout: bytes
    stderr: bytes
    returncode: int


Runner = Callable[..., CompletedProcessLike]


class GitReadTransport:
    def __init__(
        self,
        git_executable: Path,
        *,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 2 * 1024 * 1024,
        runner: Runner = subprocess.run,
    ) -> None:
        if not git_executable.is_absolute():
            raise ValueError("git_executable must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.git_executable = git_executable
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.runner = runner

    def status(self, repo_path: Path, *, include_untracked: bool) -> str:
        argv = [
            str(self.git_executable),
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-C",
            str(repo_path),
            "status",
            "--porcelain=v2",
            "--branch",
            "--untracked-files=normal" if include_untracked else "--untracked-files=no",
            "--ignore-submodules=all",
        ]
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        try:
            result = self.runner(
                argv,
                shell=False,
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as error:
            raise GitTransportError("git status timed out") from error
        except OSError as error:
            raise GitTransportError(
                f"git executable unavailable: {type(error).__name__}"
            ) from error

        if len(result.stdout) > self.max_output_bytes or len(result.stderr) > self.max_output_bytes:
            raise GitTransportError(f"git output exceeds {self.max_output_bytes} bytes")
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode != 0:
            safe = stderr[:300].replace(str(repo_path), "<repo>")
            raise GitTransportError(f"git status failed ({result.returncode}): {safe}")
        try:
            return result.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise GitTransportError("git status returned invalid UTF-8") from error
