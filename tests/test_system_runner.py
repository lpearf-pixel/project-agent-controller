from datetime import UTC, datetime
from pathlib import Path

import pytest

from project_agent_controller.control.service import ControlService
from project_agent_controller.domain.models import (
    DockerSourceConfig,
    EventRecord,
    ProcessSourceConfig,
    ProjectConfig,
    Severity,
)
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.runner import ObservationBlocked, ObserverRunner
from project_agent_controller.storage.database import Database


class FakeSystemObserver:
    def __init__(self, kind: str):
        self.kind = kind
        self.calls = 0

    def observe(self, project_id, run_id, source, previous):
        self.calls += 1
        sequence = 1 if previous is None else previous.sequence + 1
        event = EventRecord(
            event_id=f"evt-{project_id}-{source.source_id}-{sequence}",
            project_id=project_id,
            run_id=run_id,
            source_id=source.source_id,
            sequence=sequence,
            event_type=f"{self.kind}.heartbeat",
            severity=Severity.INFO,
            occurred_at=datetime.now(UTC),
            payload={},
            evidence_ref=f"{self.kind}://evidence",
        )
        return SourceObservation(
            events=(event,),
            state=SourceState(
                project_id=project_id,
                source_id=source.source_id,
                source_kind=self.kind,
                sequence=sequence,
                observed_at=datetime.now(UTC),
                state={"presence": "available"},
            ),
        )


def build_runner(tmp_path: Path):
    database = Database(tmp_path / "controller.db")
    database.initialize()
    control = ControlService(database)
    process_observer = FakeSystemObserver("process")
    docker_observer = FakeSystemObserver("docker")
    runner = ObserverRunner(
        database,
        control,
        local_root=tmp_path,
        run_id="run-1",
        process_observer=process_observer,
        docker_observer=docker_observer,
    )
    return database, control, process_observer, docker_observer, runner


def test_dispatches_process_and_docker_sources(tmp_path: Path) -> None:
    _database, _, process_observer, docker_observer, runner = build_runner(tmp_path)
    project = ProjectConfig(
        project_id="demo",
        display_name="Demo",
        sources=(
            ProcessSourceConfig(source_id="worker", pid_file_ref="local://worker.pid"),
            DockerSourceConfig(
                source_id="db",
                selector={"compose_project": "demo", "compose_service": "db"},
            ),
        ),
    )

    emitted = runner.observe_once(project)

    assert emitted == 2
    assert process_observer.calls == 1
    assert docker_observer.calls == 1
    assert runner.source_states.get("demo", "worker") is not None
    assert runner.source_states.get("demo", "db") is not None


def test_same_source_id_is_isolated_by_project(tmp_path: Path) -> None:
    _database, _, _, _, runner = build_runner(tmp_path)
    source = ProcessSourceConfig(source_id="worker", pid_file_ref="local://worker.pid")

    runner.observe_once(
        ProjectConfig(project_id="one", display_name="One", sources=(source,))
    )
    runner.observe_once(
        ProjectConfig(project_id="two", display_name="Two", sources=(source,))
    )

    assert runner.source_states.get("one", "worker") is not None
    assert runner.source_states.get("two", "worker") is not None


def test_emergency_stop_blocks_before_provider_calls(tmp_path: Path) -> None:
    _, control, process_observer, docker_observer, runner = build_runner(tmp_path)
    control.emergency_stop(actor="test", reason="stop")
    project = ProjectConfig(
        project_id="demo",
        display_name="Demo",
        sources=(
            ProcessSourceConfig(source_id="worker", pid_file_ref="local://worker.pid"),
            DockerSourceConfig(
                source_id="db",
                selector={"compose_project": "demo", "compose_service": "db"},
            ),
        ),
    )

    with pytest.raises(ObservationBlocked):
        runner.observe_once(project)

    assert process_observer.calls == 0
    assert docker_observer.calls == 0
