from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from project_agent_controller.domain.models import ProjectConfig, ProjectsConfig
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.state_store import SourceStateStore
from project_agent_controller.registry.service import ProjectRegistry
from project_agent_controller.storage.database import Database


def build_runtime(tmp_path):
    database = Database(tmp_path / "controller.db")
    database.initialize()
    registry = ProjectRegistry(
        ProjectsConfig(
            config_version=1,
            projects=(ProjectConfig(project_id="demo", display_name="Demo"),),
        )
    )
    state = SourceState(
        project_id="demo",
        source_id="worker",
        source_kind="process",
        sequence=2,
        observed_at=datetime.now(UTC),
        state={
            "presence": "available",
            "pid": 42,
            "name": "python",
            "executable": "python3",
            "rss_bytes": 2048,
        },
    )
    source_states = SourceStateStore(database)
    source_states.append(SourceObservation(events=(), state=state))
    return SimpleNamespace(
        database=database,
        registry=registry,
        source_states=source_states,
    )


def test_source_state_route_is_sanitized(tmp_path) -> None:
    from project_agent_controller.api.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.runtime = build_runtime(tmp_path)
    response = TestClient(app).get("/v1/projects/demo/sources")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["source_id"] == "worker"
    assert payload[0]["source_kind"] == "process"
    rendered = response.text.lower()
    for forbidden in (
        "pid_file_ref",
        "docker_socket",
        "environment",
        "mounts",
        "labels",
        "cmdline",
        "/users/",
        "/home/",
    ):
        assert forbidden not in rendered


def test_source_state_route_rejects_unknown_project(tmp_path) -> None:
    from project_agent_controller.api.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.runtime = build_runtime(tmp_path)

    assert TestClient(app).get("/v1/projects/missing/sources").status_code == 404


def test_sources_cli_uses_same_sanitized_state(tmp_path, monkeypatch) -> None:
    from project_agent_controller import cli

    monkeypatch.setattr(cli, "build_runtime", lambda settings: build_runtime(tmp_path))
    result = CliRunner().invoke(cli.app, ["sources", "demo"])

    assert result.exit_code == 0
    assert '"source_id": "worker"' in result.stdout
    assert "pid_file_ref" not in result.stdout
