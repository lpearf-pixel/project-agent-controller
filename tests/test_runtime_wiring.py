from project_agent_controller.observer.docker_source import DockerSourceObserver
from project_agent_controller.observer.process_source import ProcessSourceObserver
from project_agent_controller.runtime import build_runtime
from project_agent_controller.settings import Settings


def test_runtime_wires_configured_process_and_docker_observers(tmp_path) -> None:
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: worker
        kind: process
        pid_file_ref: local://demo/worker.pid
      - source_id: db
        kind: docker
        selector:
          compose_project: demo
          compose_service: db
""".strip(),
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        projects_file=projects,
        local_sources_root=tmp_path / "sources",
        docker_socket=None,
    )

    runtime = build_runtime(settings)

    assert isinstance(runtime.observer.process_observer, ProcessSourceObserver)
    assert isinstance(runtime.observer.docker_observer, DockerSourceObserver)


def test_runtime_omits_unused_system_observers(tmp_path) -> None:
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources: []
""".strip(),
        encoding="utf-8",
    )
    runtime = build_runtime(
        Settings(
            data_dir=tmp_path / "data",
            projects_file=projects,
            local_sources_root=tmp_path / "sources",
        )
    )

    assert runtime.observer.process_observer is None
    assert runtime.observer.docker_observer is None


def test_missing_docker_socket_becomes_observable_state(tmp_path) -> None:
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    sources:
      - source_id: db
        kind: docker
        selector:
          compose_project: demo
          compose_service: db
""".strip(),
        encoding="utf-8",
    )
    runtime = build_runtime(
        Settings(
            data_dir=tmp_path / "data",
            projects_file=projects,
            local_sources_root=tmp_path / "sources",
            docker_socket=None,
        )
    )

    emitted = runtime.observer.observe_once(runtime.registry.get("demo"))

    assert emitted == 1
    state = runtime.source_states.get("demo", "db")
    assert state is not None
    assert state.state["error_kind"] == "provider_unavailable"
