from pathlib import Path

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.domain.models import ProjectConfig
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
    ) -> None:
        self.database = database
        self.control = control
        self.reader = FileSourceReader(local_root)
        self.run_id = run_id

    def observe_once(self, project: ProjectConfig) -> int:
        state = self.control.get_state()
        if state is not ControllerState.ACTIVE:
            raise ObservationBlocked(f"observation blocked by controller state {state.value}")

        emitted = 0
        for source in project.sources:
            cursor = self.database.get_cursor(project.project_id, source.source_id)
            batch = self.reader.read_available(project.project_id, self.run_id, source, cursor)
            self.database.append_events_and_cursor(batch.events, batch.cursor)
            emitted += len(batch.events)
        return emitted
