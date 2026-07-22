from datetime import datetime

from pydantic import BaseModel, ConfigDict

from project_agent_controller.curation.fingerprint import fingerprint_event
from project_agent_controller.domain.models import EventRecord, Severity
from project_agent_controller.storage.database import Database


class Incident(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: str
    project_id: str
    fingerprint: str
    event_type: str
    source_id: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    samples: tuple[EventRecord, ...]

    @property
    def suppressed_count(self) -> int:
        return max(0, self.occurrence_count - len(self.samples))


class IncidentService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ingest(self, event: EventRecord) -> Incident | None:
        is_failure_type = event.event_type.endswith((".failed", ".crashed"))
        if event.severity not in {Severity.ERROR, Severity.CRITICAL} and not is_failure_type:
            return None
        incident_id = self.database.record_incident(fingerprint_event(event), event)
        return self.get(incident_id)

    def get(self, incident_id: str) -> Incident:
        raw = self.database.get_incident_record(incident_id)
        if raw is None:
            raise KeyError(f"incident not found: {incident_id}")
        return Incident.model_validate(raw)
