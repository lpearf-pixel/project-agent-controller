from pathlib import Path
from threading import Lock
from typing import Protocol

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.domain.models import (
    DockerSourceConfig,
    EventRecord,
    FileSourceConfig,
    ProcessSourceConfig,
    ProjectConfig,
)
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.file_source import FileSourceReader
from project_agent_controller.observer.state_store import SourceStateStore
from project_agent_controller.storage.database import Database


class ProcessObserver(Protocol):
    def observe(
        self,
        project_id: str,
        run_id: str,
        source: ProcessSourceConfig,
        previous: SourceState | None,
    ) -> SourceObservation: ...


class DockerObserver(Protocol):
    def observe(
        self,
        project_id: str,
        run_id: str,
        source: DockerSourceConfig,
        previous: SourceState | None,
    ) -> SourceObservation: ...


class ObservationBlocked(RuntimeError):
    pass


class ObserverRunner:
    def __init__(
        self,
        database: Database,
        control: ControlService,
        *,
        local_root: Path,
        run_id: str,
        incident_service: IncidentService | None = None,
        process_observer: ProcessObserver | None = None,
        docker_observer: DockerObserver | None = None,
        source_states: SourceStateStore | None = None,
    ) -> None:
        self.database = database
        self.control = control
        self.reader = FileSourceReader(local_root)
        self.run_id = run_id
        self.incident_service = incident_service
        self.process_observer = process_observer
        self.docker_observer = docker_observer
        self.source_states = source_states or SourceStateStore(database)
        self._lock = Lock()

    def observe_once(self, project: ProjectConfig) -> int:
        with self._lock:
            state = self.control.get_state()
            if state is not ControllerState.ACTIVE:
                raise ObservationBlocked(
                    f"observation blocked by controller state {state.value}"
                )

            emitted = 0
            for source in project.sources:
                if isinstance(source, FileSourceConfig):
                    emitted += self._observe_file(project, source)
                elif isinstance(source, ProcessSourceConfig):
                    emitted += self._observe_process(project, source)
                elif isinstance(source, DockerSourceConfig):
                    emitted += self._observe_docker(project, source)
                else:  # pragma: no cover - Pydantic discriminator prevents this
                    raise TypeError(f"unsupported source type: {type(source).__name__}")
            return emitted

    def _observe_file(self, project: ProjectConfig, source: FileSourceConfig) -> int:
        cursor = self.database.get_cursor(project.project_id, source.source_id)
        batch = self.reader.read_available(
            project.project_id,
            self.run_id,
            source,
            cursor,
        )
        incident_candidates: list[tuple[str, EventRecord]] = []
        if self.incident_service is not None:
            for event in batch.events:
                fingerprint = self.incident_service.candidate_fingerprint(event)
                if fingerprint is not None:
                    incident_candidates.append((fingerprint, event))
        self.database.append_observation(
            batch.events,
            batch.cursor,
            incident_candidates=tuple(incident_candidates),
        )
        return len(batch.events)

    def _observe_process(
        self,
        project: ProjectConfig,
        source: ProcessSourceConfig,
    ) -> int:
        if self.process_observer is None:
            raise RuntimeError("process observer is not configured")
        previous = self.source_states.get(project.project_id, source.source_id)
        observation = self.process_observer.observe(
            project.project_id,
            self.run_id,
            source,
            previous,
        )
        self.source_states.append(observation)
        return len(observation.events)

    def _observe_docker(
        self,
        project: ProjectConfig,
        source: DockerSourceConfig,
    ) -> int:
        if self.docker_observer is None:
            raise RuntimeError("docker observer is not configured")
        previous = self.source_states.get(project.project_id, source.source_id)
        observation = self.docker_observer.observe(
            project.project_id,
            self.run_id,
            source,
            previous,
        )
        self.source_states.append(observation)
        return len(observation.events)
