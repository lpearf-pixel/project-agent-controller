from pathlib import Path
from threading import Lock

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.domain.models import EventRecord, ProjectConfig
from project_agent_controller.observer.file_source import FileSourceReader
from project_agent_controller.storage.database import Database


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
    ) -> None:
        self.database = database
        self.control = control
        self.reader = FileSourceReader(local_root)
        self.run_id = run_id
        self.incident_service = incident_service
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
                emitted += len(batch.events)
            return emitted
