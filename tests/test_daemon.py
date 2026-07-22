import time
from pathlib import Path

from fastapi.testclient import TestClient

from project_agent_controller.app import create_app
from project_agent_controller.runtime import build_runtime
from project_agent_controller.settings import Settings


def daemon_settings(tmp_path: Path) -> Settings:
    projects_file = tmp_path / "projects.yaml"
    projects_file.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo
    technologies: [python]
    sources:
      - source_id: app-log
        kind: file
        path_ref: local://demo/app.log
""".strip(),
        encoding="utf-8",
    )
    return Settings(
        data_dir=tmp_path / "data",
        projects_file=projects_file,
        local_sources_root=tmp_path / "logs",
        poll_interval_seconds=0.01,
    )


def test_daemon_cycle_observes_all_projects_and_respects_stop(tmp_path: Path) -> None:
    settings = daemon_settings(tmp_path)
    log_path = settings.local_sources_root / "demo/app.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("ready\n", encoding="utf-8")
    runtime = build_runtime(settings)

    result = runtime.daemon.run_cycle()

    assert result.emitted_events == 1
    assert result.failed_projects == ()
    runtime.control.emergency_stop(actor="local-admin", reason="test")
    stopped = runtime.daemon.run_cycle()
    assert stopped.emitted_events == 0
    assert stopped.skipped_state == "EMERGENCY_STOP"


def test_fastapi_lifespan_runs_and_stops_file_observer(tmp_path: Path) -> None:
    settings = daemon_settings(tmp_path)
    log_path = settings.local_sources_root / "demo/app.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("", encoding="utf-8")
    app = create_app(settings)

    with TestClient(app) as client:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("background-ready\n")
        deadline = time.monotonic() + 1.0
        payload = []
        while time.monotonic() < deadline:
            response = client.get("/v1/projects/demo/events?limit=10")
            payload = response.json()
            if payload:
                break
            time.sleep(0.01)
        assert payload[0]["payload"]["line"] == "background-ready"

    assert app.state.runtime.daemon.is_running is False
