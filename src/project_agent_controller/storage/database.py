from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller.domain.models import EventRecord


class SourceCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    source_id: str
    device: int
    inode: int
    byte_offset: int = Field(ge=0)
    sequence: int = Field(ge=0)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with self._connect() as connection:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.commit()

    def journal_mode(self) -> str:
        with self._connect() as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        if row is None:
            raise RuntimeError("SQLite did not return journal_mode")
        return str(row[0]).lower()

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: EventRecord) -> None:
        connection.execute(
            """
            INSERT INTO events (
                event_id, project_id, run_id, source_id, sequence,
                event_type, severity, occurred_at, payload_json, evidence_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.project_id,
                event.run_id,
                event.source_id,
                event.sequence,
                event.event_type,
                event.severity.value,
                event.occurred_at.isoformat(),
                json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                event.evidence_ref,
            ),
        )

    @staticmethod
    def _upsert_cursor_on(
        connection: sqlite3.Connection,
        cursor: SourceCursor,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO source_cursors (
                project_id, source_id, device, inode, byte_offset, sequence, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, source_id) DO UPDATE SET
                device = excluded.device,
                inode = excluded.inode,
                byte_offset = excluded.byte_offset,
                sequence = excluded.sequence,
                updated_at = excluded.updated_at
            """,
            (
                cursor.project_id,
                cursor.source_id,
                cursor.device,
                cursor.inode,
                cursor.byte_offset,
                cursor.sequence,
                updated_at,
            ),
        )

    @staticmethod
    def _record_incident_on(
        connection: sqlite3.Connection,
        fingerprint: str,
        event: EventRecord,
    ) -> str:
        event_json = json.dumps(
            event.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        row = connection.execute(
            """
            SELECT incident_id, occurrence_count
            FROM incidents
            WHERE project_id = ? AND fingerprint = ?
            """,
            (event.project_id, fingerprint),
        ).fetchone()
        if row is None:
            incident_id = f"inc-{uuid4()}"
            connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, project_id, fingerprint, event_type, source_id,
                    first_seen, last_seen, occurrence_count,
                    first_event_json, last_event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    incident_id,
                    event.project_id,
                    fingerprint,
                    event.event_type,
                    event.source_id,
                    event.occurred_at.isoformat(),
                    event.occurred_at.isoformat(),
                    event_json,
                    event_json,
                ),
            )
            connection.execute(
                """
                INSERT INTO incident_samples (incident_id, slot, event_json)
                VALUES (?, 0, ?)
                """,
                (incident_id, event_json),
            )
            return incident_id

        incident_id = str(row["incident_id"])
        occurrence_count = int(row["occurrence_count"]) + 1
        connection.execute(
            """
            UPDATE incidents
            SET last_seen = ?, occurrence_count = ?, last_event_json = ?
            WHERE incident_id = ?
            """,
            (
                event.occurred_at.isoformat(),
                occurrence_count,
                event_json,
                incident_id,
            ),
        )
        slot = 1 if occurrence_count == 2 else 2
        connection.execute(
            """
            INSERT INTO incident_samples (incident_id, slot, event_json)
            VALUES (?, ?, ?)
            ON CONFLICT(incident_id, slot)
            DO UPDATE SET event_json = excluded.event_json
            """,
            (incident_id, slot, event_json),
        )
        return incident_id

    def append_event(self, event: EventRecord) -> None:
        with self._connect() as connection:
            self._insert_event(connection, event)
            connection.commit()

    def append_observation(
        self,
        events: tuple[EventRecord, ...],
        cursor: SourceCursor,
        *,
        incident_candidates: tuple[tuple[str, EventRecord], ...] = (),
    ) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for event in events:
                    self._insert_event(connection, event)
                for fingerprint, event in incident_candidates:
                    self._record_incident_on(connection, fingerprint, event)
                self._upsert_cursor_on(connection, cursor, updated_at)
            except Exception:
                connection.rollback()
                raise
            connection.commit()

    def append_events_and_cursor(
        self,
        events: tuple[EventRecord, ...],
        cursor: SourceCursor,
    ) -> None:
        self.append_observation(events, cursor)

    def list_events(self, project_id: str, limit: int = 100) -> list[EventRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, project_id, run_id, source_id, sequence,
                       event_type, severity, occurred_at, payload_json, evidence_ref
                FROM events
                WHERE project_id = ?
                ORDER BY occurred_at ASC, sequence ASC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            EventRecord.model_validate(
                {
                    "event_id": row["event_id"],
                    "project_id": row["project_id"],
                    "run_id": row["run_id"],
                    "source_id": row["source_id"],
                    "sequence": row["sequence"],
                    "event_type": row["event_type"],
                    "severity": row["severity"],
                    "occurred_at": row["occurred_at"],
                    "payload": json.loads(row["payload_json"]),
                    "evidence_ref": row["evidence_ref"],
                }
            )
            for row in rows
        ]

    def upsert_cursor(self, cursor: SourceCursor) -> None:
        with self._connect() as connection:
            self._upsert_cursor_on(connection, cursor, datetime.now(UTC).isoformat())
            connection.commit()

    def get_cursor(self, project_id: str, source_id: str) -> SourceCursor | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT project_id, source_id, device, inode, byte_offset, sequence
                FROM source_cursors
                WHERE project_id = ? AND source_id = ?
                """,
                (project_id, source_id),
            ).fetchone()
        if row is None:
            return None
        return SourceCursor.model_validate(dict(row))

    def get_controller_state(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM controller_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("controller_state is not initialized")
        return str(row["state"])

    def transition_controller_state(
        self,
        *,
        allowed_from: set[str],
        next_state: str,
        actor: str,
        reason: str,
        request_id: str,
    ) -> str:
        occurred_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM controller_state WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RuntimeError("controller_state is not initialized")
            current = str(row["state"])
            if current not in allowed_from:
                connection.rollback()
                allowed = ", ".join(sorted(allowed_from))
                raise ValueError(
                    f"cannot transition from {current}; expected one of: {allowed}"
                )
            try:
                connection.execute(
                    """
                    UPDATE controller_state
                    SET state = ?, updated_at = ?
                    WHERE singleton = 1
                    """,
                    (next_state, occurred_at),
                )
                connection.execute(
                    """
                    INSERT INTO control_events (
                        request_id, actor, reason,
                        previous_state, next_state, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (request_id, actor, reason, current, next_state, occurred_at),
                )
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return next_state

    def list_control_events(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, actor, reason,
                       previous_state, next_state, occurred_at
                FROM control_events
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            {
                "request_id": str(row["request_id"]),
                "actor": str(row["actor"]),
                "reason": str(row["reason"]),
                "previous_state": str(row["previous_state"]),
                "next_state": str(row["next_state"]),
                "occurred_at": str(row["occurred_at"]),
            }
            for row in rows
        ]

    def record_incident(self, fingerprint: str, event: EventRecord) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                incident_id = self._record_incident_on(connection, fingerprint, event)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return incident_id

    def find_incident_id(self, project_id: str, fingerprint: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT incident_id
                FROM incidents
                WHERE project_id = ? AND fingerprint = ?
                """,
                (project_id, fingerprint),
            ).fetchone()
        if row is None:
            return None
        return str(row["incident_id"])

    def get_incident_record(self, incident_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            incident = connection.execute(
                """
                SELECT incident_id, project_id, fingerprint, event_type, source_id,
                       first_seen, last_seen, occurrence_count
                FROM incidents
                WHERE incident_id = ?
                """,
                (incident_id,),
            ).fetchone()
            if incident is None:
                return None
            samples = connection.execute(
                """
                SELECT slot, event_json
                FROM incident_samples
                WHERE incident_id = ?
                ORDER BY slot ASC
                """,
                (incident_id,),
            ).fetchall()
        return {
            "incident_id": str(incident["incident_id"]),
            "project_id": str(incident["project_id"]),
            "fingerprint": str(incident["fingerprint"]),
            "event_type": str(incident["event_type"]),
            "source_id": str(incident["source_id"]),
            "first_seen": str(incident["first_seen"]),
            "last_seen": str(incident["last_seen"]),
            "occurrence_count": int(incident["occurrence_count"]),
            "samples": [json.loads(str(row["event_json"])) for row in samples],
        }
