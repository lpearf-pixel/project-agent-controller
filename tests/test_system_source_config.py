from pathlib import Path

import pytest

from project_agent_controller.config.loader import load_projects
from project_agent_controller.domain.models import DockerSourceConfig, ProcessSourceConfig


def test_loads_process_and_docker_sources(tmp_path: Path) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: worker
        kind: process
        pid_file_ref: local://demo/worker.pid
        heartbeat_seconds: 300
      - source_id: db
        kind: docker
        selector:
          compose_project: demo
          compose_service: db
        include_logs: true
""".strip(),
        encoding="utf-8",
    )

    config = load_projects(path)

    assert isinstance(config.projects[0].sources[0], ProcessSourceConfig)
    assert isinstance(config.projects[0].sources[1], DockerSourceConfig)


def test_docker_selector_rejects_name_and_compose_pair() -> None:
    with pytest.raises(ValueError, match="exactly one selector mode"):
        DockerSourceConfig.model_validate(
            {
                "source_id": "db",
                "kind": "docker",
                "selector": {
                    "container_name": "demo-db-1",
                    "compose_project": "demo",
                    "compose_service": "db",
                },
            }
        )


def test_process_pid_file_must_use_local_ref() -> None:
    with pytest.raises(ValueError, match="pid_file_ref must use local://"):
        ProcessSourceConfig(
            source_id="worker",
            pid_file_ref="/tmp/worker.pid",
        )
