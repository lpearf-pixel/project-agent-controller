from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.domain.models import ProjectConfig, TaskTemplateConfig
from project_agent_controller.runner.executor import ExecutionResult
from project_agent_controller.storage.database import Database, StoredTaskRun


class TaskRunBlocked(RuntimeError):
    pass


class TaskExecutionProvider(Protocol):
    def execute(self, task: TaskTemplateConfig) -> ExecutionResult: ...


class TaskRunnerService:
    def __init__(
        self,
        database: Database,
        control: ControlService,
        executor: TaskExecutionProvider,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.control = control
        self.executor = executor
        self.now = now or (lambda: datetime.now(UTC))

    def run(
        self, project: ProjectConfig, task_id: str, idempotency_key: str
    ) -> StoredTaskRun:
        task = self._find_task(project, task_id)
        self._validate_idempotency_key(idempotency_key)
        existing = self.database.get_task_run(project.project_id, task_id, idempotency_key)
        if existing is not None:
            if existing.state == "running":
                raise TaskRunBlocked("an idempotent task request is already running")
            return existing
        self._require_active()
        if not self.database.claim_runner_circuit(
            project.project_id,
            threshold=task.circuit_failure_threshold,
            cooldown_seconds=task.circuit_cooldown_seconds,
            now=self.now(),
        ):
            raise TaskRunBlocked(f"project {project.project_id} runner circuit is open")
        run = self.database.create_task_run(
            project.project_id,
            task_id,
            idempotency_key,
            created_at=self.now(),
        )
        if run.state != "running":
            return run

        final = self._execute_attempts(run.run_id, task)
        finished = self.database.finish_task_run(
            run.run_id,
            state=final.classification,
            classification=final.classification,
            exit_code=final.exit_code,
            stdout=final.stdout,
            stderr=final.stderr,
            output_truncated=final.output_truncated,
            finished_at=self.now(),
        )
        if final.classification == "success":
            self.database.record_runner_success(project.project_id)
        else:
            self.database.record_runner_failure(
                project.project_id,
                threshold=task.circuit_failure_threshold,
                occurred_at=self.now(),
            )
        return finished

    def _execute_attempts(
        self, run_id: str, task: TaskTemplateConfig
    ) -> ExecutionResult:
        for attempt_number in range(1, task.max_attempts + 1):
            state = self.control.get_state()
            if state is not ControllerState.ACTIVE:
                result = ExecutionResult(
                    classification="blocked",
                    exit_code=None,
                    stdout="",
                    stderr=f"controller state is {state.value}",
                    output_truncated=False,
                )
            else:
                result = self.executor.execute(task)
            self.database.append_task_attempt(
                run_id,
                attempt_number,
                classification=result.classification,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                output_truncated=result.output_truncated,
                occurred_at=self.now(),
            )
            if result.classification not in {"failed", "timeout"}:
                return result
        return result

    def _require_active(self) -> None:
        state = self.control.get_state()
        if state is not ControllerState.ACTIVE:
            raise TaskRunBlocked(f"controller state is {state.value}")

    @staticmethod
    def _find_task(project: ProjectConfig, task_id: str) -> TaskTemplateConfig:
        for task in project.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"task is not registered: {project.project_id}/{task_id}")

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None:
            raise ValueError("invalid idempotency key")
