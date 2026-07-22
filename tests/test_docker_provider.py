import struct

import pytest

from project_agent_controller.domain.models import DockerSelector
from project_agent_controller.observer.docker_provider import (
    DockerEngineProvider,
    DockerLogCursor,
    DockerSelectorAmbiguous,
)


class FakeTransport:
    def __init__(self, *, containers=None, inspect=None, logs=b"", stats=None):
        self.containers = containers or []
        self.inspect_payload = inspect or {}
        self.logs_payload = logs
        self.stats_payload = stats or {}
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append(("json", path, params))
        if path == "/containers/json":
            return self.containers
        if path.endswith("/json"):
            return self.inspect_payload
        if path.endswith("/stats"):
            return self.stats_payload
        raise AssertionError(path)

    def get_bytes(self, path, params=None):
        self.calls.append(("bytes", path, params))
        return self.logs_payload


def container(container_id="abc", name="/demo-db-1", project="demo", service="db"):
    return {
        "Id": container_id,
        "Names": [name],
        "Image": "postgres:16",
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
            "secret.label": "must-not-leak",
        },
    }


def inspect_payload():
    return {
        "Id": "abc",
        "Name": "/demo-db-1",
        "Config": {"Image": "postgres:16", "Env": ["TOKEN=secret"]},
        "RestartCount": 2,
        "State": {
            "Status": "running",
            "Running": True,
            "ExitCode": 0,
            "OOMKilled": False,
            "StartedAt": "2026-07-22T00:00:00Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
            "Health": {"Status": "healthy", "Log": [{"Output": "secret"}]},
        },
        "Mounts": [{"Source": "/private"}],
    }


def frame(stream: int, payload: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


def test_compose_selector_matches_exactly_one_container() -> None:
    provider = DockerEngineProvider(FakeTransport(containers=[container()]))

    result = provider.find_container(
        DockerSelector(compose_project="demo", compose_service="db")
    )

    assert result is not None
    assert result.container_id == "abc"
    assert result.name == "demo-db-1"


def test_ambiguous_selector_fails_closed() -> None:
    provider = DockerEngineProvider(
        FakeTransport(containers=[container("a"), container("b", "/demo-db-2")])
    )

    with pytest.raises(DockerSelectorAmbiguous, match="matched 2 containers"):
        provider.find_container(
            DockerSelector(compose_project="demo", compose_service="db")
        )


def test_inspect_returns_only_normalized_safe_fields() -> None:
    provider = DockerEngineProvider(
        FakeTransport(
            inspect=inspect_payload(),
            stats={"memory_stats": {"usage": 1234}},
        )
    )

    result = provider.inspect("abc")
    dumped = result.model_dump()

    assert result.health == "healthy"
    assert result.restart_count == 2
    assert result.memory_bytes == 1234
    assert "Env" not in dumped
    assert "Mounts" not in dumped
    assert "labels" not in dumped


def test_timestamped_multiplex_logs_are_parsed_and_deduplicated() -> None:
    payload = frame(
        1,
        b"2026-07-22T00:00:01.000000000Z first\n"
        b"2026-07-22T00:00:02.000000000Z second\n",
    )
    provider = DockerEngineProvider(FakeTransport(logs=payload))

    first = provider.logs("abc", DockerLogCursor(), limit=10, tail=100)
    second = provider.logs("abc", first.cursor, limit=10, tail=100)

    assert [line.stream for line in first.lines] == ["stdout", "stdout"]
    assert [line.line for line in first.lines] == ["first", "second"]
    assert first.cursor.last_timestamp == "2026-07-22T00:00:02.000000000Z"
    assert second.lines == ()


def test_plain_tty_logs_and_line_limit() -> None:
    payload = (
        b"2026-07-22T00:00:01Z one\n"
        b"2026-07-22T00:00:02Z two\n"
    )
    provider = DockerEngineProvider(FakeTransport(logs=payload))

    result = provider.logs("abc", DockerLogCursor(), limit=1, tail=100)

    assert len(result.lines) == 1
    assert result.lines[0].line == "one"
