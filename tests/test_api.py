from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_agent_controller.app import create_app
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.domain.models import EventRecord, Severity
from project_agent_controller.settings import Settings


def api_settings(tmp_path: Path, *, host: str = "127.0.0.1") -> Settings:
    projects_file = tmp_path / "projects.yaml"
    projects_file.write_text(
        """
config_version: 1
projects:
  - project_id: demo
    display_name: Demo Project
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
        knowledge_dir=tmp_path / "knowledge",
        local_sources_root=tmp_path / "logs",
        host=host,
    )


def test_health_and_projects_do_not_expose_absolute_paths(tmp_path: Path) -> None:
    settings = api_settings(tmp_path)
    client = TestClient(create_app(settings))

    health = client.get("/health")
    projects = client.get("/v1/projects")

    assert health.status_code == 200
    assert health.json()["controller_state"] == "ACTIVE"
    assert projects.status_code == 200
    payload = projects.json()
    assert payload == [
        {
            "project_id": "demo",
            "display_name": "Demo Project",
            "technologies": ["python"],
            "sources": [{"source_id": "app-log", "kind": "file"}],
        }
    ]
    assert str(tmp_path) not in projects.text


def test_observe_once_persists_events_and_emergency_stop_blocks(tmp_path: Path) -> None:
    settings = api_settings(tmp_path)
    log_path = settings.local_sources_root / "demo/app.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("ready\n", encoding="utf-8")
    client = TestClient(create_app(settings))

    observed = client.post("/v1/projects/demo/observe-once")
    events = client.get("/v1/projects/demo/events?limit=10")
    stopped = client.post(
        "/v1/controller/emergency-stop",
        json={"actor": "local-admin", "reason": "test"},
    )
    blocked = client.post("/v1/projects/demo/observe-once")

    assert observed.status_code == 200
    assert observed.json()["emitted_events"] == 1
    assert events.status_code == 200
    assert events.json()[0]["payload"]["line"] == "ready"
    assert stopped.json()["state"] == "EMERGENCY_STOP"
    assert blocked.status_code == 409


def test_incident_brief_endpoint_returns_bounded_evidence(tmp_path: Path) -> None:
    app = create_app(api_settings(tmp_path))
    runtime = app.state.runtime
    event = EventRecord(
        event_id="evt-api",
        project_id="demo",
        run_id="run-api",
        source_id="app-log",
        sequence=1,
        event_type="process.failed",
        severity=Severity.ERROR,
        occurred_at=datetime(2026, 7, 22, tzinfo=UTC),
        payload={"line": "ERROR user=test@example.com"},
        evidence_ref="artifact://sha256/api",
    )
    runtime.database.append_event(event)
    incident = IncidentService(runtime.database).ingest(event)
    assert incident is not None
    client = TestClient(app)

    response = client.get(f"/v1/incidents/{incident.incident_id}/brief?max_bytes=2048")

    assert response.status_code == 200
    assert response.json()["incident_id"] == incident.incident_id
    assert "test@example.com" not in response.text


def test_control_request_requires_actor_and_reason(tmp_path: Path) -> None:
    client = TestClient(create_app(api_settings(tmp_path)))

    response = client.post("/v1/controller/drain", json={})

    assert response.status_code == 422


def test_non_loopback_host_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        create_app(api_settings(tmp_path, host="0.0.0.0"))
