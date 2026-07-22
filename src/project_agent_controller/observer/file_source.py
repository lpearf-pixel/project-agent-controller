from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from project_agent_controller.domain.models import EventRecord, FileSourceConfig, Severity
from project_agent_controller.storage.database import SourceCursor


class ReadBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[EventRecord, ...]
    cursor: SourceCursor


def resolve_local_path(path_ref: str, local_root: Path) -> Path:
    if not path_ref.startswith("local://"):
        raise ValueError("path_ref must use local://")
    relative = path_ref.removeprefix("local://")
    if not relative:
        raise ValueError("path_ref must not be empty")
    root = local_root.resolve()
    candidate = (root / relative).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("resolved path escapes local root")
    return candidate


class FileSourceReader:
    def __init__(self, local_root: Path) -> None:
        self.local_root = local_root

    def read_available(
        self,
        project_id: str,
        run_id: str,
        source: FileSourceConfig,
        cursor: SourceCursor | None,
    ) -> ReadBatch:
        path = resolve_local_path(source.path_ref, self.local_root)
        previous = cursor or SourceCursor(
            project_id=project_id,
            source_id=source.source_id,
            device=0,
            inode=0,
            byte_offset=0,
            sequence=0,
        )
        try:
            stat = path.stat()
        except FileNotFoundError:
            sequence = previous.sequence + 1
            event = self._notice(
                project_id,
                run_id,
                source,
                sequence,
                "source.missing",
                Severity.WARNING,
                {"path_ref": source.path_ref},
            )
            return ReadBatch(
                events=(event,),
                cursor=previous.model_copy(update={"sequence": sequence}),
            )

        events: list[EventRecord] = []
        sequence = previous.sequence
        start_offset = previous.byte_offset
        known_file = previous.device != 0 or previous.inode != 0
        identity_changed = known_file and (
            previous.device != stat.st_dev or previous.inode != stat.st_ino
        )

        if identity_changed:
            sequence += 1
            events.append(
                self._notice(
                    project_id,
                    run_id,
                    source,
                    sequence,
                    "source.rotated",
                    Severity.INFO,
                    {
                        "path_ref": source.path_ref,
                        "previous_inode": previous.inode,
                        "current_inode": stat.st_ino,
                    },
                )
            )
            start_offset = 0
        elif stat.st_size < previous.byte_offset:
            sequence += 1
            events.append(
                self._notice(
                    project_id,
                    run_id,
                    source,
                    sequence,
                    "source.truncated",
                    Severity.WARNING,
                    {
                        "path_ref": source.path_ref,
                        "previous_offset": previous.byte_offset,
                        "current_size": stat.st_size,
                    },
                )
            )
            start_offset = 0

        with path.open("rb") as handle:
            handle.seek(start_offset)
            available = handle.read()

        final_newline = available.rfind(b"\n")
        complete = b"" if final_newline < 0 else available[: final_newline + 1]
        byte_position = start_offset
        for raw_line in complete.splitlines(keepends=True):
            sequence += 1
            byte_start = byte_position
            byte_position += len(raw_line)
            content = raw_line[:-1]
            if content.endswith(b"\r"):
                content = content[:-1]
            line = content.decode(source.encoding, errors="strict")
            events.append(
                EventRecord(
                    event_id=f"evt-{uuid4()}",
                    project_id=project_id,
                    run_id=run_id,
                    source_id=source.source_id,
                    sequence=sequence,
                    event_type="log.line",
                    severity=Severity.INFO,
                    occurred_at=datetime.now(UTC),
                    payload={
                        "line": line,
                        "byte_start": byte_start,
                        "byte_end": byte_position,
                        "parser": source.parser,
                    },
                    evidence_ref=self._evidence_ref(
                        project_id, source.source_id, byte_start, byte_position, raw_line
                    ),
                )
            )

        next_cursor = SourceCursor(
            project_id=project_id,
            source_id=source.source_id,
            device=stat.st_dev,
            inode=stat.st_ino,
            byte_offset=start_offset + len(complete),
            sequence=sequence,
        )
        return ReadBatch(events=tuple(events), cursor=next_cursor)

    @staticmethod
    def _notice(
        project_id: str,
        run_id: str,
        source: FileSourceConfig,
        sequence: int,
        event_type: str,
        severity: Severity,
        payload: dict[str, object],
    ) -> EventRecord:
        encoded = repr(sorted(payload.items())).encode("utf-8")
        return EventRecord(
            event_id=f"evt-{uuid4()}",
            project_id=project_id,
            run_id=run_id,
            source_id=source.source_id,
            sequence=sequence,
            event_type=event_type,
            severity=severity,
            occurred_at=datetime.now(UTC),
            payload=payload,
            evidence_ref=f"artifact://sha256/{sha256(encoded).hexdigest()}",
        )

    @staticmethod
    def _evidence_ref(
        project_id: str,
        source_id: str,
        byte_start: int,
        byte_end: int,
        raw_line: bytes,
    ) -> str:
        digest = sha256()
        digest.update(project_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(byte_start).encode("ascii"))
        digest.update(b":")
        digest.update(str(byte_end).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw_line)
        return f"artifact://sha256/{digest.hexdigest()}"
