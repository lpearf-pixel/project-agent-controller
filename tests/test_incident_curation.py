from datetime import UTC, datetime, timedelta

import pytest

from project_agent_controller.curation.briefs import BriefBuilder, BriefExportBlocked
from project_agent_controller.curation.fingerprint import fingerprint_event, normalize_message
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.curation.redaction import Redactor
from project_agent_controller.domain.models import EventRecord, Severity
from project_agent_controller.storage.database import Database


def error_event(index: int, line: str | None = None) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{index}",
        project_id="demo",
        run_id="run-1",
        source_id="app-log",
        sequence=index,
        event_type="process.failed",
        severity=Severity.ERROR,
        occurred_at=datetime(2026, 7, 22, tzinfo=UTC) + timedelta(seconds=index),
        payload={
            "line": line
            or f"2026-07-22T10:20:{index:02d}Z pid={1000 + index} "
            "ERROR E1234 lib=1.2.3"
        },
        evidence_ref=f"artifact://sha256/{index:064x}",
    )


def test_normalization_removes_dynamic_values_but_preserves_diagnostics() -> None:
    normalized = normalize_message(
        "2026-07-22T10:20:30Z pid=4321 request=550e8400-e29b-41d4-a716-446655440000 "
        "/tmp/build-123 ERROR E1234 package=1.2.3"
    )

    assert "2026-07-22" not in normalized
    assert "4321" not in normalized
    assert "550e8400" not in normalized
    assert "/tmp/build-123" not in normalized
    assert "E1234" in normalized
    assert "1.2.3" in normalized


def test_fingerprint_is_stable_across_dynamic_log_values() -> None:
    first = error_event(1)
    second = error_event(2)

    assert fingerprint_event(first) == fingerprint_event(second)


def test_incident_aggregates_repeats_and_keeps_three_samples(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = IncidentService(database)

    incident = None
    for index in range(1, 11):
        event = error_event(index)
        database.append_event(event)
        incident = service.ingest(event)

    assert incident is not None
    assert incident.occurrence_count == 10
    assert len(incident.samples) == 3
    assert incident.samples[0].event_id == "evt-1"
    assert incident.samples[-1].event_id == "evt-10"
    assert incident.suppressed_count == 7


def test_redactor_removes_secrets_and_personal_data() -> None:
    result = Redactor().redact(
        "Authorization: Bearer secret-token user=test@example.com phone=13800138000 "
        "path=/Users/alice/project key=ghp_abcdefghijklmnopqrstuvwxyz123456"
    )

    assert result.safe_to_export is True
    assert "secret-token" not in result.text
    assert "test@example.com" not in result.text
    assert "13800138000" not in result.text
    assert "/Users/alice" not in result.text
    assert "ghp_" not in result.text
    assert set(result.matches) >= {"authorization", "email", "phone", "home_path", "token"}


def test_brief_is_bounded_and_records_omissions(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = IncidentService(database)
    for index in range(1, 11):
        event = error_event(index, line="ERROR E1234 " + ("x" * 500))
        database.append_event(event)
        incident = service.ingest(event)
    assert incident is not None

    brief = BriefBuilder(database, Redactor()).build(incident.incident_id, max_bytes=1200)
    encoded = brief.to_json_bytes()

    assert len(encoded) <= 1200
    assert brief.occurrence_count == 10
    assert brief.omitted_event_count >= 7
    assert brief.evidence_refs


def test_brief_blocks_unredactable_control_payload(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = IncidentService(database)
    event = error_event(1, line="ERROR\x00secret")
    database.append_event(event)
    incident = service.ingest(event)
    assert incident is not None

    with pytest.raises(BriefExportBlocked, match="unsafe content"):
        BriefBuilder(database, Redactor()).build(incident.incident_id)
