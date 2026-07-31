from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller.domain.models import EventRecord


@dataclass(frozen=True, slots=True)
class StoredTaskRun:
    run_id: str
    project_id: str
    task_id: str
    idempotency_key: str
    state: str
    attempt_count: int
    classification: str | None
    exit_code: int | None
    stdout: str
    stderr: str
    output_truncated: bool
    created_at: datetime
    finished_at: datetime | None


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

    @staticmethod
    def _task_run_from_row(row: sqlite3.Row) -> StoredTaskRun:
        finished = row["finished_at"]
        return StoredTaskRun(
            run_id=str(row["run_id"]),
            project_id=str(row["project_id"]),
            task_id=str(row["task_id"]),
            idempotency_key=str(row["idempotency_key"]),
            state=str(row["state"]),
            attempt_count=int(row["attempt_count"]),
            classification=(
                None if row["classification"] is None else str(row["classification"])
            ),
            exit_code=None if row["exit_code"] is None else int(row["exit_code"]),
            stdout=str(row["stdout"]),
            stderr=str(row["stderr"]),
            output_truncated=bool(row["output_truncated"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            finished_at=None if finished is None else datetime.fromisoformat(str(finished)),
        )

    def get_task_run(
        self, project_id: str, task_id: str, idempotency_key: str
    ) -> StoredTaskRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM task_runs
                WHERE project_id = ? AND task_id = ? AND idempotency_key = ?
                """,
                (project_id, task_id, idempotency_key),
            ).fetchone()
        return None if row is None else self._task_run_from_row(row)

    def create_task_run(
        self,
        project_id: str,
        task_id: str,
        idempotency_key: str,
        *,
        created_at: datetime,
    ) -> StoredTaskRun:
        run_id = f"task-{uuid4()}"
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO task_runs (
                        run_id, project_id, task_id, idempotency_key, state, created_at
                    ) VALUES (?, ?, ?, ?, 'running', ?)
                    """,
                    (run_id, project_id, task_id, idempotency_key, created_at.isoformat()),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
        existing = self.get_task_run(project_id, task_id, idempotency_key)
        if existing is None:
            raise RuntimeError("task run was not persisted")
        return existing

    def append_task_attempt(
        self,
        run_id: str,
        attempt_number: int,
        *,
        classification: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        output_truncated: bool,
        occurred_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO task_attempts (
                    run_id, attempt_number, classification, exit_code,
                    stdout, stderr, output_truncated, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    attempt_number,
                    classification,
                    exit_code,
                    stdout,
                    stderr,
                    int(output_truncated),
                    occurred_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE task_runs SET attempt_count = ? WHERE run_id = ?",
                (attempt_number, run_id),
            )
            connection.commit()

    def finish_task_run(
        self,
        run_id: str,
        *,
        state: str,
        classification: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        output_truncated: bool,
        finished_at: datetime,
    ) -> StoredTaskRun:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_runs
                SET state = ?, classification = ?, exit_code = ?, stdout = ?, stderr = ?,
                    output_truncated = ?, finished_at = ?
                WHERE run_id = ? AND state = 'running'
                """,
                (
                    state,
                    classification,
                    exit_code,
                    stdout,
                    stderr,
                    int(output_truncated),
                    finished_at.isoformat(),
                    run_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("task run disappeared")
        return self._task_run_from_row(row)

    def count_task_attempts(self, run_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM task_attempts WHERE run_id = ?", (run_id,)
            ).fetchone()
        return 0 if row is None else int(row[0])

    def claim_runner_circuit(
        self,
        project_id: str,
        *,
        threshold: int,
        cooldown_seconds: int,
        now: datetime,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runner_circuits WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None or int(row["consecutive_failures"]) < threshold:
                connection.commit()
                return True
            opened_at = datetime.fromisoformat(str(row["opened_at"]))
            elapsed = (now - opened_at).total_seconds()
            if elapsed < cooldown_seconds or bool(row["probe_in_progress"]):
                connection.commit()
                return False
            connection.execute(
                "UPDATE runner_circuits SET probe_in_progress = 1 WHERE project_id = ?",
                (project_id,),
            )
            connection.commit()
            return True

    def record_runner_success(self, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM runner_circuits WHERE project_id = ?", (project_id,))
            connection.commit()

    def record_runner_failure(
        self, project_id: str, *, threshold: int, occurred_at: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT consecutive_failures FROM runner_circuits WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            failures = 1 if row is None else int(row[0]) + 1
            opened_at = occurred_at.isoformat() if failures >= threshold else None
            connection.execute(
                """
                INSERT INTO runner_circuits (
                    project_id, consecutive_failures, opened_at, probe_in_progress
                ) VALUES (?, ?, ?, 0)
                ON CONFLICT(project_id) DO UPDATE SET
                    consecutive_failures = excluded.consecutive_failures,
                    opened_at = excluded.opened_at,
                    probe_in_progress = 0
                """,
                (project_id, failures, opened_at),
            )
            connection.commit()

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
