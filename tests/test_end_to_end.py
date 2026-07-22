from pathlib import Path

import pytest
import yaml

from project_agent_controller.curation.fingerprint import fingerprint_event
from project_agent_controller.curation.incidents import IncidentService
from project_agent_controller.observer.runner import ObservationBlocked
from project_agent_controller.runtime import build_runtime
from project_agent_controller.settings import Settings


def test_file_log_to_incident_brief_lesson_and_stop(tmp_path: Path) -> None:
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
        parser: text-v1
""".strip(),
        encoding="utf-8",
    )
    knowledge_dir = tmp_path / "knowledge"
    lesson_path = knowledge_dir / "shared/lessons/database-timeout.yaml"
    lesson_path.parent.mkdir(parents=True)
    lesson_path.write_text(
        yaml.safe_dump(
            {
                "entry_type": "lesson",
                "lesson_id": "LESSON-E2E-0001",
                "scope": "shared",
                "status": "SHARED_APPROVED",
                "title": "Database timeout evidence",
                "summary": "Compare the first and last database timeout before retrying.",
                "applicability": {
                    "technologies": ["python"],
                    "workflows": ["incident-diagnosis"],
                },
                "verification_refs": ["test://end-to-end"],
                "counterexamples": ["A deterministic one-line exit result"],
                "review_after": "2027-01-01",
                "generated_by_ai": False,
                "fingerprints": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        projects_file=projects_file,
        knowledge_dir=knowledge_dir,
        local_sources_root=tmp_path / "logs",
    )
    log_path = settings.local_sources_root / "demo/app.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("ready\n", encoding="utf-8")
    runtime = build_runtime(settings)
    project = runtime.registry.get("demo")

    assert runtime.observer.observe_once(project) == 1
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("ERROR E900 database timeout pid=1001\n")
        handle.write("ERROR E900 database timeout pid=1002\n")
    assert runtime.observer.observe_once(project) == 2

    events = runtime.database.list_events("demo", limit=10)
    error_events = [event for event in events if event.severity.value == "error"]
    assert len(error_events) == 2
    fingerprint = fingerprint_event(error_events[0])
    incident = IncidentService(runtime.database).find("demo", fingerprint)
    assert incident is not None
    assert incident.occurrence_count == 2

    brief = runtime.brief_builder.build(incident.incident_id)
    assert len(brief.to_json_bytes()) <= 65_536
    assert brief.evidence_refs
    assert all(ref.startswith("artifact://sha256/") for ref in brief.evidence_refs)

    matches = runtime.knowledge_index.match(
        project,
        fingerprint,
        query="database timeout",
    )
    assert [item.entry_id for item in matches.items] == ["LESSON-E2E-0001"]

    runtime.control.emergency_stop(actor="local-admin", reason="end-to-end stop")
    with pytest.raises(ObservationBlocked, match="EMERGENCY_STOP"):
        runtime.observer.observe_once(project)
