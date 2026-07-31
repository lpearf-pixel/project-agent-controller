from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_agent_controller.control.service import ControlService
from project_agent_controller.domain.models import ProjectConfig, TaskTemplateConfig
from project_agent_controller.runner.executor import ExecutionResult
from project_agent_controller.runner.service import TaskRunBlocked, TaskRunnerService
from project_agent_controller.storage.database import Database


class FakeExecutor:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = results
        self.calls = 0

    def execute(self, task: TaskTemplateConfig) -> ExecutionResult:
        del task
        result = self.results[self.calls]
        self.calls += 1
        return result


def _result(classification: str, exit_code: int | None = None) -> ExecutionResult:
    return ExecutionResult(
        classification=classification,
        exit_code=exit_code,
        stdout=f"{classification}\n",
        stderr="",
        output_truncated=False,
    )


def _project(**task_updates: object) -> ProjectConfig:
    values: dict[str, object] = {
        "task_id": "verify",
        "repository_ref": "local://demo",
        "executable": "pytest",
    }
    values.update(task_updates)
    return ProjectConfig(
        project_id="demo",
        display_name="Demo",
        tasks=(TaskTemplateConfig.model_validate(values),),
    )


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "controller.db")
    database.initialize()
    return database


def test_runner_retries_then_returns_idempotent_terminal_result(tmp_path: Path) -> None:
    database = _database(tmp_path)
    executor = FakeExecutor([_result("failed", 1), _result("success", 0)])
    service = TaskRunnerService(database, ControlService(database), executor)
    project = _project(max_attempts=2)

    first = service.run(project, "verify", "request-1")
    repeated = service.run(project, "verify", "request-1")

    assert first.state == "success"
    assert first.attempt_count == 2
    assert repeated == first
    assert executor.calls == 2
    assert database.count_task_attempts(first.run_id) == 2


def test_runner_fails_closed_when_controller_is_not_active(tmp_path: Path) -> None:
    database = _database(tmp_path)
    control = ControlService(database)
    control.emergency_stop(actor="operator", reason="test")
    executor = FakeExecutor([_result("success", 0)])
    service = TaskRunnerService(database, control, executor)

    with pytest.raises(TaskRunBlocked, match="EMERGENCY_STOP"):
        service.run(_project(), "verify", "request-1")

    assert executor.calls == 0


def test_policy_errors_do_not_retry(tmp_path: Path) -> None:
    database = _database(tmp_path)
    executor = FakeExecutor([_result("policy_error"), _result("success", 0)])
    service = TaskRunnerService(database, ControlService(database), executor)

    result = service.run(_project(max_attempts=3), "verify", "request-1")

    assert result.state == "policy_error"
    assert result.attempt_count == 1
    assert executor.calls == 1


def test_project_circuit_opens_then_allows_one_probe_after_cooldown(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    current = [datetime(2026, 7, 31, tzinfo=UTC)]
    executor = FakeExecutor(
        [_result("failed", 1), _result("failed", 1), _result("success", 0), _result("success", 0)]
    )
    service = TaskRunnerService(
        database,
        ControlService(database),
        executor,
        now=lambda: current[0],
    )
    project = _project(
        max_attempts=1,
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=30,
    )

    service.run(project, "verify", "request-1")
    service.run(project, "verify", "request-2")
    with pytest.raises(TaskRunBlocked, match="circuit is open"):
        service.run(project, "verify", "request-3")
    current[0] += timedelta(seconds=31)
    probe = service.run(project, "verify", "request-3")
    after_probe = service.run(project, "verify", "request-4")

    assert probe.state == "success"
    assert after_probe.state == "success"
    assert executor.calls == 4
