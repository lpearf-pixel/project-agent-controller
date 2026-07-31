import json
from pathlib import Path

import pytest

from project_agent_controller.observer.docker_transport import (
    DockerTransportError,
    UnixSocketDockerTransport,
    validate_docker_request,
)


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            return self._body
        return self._body[:amount]


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def request(self, method: str, path: str) -> None:
        self.requests.append((method, path))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_rejects_non_get_requests() -> None:
    with pytest.raises(DockerTransportError, match="GET-only"):
        validate_docker_request("POST", "/containers/demo/stop")


def test_rejects_unapproved_get_path() -> None:
    with pytest.raises(DockerTransportError, match="not allowed"):
        validate_docker_request("GET", "/containers/demo/archive")


def test_json_request_uses_allowlisted_get() -> None:
    connection = FakeConnection(FakeResponse(200, json.dumps([{"Id": "abc"}]).encode()))
    transport = UnixSocketDockerTransport(
        Path("/tmp/docker.sock"),
        connection_factory=lambda _: connection,
    )

    result = transport.get_json("/containers/json", {"all": "1"})

    assert result == [{"Id": "abc"}]
    assert connection.requests == [("GET", "/containers/json?all=1")]
    assert connection.closed is True


def test_non_success_status_is_normalized() -> None:
    connection = FakeConnection(FakeResponse(404, b'{"message":"missing"}'))
    transport = UnixSocketDockerTransport(
        Path("/tmp/docker.sock"),
        connection_factory=lambda _: connection,
    )

    with pytest.raises(DockerTransportError, match="HTTP 404"):
        transport.get_json("/containers/demo/json")


def test_response_limit_is_enforced() -> None:
    connection = FakeConnection(FakeResponse(200, b"x" * 20))
    transport = UnixSocketDockerTransport(
        Path("/tmp/docker.sock"),
        max_response_bytes=10,
        connection_factory=lambda _: connection,
    )

    with pytest.raises(DockerTransportError, match="exceeds 10 bytes"):
        transport.get_bytes("/_ping")
