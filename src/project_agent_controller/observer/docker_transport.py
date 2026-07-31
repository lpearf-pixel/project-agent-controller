from __future__ import annotations

import http.client
import json
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit


class DockerTransportError(RuntimeError):
    pass


_ALLOWED_PATHS = (
    re.compile(r"^/_ping$"),
    re.compile(r"^/containers/json$"),
    re.compile(r"^/containers/[^/]+/json$"),
    re.compile(r"^/containers/[^/]+/logs$"),
    re.compile(r"^/containers/[^/]+/stats$"),
)


def validate_docker_request(method: str, path: str) -> None:
    if method.upper() != "GET":
        raise DockerTransportError("Docker transport is GET-only")
    clean_path = urlsplit(path).path
    if not any(pattern.fullmatch(clean_path) for pattern in _ALLOWED_PATHS):
        raise DockerTransportError(f"Docker GET path is not allowed: {clean_path}")


class DockerHTTPResponse(Protocol):
    status: int

    def read(self, amount: int | None = None) -> bytes: ...


class DockerHTTPConnection(Protocol):
    def request(self, method: str, path: str) -> None: ...
    def getresponse(self) -> DockerHTTPResponse: ...
    def close(self) -> None: ...


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float = 5.0) -> None:
        super().__init__(host="localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(str(self.socket_path))
        self.sock = connection


class UnixSocketDockerTransport:
    def __init__(
        self,
        socket_path: Path,
        *,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        connection_factory: Callable[[Path], DockerHTTPConnection] | None = None,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.connection_factory = connection_factory or (
            lambda path: UnixHTTPConnection(path, timeout=timeout_seconds)
        )

    def get_bytes(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> bytes:
        validate_docker_request("GET", path)
        target = path
        if params:
            target = f"{path}?{urlencode(params)}"
        connection = self.connection_factory(self.socket_path)
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            body = response.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise DockerTransportError(
                    f"Docker response exceeds {self.max_response_bytes} bytes"
                )
            if response.status < 200 or response.status >= 300:
                message = body.decode("utf-8", errors="replace")[:300]
                raise DockerTransportError(f"Docker HTTP {response.status}: {message}")
            return body
        except OSError as error:
            raise DockerTransportError(f"Docker endpoint unavailable: {error}") from error
        finally:
            connection.close()

    def get_json(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> Any:
        body = self.get_bytes(path, params)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DockerTransportError("Docker returned invalid JSON") from error
