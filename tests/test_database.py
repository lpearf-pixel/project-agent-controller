import sqlite3
from datetime import UTC, datetime

import pytest

from project_agent_controller.domain.models import EventRecord, Severity
from project_agent_controller.storage.database import Database, SourceCursor


def make_event(event_id: str = "evt-1", sequence: int = 1) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        project_id="demo",
        run_id="run-1",
        source_id="app-log",
        sequence=sequence,
        event_type="log.line",
        severity=Severity.INFO,
        occurred_at=datetime(2026, 7, 22, 0, 0, sequence, tzinfo=UTC),
        payload={"line": f"started-{sequence}"},
        evidence_ref=f"artifact://sha256/{event_id}",
    )


def test_database_uses_wal_and_round_trips_events(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()

    event = make_event()
    database.append_event(event)

    assert database.journal_mode() == "wal"
    assert database.list_events("demo", limit=10) == [event]


def test_database_preserves_append_only_event_identity(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    database.append_event(make_event())

    with pytest.raises(sqlite3.IntegrityError):
        database.append_event(make_event())


def test_cursor_round_trip(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    cursor = SourceCursor(
        project_id="demo",
        source_id="app-log",
        device=1,
        inode=2,
        byte_offset=128,
        sequence=4,
    )

    database.upsert_cursor(cursor)

    assert database.get_cursor("demo", "app-log") == cursor


def test_observation_transaction_rolls_back_events_cursor_and_incident(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    event = make_event()
    cursor = SourceCursor(
        project_id="demo",
        source_id="app-log",
        device=1,
        inode=2,
        byte_offset=10,
        sequence=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        database.append_observation(
            (event, event),
            cursor,
            incident_candidates=(("fp-atomic", event),),
        )

    assert database.list_events("demo", limit=10) == []
    assert database.get_cursor("demo", "app-log") is None
    assert database.find_incident_id("demo", "fp-atomic") is None
