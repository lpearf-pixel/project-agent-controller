from __future__ import annotations

import struct
from datetime import datetime
from hashlib import sha256
from typing import Any, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller.domain.models import DockerSelector
from project_agent_controller.observer.docker_transport import DockerTransportError


class DockerSelectorAmbiguous(RuntimeError):
    pass


class DockerTransport(Protocol):
    def get_json(self, path: str, params: dict[str, str | int] | None = None) -> Any: ...
    def get_bytes(self, path: str, params: dict[str, str | int] | None = None) -> bytes: ...


class DockerContainer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    container_id: str
    name: str
    image: str


class DockerSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    container_id: str
    name: str
    image: str
    state: str
    status: str
    health: str | None
    restart_count: int = Field(ge=0)
    exit_code: int
    oom_killed: bool
    started_at: str | None
    finished_at: str | None
    memory_bytes: int | None = Field(default=None, ge=0)


class DockerLogCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    last_timestamp: str | None = None
    recent_hashes: tuple[str, ...] = ()


class DockerLogLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timestamp: str
    stream: str
    line: str
    content_hash: str


class DockerLogBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    lines: tuple[DockerLogLine, ...]
    cursor: DockerLogCursor


class DockerProvider(Protocol):
    def find_container(self, selector: DockerSelector) -> DockerContainer | None: ...
    def inspect(self, container_id: str) -> DockerSnapshot: ...
    def logs(
        self, container_id: str, cursor: DockerLogCursor, *, limit: int, tail: int
    ) -> DockerLogBatch: ...


class DockerEngineProvider:
    def __init__(self, transport: DockerTransport) -> None:
        self.transport = transport

    def find_container(self, selector: DockerSelector) -> DockerContainer | None:
        raw = self.transport.get_json("/containers/json", {"all": "1"})
        if not isinstance(raw, list):
            raise DockerTransportError("Docker container list must be an array")
        matches: list[DockerContainer] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            labels_value: object = item.get("Labels")
            labels = labels_value if isinstance(labels_value, dict) else {}
            names_value: object = item.get("Names")
            names = names_value if isinstance(names_value, list) else []
            clean_names = [str(name).removeprefix("/") for name in names]
            by_name = selector.container_name is not None and selector.container_name in clean_names
            by_compose = (
                selector.compose_project is not None
                and selector.compose_service is not None
                and labels.get("com.docker.compose.project") == selector.compose_project
                and labels.get("com.docker.compose.service") == selector.compose_service
            )
            if by_name or by_compose:
                matches.append(
                    DockerContainer(
                        container_id=str(item.get("Id") or ""),
                        name=clean_names[0] if clean_names else "",
                        image=str(item.get("Image") or ""),
                    )
                )
        if len(matches) > 1:
            raise DockerSelectorAmbiguous(f"docker selector matched {len(matches)} containers")
        return matches[0] if matches else None

    def inspect(self, container_id: str) -> DockerSnapshot:
        raw = self.transport.get_json(f"/containers/{container_id}/json")
        if not isinstance(raw, dict):
            raise DockerTransportError("Docker inspect response must be an object")
        state_value: object = raw.get("State")
        state = state_value if isinstance(state_value, dict) else {}
        health_value: object = state.get("Health")
        health_raw = health_value if isinstance(health_value, dict) else None
        config_value: object = raw.get("Config")
        config = config_value if isinstance(config_value, dict) else {}
        memory_bytes: int | None = None
        try:
            stats = self.transport.get_json(f"/containers/{container_id}/stats", {"stream": "0"})
            if isinstance(stats, dict):
                memory = stats.get("memory_stats")
                if isinstance(memory, dict) and isinstance(memory.get("usage"), int):
                    memory_bytes = int(memory["usage"])
        except DockerTransportError:
            memory_bytes = None
        status = str(state.get("Status") or "unknown")
        return DockerSnapshot(
            container_id=str(raw.get("Id") or container_id),
            name=str(raw.get("Name") or "").removeprefix("/"),
            image=str(config.get("Image") or ""),
            state=status,
            status=status,
            health=str(health_raw.get("Status")) if health_raw else None,
            restart_count=max(0, int(raw.get("RestartCount") or 0)),
            exit_code=int(state.get("ExitCode") or 0),
            oom_killed=bool(state.get("OOMKilled", False)),
            started_at=self._optional_string(state.get("StartedAt")),
            finished_at=self._optional_string(state.get("FinishedAt")),
            memory_bytes=memory_bytes,
        )

    def logs(
        self,
        container_id: str,
        cursor: DockerLogCursor,
        *,
        limit: int,
        tail: int,
    ) -> DockerLogBatch:
        if limit <= 0:
            raise ValueError("limit must be positive")
        params: dict[str, str | int] = {
            "stdout": "1",
            "stderr": "1",
            "timestamps": "1",
        }
        if cursor.last_timestamp is None:
            params["tail"] = tail
        else:
            params["since"] = cursor.last_timestamp
        body = self.transport.get_bytes(f"/containers/{container_id}/logs", params)
        parsed = self._parse_log_streams(body)
        last_timestamp = cursor.last_timestamp
        recent_hashes = set(cursor.recent_hashes)
        lines: list[DockerLogLine] = []
        for stream, raw_line in parsed:
            timestamp, line = self._split_timestamp(raw_line)
            digest = sha256(f"{stream}\0{timestamp}\0{line}".encode()).hexdigest()
            if last_timestamp is not None and timestamp < last_timestamp:
                continue
            if timestamp == last_timestamp and digest in recent_hashes:
                continue
            if last_timestamp is None or timestamp > last_timestamp:
                last_timestamp = timestamp
                recent_hashes = {digest}
            else:
                recent_hashes.add(digest)
            lines.append(
                DockerLogLine(
                    timestamp=timestamp,
                    stream=stream,
                    line=line,
                    content_hash=digest,
                )
            )
            if len(lines) >= limit:
                break
        return DockerLogBatch(
            lines=tuple(lines),
            cursor=DockerLogCursor(
                last_timestamp=last_timestamp,
                recent_hashes=tuple(sorted(recent_hashes))[-100:],
            ),
        )

    @staticmethod
    def _parse_log_streams(body: bytes) -> list[tuple[str, bytes]]:
        if len(body) >= 8 and body[0] in (1, 2) and body[1:4] == b"\0\0\0":
            result: list[tuple[str, bytes]] = []
            position = 0
            while position + 8 <= len(body):
                stream_id = body[position]
                if stream_id not in (1, 2) or body[position + 1 : position + 4] != b"\0\0\0":
                    raise DockerTransportError("invalid Docker multiplex log frame")
                size = struct.unpack(">I", body[position + 4 : position + 8])[0]
                start = position + 8
                end = start + size
                if end > len(body):
                    raise DockerTransportError("truncated Docker multiplex log frame")
                stream = "stdout" if stream_id == 1 else "stderr"
                result.extend((stream, line) for line in body[start:end].splitlines())
                position = end
            if position != len(body):
                raise DockerTransportError("trailing bytes in Docker multiplex log stream")
            return result
        return [("stdout", line) for line in body.splitlines()]

    @staticmethod
    def _split_timestamp(raw_line: bytes) -> tuple[str, str]:
        try:
            encoded_timestamp, encoded_line = raw_line.split(b" ", 1)
            timestamp = encoded_timestamp.decode("ascii")
            line = encoded_line.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as error:
            raise DockerTransportError("Docker log line lacks a valid timestamp") from error
        DockerEngineProvider._validate_timestamp(timestamp)
        return timestamp, line

    @staticmethod
    def _validate_timestamp(value: str) -> None:
        normalized = value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
        if "." in normalized:
            prefix, suffix = normalized.split(".", 1)
            fraction, timezone = suffix.split("+", 1) if "+" in suffix else (suffix, "")
            normalized = f"{prefix}.{fraction[:6]}" + (f"+{timezone}" if timezone else "")
        try:
            datetime.fromisoformat(normalized)
        except ValueError as error:
            raise DockerTransportError(f"invalid Docker log timestamp: {value}") from error

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return str(value) if value is not None else None


class UnavailableDockerProvider:
    """Read-only sentinel used when a Docker endpoint is not configured."""

    def __init__(self, reason: str = "Docker endpoint is not configured") -> None:
        self.reason = reason

    def _raise(self) -> NoReturn:
        raise DockerTransportError(self.reason)

    def find_container(self, selector: DockerSelector) -> DockerContainer | None:
        self._raise()

    def inspect(self, container_id: str) -> DockerSnapshot:
        self._raise()

    def logs(
        self,
        container_id: str,
        cursor: DockerLogCursor,
        *,
        limit: int,
        tail: int,
    ) -> DockerLogBatch:
        self._raise()
