from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from project_agent_controller.domain.models import TaskTemplateConfig
from project_agent_controller.runner.executor import TaskExecutor


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repos"
    repository = root / "demo"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    return root, repository


def _task(
    *arguments: str, timeout_seconds: int = 10, output_max_bytes: int = 4096
) -> TaskTemplateConfig:
    return TaskTemplateConfig(
        task_id="verify",
        repository_ref="local://demo",
        executable="python3",
        arguments=arguments,
        timeout_seconds=timeout_seconds,
        output_max_bytes=output_max_bytes,
    )


def test_executor_runs_only_committed_head_in_disposable_workspace(tmp_path: Path) -> None:
    root, repository = _repository(tmp_path)
    (repository / "untracked-secret.txt").write_text("secret\n", encoding="utf-8")
    before = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    executor = TaskExecutor(root, tmp_path / "runner-data")

    result = executor.execute(
        _task(
            "-c",
            "from pathlib import Path; print(Path('tracked.txt').read_text().strip()); "
            "print(Path('untracked-secret.txt').exists()); Path('generated.txt').write_text('x')",
        )
    )

    after = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert result.classification == "success"
    assert result.stdout == "committed\nFalse\n"
    assert before == after
    assert not (repository / "generated.txt").exists()


def test_executor_never_interprets_arguments_as_shell(tmp_path: Path) -> None:
    root, repository = _repository(tmp_path)
    marker = repository / "shell-injected"
    executor = TaskExecutor(root, tmp_path / "runner-data")

    result = executor.execute(
        TaskTemplateConfig(
            task_id="verify",
            repository_ref="local://demo",
            executable="printf",
            arguments=("%s", f"$(touch {marker})"),
        )
    )

    assert result.classification == "success"
    assert "$(touch" in result.stdout
    assert not marker.exists()


def test_executor_times_out_and_bounds_redacted_output(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    executor = TaskExecutor(root, tmp_path / "runner-data")

    timeout = executor.execute(
        _task("-c", "import time; print('started', flush=True); time.sleep(5)", timeout_seconds=1)
    )
    bounded = executor.execute(
        _task("-c", "print('x' * 10000)", output_max_bytes=1024)
    )

    assert timeout.classification == "timeout"
    assert timeout.exit_code is None
    assert "started" in timeout.stdout
    assert bounded.classification == "success"
    assert len((bounded.stdout + bounded.stderr).encode("utf-8")) <= 1024
    assert bounded.output_truncated is True


def test_executor_rejects_repository_escape_before_starting_process(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    executor = TaskExecutor(root, tmp_path / "runner-data")
    task = _task("-c", "print('never')").model_copy(
        update={"repository_ref": "local://../escape"}
    )

    result = executor.execute(task)

    assert result.classification == "policy_error"
    assert result.stdout == ""
    assert str(tmp_path) not in result.stderr


def test_executor_does_not_inherit_credentials(tmp_path: Path, monkeypatch) -> None:
    root, _ = _repository(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-reach-child")
    executor = TaskExecutor(root, tmp_path / "runner-data")

    result = executor.execute(
        _task("-c", "import os; print('GITHUB_TOKEN' in os.environ); print(os.environ['HOME'])")
    )

    assert result.classification == "success"
    assert result.stdout.splitlines()[0] == "False"
    assert str(tmp_path) not in result.stdout


def test_executor_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    executor = TaskExecutor(root, tmp_path / "runner-data")

    result = executor.execute(
        _task(
            "-c",
            "import subprocess,time; p=subprocess.Popen(['sleep','30']); "
            "print(p.pid, flush=True); time.sleep(30)",
            timeout_seconds=1,
        )
    )

    child_pid = int(result.stdout.strip())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out child process is still alive")


def test_executor_stops_running_process_when_control_gate_closes(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    checks = [0]

    def allowed() -> bool:
        checks[0] += 1
        return checks[0] < 5

    executor = TaskExecutor(root, tmp_path / "runner-data", execution_allowed=allowed)

    result = executor.execute(_task("-c", "import time; time.sleep(30)"))

    assert result.classification == "blocked"
    assert result.exit_code is None
