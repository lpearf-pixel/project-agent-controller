#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

from project_agent_controller.domain.models import DockerSelector
from project_agent_controller.observer.docker_provider import (
    DockerEngineProvider,
    DockerLogCursor,
)
from project_agent_controller.observer.docker_transport import (
    DockerTransportError,
    UnixSocketDockerTransport,
    validate_docker_request,
)


def main() -> None:
    socket_path = Path(required_environment("PAC_DOCKER_SOCKET"))
    compose_project = required_environment("PAC_DOCKER_COMPOSE_PROJECT")
    compose_service = required_environment("PAC_DOCKER_COMPOSE_SERVICE")
    provider = DockerEngineProvider(UnixSocketDockerTransport(socket_path))
    container = provider.find_container(
        DockerSelector(
            compose_project=compose_project,
            compose_service=compose_service,
        )
    )
    if container is None:
        raise SystemExit("v0.1D Docker preflight selector matched no container")

    snapshot = provider.inspect(container.container_id)
    log_batch = provider.logs(
        container.container_id,
        DockerLogCursor(),
        limit=10,
        tail=10,
    )
    safe_snapshot = snapshot.model_dump(mode="json")
    forbidden_fields = {"Env", "Mounts", "Labels", "Config", "HostConfig"}
    if forbidden_fields.intersection(safe_snapshot):
        raise SystemExit("v0.1D Docker preflight exposed a forbidden field")
    if not log_batch.lines:
        raise SystemExit("v0.1D Docker preflight expected one timestamped fixture log")

    try:
        validate_docker_request("POST", f"/containers/{container.container_id}/stop")
    except DockerTransportError:
        pass
    else:
        raise SystemExit("v0.1D Docker preflight accepted a Docker write request")

    print(
        json.dumps(
            {
                "container_name": snapshot.name,
                "health": snapshot.health,
                "log_lines": len(log_batch.lines),
                "state": snapshot.state,
            },
            sort_keys=True,
        )
    )


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise SystemExit(f"{name} is required")
    return value


if __name__ == "__main__":
    main()
