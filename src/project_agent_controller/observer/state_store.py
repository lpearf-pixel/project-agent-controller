from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.storage.database import Database

_SOURCE_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_states (
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    state_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, source_id)
);
"""


class SourceStateStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        with self.database._connect() as connection:
            connection.execute(_SOURCE_STATE_SCHEMA)
            connection.commit()

    @staticmethod
    def _upsert_on(
        connection: sqlite3.Connection,
        state: SourceState,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO source_states (
                project_id, source_id, source_kind, sequence,
                state_json, observed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, source_id) DO UPDATE SET
                source_kind = excluded.source_kind,
                sequence = excluded.sequence,
                state_json = excluded.state_json,
                observed_at = excluded.observed_at,
                updated_at = excluded.updated_at
            """,
            (
                state.project_id,
                state.source_id,
                state.source_kind,
                state.sequence,
                json.dumps(state.state, ensure_ascii=False, sort_keys=True),
                state.observed_at.isoformat(),
                updated_at,
            ),
        )

    def append(self, observation: SourceObservation) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self.database._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for event in observation.events:
                    self.database._insert_event(connection, event)
                for fingerprint, event in observation.incident_candidates:
                    self.database._record_incident_on(connection, fingerprint, event)
                self._upsert_on(connection, observation.state, updated_at)
            except Exception:
                connection.rollback()
                raise
            connection.commit()

    def get(self, project_id: str, source_id: str) -> SourceState | None:
        with self.database._connect() as connection:
            row = connection.execute(
                """
                SELECT project_id, source_id, source_kind,
                       sequence, state_json, observed_at
                FROM source_states
                WHERE project_id = ? AND source_id = ?
                """,
                (project_id, source_id),
            ).fetchone()
        if row is None:
            return None
        return SourceState.model_validate(
            {
                "project_id": row["project_id"],
                "source_id": row["source_id"],
                "source_kind": row["source_kind"],
                "sequence": row["sequence"],
                "state": json.loads(row["state_json"]),
                "observed_at": row["observed_at"],
            }
        )

    def list(self, project_id: str) -> tuple[SourceState, ...]:
        with self.database._connect() as connection:
            rows = connection.execute(
                """
                SELECT project_id, source_id, source_kind,
                       sequence, state_json, observed_at
                FROM source_states
                WHERE project_id = ?
                ORDER BY source_id ASC
                """,
                (project_id,),
            ).fetchall()
        return tuple(
            SourceState.model_validate(
                {
                    "project_id": row["project_id"],
                    "source_id": row["source_id"],
                    "source_kind": row["source_kind"],
                    "sequence": row["sequence"],
                    "state": json.loads(row["state_json"]),
                    "observed_at": row["observed_at"],
                }
            )
            for row in rows
        )
