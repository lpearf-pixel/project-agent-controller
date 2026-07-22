import json

from pydantic import BaseModel, ConfigDict

from project_agent_controller.curation.incidents import Incident, IncidentService
from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.storage.database import Database


class BriefExportBlocked(RuntimeError):
    pass


class BriefSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    event_type: str
    occurred_at: str
    severity: str
    line: str
    evidence_ref: str


class AIBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    incident_id: str
    project_id: str
    fingerprint: str
    occurrence_count: int
    omitted_event_count: int
    samples: tuple[BriefSample, ...]
    evidence_refs: tuple[str, ...]
    redaction_classes: tuple[str, ...]

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class BriefBuilder:
    def __init__(self, database: Database, redactor: Redactor) -> None:
        self.database = database
        self.redactor = redactor

    def build(self, incident_id: str, max_bytes: int = 65_536) -> AIBrief:
        if max_bytes < 512:
            raise ValueError("max_bytes must be at least 512")
        incident = IncidentService(self.database).get(incident_id)
        samples: list[BriefSample] = []
        redaction_classes: set[str] = set()
        for event in incident.samples:
            line = str(event.payload.get("line") or event.payload.get("message") or "")
            result = self.redactor.redact(line)
            if not result.safe_to_export:
                raise BriefExportBlocked(f"unsafe content in event {event.event_id}")
            redaction_classes.update(result.matches)
            samples.append(
                BriefSample(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at.isoformat(),
                    severity=event.severity.value,
                    line=result.text,
                    evidence_ref=event.evidence_ref,
                )
            )

        brief = self._make_brief(incident, samples, redaction_classes)
        while len(brief.to_json_bytes()) > max_bytes and len(samples) > 2:
            samples.pop(len(samples) // 2)
            brief = self._make_brief(incident, samples, redaction_classes)

        for line_limit in (256, 128, 64, 32, 0):
            if len(brief.to_json_bytes()) <= max_bytes:
                return brief
            samples = [
                sample.model_copy(
                    update={
                        "line": sample.line[:line_limit]
                        + ("…" if len(sample.line) > line_limit else "")
                    }
                )
                for sample in samples
            ]
            brief = self._make_brief(incident, samples, redaction_classes)

        if len(brief.to_json_bytes()) > max_bytes:
            raise BriefExportBlocked("brief metadata exceeds configured size limit")
        return brief

    @staticmethod
    def _make_brief(
        incident: Incident,
        samples: list[BriefSample],
        redaction_classes: set[str],
    ) -> AIBrief:
        return AIBrief(
            incident_id=incident.incident_id,
            project_id=incident.project_id,
            fingerprint=incident.fingerprint,
            occurrence_count=incident.occurrence_count,
            omitted_event_count=max(0, incident.occurrence_count - len(samples)),
            samples=tuple(samples),
            evidence_refs=tuple(dict.fromkeys(sample.evidence_ref for sample in samples)),
            redaction_classes=tuple(sorted(redaction_classes)),
        )
