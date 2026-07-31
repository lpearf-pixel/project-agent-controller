from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_agent_controller.observer.git_provider import parse_porcelain_v2
from project_agent_controller.observer.git_transport import GitReadTransport, GitTransportError


class Completed:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_transport_uses_fixed_read_only_argv_and_safe_env(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def runner(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return Completed(stdout=b"# branch.oid " + b"a" * 40 + b"\n# branch.head main\n")

    transport = GitReadTransport(Path("/usr/bin/git"), runner=runner)
    text = transport.status(tmp_path, include_untracked=True)

    assert "branch.head main" in text
    call = calls[0]
    assert call["argv"] == [
        "/usr/bin/git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-C",
        str(tmp_path),
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=normal",
        "--ignore-submodules=all",
    ]
    assert call["shell"] is False
    assert call["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert call["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_transport_rejects_large_output(tmp_path: Path) -> None:
    transport = GitReadTransport(
        Path("/usr/bin/git"),
        max_output_bytes=8,
        runner=lambda *_a, **_k: Completed(stdout=b"0123456789"),
    )
    with pytest.raises(GitTransportError, match="output exceeds"):
        transport.status(tmp_path, include_untracked=False)


def test_porcelain_parser_counts_and_branch_fields() -> None:
    text = "\n".join(
        [
            "# branch.oid 0123456789012345678901234567890123456789",
            "# branch.head feature/test",
            "# branch.upstream origin/feature/test",
            "# branch.ab +2 -3",
            "1 M. N... 100644 100644 100644 a b staged.txt",
            "1 .M N... 100644 100644 100644 a b unstaged.txt",
            "u UU N... 100644 100644 100644 100644 a b c conflict.txt",
            "? new.txt",
        ]
    )
    snapshot = parse_porcelain_v2(text)
    assert snapshot.head_sha == "0123456789012345678901234567890123456789"
    assert snapshot.branch == "feature/test"
    assert snapshot.upstream == "origin/feature/test"
    assert snapshot.ahead == 2 and snapshot.behind == 3
    assert snapshot.staged_count == 1
    assert snapshot.unstaged_count == 1
    assert snapshot.conflict_count == 1
    assert snapshot.untracked_count == 1
    assert snapshot.dirty is True


def test_porcelain_parser_handles_detached_head() -> None:
    snapshot = parse_porcelain_v2(
        "# branch.oid 0123456789012345678901234567890123456789\n# branch.head (detached)\n"
    )
    assert snapshot.detached is True
    assert snapshot.branch is None


def test_real_temporary_repository_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")

    transport = GitReadTransport(Path(subprocess.check_output(["which", "git"], text=True).strip()))
    snapshot = parse_porcelain_v2(transport.status(repo, include_untracked=True))

    assert snapshot.head_sha and len(snapshot.head_sha) == 40
    assert snapshot.unstaged_count == 1
    assert snapshot.untracked_count == 1
