import sqlite3
from datetime import UTC, datetime

import pytest

from project_agent_controller.domain.models import EventRecord, Severity
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.state_store import SourceStateStore
from project_agent_controller.storage.database import Database


def test_state_store_isolates_project_and_source(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    store = SourceStateStore(database)
    state = SourceState(
        project_id="demo",
        source_id="worker",
        source_kind="process",
        sequence=1,
        observed_at=datetime.now(UTC),
        state={"pid": 42},
    )

    store.append(SourceObservation(events=(), state=state))

    assert store.get("demo", "worker") == state
    assert store.get("other", "worker") is None


def test_state_store_rolls_back_event_and_state_together(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    store = SourceStateStore(database)
    event = EventRecord(
        event_id="evt-duplicate",
        project_id="demo",
        run_id="run-1",
        source_id="worker",
        sequence=1,
        event_type="process.available",
        severity=Severity.INFO,
        occurred_at=datetime.now(UTC),
        payload={"pid": 42},
        evidence_ref="process://42/1",
    )
    database.append_event(event)
    observation = SourceObservation(
        events=(event,),
        state=SourceState(
            project_id="demo",
            source_id="worker",
            source_kind="process",
            sequence=1,
            observed_at=datetime.now(UTC),
            state={"pid": 42},
        ),
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.append(observation)

    assert store.get("demo", "worker") is None
