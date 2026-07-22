from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.observer.runner import ObservationBlocked, ObserverRunner
from project_agent_controller.registry.service import ProjectRegistry


class DaemonCycleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    emitted_events: int = 0
    failed_projects: tuple[str, ...] = ()
    skipped_state: str | None = None


class ObserverDaemon:
    def __init__(
        self,
        registry: ProjectRegistry,
        runner: ObserverRunner,
        control: ControlService,
        *,
        poll_interval_seconds: float,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.registry = registry
        self.runner = runner
        self.control = control
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self.last_errors: dict[str, str] = {}

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def run_cycle(self) -> DaemonCycleResult:
        state = self.control.get_state()
        if state is not ControllerState.ACTIVE:
            return DaemonCycleResult(skipped_state=state.value)

        emitted_events = 0
        failed_projects: list[str] = []
        for project in self.registry.list():
            try:
                emitted_events += self.runner.observe_once(project)
                self.last_errors.pop(project.project_id, None)
            except ObservationBlocked:
                current = self.control.get_state()
                return DaemonCycleResult(
                    emitted_events=emitted_events,
                    failed_projects=tuple(failed_projects),
                    skipped_state=current.value,
                )
            except Exception as error:
                failed_projects.append(project.project_id)
                self.last_errors[project.project_id] = f"{type(error).__name__}: {error}"
        return DaemonCycleResult(
            emitted_events=emitted_events,
            failed_projects=tuple(failed_projects),
        )

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_forever(),
            name="project-agent-file-observer",
        )

    async def stop(self) -> None:
        if self._task is None or self._stop_event is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        self._stop_event = None

    async def _run_forever(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            raise RuntimeError("daemon stop event is not initialized")
        while not stop_event.is_set():
            await asyncio.to_thread(self.run_cycle)
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                continue
