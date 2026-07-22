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

    @staticmethod
    def candidate_fingerprint(event: EventRecord) -> str | None:
        is_failure_type = event.event_type.endswith((".failed", ".crashed"))
        is_error = event.severity in {Severity.ERROR, Severity.CRITICAL}
        if not is_error and not is_failure_type:
            return None
        return fingerprint_event(event)

    def ingest(self, event: EventRecord) -> Incident | None:
        fingerprint = self.candidate_fingerprint(event)
        if fingerprint is None:
            return None
        incident_id = self.database.record_incident(fingerprint, event)
        return self.get(incident_id)

    def find(self, project_id: str, fingerprint: str) -> Incident | None:
        incident_id = self.database.find_incident_id(project_id, fingerprint)
        if incident_id is None:
            return None
        return self.get(incident_id)

    def get(self, incident_id: str) -> Incident:
        raw = self.database.get_incident_record(incident_id)
        if raw is None:
            raise KeyError(f"incident not found: {incident_id}")
        return Incident.model_validate(raw)
