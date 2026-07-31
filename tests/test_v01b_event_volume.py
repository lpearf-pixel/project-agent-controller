from datetime import UTC, datetime, timedelta
from pathlib import Path

from project_agent_controller.domain.models import DockerSourceConfig, ProcessSourceConfig
from project_agent_controller.observer.docker_provider import (
    DockerContainer,
    DockerLogBatch,
    DockerLogCursor,
    DockerSnapshot,
)
from project_agent_controller.observer.docker_source import DockerSourceObserver
from project_agent_controller.observer.process_provider import ProcessSnapshot
from project_agent_controller.observer.process_source import ProcessSourceObserver


class ManualClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 22, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class StableProcessProvider:
    def snapshot(self, pid: int) -> ProcessSnapshot:
        return ProcessSnapshot(
            pid=pid,
            create_time=100.0,
            status="running",
            name="python",
            executable="python3",
            cpu_seconds=10.0,
            rss_bytes=1024,
            child_count=0,
        )


class StableDockerProvider:
    def find_container(self, selector):
        return DockerContainer(container_id="abc", name="demo-db-1", image="postgres:16")

    def inspect(self, container_id: str) -> DockerSnapshot:
        return DockerSnapshot(
            container_id=container_id,
            name="demo-db-1",
            image="postgres:16",
            state="running",
            status="running",
            health="healthy",
            restart_count=0,
            exit_code=0,
            oom_killed=False,
            started_at="2026-07-22T00:00:00Z",
            finished_at=None,
            memory_bytes=1024,
        )

    def logs(self, container_id, cursor, *, limit, tail):
        return DockerLogBatch(lines=(), cursor=cursor or DockerLogCursor())


def test_stable_process_500_cycles_emit_only_initial_event(tmp_path: Path) -> None:
    pid_file = tmp_path / "demo/worker.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("42\n", encoding="utf-8")
    clock = ManualClock()
    observer = ProcessSourceObserver(tmp_path, StableProcessProvider(), clock=clock)
    source = ProcessSourceConfig(
        source_id="worker",
        pid_file_ref="local://demo/worker.pid",
        heartbeat_seconds=30,
    )
    previous = None
    emitted = 0
    for _ in range(500):
        result = observer.observe("demo", "run-1", source, previous)
        previous = result.state
        emitted += len(result.events)
        clock.advance(0.005)

    assert emitted == 1

    clock.advance(31)
    heartbeat = observer.observe("demo", "run-1", source, previous)
    assert [event.event_type for event in heartbeat.events] == ["process.heartbeat"]


def test_stable_docker_500_cycles_emit_only_initial_event() -> None:
    clock = ManualClock()
    observer = DockerSourceObserver(StableDockerProvider(), clock=clock)
    source = DockerSourceConfig(
        source_id="db",
        selector={"compose_project": "demo", "compose_service": "db"},
        include_logs=False,
        heartbeat_seconds=30,
    )
    previous = None
    emitted = 0
    for _ in range(500):
        result = observer.observe("demo", "run-1", source, previous)
        previous = result.state
        emitted += len(result.events)
        clock.advance(0.005)

    assert emitted == 1

    clock.advance(31)
    heartbeat = observer.observe("demo", "run-1", source, previous)
    assert [event.event_type for event in heartbeat.events] == ["docker.heartbeat"]
