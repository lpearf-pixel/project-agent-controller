from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

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

    def append_event(self, event: EventRecord) -> None:
        with self._connect() as connection:
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
            connection.commit()

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
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
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
                    now,
                ),
            )
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
                raise ValueError(f"cannot transition from {current}; expected one of: {allowed}")
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
                    request_id, actor, reason, previous_state, next_state, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (request_id, actor, reason, current, next_state, occurred_at),
            )
            connection.commit()
        return next_state

    def list_control_events(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, actor, reason, previous_state, next_state, occurred_at
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
