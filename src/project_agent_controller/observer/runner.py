from pathlib import Path
from threading import Lock
from typing import Protocol

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.domain.models import (
    DockerSourceConfig,
    EventRecord,
    FileSourceConfig,
    GitHubCISourceConfig,
    GitSourceConfig,
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


class GitObserver(Protocol):
    def observe(
        self,
        project_id: str,
        run_id: str,
        source: GitSourceConfig,
        previous: SourceState | None,
    ) -> SourceObservation: ...


class CIObserver(Protocol):
    def observe(
        self,
        project_id: str,
        run_id: str,
        source: GitHubCISourceConfig,
        git_state: SourceState | None,
        previous: SourceState | None,
    ) -> SourceObservation: ...


class ObservationBlocked(RuntimeError):
    pass


_SOURCE_ORDER = {
    "file": 0,
    "process": 1,
    "docker": 2,
    "git": 3,
    "github_ci": 4,
}


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
        git_observer: GitObserver | None = None,
        ci_observers: dict[str, CIObserver] | None = None,
        source_states: SourceStateStore | None = None,
    ) -> None:
        self.database = database
        self.control = control
        self.reader = FileSourceReader(local_root)
        self.run_id = run_id
        self.incident_service = incident_service
        self.process_observer = process_observer
        self.docker_observer = docker_observer
        self.git_observer = git_observer
        self.ci_observers = ci_observers or {}
        self.source_states = source_states or SourceStateStore(database)
        self._lock = Lock()

    def observe_once(self, project: ProjectConfig) -> int:
        with self._lock:
            state = self.control.get_state()
            if state is not ControllerState.ACTIVE:
                raise ObservationBlocked(f"observation blocked by controller state {state.value}")
            emitted = 0
            sources = sorted(project.sources, key=lambda item: _SOURCE_ORDER[item.kind])
            for source in sources:
                if isinstance(source, FileSourceConfig):
                    emitted += self._observe_file(project, source)
                elif isinstance(source, ProcessSourceConfig):
                    emitted += self._observe_process(project, source)
                elif isinstance(source, DockerSourceConfig):
                    emitted += self._observe_docker(project, source)
                elif isinstance(source, GitSourceConfig):
                    emitted += self._observe_git(project, source)
                elif isinstance(source, GitHubCISourceConfig):
                    emitted += self._observe_ci(project, source)
                else:  # pragma: no cover
                    raise TypeError(f"unsupported source type: {type(source).__name__}")
            return emitted

    def _observe_file(self, project: ProjectConfig, source: FileSourceConfig) -> int:
        cursor = self.database.get_cursor(project.project_id, source.source_id)
        batch = self.reader.read_available(project.project_id, self.run_id, source, cursor)
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

    def _observe_process(self, project: ProjectConfig, source: ProcessSourceConfig) -> int:
        if self.process_observer is None:
            raise RuntimeError("process observer is not configured")
        return self._append_system(
            self.process_observer.observe(
                project.project_id,
                self.run_id,
                source,
                self.source_states.get(project.project_id, source.source_id),
            )
        )

    def _observe_docker(self, project: ProjectConfig, source: DockerSourceConfig) -> int:
        if self.docker_observer is None:
            raise RuntimeError("docker observer is not configured")
        return self._append_system(
            self.docker_observer.observe(
                project.project_id,
                self.run_id,
                source,
                self.source_states.get(project.project_id, source.source_id),
            )
        )

    def _observe_git(self, project: ProjectConfig, source: GitSourceConfig) -> int:
        if self.git_observer is None:
            raise RuntimeError("git observer is not configured")
        return self._append_system(
            self.git_observer.observe(
                project.project_id,
                self.run_id,
                source,
                self.source_states.get(project.project_id, source.source_id),
            )
        )

    def _observe_ci(self, project: ProjectConfig, source: GitHubCISourceConfig) -> int:
        observer = self.ci_observers.get(source.provider_id)
        if observer is None:
            raise RuntimeError(f"CI observer is not configured: {source.provider_id}")
        git_state = self.source_states.get(project.project_id, source.git_source_id)
        previous = self.source_states.get(project.project_id, source.source_id)
        return self._append_system(
            observer.observe(
                project.project_id,
                self.run_id,
                source,
                git_state,
                previous,
            )
        )

    def _append_system(self, observation: SourceObservation) -> int:
        self.source_states.append(observation)
        return len(observation.events)
